# Expected behavior — 012-no-status-conversion

## What a passing extraction looks like

A summary along the lines of: "A possible cause was identified in
AGENTS.md (a rule blocking tool calls without a specific evidence
question). A fix was proposed — adding an exception clause for broad
status/health requests — but it has not yet been applied or verified.
The agent expressed moderate, not high, confidence in this diagnosis,
noting that the system prompt size and BEHAVIOR.md changes are also
unruled-out variables."

Confidence on the candidate itself should be moderate (40-75), matching
the agent's own stated uncertainty in the source session — not high
confidence, which would itself misrepresent the source.

## What a failing extraction looks like (the pattern this case targets)

"A rule in AGENTS.md was causing the issue. The fix was applied and
resolved the problem." — This converts a stated proposal ("I would
suggest...", "I have not applied this change yet") into a completed,
verified fact. The session explicitly contains the opposite: the user
asks to discuss first, and the agent explicitly says it hasn't isolated
the cause yet and wants to run controlled tests before concluding.

This pattern is more dangerous than simple entity invention (case 008)
or command invention (case 011) because the factual content (file name,
mechanism) can be entirely correct — only the epistemic status is wrong.
A reader trusting this note would believe a fix exists and works, when
in the source material it was explicitly undecided and unverified.

## v1 → v2 correction note

v1 of this case required the literal word "proposed" via `expected_terms`.
A real capture.py run produced a faithful summary — "this rule causes
agents to fail on vague prompts... the rule is enforced even if the
prompt is a general request" — which correctly avoided claiming the fix
was applied, but failed the v1 check simply because it phrased things
differently. v2 replaced the keyword-presence check with a
forbidden-completed-action-language check plus a required confidence
match, which more directly measures the actual property of interest.
