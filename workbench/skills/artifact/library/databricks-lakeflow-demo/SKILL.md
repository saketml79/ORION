---
name: databricks-lakeflow-demo
description: "Use when rendering approved isolated WIL_NET_DIS semantic-test artifacts as notebook JSON, Python notebook source and native Declarative Automation Bundles."
---

# Deterministic isolated Lakeflow demo artifacts

Use `compile_demo(canonical_model, workflow_id, DemoBindings(...))` only after
explicit generation and fixture-replacement approval. Returns `proposal`,
`manifest`, `artifacts` (relative path to UTF-8 text). No IO or agent invocation.
Use `export_demo(result, NEW_directory)` for a new immutable export directory.
It rejects preexisting destinations, traversal and content-hash mismatches.

The parent may show proposal graph, rationale, task coverage, gaps, notebook JSON
and YAML in the proposal tab, but must separately enforce authenticated project
scope, persistence, release version and approval policy. This module does not wire
the tab or claim agent authorship. Subsequent agents may review the result.

## Artifact rules

- Every notebook cell includes metadata.language and stable metadata.id.
- JSON notebook and Python source represent identical cells. DAB sync excludes
  JSON notebooks to prevent duplicate workspace notebook names.
- Use yaml.safe_dump, native notebook_task, condition_task and run_job_task.
- Serverless STANDARD, user run_as, no service principal or cluster fallback.
- Keep 300-second original wait default; only the outer test suite uses zero.
- SDK child launch returns submission run ID without waiting for completion.
- No production source binding, destructive overwrite, email delivery or invented
  original child logic. Require existing dedicated orion_demo_ schema.
- Manifest records original package hash, canonical scope hash, binding hash,
  per-content hashes, task dispositions and precise tests/statuses.
- Generated is not locally validated, validated is not remotely executed,
  remote test success is not Oracle equivalence or production readiness.

## Checklist before handoff

Run the compiler regression using the actual original XML once. Check notebook
syntax, generated execution on local doubles, strict branch/reset cases, hash
integrity and installed bundle schema. Exported portable unittest checks do not
replace generated execution tests. Record exact commands and observed results.
No deployment may be performed without a new, explicit user approval.