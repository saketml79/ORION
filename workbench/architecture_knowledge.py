"""Bounded, data-only architecture knowledge. No inference, execution or implicit writes.

Release claims are extracted from independently fetched official documentation,
not supplied by a model. Historical GA evidence is not a claim of latest coverage.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
import ipaddress
import json
import re
import socket
from time import monotonic
from urllib.parse import urljoin, urlsplit

import httpx
import networkx as nx
from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated, Literal

from workbench import delta_state as ds

FORMAT = "orion-architecture-knowledge-v1"
MAX_BYTES = 1024 * 1024
MAX_NODES, MAX_EDGES = 500, 2000
PILLARS = ("optimization", "accuracy", "integrity", "security", "databricks_architecture", "data_validation")
HASH = r"^[a-f0-9]{64}$"
ShortText = Annotated[str, Field(min_length=1, max_length=400)]


def now():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat()


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("Knowledge timestamps require UTC")
    return parsed


def official_url(value):
    p = urlsplit(value)
    databricks_path = p.netloc == "docs.databricks.com" and re.fullmatch(r"/(aws|gcp)/en/[a-zA-Z0-9/_.,-]+", p.path)
    spark_path = p.netloc == "spark.apache.org" and re.fullmatch(
        r"/docs/latest(?:/[A-Za-z0-9/_.,-]+)?", p.path)
    if (p.scheme != "https" or not (databricks_path or spark_path) or p.username or p.password
            or p.query
            or any(part in {".", ".."} for part in p.path.split("/"))
            or not re.fullmatch(r"[a-zA-Z0-9_-]*", p.fragment) or len(value) > 500):
        raise ValueError("Only allowlisted current Databricks and Apache Spark documentation URLs are allowed")
    return value


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Source(Strict):
    url: str = Field(max_length=500)
    retrieved_at: str
    expires_at: str
    published_at: str | None
    updated_at: str | None = None
    sha256: str = Field(pattern=HASH)
    section_sha256: str = Field(pattern=HASH)
    title: str = Field(min_length=1, max_length=240)
    excerpt: str = Field(max_length=240)
    release_statement: str | None = Field(default=None, max_length=240)


class Node(Strict):
    id: str = Field(pattern=HASH)
    kind: Literal["feature", "document", "pattern", "constraint", "pillar"]
    label: str = Field(min_length=1, max_length=180)
    summary: str = Field(max_length=500)
    release_state: Literal["GA", "preview", "deprecated", "unknown"] = "unknown"
    verification: Literal["curated", "unverified"] = "unverified"
    source: Source | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    platforms: list[Literal["aws", "gcp", "spark"]] = Field(default_factory=list, max_length=3)
    region_constraint: str = Field(default="Workspace and region availability must be checked", max_length=300)
    applicability: list[ShortText] = Field(default_factory=list, max_length=8)
    tradeoffs: list[ShortText] = Field(default_factory=list, max_length=8)
    requirements: list[ShortText] = Field(default_factory=list, max_length=8)
    pillars: list[Literal["optimization", "accuracy", "integrity", "security", "databricks_architecture", "data_validation"]] = Field(default_factory=list, max_length=6)


class Edge(Strict):
    source: str = Field(pattern=HASH)
    target: str = Field(pattern=HASH)
    relation: Literal["evidenced_by", "supports", "requires", "addresses"]


class GraphData(Strict):
    directed: Literal[True] = True
    multigraph: Literal[False] = False
    graph: dict = Field(default_factory=dict)
    nodes: list[Node] = Field(max_length=MAX_NODES)
    edges: list[Edge] = Field(max_length=MAX_EDGES)


class Envelope(Strict):
    format: Literal["orion-architecture-knowledge-v1"] = FORMAT
    version: str = Field(pattern=HASH)
    created_at: str
    workspace_id: str
    user_id: str
    project_id: str
    graph_sha256: str = Field(pattern=HASH)
    data: GraphData


class FeatureRequest(Strict):
    label: str = Field(min_length=3, max_length=120)
    release_url: str = Field(max_length=500)
    usage_url: str = Field(max_length=500)
    summary: str = Field(min_length=1, max_length=400)
    applicability: list[ShortText] = Field(min_length=1, max_length=6)
    tradeoffs: list[ShortText] = Field(min_length=1, max_length=6)
    requirements: list[ShortText] = Field(min_length=1, max_length=6)
    pillars: list[Literal["optimization", "accuracy", "integrity", "security", "databricks_architecture", "data_validation"]] = Field(min_length=1, max_length=6)


class _Page(HTMLParser):
    """Extract visible main/article data and exact heading sections, never scripts."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = 0
        self.skip = 0
        self.parts = []
        self.headings = []
        self.heading = None
        self.published = None
        self.updated = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in {"main", "article"}:
            self.active += 1
        if tag in {"script", "style", "nav"}:
            self.skip += 1
        if tag == "meta":
            key = a.get("property", a.get("name"))
            if key == "article:published_time":
                self.published = a.get("content")
            elif key in {"article:modified_time", "dateModified"}:
                self.updated = a.get("content")
        if self.active and not self.skip:
            if tag in {"h1", "h2", "h3"}:
                self.heading = [a.get("id", ""), len(self.parts), [], int(tag[1])]
            if tag == "time" and a.get("datetime"):
                self.updated = a["datetime"]

    def handle_endtag(self, tag):
        if tag in {"h1", "h2", "h3"} and self.heading:
            self.headings.append(self.heading)
            self.heading = None
        if tag in {"main", "article"}:
            self.active = max(0, self.active - 1)
        if tag in {"script", "style", "nav"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, text):
        if self.active and not self.skip and text.strip():
            self.parts.append(text.strip())
            if self.heading:
                self.heading[2].append(text.strip())

    def section(self, fragment):
        candidates = [h for h in self.headings if h[0] == fragment] if fragment else [h for h in self.headings if h[3] == 1][:1]
        if len(candidates) != 1:
            raise ValueError("Official documentation section not found or ambiguous")
        h = candidates[0]
        end = next((other[1] for other in self.headings if other[1] > h[1] and other[3] <= (h[3] if fragment else 2)), len(self.parts))
        text = " ".join(self.parts[h[1]:end])
        return " ".join(h[2])[:240], text


def fetch_document(url):
    """Unauthenticated public-data fetch. No env proxy, cookies, credentials or offsite redirects."""
    official_url(url)
    original = url
    deadline = monotonic() + 30
    with httpx.Client(trust_env=False, timeout=10, follow_redirects=False) as client:
        for _ in range(4):
            official_url(url)
            hostname = urlsplit(url).hostname
            addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError("Non-public documentation address rejected")
            with client.stream("GET", url.split("#")[0], headers={"Accept": "text/html", "Accept-Encoding": "identity"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    url = official_url(urljoin(url, response.headers.get("location", "")))
                    continue
                response.raise_for_status()
                if "text/html" not in response.headers.get("content-type", ""):
                    raise ValueError("Documentation must be HTML")
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 2 * MAX_BYTES or monotonic() > deadline:
                        raise ValueError("Documentation byte/time bound exceeded")
            break
        else:
            raise ValueError("Documentation redirect bound exceeded")
    page = _Page()
    page.feed(bytes(raw).decode("utf-8", errors="strict"))
    title, text = page.section(urlsplit(original).fragment)
    fetched = now()
    published = page.published
    if not published:
        match = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}\b", text)
        published = datetime.strptime(match[0], "%B %d, %Y").date().isoformat() if match else None
    for date in (published, page.updated):
        # A page's publication/update date is independent of retrieval and release dates.
        if date:
            datetime.fromisoformat(date.replace("Z", "+00:00"))
    assertion = next((sentence.strip() for sentence in re.split(r"(?<=\.)\s+", text[:1200])
        if re.search(r"\b(generally available|is (?:now )?GA|beta|public preview|deprecated)\b", sentence, re.I)), None)
    source = Source(url=original, retrieved_at=stamp(fetched), expires_at=stamp(fetched + timedelta(days=14)),
        published_at=published, updated_at=page.updated, sha256=sha256(raw).hexdigest(), section_sha256=sha256(text.encode()).hexdigest(),
        title=title, excerpt=text[:240], release_statement=assertion[:240] if assertion else None)
    return source, text


def release_state(label, source, text):
    """No absence-of-preview heuristic. Associate explicit release text with the named feature."""
    normalize = lambda s: re.sub(r"[^a-z0-9 ]", "", s.casefold())
    subject = normalize(label)
    if subject not in normalize(source.title) and subject not in normalize(text[:800]):
        return "unknown"
    # Only the selected feature's heading/intro, not unrelated later feature sections.
    intro = text[:1200]
    if re.search(r"\b(deprecated|retired|end of support)\b", intro, re.I):
        return "deprecated"
    if re.search(r"\b(beta|public preview|private preview)\b", source.title, re.I):
        return "preview"
    if re.search(r"\b(generally available|is (?:now )?GA|GA for)\b", intro, re.I) and (source.published_at or source.updated_at):
        return "GA"
    if re.search(r"\b(beta|public preview|private preview)\b", intro, re.I):
        return "preview"
    return "unknown"


def curate(specs, workspace, user, project, *, fetch=fetch_document):
    """Explicit human curation only. Summaries are curated assertions, not semantic proof."""
    if not 1 <= len(specs) <= 12:
        raise ValueError("Curate 1..12 features per immutable snapshot")
    nodes, edges = {}, []
    def add(node):
        nodes[node.id] = node
        return node.id
    for pillar in PILLARS:
        add(Node(id=ds._hash(["pillar", pillar]), kind="pillar", label=pillar, summary="Review dimension, not a passing metric"))
    for request in specs:
        spec = FeatureRequest.model_validate(request)
        for text in spec.applicability + spec.tradeoffs + spec.requirements:
            if not 1 <= len(text) <= 400:
                raise ValueError("Curated facts must be short")
        sources = []
        state = "unknown"
        for role, url in (("release", spec.release_url), ("usage", spec.usage_url)):
            official_url(url)
            source, text = fetch(url)
            if source.url != url:
                raise ValueError("Fetched source identity mismatch")
            identifier = ds._hash(source.model_dump())
            add(Node(id=identifier, kind="document", label=source.title[:180], summary="Official public documentation, untrusted data not instructions", source=source))
            sources.append(identifier)
            if role == "release":
                state = release_state(spec.label, source, text)
            else:
                usage_state = release_state(spec.label, source, text)
                if usage_state in {"preview", "deprecated"}:
                    state = usage_state
        feature_id = ds._hash([spec.model_dump(), sources])
        feature = Node(id=feature_id, kind="feature", label=spec.label, summary=spec.summary,
            verification="curated", release_state=state, evidence_ids=sources,
            platforms=["spark" if urlsplit(spec.usage_url).netloc == "spark.apache.org"
                       else urlsplit(spec.usage_url).path.split("/")[1]], applicability=spec.applicability,
            tradeoffs=spec.tradeoffs, requirements=spec.requirements, pillars=spec.pillars)
        add(feature)
        for document in sources:
            edges.append(Edge(source=feature_id, target=document, relation="evidenced_by"))
        for kind, texts, relation in (("pattern", spec.applicability, "supports"), ("constraint", spec.requirements + spec.tradeoffs, "requires")):
            for text in texts:
                nid = add(Node(id=ds._hash([feature_id, kind, text]), kind=kind, label=text[:180], summary=text,
                    verification="curated", evidence_ids=sources))
                edges.append(Edge(source=feature_id, target=nid, relation=relation))
                for document in sources:
                    edges.append(Edge(source=nid, target=document, relation="evidenced_by"))
        for pillar in spec.pillars:
            edges.append(Edge(source=feature_id, target=ds._hash(["pillar", pillar]), relation="addresses"))
    data = GraphData(nodes=list(nodes.values()), edges=list({(e.source, e.target, e.relation): e for e in edges}.values()))
    graph_hash = ds._hash(data.model_dump())
    created = stamp(now())
    version = ds._hash([FORMAT, workspace, user, project, created, graph_hash])
    return validate(Envelope(version=version, created_at=created, workspace_id=workspace, user_id=user,
        project_id=project, graph_sha256=graph_hash, data=data).model_dump(), workspace, user, project)


def validate(value, workspace, user, project):
    raw = ds._json(value).encode()
    if not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("Knowledge snapshot byte bound exceeded")
    ds._safe(value)
    env = Envelope.model_validate(value)
    if (env.workspace_id, env.user_id, env.project_id) != (workspace, user, project):
        raise PermissionError("Knowledge snapshot identity/project mismatch")
    instant(env.created_at)
    if env.data.graph or ds._hash(env.data.model_dump()) != env.graph_sha256 or env.version != ds._hash([FORMAT, workspace, user, project, env.created_at, env.graph_sha256]):
        raise ValueError("Knowledge schema or version/hash mismatch")
    ids = {n.id for n in env.data.nodes}
    docs = {n.id: n for n in env.data.nodes if n.kind == "document"}
    if len(ids) != len(env.data.nodes):
        raise ValueError("Duplicate knowledge node")
    pillars = [n for n in env.data.nodes if n.kind == "pillar"]
    if {n.label for n in pillars} != set(PILLARS) or len(pillars) != len(PILLARS) or any(n.id != ds._hash(["pillar", n.label]) for n in pillars):
        raise ValueError("All six exact review pillars are required")
    for node in env.data.nodes:
        if node.source:
            if node.kind != "document" or node.id != ds._hash(node.source.model_dump()):
                raise ValueError("Source node identity mismatch")
            official_url(node.source.url)
            for date in (node.source.published_at, node.source.updated_at):
                if date:
                    datetime.fromisoformat(date.replace("Z", "+00:00"))
            retrieved, expiry = instant(node.source.retrieved_at), instant(node.source.expires_at)
            if not retrieved < expiry <= retrieved + timedelta(days=14) or retrieved > now() + timedelta(minutes=5):
                raise ValueError("Invalid source freshness window")
        elif node.kind == "document":
            raise ValueError("Document provenance missing")
        if any(i not in docs for i in node.evidence_ids):
            raise ValueError("Dangling evidence reference")
        if node.kind in {"feature", "pattern", "constraint"} and not node.evidence_ids:
            raise ValueError("Knowledge requires linked documentary evidence")
        if node.kind == "feature" and (not node.platforms or not node.pillars or not node.applicability or not node.tradeoffs or not node.requirements):
            raise ValueError("Feature applicability, tradeoffs, constraints and pillars required")
        if node.kind == "feature" and node.release_state == "GA":
            release = docs[node.evidence_ids[0]].source
            if not release or not release.release_statement or not (release.published_at or release.updated_at):
                raise ValueError("GA requires explicit dated documentary release evidence")
            if release_state(node.label, release, release.release_statement) != "GA":
                raise ValueError("GA assertion does not match the feature's release evidence")
    seen = set()
    for edge in env.data.edges:
        key = edge.source, edge.target
        if edge.source not in ids or edge.target not in ids or key in seen:
            raise ValueError("Dangling or duplicate knowledge edge")
        seen.add(key)
    for node in env.data.nodes:
        if any((node.id, evidence) not in seen for evidence in node.evidence_ids) and node.kind == "feature":
            raise ValueError("Feature evidence edges missing")
    return env.model_dump()


class Snapshot:
    """One invocation-owned in-process NetworkX graph, never a global/user cache."""
    def __init__(self, value, workspace, user, project):
        self.value = validate(value, workspace, user, project)
        self.graph = nx.freeze(nx.node_link_graph(self.value["data"], edges="edges"))
        self.retrievals = []
        self.proposals = []

    def query(self, text="", *, allow_preview=False, platform="", limit=6):
        if type(allow_preview) is not bool or not 1 <= limit <= 12 or len(text) > 200 or platform not in {"", "aws", "gcp", "spark"}:
            raise ValueError("Invalid bounded knowledge query")
        items = []
        for identifier, node in self.graph.nodes(data=True):
            if node["kind"] != "feature" or node["verification"] != "curated":
                continue
            if node["release_state"] not in ({"GA", "preview"} if allow_preview else {"GA"}):
                continue
            sources = [self.graph.nodes[i]["source"] for i in node["evidence_ids"]]
            if any(instant(s["expires_at"]) <= now() for s in sources):
                continue
            if platform and platform not in node["platforms"]:
                continue
            words = text.casefold().split()
            if words and not any(w in ds._json(node).casefold() for w in words):
                continue
            items.append({"id": identifier, **node, "sources": sources, "fact_sha256": ds._hash(node),
                "applicability_verified": False, "data_availability": None,
                "interpretation": "Curated candidate only. Check actual workspace, region, runtime, security and correctness before proposing."})
        page = items[:limit]
        self.retrievals.append({"version": self.value["version"], "ids": [i["id"] for i in page], "allow_preview": allow_preview})
        return {"status": "available", "version": self.value["version"], "graph_sha256": self.value["graph_sha256"],
            "items": page, "total": len(items), "truncated": len(items) > limit,
            "latest_catalogue_guaranteed": False, "instructions_trusted": False}

    def stage(self, label, url, summary):
        official_url(url)
        if not 1 <= len(label) <= 120 or not 1 <= len(summary) <= 400 or len(self.proposals) >= 12:
            raise ValueError("Knowledge proposal bound exceeded")
        proposal = {"label": label, "url": url, "summary": summary, "verification": "unverified",
            "release_state": "unknown", "accepted": False, "storage_consent_required": True}
        ds._safe(proposal)
        self.proposals.append(proposal)
        return proposal

    def status(self):
        features = [n for _, n in self.graph.nodes(data=True) if n["kind"] == "feature"]
        fresh = self.query()["total"]
        # Status reads are not agent retrieval evidence.
        self.retrievals.pop()
        return {"status": "available", "version": self.value["version"], "graph_sha256": self.value["graph_sha256"],
            "feature_count": len(features), "fresh_ga_count": fresh,
            "expired_feature_count": sum(any(instant(self.graph.nodes[i]["source"]["expires_at"]) <= now() for i in n["evidence_ids"]) for n in features),
            "earliest_expiry": min((self.graph.nodes[i]["source"]["expires_at"] for n in features for i in n["evidence_ids"]), default=None),
            "release_states": {state: sum(n["release_state"] == state for n in features) for state in ("GA", "preview", "deprecated", "unknown")},
            "pillars": {p: {"knowledge_count": sum(p in n["pillars"] for n in features), "validated": False} for p in PILLARS},
            "data_availability": None, "latest_catalogue_guaranteed": False,
            "production_ready": False}


def unavailable():
    return {"status": "not_curated", "version": None, "feature_count": None, "fresh_ga_count": None,
        "data_availability": None, "pillars": {p: {"knowledge_count": None, "validated": False} for p in PILLARS},
        "latest_catalogue_guaranteed": False, "production_ready": False}