"""Explicit public-document research and optional user-approved UC publication.

Seed definitions are research inputs only. They contain no asserted release state,
retrieval time, hash or workspace availability. Every invocation fetches anew.
"""
import argparse
import json
from pathlib import Path
import tempfile

from workbench.architecture_knowledge import Snapshot, curate

BASE = "https://docs.databricks.com/aws/en/"


def seed_requests():
    return [
        {"label": "Liquid clustering", "release_url": BASE + "tables/clustering", "usage_url": BASE + "tables/clustering",
         "summary": "Evaluate liquid clustering for Delta data layout rather than automatically retaining legacy partitioning.",
         "applicability": ["Query-filter and data-skew evidence can motivate clustering key review"],
         "tradeoffs": ["Layout changes require performance measurement and reader/writer protocol compatibility"],
         "requirements": ["The documented Delta GA scope requires Runtime 15.4 LTS or above; do not extend it to automatic or Iceberg subfeatures", "Do not combine clustering with partitioning or ZORDER"],
         "pillars": ["optimization", "integrity", "databricks_architecture"]},
        {"label": "Predictive optimization", "release_url": BASE + "release-notes/product/2024/june#predictive-optimization-is-now-ga",
         "usage_url": BASE + "optimizations/predictive-optimization",
         "summary": "Evaluate managed-table maintenance with explicit retention, permissions and cost review.",
         "applicability": ["Unity Catalog managed tables needing maintenance"],
         "tradeoffs": ["Serverless maintenance incurs cost; VACUUM retention affects time travel and recovery"],
         "requirements": ["Premium or higher, supported region and SQL warehouse or Runtime 12.2 LTS or above", "Review owner or MANAGE privileges and retention before enabling; external tables are excluded"],
         "pillars": ["optimization", "integrity", "security", "databricks_architecture"]},
        {"label": "Serverless compute for workflows", "release_url": BASE + "release-notes/product/2024/july#serverless-compute-for-workflows-is-ga",
         "usage_url": BASE + "jobs/run-serverless-jobs",
         "summary": "Consider serverless Jobs compute when documented task, dependency and networking requirements fit.",
         "applicability": ["Supported job workloads without required unsupported classic-compute customizations"],
         "tradeoffs": ["Validate networking, libraries and task compatibility rather than assuming arbitrary Spark configuration support"],
         "requirements": ["Unity Catalog workspace and a supported serverless region; workspace feature availability remains unverified"],
         "pillars": ["optimization", "security", "databricks_architecture"]},
        {"label": "For each", "release_url": BASE + "release-notes/product/2024/august#the-databricks-jobs-for-each-task-is-ga",
         "usage_url": BASE + "jobs/tasks/for-each",
         "summary": "Consider parameterized nested tasks for repeated independent operations, preserving source ordering requirements.",
         "applicability": ["Repeated transformations or validation tasks with explicit parameter contracts"],
         "tradeoffs": ["Parallelism can change side-effect ordering; matching templates alone do not prove semantic equivalence"],
         "requirements": ["No nested For each; downstream dependencies reference the outer task", "Respect documented input-size bounds and verify per-iteration failure behavior"],
         "pillars": ["optimization", "accuracy", "integrity", "data_validation", "databricks_architecture"]},
        {"label": "Job completion triggers", "release_url": BASE + "jobs/job-completion-triggers",
         "usage_url": BASE + "jobs/job-completion-triggers",
         "summary": "Non-GA dependency-trigger research retained for explicit preview review, excluded from default architecture candidates.",
         "applicability": ["Explicitly opted-in upstream job completion monitoring"],
         "tradeoffs": ["Completions are not matched by business date or data batch; never substitute for proven data readiness"],
         "requirements": ["Admin enables the Beta in participating workspaces, same UC metastore and appropriate Run as permissions"],
         "pillars": ["accuracy", "security", "databricks_architecture", "data_validation"]},
        {"label": "Enforced Delta constraints", "release_url": BASE + "tables/constraints",
         "usage_url": BASE + "tables/constraints",
         "summary": "Constraint research requires explicit release evidence; documentation existence alone does not establish GA.",
         "applicability": ["Review enforced versus informational constraints as part of data correctness"],
         "tradeoffs": ["Do not equate informational keys with enforced data validation or output parity"],
         "requirements": ["Verify the exact constraint type, enforcement and runtime requirements independently"],
         "pillars": ["accuracy", "integrity", "data_validation"]},
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--consent", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.publish and not args.consent:
        parser.error("--publish requires explicit --consent")
    if args.report.exists():
        parser.error("Report destination must be new")
    from workbench.databricks_models import profile_identity
    from workbench.hosted_storage import provision
    from workbench.delta_runtime import DeltaRuntime
    from workbench.hosted_registry import _registry
    from workbench.knowledge_store import KnowledgeStore
    # Exactly one approved human profile. No ambient/alternate authentication.
    identity = profile_identity("labs")
    remote = provision(identity, "orion_dev_", initialize=False)
    with tempfile.TemporaryDirectory(prefix="orion-knowledge-") as temporary:
        runtime = DeltaRuntime(lambda: identity, remote, temporary, warehouse="e3a0fc2c08db05bb")
        _, registry = _registry(runtime, tables=("projects",))
        if not any(owner == args.project_id for owner, _ in registry["projects"]):
            raise PermissionError("Project is not in this user's committed registry")
        value = curate(seed_requests(), identity.host, identity.user_id, args.project_id)
        store = KnowledgeStore(runtime.store, runtime.artifacts)
        receipt = store.save(value, consent=True) if args.publish else None
        # Fresh clients and authorization for independent durable restore, no process snapshot reuse.
        restored = None
        if receipt:
            fresh = profile_identity("labs")
            fresh_remote = provision(fresh, "orion_dev_", initialize=False)
            reader = DeltaRuntime(lambda: fresh, fresh_remote, temporary, warehouse="e3a0fc2c08db05bb")
            restored = KnowledgeStore(reader.store, reader.artifacts).load(args.project_id)
            if restored is None or restored.value != value:
                raise ValueError("Fresh authorized UC readback mismatch")
        snapshot = restored or Snapshot(value, identity.host, identity.user_id, args.project_id)
        report = {"project_id": args.project_id, "authentication": "explicit_labs_human_profile_not_hosted_OBO",
            "receipt": receipt, "knowledge": snapshot.status(),
            "features": [{"id": identifier, "label": n["label"], "release_state": n["release_state"],
                "sources": [snapshot.graph.nodes[d]["source"] for d in n["evidence_ids"]]}
                for identifier, n in snapshot.graph.nodes(data=True) if n["kind"] == "feature"],
            "fresh_identity_readback_verified": restored is not None, "app_deployed": False,
            "business_data_written": False, "inference_invoked": False}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()