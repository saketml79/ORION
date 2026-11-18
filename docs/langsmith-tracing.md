# LangSmith integration

Updated: 2026-10-30.

## Server configuration

- WORKBENCH_LANGSMITH_ENABLED=1: explicit export opt-in.
- WORKBENCH_LANGSMITH_PROJECT: explicit project label.
- LANGSMITH_API_KEY: inject through a Databricks Apps secret resource. Never enter it in ORION forms, review comments, XML, browser storage or chat.
- LANGSMITH_ENDPOINT: optional, currently restricted to https://api.smith.langchain.com. Other regions require explicit configuration support and review.

The existing global LANGSMITH_TRACING=false and LANGCHAIN_TRACING_V2=false defaults prevent automatic full-payload tracing. Keep these defaults: the application uses its own explicit metadata-minimal callback integration instead.

## Actual tracing

Real LangChain callback run IDs create parent/child LangSmith spans for chain, model and tool execution. Inputs, outputs, serialized model configuration, tool arguments, documents, XML, SQL and credentials are excluded. Numeric provider token counters and hashed owner scope are allowed. A bounded delivery queue runs outside model/tool callbacks; transport failures are reported without failing analysis or granting approval.

Agent Activity exposes GET /api/tracing/status. Reading status does not probe LangSmith or invoke a model. Each new run records tracing delivery status. Existing runs are not retrospectively traced. Submitted means an acknowledged submission, not verified LangSmith UI readback. Pending or failed export is not represented as working tracing.

Loop verification can export allowlisted deterministic rubric feedback tied to the same runtime run and owner. This is advisory output checking, not semantic parity, an independently curated evaluation dataset, a comparative model experiment, or approval.

## Activation boundary

Implementation alone does not establish a configured LangSmith project. No credential or project was supplied during this change, so live trace delivery remains unverified and disabled until explicitly configured. Use the user's approved secret-management workflow. Never silently use another project's credentials or Databricks service-principal identity.

Invocation controls were not unified or otherwise changed. Project-loading latency remains a separate unresolved issue.
