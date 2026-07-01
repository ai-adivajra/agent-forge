# Investigation log — 2026-06-30

## Question

"peux-tu analyser cette machine et me conseiller des améliorations pour
optimiser les performances d'openclaw"

## Observed facts

- Wall-clock duration: ~20 minutes.
- Only one user-visible tool call (`top`).
- "Je vais analyser..." appeared twice before the final response.
- Response covered CPU, RAM, disk, swap. No mention of GPU, VRAM,
  Ollama model state, or ROCm status anywhere in the response.
- `knowledge.md` injected for this session: ~971 tokens, 5 notes, all
  unrelated to hardware/performance (OpenClaw debugging incidents).

## Possible explanations (unconfirmed)

- Multiple internal LLM calls (one per "Je vais analyser..." occurrence)
- A planning/reflection loop before the final answer
- Long generation time caused by model behavior on this hardware/context
- Some other orchestration mechanism not yet instrumented

These are *possible* causes, not established ones — distinguishing
between them is exactly what `profile_session.py` is being built to do.

## Evidence required vs. evidence missing

**Required for "optimize OpenClaw" specifically** (OpenClaw runs LLM
inference, so its performance depends on more than general system load):

| Component | Collected? |
|---|---|
| CPU | ✓ |
| RAM | ✓ |
| Disk | ✓ |
| GPU | ✗ |
| VRAM | ✗ |
| Ollama model state (`ollama ps`) | ✗ |
| ROCm / driver status | ✗ |

## Hypothesis (unconfirmed — single occurrence)

The agent considered the investigation complete after a single `top`
call, without reasoning about which evidence categories were actually
relevant to "optimize OpenClaw performance" specifically (as opposed to
general system health). GPU was the missing evidence observed *here* —
this is a symptom, not necessarily the rule that should be encoded.

## Confidence

- **Observation quality:** High — directly read from the session
  transcript, not inferred.
- **Hypothesis confidence:** Low — single occurrence. Per project
  discipline (rules require repeated independent evidence, same
  threshold already applied to BEHAVIOR.md candidates), this does not
  yet warrant a BEHAVIOR.md change.

## Status

**Needs another occurrence before generalizing.**

If a similar evidence-sufficiency gap (any domain — not necessarily
GPU) recurs in 2-3 independent sessions, the following candidate rule
becomes warranted:

> Before concluding an investigation, verify that the collected
> evidence covers the major components relevant to the user's question.
> If essential evidence is missing, either collect it or explicitly
> state the limitation.

This formulation deliberately avoids naming GPU/CPU/network/etc. — it
describes a method (evidence-sufficiency check), not a checklist. The
rule is "always verify you have sufficient evidence," not "always check
component X." This distinction matters: a named-component checklist
grows without bound as new domains are investigated (GPU today, network
tomorrow, NUMA the day after); a method-level rule generalizes without
needing to be extended each time.

## Separate, higher-priority finding: performance

The ~20 minute duration is anomalous and, unlike the reasoning gap, hard
to reconstruct retroactively once the session has ended. This warrants
immediate instrumentation rather than waiting for repeated occurrences —
performance incidents are individually costly to diagnose after the
fact, unlike reasoning gaps which can be documented and pattern-matched
over time.

## Open questions (for profile_session.py to answer)

- Was only one LLM call executed, or several?
- How many prompt tokens were injected (system prompt + context + history)?
- Was reasoning/planning performed multiple times before the final answer?
- How much time was spent in generation vs. orchestration overhead?
- (Out of scope for a post-hoc profiler: was GPU already saturated during
  this session — this requires live sampling, not trajectory analysis)

See: `profile_session.py` (instructions sent to Claude Code, not yet run).
