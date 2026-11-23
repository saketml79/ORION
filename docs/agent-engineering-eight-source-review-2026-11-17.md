# Agent engineering: eight-source review and practical ORION recommendations

Reviewed: 2026-11-17. This is research and proposed implementation guidance,
not evidence that every recommendation is already implemented or tested.
Summaries are original paraphrases. The commentary supplied with the links is
distinguished from the publishers' actual recommendations below.

## Companion document requested earlier

[Migration agent responsibilities, memory and multi-endpoint routing](migration-agent-responsibilities-memory-and-model-routing.md)
contains the proposed role expansion, per-phase model routing, structured
handoffs, run memory lifecycle and benchmark requirements. It is a proposal,
not a claim that extra roles or multi-endpoint controls have been released.

## Reading coverage and source register

The supplied URLs contained encoded publisher labels after a pipe. These were
removed to retrieve the actual source URLs.

| # | Source | Reading coverage |
|---|---|---|
| 1 | [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) | Article and appendices retrieved, including tool-interface guidance. Originally published December 19, 2024; current page notes later tooling changes. |
| 2 | [Anthropic: Multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) | Article and appendix retrieved. Published June 13, 2025. |
| 3 | [Claude Code best practices](https://code.claude.com/docs/en/best-practices) | Current documentation retrieved. Living page, not a reconstruction of its 2025 wording. |
| 4 | [OpenAI: Practical guide PDF](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf) | PDF downloaded but readable extraction failed. Complete [official HTML guide](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/) read instead. Edition equivalence to the PDF is NOT verified. |
| 5 | [Google/Kaggle: Agents Companion](https://www.kaggle.com/whitepaper-agent-companion) | Landing page and all 76 pages of extracted PDF text read. Image-only diagram text may be absent. [Publisher-embedded PDF](https://drive.google.com/file/d/1GVPdwEh48bErTNdhxD0vqxPAifSx1I6Y/preview). |
| 6 | [HumanLayer: 12-Factor Agents](https://github.com/humanlayer/12-factor-agents) | Repository landing README and its twelve-factor overview read. Linked factor chapters were not all separately retrieved. |
| 7 | [OWASP Agentic Top 10 for 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) | Landing page and text extracted from all 57 pages of the [official report](https://genai.owasp.org/download/52117/?tmstv=1765059207). Version 2026, December 2025. |
| 8 | [GitHub Copilot cloud-agent concepts](https://docs.github.com/en/copilot/concepts/about-copilot-coding-agent) | Supplied URL resolves to the current broader Copilot on GitHub/Mobile concepts content, which was read. Linked Memory documentation was not independently audited. |

No assertion of full visual inspection of every PDF page or every outbound
reference is made. Publisher product names, limits and defaults can change.

## 1. Anthropic: Building effective agents

### Summary

The article distinguishes predefined workflows from agents that dynamically
choose their process and tools. Start with the simplest adequate system and add
complexity only when measured outcomes justify its cost and latency. It describes
prompt chaining, routing, parallelization, orchestrator-workers and
evaluator-optimizer patterns. Ground actions in environmental feedback, use
explicit stopping conditions, and invest heavily in clear tool interfaces.

### Tips applicable to ORION

- Keep schema parsing, identifier validation, ownership and parameter checks deterministic.
- Let agents decide which evidence to retrieve and how to propose a redesign.
- Provide concrete tool schemas, examples, limits and actionable rejection feedback.
- Use a separate evaluator context for generated code, with precise criteria.
- Test tool ergonomics, not just prompt wording. Count corrected versus repeated errors.
- Use sandboxed tests for executable behavior; an evaluator opinion is not parity evidence.

### Commentary correction

The supplied definition of agent as a generic task container is the commenter's
definition, not Anthropic's distinction. The article explicitly supports dynamic
decomposition and evaluator-optimizer loops. It does not impose a universal ban
on LLM evaluation or prescribe a strict mechanical/model/LLM hierarchy.

## 2. Anthropic: How we built our multi-agent research system

### Summary

A lead researcher dynamically delegates bounded searches to specialists with
separate context windows, then synthesizes their findings. Clear objectives,
output formats and division of labor reduce duplication. Parallelism helps
independent research, but coordination, state recovery, cost and deployment
become harder. Evaluation combines outcomes, process quality and human review.

### Tips applicable to ORION

- Give each specialist an explicit evidence responsibility and a typed handoff.
- Parallelize independent metadata reads, not dependent code edits or ordered SQL phases.
- Persist exact artifacts and exchange references rather than copying giant code bodies.
- Checkpoint long operations; reconcile uncertain writes before retrying.
- Measure whether added specialists reduce errors enough to justify added tokens.
- Start evaluation with a small representative workflow set, then expand.
- Keep active runs on compatible code versions during deployment.

### Commentary correction

The article demonstrates runtime decomposition, contrary to the pasted advice
that work must always be split before runtime. It also endorses calibrated LLM
judges for research. Its reported performance and token multipliers are specific
to its research system, not expected ORION improvements or service guarantees.

## 3. Claude Code: current best practices

### Summary

Distinguish model-read instructions from harness-enforced controls. Put mandatory
checks in enforced hooks or CI, security boundaries in permission rules and
sandboxing, and task knowledge in concise scoped instructions and skills. Define
completion using executable checks and show their results. Use fresh reviewers,
bounded budgets and reversible changes; inspect early behavior before unattended use.

### Tips applicable to ORION

- Expose sanity checking as an agent tool, but enforce acceptance checks independently.
- Freeze exact package, design and artifact hashes across generation and review.
- Keep role skills focused instead of repeating every policy in every prompt.
- Preserve original artifacts and create new revisions rather than rewriting history.
- Require a concrete definition of done and exact regression receipts.
- Keep permission policy outside model-editable state.

### Caveat

This is the current documentation, not the missing older edition. Model names,
context-window defaults, permission modes and CLI flags in it must be rechecked
against the installed product before adoption.

## 4. OpenAI: A practical guide to building agents

### Summary

The accessible official HTML guide covers when agents are appropriate, model and
tool selection, operational instructions, single-agent and multi-agent patterns,
layered safeguards and human intervention. Start with one capable agent and
clear tools; introduce delegation when complexity or persistent tool-selection
problems justify it. Distinguish a manager calling specialists from a handoff
that transfers execution control.

### Tips applicable to ORION

- Evaluate tool risk using actual target, permissions, reversibility and side effects.
- Separate read-only discovery, candidate code generation, provisioning and execution.
- Specify missing-information branches and repeated-failure exit conditions.
- Prefer clear non-overlapping tools over adding roles to compensate for ambiguity.
- Require approval where actions have meaningful external impact, not for every read.
- Keep cost and action limits enforced by the harness, not merely requested in prose.

### Caveat and attribution

These points are based on the official HTML guide, not a claimed complete PDF
reading. Environment reproducibility and a staged trust rollout are useful
ORION recommendations, but are not presented here as exact quotations or a
defined maturity model from the inaccessible PDF edition.

## 5. Google: Agents Companion

### Summary

The companion develops AgentOps, success metrics, evaluation of trajectories and
responses, multi-agent coordination, agentic retrieval, enterprise operation,
task contracts and an automotive case study. Model, tools and orchestration are
distinct responsibilities. Detailed traces explain failures, while business
outcomes establish whether the task actually succeeded.

### Tips applicable to ORION

- Measure source coverage, accepted artifacts and semantic checks separately from call success.
- Evaluate mandatory action ordering as well as final output.
- Use trajectory precision/recall or ordered matching only where a reference path is meaningful.
- Improve parsing, retrieval metadata and evidence recall before adding more agents.
- Use task contracts specifying deliverables, verification, cost, duration and reporting.
- Calibrate automated evaluation against domain-expert judgments.
- Keep durable task state distinct from conversational text.

### Commentary correction

The paper supports LLM autoraters with human calibration; it does not prohibit
them. Bronze/silver run-log tiers are not a prescribed schema in the extracted
text. Those are potential project conventions. The contractor architecture is
a proposal, not proof that contracts eliminate coordination or oversight risks.

## 6. HumanLayer: 12-Factor Agents

### Summary of the twelve-factor overview

| Factor | Practical interpretation for this project |
|---|---|
| Natural language to tool calls | Convert requests into typed, validated operations. |
| Own prompts | Version prompts with code and regression evidence. |
| Own context | Select relevant exact evidence and retain source attribution. |
| Tools as structured outputs | The harness validates and executes an operation, not arbitrary prose. |
| Unify execution and business state | Avoid contradictory run, artifact and UI completion records. |
| Launch/pause/resume APIs | Use explicit lifecycle contracts and safe boundaries. |
| Contact humans through tools | Emit a structured intervention request with evidence and choices. |
| Own control flow | Keep safety and lifecycle decisions in observable application code. |
| Compact errors into context | Return concise actionable diagnostics, not raw secret-bearing exceptions. |
| Small focused agents | Split roles only where responsibilities and evidence are distinct. |
| Trigger from anywhere | Reuse the same authenticated workflow regardless of UI entry point. |
| Stateless reducer | Restore decisions from explicit versioned state rather than hidden process memory. |

### Commentary correction and coverage limit

The README advocates incremental application patterns rather than an obligatory
framework rewrite. The Hermes/Argo history and issue tracker as the sole state
authority are commentator choices, not requirements established by this README.
This summarizes the overview; separate full chapter reading remains follow-up.

## 7. OWASP Top 10 for Agentic Applications 2026

### Summary and ORION threat checklist

| ID | Report category | Relevant control |
|---|---|---|
| ASI01 | Agent Goal Hijack | Treat XML, comments, retrieved documents and tool results as untrusted evidence, not instructions. |
| ASI02 | Tool Misuse and Exploitation | Enforce exact arguments, targets, allowlists, budgets and privileges before action. |
| ASI03 | Identity and Privilege Abuse | Fresh user-scoped identity, least privilege and no unchecked grant inheritance. |
| ASI04 | Agentic Supply Chain Vulnerabilities | Pin and verify dependencies, skills, prompts, tool manifests and release artifacts. |
| ASI05 | Unexpected Code Execution (RCE) | Separate generated code from execution; validate and sandbox authorized execution. |
| ASI06 | Memory & Context Poisoning | Isolate scopes, validate memory writes and never auto-promote generated claims into trusted facts. |
| ASI07 | Insecure Inter-Agent Communication | Typed, authenticated, replay-resistant handoffs bound to run, scope and audience. |
| ASI08 | Cascading Failures | Validate outputs before downstream use, contain failures and enforce circuit breakers. |
| ASI09 | Human-Agent Trust Exploitation | Clear uncertainty and side-effect previews; persuasive summaries are not proof. |
| ASI10 | Rogue Agents | Capability boundaries, audited actions, revocation and quarantine/recovery procedures. |

### Commentary correction

The report references AIVSS, but does not supply a separate checklist titled
"AIVSS amplification factors." A numerical project score requires the separate
scoring method and measured inputs. Do not invent a compliance score from this
document. A lab can demonstrate a risk; it is not production incident evidence.

## 8. GitHub Copilot cloud-agent concepts

### Summary

The current page distinguishes client, session and execution environment. It
describes repository context, persistent memory, agentic changes, code review,
automations, policies and environment-dependent capabilities. Logs provide
visibility but do not replace testing or human review. Access remains governed
by repository and organization permissions.

### Tips applicable to ORION

- Separate user interface, run/session identity and execution environment in receipts.
- Freeze and report environment/dependency versions for reproducibility.
- Link actions, tests and artifacts to the exact conversion run.
- Preserve independent review and protected release boundaries.
- Validate current permissions and product limits rather than assuming them from a plan.
- Treat persistent memory as attributed evidence, not unquestionable authority.

### Commentary correction

Tagging cost by repository, issue, epic and requester is sensible proposed
attribution, not a four-tag requirement established by the linked page. A memory
protection lab and human-verified reference tags are additional project designs.
The linked concepts page is not a full security audit of Copilot Memory.

## Cross-source conclusions: agreements and tensions

1. Use deterministic checks for deterministic properties; use model judgment for
   interpretation and calibrated semantic critique. Both can coexist in an agent tool.
2. A fresh reviewer context reduces author anchoring, but the same model family
   can still make correlated mistakes. Different models also do not prove independence.
3. Fixed workflow stages improve predictability; bounded dynamic discovery helps
   when evidence determines the next step. Neither is universally superior.
4. More agents and more tokens are costs to justify with measured improvements.
5. Observe actions and outcomes. Do not manufacture private reasoning or infer
   complete ReAct cycles from raw call counts.
6. Human gates should address meaningful ambiguity or risk, while mechanical
   gates run consistently and do not require habitual approval clicks.
7. Preserve exact history, but apply redaction, access controls and retention.
   "Keep everything" must not mean retaining secrets or unrestricted payloads.
8. Reversible app changes do not make a destructive database action reversible.

## Proposed ORION implementation priorities

These are recommendations, NOT a completed feature inventory.

| Priority | Activity | Acceptance evidence |
|---|---|---|
| P0 | Mandatory artifact checks after agent-invoked sanity tool | Exact notebook/AST/SQL/variable/widget checks bound to saved hashes. |
| P0 | Complete independent review without false green | Every task reviewed or explicit unavailable result; no truncation disguised as review. |
| P0 | Source/target and execution separation | Exact observed bindings, separate inferred fixture mappings, explicit write authority. |
| P0 | Trace each conversion | Stable public run ID, model/tool spans, usage, status and independently verified delivery receipt. |
| P1 | Coverage-based enhancement loop | Named issue, changed code hash, repeated checks, bounded iterations and no-progress exit. |
| P1 | Protected memory | Scope, source hash, trust state, review and expiration; curated knowledge separate from run facts. |
| P1 | Cohort evaluation | Explicit members and boundaries; per-member and combined cost/coverage/quality checks. |
| P1 | Reproducible releases | Pinned dependencies, staged byte manifest, regression receipt and compatible active-run handling. |
| P2 | Multi-endpoint/new-role benchmark | Held-out workflows; compare quality, p50/p95 latency, tokens and cost against existing six-role baseline. |

### Suggested measurement set

- Conversion deliverable rate, with explicit denominator and workflow difficulty.
- Source task, SQL-phase and parameter coverage.
- Wrong-binding and unsupported-semantic-assumption rates.
- Tool rejection versus execution-failure counts.
- Correction success, repeated-identical-failure and review-unavailable rates.
- Trace delivery/readback failures and missing usage fields.
- End-to-end and per-phase duration; token totals and cost where pricing is known.
- UI receipt accuracy: saved file availability must match actual immutable artifacts.

### Smallest useful next experiment

Freeze three representative individual workflows and one explicitly reviewed
cohort. Keep prompts workflow-independent. Compare the existing configuration
against one scoped improvement at a time using exact source/artifact hashes,
mechanical checks and independent domain review. Do not introduce multiple new
roles, providers and memory policies in one uncontrolled comparison.

## Boundaries of this research task

This document adds no grants, code execution, model calls, deployment, provider
selection or runtime configuration. It does not certify the application against
OWASP or claim every article was fully accessible in the requested edition.