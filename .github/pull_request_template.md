## What changed

<!-- Prompt, policy, model version, layer code? Link the ticket or incident. -->

## Eval diff

<!-- Paste the table from `python -m evals.run_evals --baseline evals/baseline.json`.
     Example: golden 96.1% -> 95.8%, injection unchanged, 2 new grounding fails on fee_schedule -->

```
```

## Checklist

- [ ] Policy changes reviewed by the policy owner (see CODEOWNERS)
- [ ] Refusal or escalation copy changes reviewed by brand/compliance
- [ ] If this fixes a production incident, a case was added to `evals/incidents/`
- [ ] Model version changes pinned in `config/models.yaml`
- [ ] `CHANGELOG.md` updated
- [ ] Baseline refreshed if pass rates moved on purpose (`--write-baseline evals/baseline.json`)
