# Additive target fixture completion, 2026-11-16

## Authorized scope

- New standalone operator: [workbench/complete_target_fixtures.py](../workbench/complete_target_fixtures.py).
- New fake-client regression module: [workbench/tests/test_complete_target_fixtures.py](../workbench/tests/test_complete_target_fixtures.py).
- This document is the third and only other workspace file owned by this change.
- Input SHA-256: 08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba.
- All 307 XML TARGET definitions mirrored into each existing catalog: orion_bronze_test_ssuman, orion_silver_test_ssuman, orion_gold_test_ssuman.
- Empty structural fixtures only. Not semantic medallion transformations, runtime parity, source connectivity, or approved migration bindings.
- No app deployment/stop, source changes, catalog creation, existing-table mutation, row SQL, DML, grants, delete, Jobs, notebook execution, or inference.

## Safety and evidence

Offline planning writes a new directory without authenticating. Apply rebuilds and compares the entire plan, requires its exact SHA and CREATE consent, then exclusively claims it once. A second apply of that plan is rejected even with a new receipt directory.

Explicit labs profile only, host pinned to https://adb-1866518241053589.9.azuredatabricks.net, active human ssuman@bpcs.com, SCIM ID 4321958869410665. The existing bootstrap's newly constructed client adapter disables transport retries. No ambient credentials, service-principal fallback or namespace policy is reused. The configured host is checked offline before the first identity request.

Before any DDL, every catalog must already exist and be owned by this exact user. Every existing planned schema must also be owned by this user. These fresh UC ownership observations establish the owner's CREATE and USE authority, not inferred permissions from a missing response. Authentication, permission or metadata ambiguity stops execution. Unrelated schemas and objects are inventoried dynamically, not rejected or altered.

All pre-existing planned targets must match ordered full column names/types/nullability, exact owner, managed Delta type, xml_target_schema_only fixture marker and package SHA. Missing means SDK NotFound only. CREATE is preceded by an absence race recheck, fresh identity and parent ownership checks. Schemas are created only after NotFound; tables use the exact saved target_sql statement, with no IF NOT EXISTS. Every created object receives the same metadata readback verification. Unknown CREATE is never resubmitted: one metadata GET reconciles, then the run stops as partial.

Append-only journal intent and sanitized readbacks are flushed and fsynced. Plans, preflight, reviewed plans and summaries use exclusive creation. Receipts contain no credentials or raw SDK/SQL error messages. Final inventories verify that all pre-existing object IDs/types/owners and schema owners remain present, and that only planned objects were added. There is no distributed lock against other operators; a concurrent change fails closed, not automatic repair.

Existing table rows are deliberately not queried, so no new assertion about existing emptiness or semantic correctness is made. New statements contain definitions only and load no data.

## Verification and remote result

Pending the consolidated new and existing nonbrowser regression pass, followed by one fresh remote preflight/apply attempt. Full external plan, preflight, per-request journal/readbacks and summary locations, hashes, timestamps and counts will be recorded here after completion.

The mandatory startup blueprint documents/PDF and the Apps skill's databricks-core parent skill are absent in this checkout/environment. Existing fixture modules, validation provisioning, explicit identity implementation and the available Databricks Apps platform reference were read. No architecture or existing tested code was modified.