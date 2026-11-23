"""Read-only external skill catalog. Content is data, never executable tooling.

No agent integration or prompt injection is performed here. Reads return a
receipt with a content hash and logical path so callers can record visibility.
Offsets and limits are Unicode character counts; file and page sizes are bounded.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat

WORKBENCH = Path(__file__).resolve().parent
PACKAGED_ROOT = WORKBENCH / "official_skills"
NOTICE_ROOT = WORKBENCH / "skill_notices"
SKILLS = {
    "anthropic": (
        "academy-guide", "algorithmic-art", "brand-guidelines", "canvas-design",
        "claude-api", "discernment-nudge", "doc-coauthoring", "docx",
        "frontend-design", "internal-comms", "mcp-builder", "pdf", "pptx",
        "skill-creator", "slack-gif-creator", "theme-factory", "webapp-testing",
        "web-artifacts-builder", "xlsx",
    ),
    "databricks": (
        "databricks-agent-bricks", "databricks-ai-functions", "databricks-ai-runtime",
        "databricks-aibi-dashboards", "databricks-app-design", "databricks-apps",
        "databricks-apps-python", "databricks-core", "databricks-dabs", "databricks-data-discovery",
        "databricks-dbsql", "databricks-docs", "databricks-execution-compute", "databricks-genie-agents",
        "databricks-iceberg", "databricks-jobs", "databricks-lakebase", "databricks-lakeflow-connect",
        "databricks-metric-views", "databricks-ml-training", "databricks-mlflow-evaluation",
        "databricks-model-serving", "databricks-pipelines", "databricks-python-sdk", "databricks-serverless",
        "databricks-serverless-migration", "databricks-setup-local", "databricks-spark-structured-streaming",
        "databricks-synthetic-data-gen", "databricks-unity-catalog", "databricks-unity-gateway",
        "databricks-unstructured-pdf-generation", "databricks-vector-search", "databricks-zerobus-ingest",
    ),
}
SKILL_NAMES = {
    'academy-guide': 'Learning and training guidance', 'algorithmic-art': 'Algorithmic art',
    'brand-guidelines': 'Brand guidelines', 'canvas-design': 'Visual canvas design',
    'claude-api': 'Claude API development', 'discernment-nudge': 'Decision support',
    'doc-coauthoring': 'Collaborative document writing', 'docx': 'Word document processing',
    'frontend-design': 'Frontend interface design', 'internal-comms': 'Internal communications',
    'mcp-builder': 'MCP server development', 'pdf': 'PDF document processing',
    'pptx': 'PowerPoint presentation processing', 'skill-creator': 'Skill creation and evaluation',
    'slack-gif-creator': 'Slack animation creation', 'theme-factory': 'Visual theme design',
    'webapp-testing': 'Web application testing', 'web-artifacts-builder': 'Interactive web artifact development',
    'xlsx': 'Spreadsheet processing', 'databricks-ai-runtime': 'Databricks AI Runtime workloads',
    'databricks-apps': 'Databricks application development', 'databricks-genie-agents': 'Databricks Genie agents',
    'databricks-serverless': 'Databricks serverless jobs and pipelines',
    'databricks-setup-local': 'Databricks local environment setup',
    'databricks-unity-gateway': 'Databricks Unity AI Gateway',
    'databricks-zerobus-ingest': 'Databricks streaming ingestion',
}
SKILL_NAMES.update({
    'databricks-agent-bricks': 'Databricks Agent Bricks', 'databricks-ai-functions': 'Databricks SQL AI functions',
    'databricks-aibi-dashboards': 'Databricks AI/BI dashboards', 'databricks-app-design': 'Databricks application design',
    'databricks-apps-python': 'Databricks Python applications', 'databricks-core': 'Databricks authentication and data access',
    'databricks-dabs': 'Databricks Asset Bundles', 'databricks-data-discovery': 'Databricks data discovery',
    'databricks-dbsql': 'Databricks SQL', 'databricks-docs': 'Databricks documentation',
    'databricks-execution-compute': 'Databricks compute execution', 'databricks-iceberg': 'Apache Iceberg on Databricks',
    'databricks-jobs': 'Databricks Lakeflow Jobs', 'databricks-lakebase': 'Databricks Lakebase',
    'databricks-lakeflow-connect': 'Databricks Lakeflow Connect', 'databricks-metric-views': 'Databricks metric views',
    'databricks-ml-training': 'Databricks machine learning training', 'databricks-mlflow-evaluation': 'MLflow agent evaluation',
    'databricks-model-serving': 'Databricks model serving', 'databricks-pipelines': 'Databricks Lakeflow pipelines',
    'databricks-python-sdk': 'Databricks Python SDK', 'databricks-serverless-migration': 'Databricks serverless migration',
    'databricks-spark-structured-streaming': 'Spark Structured Streaming',
    'databricks-synthetic-data-gen': 'Databricks synthetic data generation',
    'databricks-unity-catalog': 'Databricks Unity Catalog',
    'databricks-unstructured-pdf-generation': 'Synthetic PDF generation', 'databricks-vector-search': 'Databricks vector search',
})
TEXT_SUFFIXES = frozenset({".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js",
                           ".mjs", ".ts", ".sh", ".html", ".xml", ".xsd"})
NOTICE_NAMES = frozenset({"license", "licence", "notice", "copying", "authors", "attribution"})
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_PAGE_CHARS = 16_000
_COMPONENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}\Z")
_SECRET_NAME = re.compile(
    r"(?i)(?:^|[._-])(?:credentials?|secrets?|passwords?|private[-_]?key|"
    r"access[-_]?token|api[-_]?key)(?:$|[._-])|\.(?:pem|p12|pfx|key)$"
)
_SECRET_CONTENT = re.compile(
    rb"-----BEGIN (?:[A-Z0-9 ]* )?PRIVATE KEY-----|"
    rb"\bAKIA[0-9A-Z]{16}\b|\bghp_[A-Za-z0-9]{36,}\b|"
    rb"\bgithub_pat_[A-Za-z0-9_]{40,}\b"
)


def _local_roots():
    """Only called for local reads or explicit local packaging."""
    return {
        "anthropic": WORKBENCH.parent / "vendor" / "anthropic-skills",
        "databricks": Path.home() / ".cursor" / "skills",
    }


def _roots():
    # A staged tree is authoritative even when some files are missing. Hosted
    # environments must never consult the service account's home directory.
    if (PACKAGED_ROOT.exists() or os.environ.get("DATABRICKS_APP_PORT")
            or os.environ.get("WORKBENCH_TRUST_DATABRICKS_PROXY") == "1"):
        return {provider: PACKAGED_ROOT / provider for provider in SKILLS}, "packaged"
    return _local_roots(), "local"


def _skill_root(provider_root, provider):
    return provider_root / "skills" if provider == "anthropic" else provider_root


def _is_link(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _check_path(path):
    try:
        if any(_is_link(part) for part in (path, *path.parents)):
            raise ValueError("Symlinks and reparse points are forbidden")
    except OSError:
        raise ValueError("Skill source is unavailable") from None


def _allowed_component(name):
    return bool(_COMPONENT.fullmatch(name)) and not name.endswith(".") and not _SECRET_NAME.search(name)


def _allowed_file(name):
    if not _allowed_component(name):
        return False
    path = Path(name)
    return path.suffix.lower() in TEXT_SUFFIXES or name.lower() in NOTICE_NAMES


def _read_bytes(path):
    _check_path(path)
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("Not a regular skill file")
        with path.open("rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("Not a regular skill file")
            data = stream.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise ValueError("Skill file unavailable") from None
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Skill file exceeds the read limit")
    if b"\x00" in data or _SECRET_CONTENT.search(data):
        raise ValueError("Unsafe skill file content")
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("Skill file is not UTF-8 text") from None
    return data


def _files_under(root):
    """Walk only visible, safe names. Never follow links, even excluded types."""
    _check_path(root)
    if not root.is_dir():
        raise ValueError("Required skill source is unavailable")
    result = []

    def visit(folder):
        try:
            children = sorted(folder.iterdir(), key=lambda p: p.name)
            for path in children:
                if path.name.startswith(".") or _SECRET_NAME.search(path.name):
                    continue
                if not _allowed_component(path.name):
                    raise ValueError("Unsafe skill source filename")
                _check_path(path)
                if path.is_dir():
                    visit(path)
                elif path.is_file() and _allowed_file(path.name):
                    _read_bytes(path)
                    result.append(path)
                elif not path.is_file():
                    raise ValueError("Unsafe skill source entry")
        except OSError:
            raise ValueError("Skill source is unavailable") from None

    visit(root)
    return result


def packaging_sources():
    """Return validated source/relative-target pairs for local staging only.

    Required skills fail closed. Provider-level attribution is retained in its
    original relative location; no license interpretation is made.
    """
    roots = _local_roots()
    pairs = []
    for provider, names in SKILLS.items():
        root = roots[provider]
        base = _skill_root(root, provider)
        for name in names:
            skill_dir = base / name
            files = _files_under(skill_dir)
            if skill_dir / "SKILL.md" not in files:
                raise ValueError("Required skill is incomplete: " + provider + "/" + name)
            for path in files:
                pairs.append((path, Path(provider) / path.relative_to(root)))
        # Preserve provider-root attribution without sweeping unrelated files.
        if provider == "anthropic":
            for name in ("README.md", "THIRD_PARTY_NOTICES.md"):
                path = root / name
                _read_bytes(path)
                pairs.append((path, Path(provider) / name))
        if provider == "databricks":
            for name in ("LICENSE", "NOTICE", "SOURCE.json"):
                path = NOTICE_ROOT / "databricks" / name
                _read_bytes(path)
                pairs.append((path, Path(provider) / name))
    return sorted(pairs, key=lambda pair: pair[1].as_posix())


def skill_notice_manifest():
    """List bundled provider/per-skill license files and explicitly missing skill licenses."""
    roots, mode = _roots()
    documents, missing = [], []
    for provider, names in SKILLS.items():
        if provider == "anthropic":
            provider_names = ("THIRD_PARTY_NOTICES.md",)
            provider_root = roots[provider]
        else:
            provider_names = ("LICENSE", "NOTICE", "SOURCE.json")
            provider_root = PACKAGED_ROOT / provider if mode == "packaged" else NOTICE_ROOT / provider
        for name in provider_names:
            try:
                data = _read_bytes(provider_root / name)
            except ValueError:
                continue
            documents.append({"provider": provider, "skill": names[0],
                              "path": "@provider/" + name, "sha256": hashlib.sha256(data).hexdigest(),
                              "byte_size": len(data), "kind": "provider_notice"})
        for skill in names:
            base = _skill_root(roots[provider], provider) / skill
            try:
                files = _files_under(base)
            except ValueError:
                files = []
            notices = [path for path in files if path.name.lower() in NOTICE_NAMES]
            if not any(path.name.lower() in {"license", "licence", "copying"} for path in notices):
                missing.append({"provider": provider, "skill": skill,
                                "status": "no_standalone_license_file_found"})
            for path in notices:
                data = _read_bytes(path)
                documents.append({"provider": provider, "skill": skill,
                                  "path": path.relative_to(base).as_posix(),
                                  "sha256": hashlib.sha256(data).hexdigest(),
                                  "byte_size": len(data), "kind": "skill_notice"})
    return {"status": "available", "mode": mode, "documents": documents,
            "skills_without_standalone_license": missing}


def _frontmatter(text):
    fields = {}
    if text.startswith("---\n"):
        folded = None
        for line in text.split("\n")[1:]:
            if line.strip() == "---":
                break
            if folded and line.startswith("  "):
                fields[folded] += " " + line.strip()
                continue
            folded = None
            match = re.match(r"^(name|description):\s*(.*?)\s*$", line)
            if match:
                key, value = match.groups()
                if value in {">", ">-", "|", "|-"}:
                    fields[key] = ""
                    folded = key
                else:
                    fields[key] = value.strip("\"'")
    fields = {key: value.strip()[:1000] for key, value in fields.items()}
    return fields


def list_skills():
    """Return all expected entries, completeness, and source-relative metadata."""
    roots, mode = _roots()
    entries = []
    for provider, names in SKILLS.items():
        for name in names:
            path = _skill_root(roots[provider], provider) / name / "SKILL.md"
            try:
                text = _read_bytes(path).decode("utf-8-sig")
                fields = _frontmatter(text.replace("\r\n", "\n"))
                entry = {"provider": provider, "skill": name,
                         "display_name": SKILL_NAMES.get(name, name),
                         "description": fields.get("description", ""),
                         "available": True, "skill_path": "SKILL.md"}
            except ValueError as error:
                entry = {"provider": provider, "skill": name, "display_name": SKILL_NAMES.get(name, name),
                         "description": "", "available": False, "skill_path": "SKILL.md",
                         "reason": str(error)}
            entries.append(entry)
    available = sum(entry["available"] for entry in entries)
    return {"status": "complete" if available == len(entries) else "incomplete",
            "mode": mode, "expected_count": len(entries), "available_count": available,
            "provider_notices": {"anthropic": ["@provider/README.md", "@provider/THIRD_PARTY_NOTICES.md"],
                                 "databricks": ["@provider/LICENSE", "@provider/NOTICE", "@provider/SOURCE.json"]},
            "skills": entries}


def read_skill(provider, skill, relative_path="SKILL.md", offset=0, limit=MAX_PAGE_CHARS):
    """Read one bounded text page and return a per-read, content-addressed receipt.

    Provider notices are addressable using the listed @provider/ logical paths.
    Callers must persist receipts themselves if durable audit visibility is needed.
    """
    if not isinstance(provider, str) or not isinstance(skill, str) or provider not in SKILLS or skill not in SKILLS[provider]:
        raise ValueError("Unknown skill")
    if (type(offset) is not int or type(limit) is not int or offset < 0
            or not 1 <= limit <= MAX_PAGE_CHARS):
        raise ValueError("Invalid page bounds")
    if not isinstance(relative_path, str) or len(relative_path) > 1024 or "\\" in relative_path:
        raise ValueError("Invalid skill path")
    parts = relative_path.split("/")
    roots, mode = _roots()
    if len(parts) == 2 and parts[0] == "@provider" and provider == "anthropic" and parts[1] in {"README.md", "THIRD_PARTY_NOTICES.md"}:
        path = roots[provider] / parts[1]
    elif len(parts) == 2 and parts[0] == "@provider" and provider == "databricks" and parts[1] in {"LICENSE", "NOTICE", "SOURCE.json"}:
        path = (PACKAGED_ROOT / provider / parts[1] if mode == "packaged"
                else NOTICE_ROOT / provider / parts[1])
    else:
        if not parts or any(not _allowed_component(part) for part in parts) or not _allowed_file(parts[-1]):
            raise ValueError("Invalid skill path")
        path = _skill_root(roots[provider], provider) / skill
        for part in parts:
            path = path / part
    data = _read_bytes(path)
    text = data.decode("utf-8-sig")
    end = min(offset + limit, len(text))
    return {"provider": provider, "skill": skill, "path": relative_path, "mode": mode,
            "offset": offset, "next_offset": end if end < len(text) else None,
            "limit": limit, "total_characters": len(text), "file_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "content": text[offset:end]}