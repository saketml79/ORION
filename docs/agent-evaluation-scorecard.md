# Agent activity evaluation scorecard

The desktop activity section has two equal columns: recorded model/tool activity
on the left, an evaluation scorecard on the right. Narrow screens stack them.

## Current measurements

- Model execution success: completed calls divided by completed plus failed calls.
  Pending calls are excluded. This measures technical execution, not correctness.
- Tool acceptance: accepted calls divided by accepted, rejected and failed calls.
  Historical recordings that combine failures and rejections do not get a score.
- Role output completion: recorded completed required roles divided by six.
  Completion does not measure the correctness of their findings.
- Task artifact completion: writer-reported drafted tasks divided by reported tasks,
  including native condition tasks. This is not independently verified execution.

Cards display numerators, denominators and bars only when observations support a
valid nonzero denominator. Missing evaluations are not displayed as zero percent.
Run completion and code-generation results are separate from migration correctness.

## Reference-dependent evaluations

Precision@K and Recall@K require labeled relevant evidence and an explicit K.
Groundedness and citation support require claim-level judgments. Schema-linking
accuracy, recovery, memory quality and business parity need task-specific evaluation
cases or independent outcomes. These remain explicitly not measured until actual
versioned evaluator results are attached to the selected run. No new judge calls,
tracing exports, evaluation experiments or execution are initiated by this view.

## Tracing status

LangSmith missing server credentials or project configuration is Not configured,
not delivery Failed. Invalid destination configuration is a configuration error.
Actual delivery failures retain their failed status. Historical receipts are not
rewritten; the UI interprets their recorded configuration reason. MLflow tracing
is not integrated, and no MLflow delivery is claimed or enabled by this change.

Methodology: [AI Evaluation Metrics Explained](https://agenticmusings.hashnode.dev/ai-evaluation-metrics-explained-from-precision-k-and-recall-k-to-reliable-agents).
