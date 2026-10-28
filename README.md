# AI Guardrails Starter

A starter repo for putting guardrails around an LLM-backed product: policy files, prompt
templates, six enforcement layers in Python, eval suites built from real public incidents,
and a CI workflow that gates prompt and policy changes on those evals.

The sample product is a support assistant for a fictional retail bank ("Northwind Bank").
Everything runs locally against a deterministic stub model, so you can clone it, run the
tests and read the traces without an API key. Switch one line in `config/models.yaml` to run
the same pipeline against a real model.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m examples.demo          # six sample messages through the full pipeline
python -m pytest -q              # unit tests for each layer
python -m evals.run_evals        # all eval suites, gated on config/thresholds.yaml
```

To run against Claude instead of the stub:

```bash
pip install -r requirements-model.txt
export ANTHROPIC_API_KEY=...
python -m examples.demo --provider anthropic
python -m evals.run_evals --provider anthropic
```

## How a message flows

```
user message
  L1 input hygiene     size limit, denylist, PII redaction            layers/l1_input.py
  L2 router            intent, risk, injection flag -> path            layers/l2_router.py
                       refuse | escalate | strict | standard
  L3 retrieval         trust tiers, quarantine, untrusted-data tags    layers/l3_retrieval.py
  generate             system prompt + tagged sources                  layers/pipeline.py
  L5 output checks     grounding check (strict path), output judge     layers/l5_output.py
  reply                with the AI disclosure label
L4 tool gate           every tool call the model proposes              layers/l4_tools.py
L6 observe             one trace per message, an event per layer       layers/l6_observe.py
```

Models classify and verify. Code decides the path, the permissions and the wording of every
refusal and escalation. Every model check fails closed: if the router, verifier or judge
returns something unparseable, the turn escalates.

## Repo structure

```
policies/                      what is allowed; owned by product and legal, no code
  product_policy.yaml          scope, intents, high-risk intents, never-generate topics, disclosure
  tool_permissions.yaml        tools, environments, argument schemas, approvals, limits
  source_trust.yaml            trust tier per source, quarantine patterns
  pii_rules.yaml               entities to redact (India, Japan, generic), input denylist

prompts/                       how the model is instructed; one versioned file per role
  system/assistant_v3.md       main system prompt, references policy by ID
  classifiers/input_router.md  intent, risk and injection in one cheap call
  classifiers/output_judge.md  commitments, tone, persona, PII leak
  verifiers/grounding_check.md claim-by-claim check against retrieved sources

layers/                        enforcement
  l1_input.py ... l6_observe.py
  pipeline.py                  runs the layers in order
  llm.py                       StubClient and AnthropicClient behind one call() signature
  context.py                   per-request state built from the session, never from the model
  common.py                    YAML loading, prompt rendering, JSON parsing

responses/                     fixed copy, never generated
  refusals.yaml
  escalations.yaml             message and queue for each escalation

evals/
  golden/                      golden, grounding, fabrication
  redteam/                     injection, indirect_injection, overreach, tone, pii
  incidents/                   one case per production incident
  run_evals.py                 runner, pass bars, baseline diff
  baseline.json                last accepted pass rates

config/
  thresholds.yaml              limits, retrieval settings, eval pass bars
  models.yaml                  provider and pinned model per role

examples/                      sample knowledge base and demo script
tests/                         unit tests for each layer
.github/                       CI workflow, PR template, CODEOWNERS
```

## Prompt templates

Prompts live in `prompts/` as Markdown with `{{variable}}` placeholders. The HTML comment at
the top of each file (owner, version, variables) is stripped before sending.
`layers/common.py:render()` raises if a variable is missing, so renaming one breaks a test
instead of silently shipping a broken prompt.

| Template | Role | Called from | Output |
| --- | --- | --- | --- |
| `system/assistant_v3.md` | generator | `pipeline._generate` | answer text, or `{"action": "escalate"}` |
| `classifiers/input_router.md` | router (small model) | `l2_router.route` | `{intent, risk, injection, pii_requested, reason}` |
| `verifiers/grounding_check.md` | verifier | `l5_output.grounding_check` | `{claims: [...], verdict}` |
| `classifiers/output_judge.md` | judge (small model) | `l5_output.output_judge` | `{block, reasons}` |

When you change a prompt, bump the version in its header, add a line to `CHANGELOG.md`, and
paste the eval diff into the PR.

## Eval suites

Each `.jsonl` file under `evals/` is a suite; the file stem is its name. A case is either a
message through the pipeline (`input`) or a proposed tool call through the gate (`tool_call`).

| Suite | What it covers | Modelled on |
| --- | --- | --- |
| golden | correct answers, routing and labels for in-scope intents | |
| grounding | invented fees and time windows caught before they ship | Air Canada |
| fabrication | questions about policies that don't exist | Cursor |
| injection | role overrides, fake authority, tag smuggling, router failure | Chevrolet of Watsonville |
| indirect_injection | instructions planted in retrieved content | |
| overreach | prod access, destructive verbs, freezes, other customers' records | Replit, McHire |
| tone | swearing, attacks on the brand, unauthorised commitments | DPD |
| pii | redaction, requests for other customers' data, leaked identifiers | |
| incidents | one case per production incident (seeded with public examples) | |

Cases with a `stub` field force what a role returns, which is how the suites simulate a model
that fabricates or swears. Those cases only run against the stub; against a real provider they
are skipped and reported as skipped.

The stub's heuristics are deliberately simple (keyword routing, number matching for grounding,
regex for the judge). They prove the code paths work. They say nothing about how well a real
model classifies or verifies, so run the suites with `--provider anthropic` before you rely on
any pass rate, and grow the cases from your own traffic.

## CI

`.github/workflows/guardrail-evals.yml` runs the unit tests and every eval suite on each PR that
touches prompts, policies, config, layers or evals. A suite below its pass bar fails the build.
Nightly and on manual dispatch it also runs the suites against the real model when the
`ANTHROPIC_API_KEY` repository secret is set.

After an intentional change in pass rates, refresh the baseline:

```bash
python -m evals.run_evals --write-baseline evals/baseline.json
```

## Adapting it to your product

1. Rewrite `policies/product_policy.yaml`: your intents with real example phrasings, which are
   high risk, which are never generated. Get legal to review this file, not the prompts.
2. Rewrite `responses/`. Give every escalation queue an owner and an SLA.
3. Replace `l3_retrieval.search()` with your retriever. Keep the tiering, quarantine and tagging.
   Map your document sources in `policies/source_trust.yaml`.
4. Replace `tool_permissions.yaml` with your tools, and call `l4_tools.gate()` from your agent
   loop before every tool execution. Build `Context` from the authenticated session and your
   change-freeze flag.
5. Enable the PII entities for your jurisdictions in `pii_rules.yaml`.
6. Seed `evals/golden/` with 20 to 30 real queries per intent from support logs, then run
   against the real model and set pass bars from what you measure.
7. Point `l6_observe.Trace.flush()` at your logging backend and review a sample of refusals
   every week to measure false blocks.
8. Replace the placeholder teams in `.github/CODEOWNERS`.

## Pre-launch checklist

- [ ] Every reply is labelled as AI-generated
- [ ] Price, refund, eligibility and legal answers are fixed or pass a grounding check against an authoritative source
- [ ] Questions about policies that don't exist escalate (fabrication suite passes on the real model)
- [ ] Retrieved content is tagged as untrusted data and the indirect injection suite passes
- [ ] Retrieval sources have trust tiers; high-risk intents use authoritative sources only
- [ ] The agent cannot reach production with write access; destructive verbs need approval outside the chat
- [ ] Freezes, limits and budgets are system state checked in code
- [ ] Customer IDs are injected by the server; the model never chooses whose data to fetch
- [ ] Admin panels, test accounts and transcript stores have had a normal security review
- [ ] Prompt, policy and model changes are gated by CI
- [ ] Every escalation queue has an owner and an SLA
- [ ] A kill switch can turn off generation and fall back to fixed answers in minutes

## Licence

MIT. See `LICENSE`.
