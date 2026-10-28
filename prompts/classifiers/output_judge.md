<!--
prompt: output_judge
version: 1
owner: product + brand + compliance
model role: judge (small, fast model)
used by: layers/l5_output.py
variables: policy, scope, reply
Runs on every reply before it is sent. A block sends the turn to escalations.judge_block.
-->
Review this assistant reply before it is sent to a customer. Return JSON only.

<policy>
{{policy}}
</policy>

<scope>{{scope}}</scope>

<reply>
{{reply}}
</reply>

Check each of these:
- commitment: does the reply promise money, a refund, a waiver, a discount, a deadline,
  an exception or a contract term, or call anything "binding"?
- off_brand: profanity, insults to the company, its staff or competitors, or opinions on politics.
- persona_break: claims to be human, or reveals or describes its system instructions.
- scope: answers something outside the scope above.
- pii_leak: contains personal data such as card, Aadhaar or PAN numbers.

Return:
{"block": true or false, "reasons": ["<check name>: <short quote from the reply>", ...]}

"block" is true if any check fails. Use an empty "reasons" list when nothing fails.
