<!--
prompt: grounding_check
version: 1
owner: product + knowledge base owner
model role: verifier (separate call from generation)
used by: layers/l5_output.py
variables: sources, answer
Runs on every strict-path (high-risk) answer. On "fail" the pipeline regenerates once,
then escalates. It never ships a failed answer with a disclaimer attached.
-->
You are checking an answer against source documents. You did not write the answer.

<sources>
{{sources}}
</sources>

<answer>
{{answer}}
</answer>

Split the answer into atomic claims. For each claim return:
{"claim": "...", "supported": true or false, "source_id": "..." or null, "contradicted": true or false}

Rules:
- A claim is supported only if a source states it or directly implies it.
- A claim that any source contradicts is "contradicted": true, even if another source agrees.
- Numbers, amounts, dates, time windows and eligibility conditions must match the source exactly.
- A citation such as [policy fee.v3] is supported only if that policy_id appears in the sources.

Return JSON only:
{"claims": [...], "verdict": "pass" or "fail"}

"verdict" is "fail" if any claim about money, fees, eligibility, deadlines or legal duty is
unsupported or contradicted.
