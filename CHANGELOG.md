# Changelog

Record every change to a policy, prompt, response or model version here, with the eval diff.

Format: `## <date> <area>: <change>`, then the eval diff and the PR link.

## 2026-10-28 initial: starter repo

- Policies: product_policy 2026.10.2, tool_permissions, source_trust, pii_rules (IN, JP, generic)
- Prompts: assistant v3, input_router v1, output_judge v1, grounding_check v1
- Evals (stub model): golden 8/8, grounding 4/4, fabrication 2/2, injection 6/6,
  indirect_injection 2/2, overreach 11/11, tone 4/4, pii 4/4, incidents 5/5
