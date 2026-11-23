---
name: implementation
description: Draft real PySpark, Databricks SQL, notebook JSON and assertion tests from an exact current submitted design under the host's candidate or reviewed-draft authority.
---

# Activation and procedure

Activate from the explicitly invoked Agent activity run after a workflow design is
submitted. This is an unapproved candidate-code draft for exactly the workflows and
tasks in the submitted design. Do not infer extra workflows from source references:
a single-workflow design stays single-workflow; a cohort includes only its explicitly
selected members. Bronze/silver/gold are optional destination layers, not source
selectors. Treat input as synthetic only when the caller/design explicitly identifies
it as synthetic. Record assumptions and source/behavior provenance. This candidate is
not a production parity certificate.

The host may invoke this writer directly after the explicit Agent activity consent.
Do not require a separate approval to draft this unapproved candidate. Execution,
deployment and parity remain prohibited without their separate gates.

1. Read the host envelope, exact design task IDs, artifact plan and scoped gaps.
   Keep split/combine decisions, dependencies, disabled/removed dispositions and
   shared-component responsibilities unchanged. No one-to-one mapping rewrite.
2. For each task call fetch_source_task, then fetch_sql_phases, paging with the
   returned next_offset until null. Actual node attributes, fields (including
   EXPRESSION), nested property attributes NAME/VALUE/text, and connector edges
   are the evidence. A task description or SQL override alone is insufficient.
   Finish retrieval for that exact task before staging its task or artifact-binding
   citations. Use only canonical IDs returned by that task's fetch calls; never copy
   IDs from a design, another task, a related workflow, a previous run, or memory.
   If a citation is rejected as not retrieved, fetch the remaining page(s) for that
   task, then revise the affected row with only IDs actually returned. Do not repeat
   the rejected citation or spend repair rounds retrying an unchanged row.
   Property embedded_code contains sanitized Java structural source and separate
   dynamic SQL expressions. Literal values are masked. These are not standalone
   SQL phases: preserve the surrounding loop, binding, timeout and exception contract
   or block that task. Missing stored procedure bodies cannot be invented from calls.
3. Implement actual non-SQL transformations in PySpark or Databricks SQL as the
   evidence and approved boundary require. Use exact field/port lineage and
   ordered transformation semantics. Do not manufacture columns or use SELECT *
   to hide missing expressions. Thin notebooks may call shared modules.
4. Preserve source query, lookup, target override, pre-SQL and post-SQL as distinct
   ordered phases, including session overrides, transaction/commit scope and
   failure handling. Do not assume Spark provides a cross-statement transaction.
5. In the UC-source scenario, use the provided source table data and map source
   identities from the package. Ignore historical redacted Oracle connection aliases.
   Bronze/silver/gold are output destinations. If a target/table binding is not in the
   package, use a clearly named runtime parameter rather than claiming a missing source
   table. Draft every supported task. Commands, notifications and orchestration are not
   SQL transformations and must not be rejected merely for lacking a SQL counterpart.
   A captured Email task is not merely a message-rendering placeholder: generate
   actual send-capable code for the exact captured recipient, subject and body.
   Map each Informatica variable reference to a Databricks runtime parameter; do
   not insert a literal source-variable token as an empty-value fallback. If mail
   configuration is unavailable, use explicit host, port, sender and TLS runtime
   parameters and secret-scope/key references for credentials. Do not invent these
   settings, send mail during generation, or claim a runtime delivery occurred.
   Translate the captured purpose to a suitable Databricks task when possible. When a
   specific behavior cannot be translated, include an
   explanatory notebook or other appropriate placeholder artifact with its source
   purpose, what is not applicable or missing, and what implementation is needed.
   A placeholder must not claim execution or equivalence and must never perform the
   missing external side effect. Still submit artifacts for the supported tasks.
   Keep assumptions visible in the manifest; do not claim parity or production readiness.
   A literal pmcmd command that invokes an Informatica workflow is an external
   Informatica invocation, not a Databricks child Job. In particular, `-nowait` means
   asynchronous submission, not child completion. Do not invent `child_job_id`, add
   child-workflow tasks, or claim the invoked workflow is converted unless the exact
   Databricks job binding and included workflow are present in the submitted design
   and retrieved evidence. In single-workflow mode, produce a clearly identified
   non-executing placeholder for the external call. State that the called workflow's
   notebook is not implemented. Do not require child conversion, a child Job ID,
   demo mode, or simulated submission to draft the selected workflow. Do not create
   a child-job parameter or widget, call the Databricks Jobs API, or simulate a
   launch in this placeholder, even when an older design suggests doing so.
6. Stage one complete file and one typed metadata row at a time, then call
   commit_generation_receipts() last, with NO arguments. Never send a monolithic
   manifest. The host pins exact envelope hashes/version from its server record.
   Allowed files: notebooks/*.ipynb, modules/*.py, sql/*.sql, tests/test_*.py.
   Artifact-plan .py notebook proposals require an outline amendment before
   this JSON-only writer can implement that path. Do not silently rename them.
   Use importable modules and explicit parameters, not hidden notebook state.
7. Notebook content is a JSON string with nbformat=4, nbformat_minor=5, metadata,
   and cells. Every cell needs metadata.language (markdown/python/sql), cell_type
   and source. Code cells need execution_count:null and outputs:[]. New cells
   may omit IDs; the host assigns stable IDs. Do not embed magics in Python cells.
8. For reviewed noncandidate work, provide all obligation categories: parameters, nulls,
   types, writes, transactions, sql_phases, control, recovery. Explain cited
   not_applicable cases. test_required references a real test artifact and a
   test_ function containing nonconstant assertions. Write substantive fixtures
   and expected outcomes, not checks that code strings exist. Tests are authored,
   not executed, and are not an independent parity oracle. Candidate generation
   may save valid owned code without these bookkeeping rows; retain gaps as review notes.
9. Cite only canonical evidence IDs actually returned by this invocation's tools.
   Every artifact links to approved tasks; task artifact_paths must agree with
   artifact task_ids. Generate the selected tasks and disclose actual missing code;
   an intentional external-call placeholder is a generated task, not a blocker.
   Preserve each approved design implementation_kind as the matching native
   Databricks Lakeflow Jobs task type in generated workflow resources. Do not silently
   replace a native task type with a generic task or downgrade it to a notebook.
   Model self-confidence cannot resolve gaps. Fix validation feedback; revision
   limits apply only when an explicit budget is supplied. Stop on provider failure,
   never invent a fallback.
10. For reviewed noncandidate work, stage TranslationItem rows for transformations, ordered SQL phases,
    complete Java behavior, procedures and control/side effects. Each item names
    task_id, kind, exact retrieved source_record_keys, evidence_ids,
    artifact_paths, status (translated/blocked), explanation and blockers.
    Cite every record's canonical IDs and include those citations on its owned
    implementation artifacts. Blocked items require a blocked owning task and
    explicit missing behavior. Cover the full source inventory, not selected
    favorable snippets. Missing coverage remains unverified for historical sets.
    Set input_data_basis (unknown/synthetic/source_snapshot/production) separately
    from behavior_basis (unknown/source_preserving/substituted). Neither authored
    coverage nor either provenance label clears execution or parity gates.
   Derive expected results independently of generated implementation output.
   Candidate mode does not require the translation inventory to save code.
11. Always call commit_generation_receipts() after all file and metadata staging.
   Legacy finalize_generation_artifact_set and submit_generation_artifact_set remain
   compatible for old clients only, not the primary writer procedure.
   If exact behavior is unavailable, submit supported artifacts and state the
   missing behavior explicitly; never finish with free-text-only commentary.

## Strict writer tool shape and repair

## Parameter and completion discipline

- Author a parameter/default contract for parameters actually required by the
   selected workflow. Use source evidence for defaults, never sibling examples.
   Secret scope/key references default to empty strings, never secret values.
- Keep UC source bindings read-only and distinct from isolated output/counter tables.
   Validate fully qualified names and reject source/output identity collisions before
   any write. Do not initialize, reset or delete source data to make a demo pass.
- Maintain task-owned coverage while paging evidence: stage disjoint translation
   rows as each page is implemented. Before commit, compare all retrieved record keys
   with accepted translation receipts. Missing-record counts are not completion;
   revisit the named task's evidence pages and repair uncovered keys explicitly.
   Do not repeat an unchanged rejected commit or dump the entire manifest again.

## Submission wrapper

### Preferred incremental protocol

- Call stage_generation_artifact with {relative_path, kind, content}. Content is
   literal Python/SQL/test source or a string containing complete notebook JSON.
   The host checks path, credentials, the existing size bounds, notebook languages,
   Python AST/SQL syntax and nonconstant test assertions. It executes nothing.
   Exact audit draft bytes are preserved on disk as incomplete, not committed
   executable artifacts. Notebook IDs are normalized deterministically.
- Keep each receipt's relative_path, kind and content_sha256. An identical restage
   is idempotent. To revise a staged file, pass replace_sha256 equal to its current
   receipt hash. Case collisions and implicit conflicting replacements are rejected.
- If a single file is too large for one response, call stage_generation_file_chunk
   with {relative_path, kind, content, offset:0, final:false}. Append literal text at
   exactly the receipt's next_offset in UTF-8 bytes. Set final:true on the last chunk.
   All chunks of a revision must carry the same replace_sha256. The full file must
   validate before any staging receipt is issued. Do not JSON-escape literal chunks.
   A rejected final chunk leaves the preceding prefix unchanged for correction.
- Call stage_generation_task({task: object}) for each strict TaskDraft: task_id,
   implementation_kind, status, artifact_paths, evidence_ids, blockers, obligations.
   For reviewed noncandidate work, include all eight obligations, or use obligations:[] and stage each strict
   Obligation via stage_generation_obligation({task_id, item}). Each obligation has
   category, status, explanation, evidence_ids and optional test_path/test_function.
   Candidate code may use obligations:[] without individual obligation staging;
   missing obligations remain review notes, not translation or parity certification.
- Call stage_generation_artifact_binding({relative_path, content_sha256, task_ids,
   evidence_ids}) for every complete file. Copy exact file receipt hashes. Ownership
   is explicit, never inferred from paths or prose. Bindings must be revised after
   file revisions. Metadata revisions use replace_sha256 from that row's current
   receipt_sha256, which is bound to this invocation and exact provenance.
- Call stage_generation_translation({item}) for each strict TranslationItem. Use
   separate small items for large translation inventories, with disjoint explicit
   source_record_keys, no summary omission. In reviewed noncandidate work, every task-owned canonical record needs
   an item. Every item has task_id, kind, source_record_keys, evidence_ids,
   artifact_paths, status, explanation and blockers. Blocked behavior remains blocked.
   Candidate code does not require this inventory to save exact owned artifacts;
   retain missing or contradictory authored claims as unverified review evidence.
- Call stage_generation_basis({input_data_basis, behavior_basis}) explicitly.
   Then call commit_generation_receipts() with NO arguments. The server assembles
   all stored strict rows, pins the provenance envelope from its exact authoritative
   record, hydrates exact invocation-owned file bytes and applies GenerationArtifactSet,
   translation ownership/completeness and unchanged _validate gates. No defaulted
   metadata, guessed ownership, synthetic completion or monolithic manifest call.
   In candidate mode, exact current bindings and the pinned design may project task
   availability without authored bookkeeping. `drafted` means generated owned code,
   and `generation_complete` means every selected task has its required implementation,
   not review-ready behavior or certified translation. Review quality, retrieval gaps,
   execution, deployment and parity keep their independent gates.
- Use inspect_generation_receipts({offset:0}), following next_offset, to inspect
   bounded under-2KB inventory pages. Do not repeatedly request a full record list.
   Missing commit metadata returns an actionable missing list and state hash.
   Fix those rows before committing again. Only repeated identical invalid state
   after feedback may stop as no-progress, never as an elapsed timeout. Empty {}
   is the correct argument object for the new commit tool.
- finish_reason=length means nothing from that response was accepted. Do not repeat
   the oversized full-set call. Switch to per-file staging or smaller literal chunks,
   then stage typed metadata incrementally and commit. Upstream truncation cannot be guaranteed absent.
   There is no new provider max_tokens override, elapsed cutoff or silent truncation.

### Compatible full-set protocol

- The tool call is {artifact_set: object}, not {design: object}, a file list or prose.
   Use the injected GenerationArtifactSet schema for every required nested field.
   Copy design_id, design_version (integer), design_sha256, input_version and package_hash
   from the current envelope, never derive replacements or assert an approval.
- Each artifact has relative_path, kind (notebook/module/sql/test), content (a string
   containing actual code or notebook JSON), task_ids and retrieved evidence_ids.
   Each TaskDraft has task_id, implementation_kind, status, artifact_paths, evidence_ids,
   blockers and obligations. Use proposed IDs, not original XML IDs. All reciprocal
   artifact/task links must match, including tests and shared modules.
- In reviewed noncandidate work, every drafted task has exactly the eight documented obligation categories once.
   test_required needs test_path and test_function; not_applicable needs cited reasoning.
   Non-SQL tasks may explain sql_phases as not_applicable, never fabricate a SQL phase.
   A blocked behavior must agree across task status, blockers and translation coverage.
- Preserve all source-owned constants/thresholds, status updates, loop bounds, sleep
   units and exception paths from retrieved records. Masked Java literals, unavailable
   procedure bodies and dynamic child bindings cannot be replaced by the WIL example.
   The writer tools expose only the frozen selected task scope, not arbitrary caller/
   child retrieval. Use that scope honestly; recommend further conversion separately.
- Check Python syntax, Databricks SQL syntax, constrained notebook JSON and strict
   schema types before submission. Notebook content is serialized JSON, not an object
   in content. Code cells have null execution_count and []; markdown cells have neither
   execution_count nor outputs. Static acceptance is not execution or parity proof.
- On rejected submissions repair the exact contract using the injected schema and
   safe feedback within the existing revision budget. Do not replace code with prose,
   erase real blockers, lower validators or fabricate successful tests/provenance.

## Examples and counterexamples

- An approved shared cleansing module may serve two independently parameterized
  notebook tasks without merging their schedules. Cite both exact source graphs.
- A required join expression and exact nullable decimal ports support a PySpark
  join plus null/precision fixtures, not a copy of one source query.
- An unknown JDBC endpoint can remain a required runtime binding. An unknown
  join predicate cannot be replaced with a cross join or a guessed key.
- A transaction spanning pre/post SQL with no supported preservation contract
  blocks that task. Successful SQL parsing does not clear the blocker.

## Exit checklist

Actual implementation bodies, exact approved task coverage and version, complete
source/SQL retrieval, cited obligation tests for reviewed noncandidate work, explicit scoped blockers, no secrets,
no execution or parity claims. Static acceptance only creates a draft for review.

## User-authorized synthetic demo bindings and translations

This section applies only when the caller explicitly authorizes a synthetic UC demo
for this generation and identifies its supplied synthetic UC source data and scope.
UC tables, bronze/silver/gold, or candidate-code mode do not imply synthetic-demo authorization.
For an ordinary single-workflow conversion, ignore this section. No demo parameter,
simulation, or child conversion is required to draft the selected workflow.

- Use only the supplied synthetic UC source data and explicitly authorized isolated
   outputs. Keep source bindings read-only; do not initialize, reset or delete source
   data, invent columns/defaults, or borrow bindings from another workflow.
- In explicitly authorized `demo_mode` only, an unavailable original child or email
   adapter may produce a clearly labeled SIMULATED CHILD SUBMISSION or SIMULATED EMAIL
   receipt with no external call. A supplied child ID must not bypass that no-call
   condition. Preserve branch selection and asynchronous submission intent, disclose
   substituted behavior, and do not claim original child conversion or email delivery.
- Non-demo mode must require the actual child job/notification binding, supported by
   the exact submitted design and retrieved evidence. Never imply that a blocking Run
   Job task preserves a captured `-nowait` command. The ordinary single-workflow
   external-call placeholder remains non-executing; this section does not add children,
   change native task types, or authorize a launch outside the submitted scope.
- Record synthetic input and substituted behavior independently from translation
   claims. Demo receipts never clear review, execution, deployment or production parity
   gates. Generation itself does not execute even an authorized demo.