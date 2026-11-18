# ORION | Informatica Modernization Workbench

**Optimized Refactoring of Informatica Operations to Next-gen (Databricks) Lakehouse**

ORION is an evidence-led workbench for understanding Informatica exports and reviewing candidate paths to Databricks. It combines XML-backed discovery, interactive exploration, human review, and optional AI-assisted design and artifact generation. It is an accelerator for migration teams, not a guarantee of automated conversion or business equivalence.

> **Repository layout:** The `main` branch contains this standalone overview. The application, requirements, tests, and detailed documentation are on the [feature/dev branch](https://github.com/saketml79/ORION/tree/feature/dev). Check out that branch before following the setup steps.

## What ORION does

- **Understand the estate:** Ingest Informatica XML, inspect scoped workflows, sessions, mappings, sources, targets, fields, SQL, and control links, and review diagnostics and source evidence.
- **Explore and assess:** Browse inventories and focused graphs, inspect workflow and package context, trace candidate dependencies, and export structural reports. Views can be bounded or paginated.
- **Review designs:** Compare structural candidates for original-source and bronze-resident scenarios, review versioned agent-proposed designs, record decisions, and identify missing source, target, or operational information.
- **Use agents deliberately:** Invoke specialist Deep Agents with explicit model configuration and consent. The interface exposes available run, tool, and evaluation information; missing measurements remain unknown. Model configuration is not proof of successful inference.
- **Inspect candidate artifacts:** Generate and review scoped SQL, notebook, and workflow candidates where supported, including separately consented folder candidates and agent-assisted drafts. Supported paths, approvals, and completeness vary by scope.
- **Track readiness:** Review evidence, validation contracts, lineage, and release blockers without treating inventory counts or generated files as passing runtime tests.

Typical workflow: **load XML → inspect evidence → select scope and source scenario → review a proposal → generate and review eligible candidates → independently validate before any authorized execution or deployment**.

## Local quick start

The following Windows PowerShell example assumes Python 3.13 is installed and the DEV feature branch has already been checked out. Run from the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r workbench/requirements.txt
python -m workbench --port 8000
```

Open <http://127.0.0.1:8000>. Create or select a project and upload an Informatica XML export, or use a sample if one is available in your checkout. The optional VS Code task **Run local modernization workbench** also starts the local app after the environment is prepared.

The workbench requirements are separate from the root compiler requirements. The local service binds to loopback and runs as one process; it is not an authenticated multi-user server. This example reflects the repository entrypoint and dependency declaration, not a freshly verified clean install on every platform.

## Scope and limitations

- XML provides structural evidence, not the complete business, runtime, connection, parameter, or data contract. Resolve missing information with appropriate owners and evidence.
- Structural candidates, AI recommendations, captured-SQL drafts, generated notebooks, and native workflow definitions are distinct artifacts. None alone proves semantic correctness, executable completeness, performance, or output parity.
- General conversion of arbitrary workflows, Java and stored-procedure behavior, external side effects, and full production promotion remain incomplete. Some deterministic compiler paths cover only explicitly reviewed scopes; other candidates require independent review.
- Source and target bindings, permissions, dependencies, control flow, disabled tasks, operational behavior, and test data must be checked for each migration. A logical design review is not permission to execute or deploy.
- Validation and quality indicators reflect available evidence. Unmeasured results, failed checks, and unresolved blockers must not be interpreted as passing. Historical demonstrations and test results are not current release certification.
- The repository also contains a legacy compiler path. Its draft artifacts are separate from the workbench review flow and must not be treated as approved or production-ready migrations.

## Security and privacy

- Treat XML exports, supporting documents, generated artifacts, logs, local state, and backups as sensitive. Do not upload secrets or real credentials into project evidence, prompts, or repository files. Redaction is not a data-loss-prevention guarantee.
- Local workbench state is written under `output/workbench`; local review labels are not authenticated identities. Protect access to the machine and its files. The workbench does not automatically load a local `.env` file.
- Model calls are optional and require configuration and consent. Review provider terms, data handling, network egress, access permissions, and costs before sending source context. A selected endpoint or configured key does not establish that inference works.
- Optional tracing and any external integrations require separate configuration and privacy review. Keep credentials in an approved secret-management system, not in the repository or evidence uploads.

## Hosted and deployment boundaries

A separate Databricks Apps implementation and staging templates exist on the DEV feature branch. The hosted path is designed for the managed Apps proxy, request-scoped user authorization, and appropriately permissioned Unity Catalog and SQL resources. Its committed Delta metadata and immutable Volume artifacts are distinct from local SQLite state. Do not expose the hosted entrypoint behind an arbitrary proxy or treat local loopback mode as hosted authentication.

Hosted execution has operational constraints, including single-process coordination and platform request timeouts; long actions and ambiguous acknowledgements require state inspection before retrying. App packaging or a running app does not authorize migration jobs, prove parity, or provide general production CI/CD. Review the hosted architecture and release guidance on the DEV feature branch, validate the target environment, and obtain explicit approval before provisioning, execution, or deployment.
