# Golden Dataset

The Golden Dataset is the quality assurance layer for openclaw-knowledge.
It is not a project goal — it is a by-product of real development work.

---

## Rule

**One new feature or prompt improvement → one new Golden case.**

**Every bug fixed must either make an existing Golden case pass, or
introduce a new Golden case that would have caught the bug.**

This second rule guarantees a fixed bug cannot silently return.
It also forces a precise enough understanding of the bug to write a
minimal reproducing fixture — which improves diagnosis quality.

Never build Golden cases in isolation from development.
Build them when you ship something:

- Improved configuration extraction → add `unit/002-configuration-keys/`
- Embedding search → add `unit/003-embeddings/`
- Workflow merging rule → add `unit/004-workflow-merge/`
- Real ROCm session → add `integration/rocm/`

---

## Two families of cases

### Type A — Unit (fixtures)

Hand-crafted minimal conversations. 2–4 turns. One primary behaviour.
These are unit tests: they verify that one extraction behaviour works correctly.

**Rule: one fixture → one primary behaviour.**

**Rule: a fixture must test a reusable knowledge item, not a transient observation.**

A transient observation is specific to a session and has no future value:
  ✗  "My GPU is an RX 6800 XT."
  ✗  "The file foo.zip was downloaded."
  ✗  "Today's weather in Siem Reap is 32°C."

A reusable knowledge item applies beyond the session that produced it:
  ✓  "lspci identifies installed PCI devices including GPUs."
  ✓  "OpenClaw stores sessions as JSONL files."
  ✓  "The write tool creates files in the active workspace."

If a fixture produces `{"knowledge": []}` consistently, ask first:
is the knowledge item actually reusable? The model may be correct.

Not "one concept" — a composition test may contain several concepts on purpose.
But it must test exactly one behaviour:

| `primary_behaviour` | Tests whether the model…                                         | Typical `max_candidates` |
|---------------------|------------------------------------------------------------------|--------------------------|
| `recognition`       | correctly identifies and labels a single concept                 | 1                        |
| `composition`       | merges or splits related concepts into the right number of items | 1                        |
| `filtering`         | ignores noise — greetings, weather, one-off actions              | 0 or low                 |
| `classification`    | assigns the correct category from the controlled vocabulary      | 1                        |

`ranking` and `robustness` are intentionally not unit behaviours —
they are properties better tested in integration cases.

Declare `primary_behaviour` in every `meta.yaml`.
When a case fails, this field tells you immediately which family regressed.

```
unit/
    001-configuration/
    002-workflow-merge/
    003-models/
```

**When a unit test fails:** the extraction rule is broken or the prompt regressed.

### Type B — Integration (real sessions)

Real OpenClaw sessions with full conversational noise:
digressions, failed attempts, corrections, long tool outputs.
These are integration tests: they verify the system holds under real conditions.

```
integration/
    rocm/
    telegram/
    context-overflow/
```

**When an integration test fails but unit tests pass:** the system cannot
handle noise or volume — not a rule problem, a robustness problem.
**When both fail:** the extraction rule itself is broken.

This distinction makes diagnosis fast.

---

## Structure

```
golden/
    README.md                   this file
    report.txt                  last validate.py output (git-ignored)

    unit/
        001-configuration/
            session.jsonl       hand-crafted fixture, 3–5 turns
            expected.md         expected output note
            meta.yaml           case metadata

    integration/
        rocm/
            session.jsonl       real session (may be anonymised)
            expected.md         expected key knowledge items
            meta.yaml           case metadata
```

---

## Stability rules for fixtures

These rules keep the Golden Dataset stable over time.

**Do include:**
- Structural keys and patterns (`agents.defaults.model.primary`)
- File paths that are part of the documented interface (`~/.openclaw/openclaw.json`)
- Concepts and capabilities that are not version-specific
- Terminal commands that are standard and stable

**Never include:**
- Version numbers or release dates (`OpenClaw 2026.6.10`)
- Temporary file names created during a session
- Output that depends on the current date or time
- Anything that will change in the next minor release

A fixture that fails because the software shipped a version bump is a
bad fixture, not a regression.

---

## Scoring

`validate.py` produces a score per case and a global score across all cases.

### Per-case scoring

| Check                  | Weight  | Method                                          |
|------------------------|---------|-------------------------------------------------|
| Category match         | blocking| if wrong category → case scores 0, stop         |
| Expected terms present | 40%     | count of terms found / total expected terms      |
| Confidence in range    | 20%     | pass/fail within `confidence_min`–`confidence_max` |
| Commands extracted     | 20%     | set intersection ≥ 50% of expected commands      |
| No spurious category   | 20%     | output category is in ALLOWED_CATEGORIES         |

Category is blocking because a note filed under the wrong category is
unretrievable in practice — correct terms in the wrong folder are useless.

### Example output

```
Unit: 001-configuration
  Category        PASS   Configuration
  Expected terms  PASS   4 / 4
  Confidence      PASS   88  (range: 70–100)
  Commands        N/A    (no expected commands)
  Spurious cat.   PASS
  ─────────────────────────────────────────
  Score           100 %

Integration: rocm
  Category        PASS   Troubleshooting
  Expected terms  PASS   6 / 7
  Confidence      PASS   82  (range: 65–100)
  Commands        PASS   3 / 4 (75% overlap)
  Spurious cat.   PASS
  ─────────────────────────────────────────
  Score           92 %

─────────────────────────────────────────────
Global score      96 %   (2 / 2 cases passed)
```

### Using the score to compare prompts

Run `validate.py` before and after a prompt change.
A global score drop of more than 5% is a regression — revert or fix before merging.
A global score improvement of more than 10% is a significant gain — document it.

---

## Three test families

```
golden/
    unit/           hand-crafted fixtures, one primary behaviour each
    integration/    real sessions — noise and volume
    regression/     minimal fixtures created when a production bug is found
```

`regression/` is not created until the first production bug is found.
Its structure mirrors `unit/`:

```
regression/
    issue-023-merge-loop/
        session.jsonl     minimal fixture that reproduces the bug
        expected.md
        meta.yaml         primary_behaviour: the behaviour that regressed
```

Workflow for every production bug:
1. Write the minimal `session.jsonl` that reproduces it
2. Place it in `regression/<issue-slug>/`
3. Confirm `validate.py` FAILs before fixing
4. Fix — the case must now PASS
5. The bug cannot return undetected

---

## How to add a unit case

1. Create `unit/<NNN>-<slug>/`
2. Write a minimal `session.jsonl` (2–4 turns, one primary behaviour only)
3. Run `python capture.py --debug-prompt` to inspect the payload
4. Write `expected.md` at the level of detail the prompt should produce
5. Write `meta.yaml` with category, terms, confidence range
6. Run `python validate.py --case unit/<NNN>-<slug>` and confirm it passes
7. Commit both the case and the feature that motivated it together

## How to add an integration case

1. Create `integration/<slug>/`
2. Copy the real session `.jsonl` (anonymise if needed — remove personal paths, names)
3. Run `capture.py` on it and review the output manually
4. Write `expected.md` listing the key knowledge items you expect to find
5. Write `meta.yaml` — use a wider confidence range (e.g. 55–100) for real sessions
6. Run `python validate.py --case integration/<slug>` and confirm it passes
7. Document in `meta.yaml` why this session was chosen

## How to run

```bash
# Run all cases
python validate.py

# Run only unit tests
python validate.py --unit

# Run only integration tests
python validate.py --integration

# Run a single case
python validate.py --case unit/001-configuration

# Verbose diff output
python validate.py --verbose
```
