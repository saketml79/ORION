"""Versioned implementation inventory. Discovery never performs runtime probes."""
from datetime import datetime, timezone


def capability_inventory(hosted_authenticated=False):
    def row(key, label, purpose, limits, *, runtime="not_verified", state="implemented_scoped"):
        return {"key": key, "label": label, "purpose": purpose, "limits": limits,
                "implementation": state, "runtime_validation": runtime,
                "generation_allowed": False, "deployment_allowed": False}

    items = [
        row("xml_understanding", "XML understanding", "Scoped definitions, fields and original control links.", "Parsing is not semantic equivalence. Saved analyses are not silently upgraded."),
        row("bounded_original_graphs", "Original graphs", "Browse canonical objects and field/control evidence.", "Views are bounded and paginated; a partial view is not a complete execution DAG."),
        row("package_context", "Workflow invocation context", "Review literal child calls and connected migration cohorts.", "Invocation candidates do not prove runtime repository routing or child completion."),
        row("provisional_reviews", "Versioned design review", "Review advisory designs and evidence-linked decisions.", "Logical approval does not authorize code execution, deployment or retirement."),
        row("supporting_evidence", "Supporting evidence", "Attach documents and scoped owner evidence.", "Documents and attestations are not observed runtime or data parity."),
        row("agents", "Six specialist agent roles", "Evidence tools and consent-gated advisory agents.", "A configured route is not successful inference. Live model authentication and run success remain unverified.", state="configured_unverified"),
        row("authentication", "Request identity and access", "Fresh hosted request/OBO identity, or local loopback access.", "Hosted identity is not model inference authentication. Local loopback is not authenticated user identity. No service-principal fallback.", runtime="request_verified" if hosted_authenticated else "local_boundary_only"),
        row("durable_evidence", "Durable evidence state", "Committed Delta records and immutable Volume artifacts.", "Hosted permissions are required. Commit-last visibility is not a distributed transaction; local SQLite is not hosted authority."),
        row("metadata_reuse_analysis", "Structural reuse candidates", "Compare fingerprints and captured planning contracts.", "Similar structure is not proven equivalence or permission to combine workflows."),
        row("captured_sql_drafts", "Captured SQL drafts", "Consent-gated syntax translation for captured SQL.", "Non-SQL transformations, data parity and complete workflow conversion are not provided."),
        row("generation", "Reviewed workflow compiler", "Fingerprint-pinned bronze artifacts for four reviewed workflows.", "Only four reviewed scopes. Original-source scenarios and arbitrary workflows remain blocked. Bindings, tests and separate approvals are required."),
        row("fixture_metadata", "Source fixture metadata", "Read exact UC schema, columns and fixture provenance.", "Metadata only. Empty fixtures are not source data or production bindings. Called children require separate scoped reads."),
        row("readiness", "Readiness and lineage", "Separate structural coverage from conversion and parity.", "Unmeasured results stay unknown. Counts never clear release gates."),
        row("validation_campaigns", "Validation campaigns", "Versioned validation contracts and evidence receipts.", "Immutable reports and scoped publication receipts are not passing business runtime tests. End-to-end parity and release remain blocked."),
        row("architecture_knowledge", "Architecture knowledge graph", "Role-scoped NetworkX retrieval with source citations.", "Current local implementation, not proof this version is deployed. The recorded UC readback used a user profile, not hosted OBO. Knowledge is not runtime validation."),
    ]
    roadmap = [
        {"key": "semantic_translation", "label": "General behavior-preserving conversion", "limits": "Arbitrary Informatica graphs, Java/procedures and side effects still need implemented semantics and independent parity tests."},
        {"key": "deployment", "label": "Controlled customer release", "limits": "General approved-design generation, GitHub publication and protected production promotion remain unfinished. Hosted app packaging is separate."},
        {"key": "zip_upload", "label": "Archive intake", "limits": "ZIP intake is unsupported; use the supported XML intake."},
        {"key": "reviewed_memory", "label": "Reviewed learning and skills promotion", "limits": "Scoped knowledge retrieval exists. Automatic promotion of lessons, governed reusable skills and fine-tuning remain planned."},
    ]
    return {"version": "capability-inventory-v1", "assessed_at": datetime.now(timezone.utc).isoformat(),
            "assessment_basis": "this_server_implementation_not_deployment_or_runtime_certification",
            "count_definition": "One scoped implementation group per card; authentication and inference are one group. Roadmap items and approval gates are excluded.",
            "items": items, "roadmap": roadmap, "generation_allowed": False, "deployment_allowed": False}