# Expected behavior — 010-investigation-faithfulness

## What a passing extraction looks like

The summary for the checklist-paralysis candidate should communicate that:

- The fix (exception clause) was validated by isolating the variable
  (disabling BEHAVIOR.md first, confirming it was innocent; testing
  explicit vs vague prompts; testing across multiple models)
- qwen3:30b-a3b's behavior was not simply "it works" — it failed twice,
  then succeeded once under contaminated conditions, then succeeded again
  cleanly without the patch on a fourth attempt — meaning the patch
  reduces but does not single-handedly cause success on larger models
- The takeaway is calibrated: "less susceptible" / "more reliable with
  the fix" rather than "fixed" / "model X is immune"

## What a failing extraction looks like (actual observed failure)

The real capture.py run summarized the investigation as: "The issue was
resolved by adding an exception clause... tested with multiple models and
confirmed to resolve the unresponsiveness issue." This reads as a single
clean test-and-fix cycle. It omits that the isolation methodology required
discarding a contaminated test and rerunning it, and it implies the fix
was confirmed to "resolve" the issue rather than "reduce the frequency of"
an intermittent failure — a meaningfully different and overconfident claim.
