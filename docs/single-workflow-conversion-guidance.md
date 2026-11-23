# Single-workflow conversion guidance

A single-workflow request converts only the selected Informatica workflow. A captured
`pmcmd startworkflow` command for another workflow is displayed and generated as an
informational, non-executing placeholder. The called workflow is not implemented by
that placeholder. It does not require or generate a child Job ID, a child-job widget,
a demo-mode switch, a simulated launch, or an automatic cohort expansion. Cohort
review is optional and its members must be selected explicitly.

A successful candidate generation means the selected workflow's expected notebook
and other task artifacts have actually been saved, with structured condition tasks
represented in the design. This does not certify source equivalence, runtime behavior,
or deployment readiness. Intentional external placeholders and missing receipt
bookkeeping do not block candidate code generation. Actual missing artifacts, invalid
output, provider errors, and unverified behavior must remain visible and must not be
reported as successful implementations. Execution and deployment remain separate user
actions.

The workflow-call labels reflect literal captured calls only. A workflow with no
captured literal call is not necessarily independent of external scheduling, data,
or other invocation mechanisms. Historical run receipts and artifacts document what
actually happened and are not rewritten to match this guidance.
