---
name: databricks-lakeflow-counter-control
description: "Use when reviewing WIL_NET_DIS counter SQL, TgtSuccessRows branching, timer or asynchronous child semantics for a bounded Lakeflow Jobs demo."
---

# Counter and control, reviewed isolated test pattern

## Required evidence

Read the canonical workflow ID, complete session overrides, mapping connectors,
field types, writer properties, control links, enabled states and source XML hash.
Use the deterministic `workbench.demo_conversion.compile_demo` boundary with an
explicit `DemoBindings` contract. Do not edit the existing agent runtime to invoke
it. Parent application authorization and proposal persistence remain separate.

## Supported semantics

Only the exact fingerprint-reviewed WIL_NET_DIS scope is supported. The session
SQL overrides the mapping's DUAL/18537 default. It selects constant (18300,1) for
EVERY source row, without a template predicate. Connectors are identity copies,
not an expression transformation. Treat source rows as Insert is authoritative;
writer update/delete capability settings do not imply an update strategy.

Post-SQL adds another row, then removes only template 18300 if its count reaches
60. Branches use session TgtSuccessRows BEFORE post-SQL: >=35 email, <35 child.
Timer is a single 300-second edge, not a recurring dependency polling loop.

## Permitted proposal

Constrained isolated test only: new run-owned Delta fixture, original numeric
branches, explicitly disabled email mock, explicitly labeled child replacement.
The original child has 15 tasks and is not converted. SDK run_now without result()
preserves asynchronous submission; a native Run Job task would wait and is NOT an
equivalent child-command replacement. Native Run Job is appropriate for the
outer 34/35 TEST suite only. No business child transformations may be invented.

## Abstention and validation

Unknown or changed scoped nodes, properties, fields, links or SQL fail closed.
Report partition-DDL uncertainty, transaction differences, missing credentials,
user identity requirements, disabled delivery and unconverted child as gaps.
Test 0,29,30,34,35; preserve non-target templates; reject overwrite and identifier
injection. Validate actual original XML once, execute generated notebook bodies
against local doubles, then report local versus remote evidence separately.
Never call this a full migration, AI-authored proposal or deployment approval.