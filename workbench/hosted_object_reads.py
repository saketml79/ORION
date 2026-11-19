"""Fresh OBO, pinned-generation canonical reads, never execution-cache restore.

Only explicit selected project/package reads use this projection. No source XML,
English export, audit history, reviews, model routes or jobs are dependencies of
these presenters. Mutations and unselected legacy requests retain full restore.
"""
from hashlib import sha256
from collections import OrderedDict
from concurrent.futures import Future
from copy import copy
import gc
import json
from pathlib import Path
import re
from threading import RLock

from fastapi import HTTPException
from fastapi.responses import Response

from workbench import delta_state as ds
from workbench.hosted_registry import committed_generation, _registry, _envelope
from workbench.object_views import object_response, migration_response

ROUTE = re.compile(r"/api/(?:object/([a-f0-9]{64})|workflows/([a-f0-9]{64})/migration-context|workflow-invocation-summary|readiness(?:/lineage)?|validation/campaigns/([a-f0-9]{32})(/export)?|workflows/([a-f0-9]{64})/target-review|redesigns(?:/([a-f0-9]{64}))?|proposals(?:/[a-f0-9]{64})?|cohorts/[a-f0-9]{32}|target-selections/[a-f0-9]{64}/[a-f0-9]{64}|jobs/[a-f0-9]{32}(?:/spark-drafts|/completeness)?|package-graph/chunk|objects|inventory/[a-z]+/[a-f0-9]{64})\Z")

# One immutable graph, never an identity, client, permission or freshness cache.
# Every use follows current receipt, scope inventories and Volume authorization.
_REVIEW_INDEX_CACHE = OrderedDict()
_REVIEW_INDEX_LOCK = RLock()
_REVIEW_INDEX_FLIGHTS = {}
_REVIEW_INDEX_LOAD_LOCK = RLock()
_SESSION_COMPONENTS_MAX_BYTES = 512 * 1024 * 1024
_SESSION_MEMORY_RESERVE_BYTES = 1536 * 1024 * 1024
_INDEX_ESTIMATE_MULTIPLIER = 20
_INDEX_CACHE_MAX_ESTIMATE_BYTES = 3 * 1024 * 1024 * 1024


def _container_memory_state():
    """Return cgroup current/limit when available, without adding a dependency."""
    for current_path, limit_path in (
            ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max"),
            ("/sys/fs/cgroup/memory/memory.usage_in_bytes",
             "/sys/fs/cgroup/memory/memory.limit_in_bytes")):
        try:
            current = int(Path(current_path).read_text(encoding="ascii").strip())
            raw_limit = Path(limit_path).read_text(encoding="ascii").strip()
            if raw_limit == "max":
                return None
            limit = int(raw_limit)
            if 0 < current <= limit < (1 << 60):
                return current, limit
        except (OSError, ValueError):
            continue
    return None


def _admit_package_memory(estimated_bytes):
    if type(estimated_bytes) is not int or not 0 <= estimated_bytes <= _INDEX_CACHE_MAX_ESTIMATE_BYTES:
        raise HTTPException(503, "Selected package exceeds the hosted in-memory session budget")
    state = _container_memory_state()
    if state is not None:
        current, limit = state
        if current + estimated_bytes + _SESSION_MEMORY_RESERVE_BYTES > limit:
            raise HTTPException(503, "Insufficient memory headroom to safely load this package")


def _immutable_index(key, directory, load, estimated_memory_bytes):
    """One hydration owner per verified immutable identity, not per route.

    Callers must verify current membership, committed inventories and Volume
    permission BEFORE joining. Futures contain data only, never runtime clients.
    """
    with _REVIEW_INDEX_LOCK:
        cached = _REVIEW_INDEX_CACHE.get(key)
        if cached is not None:
            _REVIEW_INDEX_CACHE.move_to_end(key)
            current = copy(cached)
            current.directory = directory
            return current
        future = _REVIEW_INDEX_FLIGHTS.get(key)
        owner = future is None
        if owner:
            future = Future()
            _REVIEW_INDEX_FLIGHTS[key] = future
    if owner:
        try:
            with _REVIEW_INDEX_LOAD_LOCK:
                # Another package can finish while this owner waits for the
                # cold-load lock. Reuse it if it is now the active cache entry.
                with _REVIEW_INDEX_LOCK:
                    cached = _REVIEW_INDEX_CACHE.get(key)
                    if cached is not None:
                        _REVIEW_INDEX_CACHE.move_to_end(key)
                if cached is None:
                    with _REVIEW_INDEX_LOCK:
                        # One active package per app process. Release stale
                        # graphs before allocating another expanded model.
                        _REVIEW_INDEX_CACHE.clear()
                    from workbench.app import _evict_hosted_package_indexes
                    _evict_hosted_package_indexes()
                    gc.collect()
                    _admit_package_memory(estimated_memory_bytes)
                    index = load()
                    state = _container_memory_state()
                    if state is not None and state[0] + _SESSION_MEMORY_RESERVE_BYTES > state[1]:
                        from workbench.app import _evict_hosted_package_indexes
                        with _REVIEW_INDEX_LOCK:
                            _REVIEW_INDEX_CACHE.clear()
                        _evict_hosted_package_indexes()
                        del index
                        gc.collect()
                        raise HTTPException(503, "Package load used the protected hosted memory reserve")
                    with _REVIEW_INDEX_LOCK:
                        _REVIEW_INDEX_CACHE[key] = index
                        _REVIEW_INDEX_CACHE.move_to_end(key)
                else:
                    index = cached
            future.set_result(index)
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with _REVIEW_INDEX_LOCK:
                if _REVIEW_INDEX_FLIGHTS.get(key) is future:
                    del _REVIEW_INDEX_FLIGHTS[key]
    current = copy(future.result())
    current.directory = directory
    return current


def scope_records(runtime, table, inventory, generation, scope, maximum=None):
    """Verify the complete queried scope before selecting any file/column."""
    expected = inventory[table]
    if not isinstance(expected, list):
        raise ValueError("Invalid projection inventory")
    wanted = []
    for item in expected:
        if (not isinstance(item, dict) or set(item) != {
                "record_id", "content_hash", "project_id", "package_hash", "context_id"}
                or any(not isinstance(v, str) for v in item.values())
                or any(not re.fullmatch(r"[a-f0-9]{64}", item[k]) for k in ("record_id", "content_hash"))):
            raise ValueError("Invalid projection inventory entry")
        if all(item[k] == v for k, v in scope.items()):
            wanted.append((item["record_id"], item["content_hash"]))
    if not wanted:
        return []
    if maximum is not None and len(wanted) > maximum:
        raise ValueError("Readiness evidence bound exceeded")
    rows, cursor = [], None
    while True:
        page = runtime.store.read_table(table, **scope, migration_id=generation, after=cursor)
        if len(page) > 50:
            raise ValueError("Oversize projection page")
        for row in page:
            _envelope(runtime, table, row, scope, generation)
            position = (row["record_id"], row["content_hash"], row["migration_id"])
            if cursor is not None and position <= cursor:
                raise ValueError("Non-advancing projection cursor")
            cursor = position
        rows.extend(page)
        if len(rows) > len(wanted):
            raise ValueError("Unexpected projection scope records")
        if len(page) < 50:
            break
    actual = [(row["record_id"], row["content_hash"]) for row in rows]
    if len(set(wanted)) != len(wanted) or sorted(actual) != sorted(wanted):
        raise ValueError("Incomplete or unexpected projection scope records")
    return rows


def workflow_artifact_response(runtime, path, project, package, body, progress=None):
    """Server-only projection, never a public artifact GET or query override."""
    from workbench.workflow_artifact_responses import compile_response, archive_response
    if path not in {"/api/proposals/workflow-artifacts", "/api/proposals/workflow-artifacts/archive"}:
        raise HTTPException(404, "Unknown workflow artifact read")
    if body.consent is not True:
        raise HTTPException(403, "Explicit workflow artifact generation consent:true is required")

    def project_artifacts(index):
        result = compile_response(index, body)
        return archive_response(result, body.workflow_id) if path.endswith("/archive") else result

    return object_read_response(runtime, "/api/object/" + body.workflow_id, project, package,
                                {}, progress, _projector=project_artifacts)


def object_read_response(runtime, path, project, package, query, progress=None, *, _projector=None):
    from workbench.app import PackageIndex
    if not re.fullmatch(r"[a-f0-9]{32}", project):
        raise HTTPException(404, "Unknown project ID")
    if not re.fullmatch(r"[a-f0-9]{64}", package):
        raise HTTPException(403, "Package is not a ready member of this project")
    match = ROUTE.fullmatch(path)
    if match is None:
        raise HTTPException(404, "Unknown canonical read")
    maximum, default = ((50, 20) if path.startswith("/api/proposals") else
                        (100, 50) if path.startswith("/api/redesigns") else
                        (100, 100) if match[1] or path == '/api/package-graph/chunk' or path.startswith('/api/inventory/') else
                        (100, 50) if path == '/api/objects' else (100, 20) if match[5] else (200, 40))
    try:
        offset = int(query.get("offset", ["0"])[-1])
        limit = int(query.get("limit", [str(default)])[-1])
        if offset < 0 or not 1 <= limit <= maximum:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid canonical read pagination") from None
    if progress:
        progress("metadata_restore")
    committed = committed_generation(runtime)
    generation, registry = _registry(runtime, tables=("projects", "packages"), committed=committed)
    if not any(owner == project for owner, _ in registry["projects"]):
        raise HTTPException(404, "Unknown project ID")
    member = next((r for owner, r in registry["packages"]
                   if owner == project and r["sha256"] == package), None)
    if member is None or member["status"] != "completed":
        raise HTTPException(403, "Package is not a ready member of this project")
    inventory = committed[1]["inventory"]
    scope = dict(project_id=project, package_hash=package, context_id="package:" + project + ":" + package)
    files = scope_records(runtime, "artifact_manifest", inventory, generation, scope)
    prefix = f"projects/{project}/package-contexts/{package}/packages/{package}/"
    by_path = {}
    for row in files:
        name = runtime._local_path(row["source_path"])
        if name in by_path or row["source_table"] != "file" or json.loads(row["source_key"]) != name:
            raise ValueError("Invalid projection file identity")
        if ds._scope(name) != scope:
            raise ValueError("Projection file scope mismatch")
        by_path[name] = row
    metadata = scope_records(runtime, "packages", inventory, generation, scope)
    settings = scope_records(runtime, "settings", inventory, generation, scope)

    def materialize(name, table_rows=None, full=None):
        full = full or prefix + name
        row = by_path.get(full)
        if row is None:
            raise ValueError("Required canonical artifact missing")
        reference = runtime._decode(json.loads(row["payload"]))
        if (set(reference) not in ({"volume_path", "sha256", "byte_size"},
                       {"volume_path", "sha256", "byte_size", "source"})
            or ("source" in reference and reference["source"] is not True)
                or reference["volume_path"] != row["artifact_path"]
                or reference["sha256"] != row["artifact_sha256"] or reference["byte_size"] != row["byte_size"]):
            raise ValueError("Projection artifact reference mismatch")
        target = runtime.local / full
        target.parent.mkdir(parents=True, exist_ok=True)
        if table_rows is not None:
            rows = [r for r in table_rows if r["source_path"] == full]
            if any(ds._scope(r["source_path"]) != scope or json.loads(r["source_key"]) != full for r in rows):
                raise ValueError("Projection JSON identity mismatch")
            raw = ds._json(runtime._json_file(name, rows)).encode()
        elif reference.get("source") is True:
            if not full.endswith(".xml"):
                raise ValueError("Only verified XML source artifacts may use streaming source reads")
            runtime._verify_source(reference, target)
            raw = target.read_bytes()
        else:
            raw = runtime._object(reference)
        if not (table_rows is None and reference.get("source") is True):
            target.write_bytes(raw)
        return target

    active_path = f"projects/{project}/package-contexts/{package}/state/active.json"
    active = json.loads(materialize("active.json", settings, active_path).read_text(encoding="utf-8"))
    if active.get("sha256") != package:
        raise HTTPException(409, "Persisted project package does not match selection")
    canonical_manifest = json.loads(materialize("manifest.json", metadata).read_bytes())
    if (canonical_manifest.get("sha256") != package
            or not re.fullmatch(r"[a-f0-9]{64}", canonical_manifest.get("understanding_sha256", ""))):
        raise ValueError("Selected canonical manifest mismatch")
    review_read = path.startswith(("/api/proposals", "/api/cohorts/", "/api/target-selections/", "/api/jobs/"))
    if review_read and path.startswith("/api/jobs/"):
        # Committed writer outputs and exact design versions need no canonical
        # graph, source XML, runtime clients or package-index hydration.
        from workbench.hosted_review_reads import review_response
        return review_response(runtime, None, path, query, inventory, generation, scope,
                               by_path, materialize, offset, limit)
    if match[3]:
        # Full campaign downloads need only verified membership/current pins,
        # not the potentially huge canonical model or execution-cache restore.
        from workbench.validation_campaigns import CampaignStore
        pins = {"package_hash": package, "understanding_sha256": canonical_manifest["understanding_sha256"],
                "analysis_version": None}
        if prefix + "source-analysis.json" in by_path:
            pointer = json.loads(materialize("source-analysis.json", settings).read_bytes())
            if not re.fullmatch(r"[a-f0-9]{64}", pointer.get("analysis_version", "")):
                raise ValueError("Invalid campaign current analysis version")
            pins["analysis_version"] = pointer["analysis_version"]
        store = CampaignStore(delta=runtime.store, artifacts=runtime.artifacts)
        report = store.read(project, package, match[3])
        if report is None:
            raise HTTPException(404, "Campaign not found in this user/project/package")
        if match[4]:
            raw = ds._json(report).encode()
            return Response(raw, media_type="application/json", headers={
                "Content-Disposition": 'attachment; filename="validation-report.json"',
                "X-Workbench-Campaign-Artifact-SHA256": sha256(raw).hexdigest(),
                "X-Workbench-Campaign-Artifact-Bytes": str(len(raw)),
                "X-Workbench-Campaign-Current-Input-Pins-Match": str(report["pins"] == pins).lower()})
        return {"report": report, "current_input_pins_match": report["pins"] == pins,
            "provisioning_events": store.provisioning_events(report), "historical_receipt_immutable": True,
            "execution_allowed": False}
    canonical_names = [prefix + 'understanding.json.gz']
    if prefix + "source-analysis.json" in by_path:
        pointer = json.loads(materialize("source-analysis.json", settings).read_text(encoding="utf-8"))
        version = pointer.get("analysis_version", "")
        if not re.fullmatch(r"[a-f0-9]{64}", version):
            raise ValueError("Invalid source analysis version")
        canonical_names.extend([prefix + 'source-analysis.json', prefix + 'analysis/' + version + '.json.gz'])
    cache_scope = sha256((runtime.store.workspace_id + "\n" + runtime.store.user_id).encode()).hexdigest()
    # All selected canonical presenters share this exact content identity.
    # Metadata/authority remain request-local; no response or approval is cached.
    runtime.artifacts.authorize()
    identities = []
    for name in canonical_names:
        row = by_path.get(name)
        if row is None:
            raise ValueError('Required canonical artifact missing')
        reference = runtime._decode(json.loads(row['payload']))
        if (set(reference) != {'volume_path', 'sha256', 'byte_size'}
                or reference['volume_path'] != runtime.artifacts._path(row['artifact_sha256'])
                or reference['volume_path'] != row['artifact_path']
                or reference['sha256'] != row['artifact_sha256']
                or type(reference['byte_size']) is not int or reference['byte_size'] != row['byte_size']):
            raise ValueError('Invalid immutable canonical reference')
        identities.append((name, row['content_hash'], row['artifact_sha256'], row['byte_size']))
    if by_path[canonical_names[0]]['artifact_sha256'] != canonical_manifest['understanding_sha256']:
        raise ValueError('Canonical manifest checksum mismatch')
    session_names = list(dict.fromkeys([
        name for name in (prefix + "source.xml", prefix + "english.md") if name in by_path
    ] + canonical_names))
    session_identities = []
    session_component_bytes = 0
    for name in session_names:
        row = by_path.get(name)
        if row is None:
            raise ValueError("Required hosted package-session artifact missing")
        reference = runtime._decode(json.loads(row["payload"]))
        if (set(reference) != {"volume_path", "sha256", "byte_size"}
                and set(reference) != {"volume_path", "sha256", "byte_size", "source"}):
            raise ValueError("Invalid hosted package-session artifact reference")
        if (reference["volume_path"] != row["artifact_path"]
                or reference["sha256"] != row["artifact_sha256"]
                or type(reference["byte_size"]) is not int
                or reference["byte_size"] != row["byte_size"]):
            raise ValueError("Hosted package-session artifact reference mismatch")
        session_component_bytes += reference["byte_size"]
        session_identities.append((name, row["content_hash"], row["artifact_sha256"], row["byte_size"]))
    if session_component_bytes > _SESSION_COMPONENTS_MAX_BYTES:
        raise HTTPException(503, "Selected package artifacts exceed the bounded hosted memory-session size")
    immutable_key = (cache_scope, runtime.artifacts.root, project, package,
                     ds._json(canonical_manifest), tuple(identities), tuple(session_identities))
    understanding_size = by_path[canonical_names[0]]["byte_size"]
    estimated_memory_bytes = understanding_size * _INDEX_ESTIMATE_MULTIPLIER + session_component_bytes
    if estimated_memory_bytes > _INDEX_CACHE_MAX_ESTIMATE_BYTES:
        raise HTTPException(503, "Selected package exceeds the hosted in-memory session budget")

    def hydrate():
        session_artifacts = {}
        for full in session_names:
            relative = full[len(prefix):]
            path = materialize(relative)
            session_artifacts[relative] = path.read_bytes()
        index = PackageIndex.load(runtime.local / prefix, cache_scope=cache_scope)
        # Publish one complete immutable package session only after every
        # referenced artifact has passed its current authorization/hash checks.
        index.hosted_session_artifacts = session_artifacts
        index.hosted_session_artifact_bytes = sum(len(raw) for raw in session_artifacts.values())
        index.hosted_session_identity = tuple(session_identities)
        return index

    index = _immutable_index(immutable_key, runtime.local / prefix, hydrate, estimated_memory_bytes)
    if progress:
        total = 5 if prefix + "source-analysis.json" in by_path else 3
        progress("artifact_files", verified_files=total, total_files=total)
        progress("preparing_package")
    if index.model["package"]["sha256"] != package:
        raise ValueError("Selected canonical package mismatch")
    if path == '/api/workflow-invocation-summary':
        from workbench.package_context import workflow_invocation_summary
        return workflow_invocation_summary(index)
    if path == '/api/objects':
        from workbench.inventory_browser import inventory_response
        kind, term = query.get('kind', [''])[-1], query.get('q', [''])[-1]
        flag = query.get('inventory', ['false'])[-1].lower()
        if flag not in {'true', 'false', '1', '0'} or len(term) > 200:
            raise HTTPException(422, 'Invalid inventory query')
        if flag in {'true', '1'}:
            return inventory_response(index, kind, term, offset, limit, query.get('input_version', [None])[-1])
        # Preserve the generic Explorer listing, including transformation kinds.
        from workbench.app import _brief
        candidates = index.kinds.get(kind, ()) if kind else index.model['nodes']
        found = [n for n in candidates if not term or term.casefold() in n['name'].casefold()]
        return {'items': [_brief(n) for n in found[offset:offset + limit]], 'total': len(found),
                'offset': offset, 'limit': limit, 'truncated': offset + limit < len(found)}
    if path.startswith('/api/inventory/'):
        from workbench.inventory_browser import inventory_detail
        _, _, _, kind, identifier = path.split('/')
        return inventory_detail(index, kind, identifier, query.get('section', ['overview'])[-1],
                                offset, limit, query.get('input_version', [None])[-1])
    if path == '/api/package-graph/chunk':
        from workbench.package_graph import package_graph
        return package_graph(index, 'workflow', offset, limit, chunk=True)
    if review_read:
        from workbench.hosted_review_reads import review_response
        return review_response(runtime, index, path, query, inventory, generation, scope,
                               by_path, materialize, offset, limit)
    if _projector is not None:
        # Callable exists only in server code after strict POST validation. No
        # HTTP query/header can enable it or substitute the canonical workflow.
        if not match[1]:
            raise ValueError("Internal projection requires an exact canonical object path")
        return _projector(index)
    if path.startswith("/api/redesigns"):
        from workbench.readiness import hosted_evidence
        from workbench.redesign import projected_read
        evidence, complete = hosted_evidence(runtime, inventory, generation, scope,
            families={"designs", "reviews", "proposals", "documents"},
            workflow_id=query.get("workflow_id", [None])[-1], design_id=match[6])
        if not complete:
            raise ValueError("Incomplete redesign projection evidence")
        # Match the existing restored SQLite lesson ordering, without restoring
        # that database, attempts, models, audit or any source XML.
        loop_workflows = {r["workflow_id"] for r in evidence["designs"]
            if r["source"]["kind"] == "loop"
            and (match[6] is None or r["id"] == match[6])
            and ("workflow_id" not in query or r["workflow_id"] == query["workflow_id"][-1])
            and ("scenario" not in query or r["scenario"] == query["scenario"][-1])}
        evidence["lessons"] = []
        if loop_workflows:
            lessons, complete = hosted_evidence(runtime, inventory, generation, scope, families={"lessons"},
                workflow_id=next(iter(loop_workflows)) if len(loop_workflows) == 1 else None)
            if not complete:
                raise ValueError("Incomplete redesign lesson evidence")
            evidence["lessons"] = lessons["lessons"]
        value = projected_read(index, evidence, query, match[6])
        if match[6] and query.get('draft_jobs', ['false'])[-1].lower() in {'1', 'true'}:
            from workbench.hosted_review_reads import Evidence, draft_response
            from workbench.draft_artifact_reads import matching_jobs
            receipts = Evidence(runtime, inventory, generation, scope)
            jobs = []
            job_prefix = f'projects/{project}/package-contexts/{package}/state/jobs/'
            for row in receipts.rows('jobs'):
                if row['source_table'] == 'json' and row['source_path'].startswith(job_prefix):
                    # rows() has already verified every scoped envelope and the
                    # complete inventory, including unrelated workflow receipts.
                    if row['workflow_id'] != value['workflow_id']:
                        continue
                    identifier = row['source_path'][len(job_prefix):].removesuffix('.json')
                    if not re.fullmatch(r'[a-f0-9]{32}', identifier):
                        raise ValueError('Invalid writer receipt path')
                    jobs.append(draft_response(runtime, receipts, '/api/jobs/' + identifier,
                                               by_path, materialize, metadata_only=True))
            value['draft_jobs'] = matching_jobs(value, jobs)
        return value
    if match[5]:
        from workbench.target_review import target_review
        try:
            return target_review(index, match[5], query.get("scope", ["workflow"])[-1],
                                 offset, limit, query.get("input_version", [None])[-1])
        except ValueError:
            raise HTTPException(422, "Target review is unavailable for this workflow or evidence") from None
    if path == "/api/readiness/lineage":
        from workbench.migration_lineage import Lineage
        try:
            saved = None
            if query.get("family", ["nodes"])[-1] == "trace":
                from workbench.readiness import hosted_evidence, assess
                scenario = query.get("scenario", ["original"])[-1]
                if scenario not in {"original", "bronze"}:
                    raise ValueError("Invalid trace scenario")
                evidence, complete = hosted_evidence(runtime, inventory, generation, scope)
                wid = query.get("workflow_id", [None])[-1]
                saved = assess(index, project, evidence, complete, scenario=scenario, generation=generation,
                    workflow_id=wid or (Lineage(index).selected[0] if Lineage(index).selected else None))
            return Lineage(index).response(project, wid=query.get("workflow_id", [None])[-1],
                root=query.get("root_id", [None])[-1], family=query.get("family", ["nodes"])[-1],
                offset=offset, limit=limit, expected=query.get("input_version", [None])[-1], saved=saved,
                example=query.get("example", ["false"])[-1].lower() == "true")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
    if path == "/api/readiness":
        from workbench.readiness import hosted_evidence, assess
        scenario = query.get("scenario", ["original"])[-1]
        if scenario not in {"original", "bronze"} or limit > 100:
            raise HTTPException(422, "Invalid readiness scope or pagination")
        evidence, complete = hosted_evidence(runtime, inventory, generation, scope)
        from workbench.knowledge_store import KnowledgeStore
        from workbench.architecture_knowledge import unavailable
        snapshot = KnowledgeStore(runtime.store, runtime.artifacts).load(project)
        return assess(index, project, evidence, complete, offset=offset, limit=limit,
                      scenario=scenario, generation=generation,
                  workflow_id=query.get("workflow_id", [None])[-1],
                  knowledge=snapshot.status() if snapshot else unavailable())
    if match[1]:
        return object_response(index, match[1], offset, limit)
    return migration_response(index, match[2], offset, limit)