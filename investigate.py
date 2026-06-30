#!/usr/bin/env python3
"""
investigate.py — Post-session investigation analyzer.

Reads a completed OpenClaw session and produces a structured investigation
report containing observable facts, evidence, possible mistakes, and possible
lessons — each with citations back to the session.

The LLM produces observations only. Policy compliance and workflow decisions
are made by deterministic Python functions, not the model.

Output: investigations/YYYY-MM-DD/<session_id>/report.json + report.md

Usage:
    python investigate.py                    # analyze the latest session
    python investigate.py --session <name>   # analyze a specific session
    python investigate.py --dry-run          # print report, write nothing
    python investigate.py --list-sessions    # list available sessions
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib  import Path

from openclaw  import OpenClaw
from parser    import SessionParser
from extractor import Extractor
from ollama    import Ollama, OllamaError
from config    import ROOT

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log     = logging.getLogger(__name__)
DIVIDER = "─" * 70

INVESTIGATIONS_DIR = ROOT / "investigations"
BEHAVIOR_MD        = Path.home() / ".openclaw" / "workspace" / "BEHAVIOR.md"


# ---------------------------------------------------------------------------
# Prompt — evidence-first, citation-required
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
You produce structured JSON investigation reports. You must follow the exact
output schema provided — no variations, no extra keys, no prose.

Critical rules:
1. Output ONLY a JSON object. No markdown. No explanation. No ```json fences.
2. Use EXACTLY the field names specified: observable_facts, possible_mistakes,
   possible_lessons — not observations, mistakes, lessons, or any other names.
3. Every item in observable_facts must have: fact, evidence, fact_type.
4. Every item in possible_mistakes must have: mistake, evidence, severity.
5. Every item in possible_lessons must have: lesson, addresses, evidence, confidence.
6. fact_type must be one of: tool_call, tool_error, repeated_call,
   context_ignored, assumption, success, other.
7. If the agent used apt/apt-get on a Fedora/rpm system, that is a fact_type=assumption.
8. Do not invent facts. Cite turn numbers or direct quotes as evidence.
""".strip()


def _build_prompt(turns: list[dict]) -> str:
    conversation = json.dumps(turns, ensure_ascii=False, indent=2)

    return f"""Produce an investigation report for this agent session.

Return a JSON object with exactly this structure:

{{
  "goal": {{
    "stated": "what the user asked for, verbatim or close paraphrase",
    "type": "investigation | explanation | task | unknown"
  }},

  "outcome": {{
    "status": "success | failure | partial | abandoned",
    "reason": "one sentence: why the session ended this way",
    "goal_achieved": true or false
  }},

  "observable_facts": [
    {{
      "fact": "a single objective observation — no interpretation",
      "evidence": "turn N, tool call X, or direct quote from session",
      "fact_type": "tool_call | tool_error | repeated_call | context_ignored | assumption | success | other"
    }}
  ],

  "possible_mistakes": [
    {{
      "mistake": "description of what went wrong",
      "evidence": "which observable_fact(s) support this — cite fact text",
      "severity": "high | medium | low"
    }}
  ],

  "possible_lessons": [
    {{
      "lesson": "a short, actionable, general rule that addresses the mistake",
      "addresses": "which mistake this prevents — cite mistake text",
      "evidence": "which observable_fact(s) support this lesson",
      "confidence": 0-100
    }}
  ],

  "what_worked": "brief description of anything the agent did correctly, or null"
}}

Session:
{conversation}
"""


# ---------------------------------------------------------------------------
# Schema normaliser — handles model output variations
# ---------------------------------------------------------------------------

def _normalise_report(raw: dict) -> dict:
    """
    Normalise the model's output to our expected schema.

    Models sometimes nest the report under an extra key, or use alternative
    field names (observations vs observable_facts, etc.). This function
    maps known variations to the canonical schema without modifying valid output.
    """
    # Unwrap nested report (e.g. {"investigation": {...}})
    if "investigation" in raw and isinstance(raw["investigation"], dict):
        inner = raw["investigation"]
        # Only unwrap if inner has our schema keys or alternative keys
        if any(k in inner for k in ("observable_facts", "observations", "goal", "outcome")):
            raw = inner

    # Remap alternative field names to canonical names
    FIELD_MAP = {
        "observations":    "observable_facts",
        "facts":           "observable_facts",
        "mistakes":        "possible_mistakes",
        "errors":          "possible_mistakes",
        "lessons":         "possible_lessons",
        "learnings":       "possible_lessons",
        "recommendations": "possible_lessons",
    }
    for old_key, new_key in FIELD_MAP.items():
        if old_key in raw and new_key not in raw:
            raw[new_key] = raw.pop(old_key)

    # Normalise observable_facts items
    facts = raw.get("observable_facts", [])
    normalised_facts = []
    for f in facts:
        if isinstance(f, dict):
            normalised_facts.append({
                "fact":      f.get("fact") or f.get("description") or f.get("observation") or str(f),
                "evidence":  f.get("evidence") or f.get("event") or f.get("turn") or "unspecified",
                "fact_type": f.get("fact_type") or _infer_fact_type(f),
            })
    raw["observable_facts"] = normalised_facts

    # Normalise possible_mistakes items
    mistakes = raw.get("possible_mistakes", [])
    normalised_mistakes = []
    for m in mistakes:
        if isinstance(m, dict):
            normalised_mistakes.append({
                "mistake":  m.get("mistake") or m.get("description") or m.get("error") or str(m),
                "evidence": m.get("evidence") or "unspecified",
                "severity": m.get("severity") or "medium",
            })
    raw["possible_mistakes"] = normalised_mistakes

    # Normalise possible_lessons items
    lessons = raw.get("possible_lessons", [])
    normalised_lessons = []
    for l in lessons:
        if isinstance(l, dict):
            normalised_lessons.append({
                "lesson":     l.get("lesson") or l.get("description") or l.get("recommendation") or str(l),
                "addresses":  l.get("addresses") or l.get("mistake") or "unspecified",
                "evidence":   l.get("evidence") or "unspecified",
                "confidence": _parse_confidence(l.get("confidence")),
            })
    raw["possible_lessons"] = normalised_lessons

    # Ensure required top-level keys exist
    raw.setdefault("goal",    {"stated": "unknown", "type": "unknown"})
    raw.setdefault("outcome", {"status": "unknown", "reason": "unknown", "goal_achieved": False})
    raw.setdefault("what_worked", None)

    return raw


def _parse_confidence(value) -> int:
    """Parse confidence from int, float, or verbal string."""
    if value is None:
        return 70
    if isinstance(value, (int, float)):
        return int(value)
    mapping = {"high": 85, "medium": 65, "low": 40, "very high": 95, "very low": 25}
    return mapping.get(str(value).lower().strip(), 70)


def _infer_fact_type(fact: dict) -> str:
    """Infer fact_type from content when the model omits it."""
    text = " ".join(str(v) for v in fact.values()).lower()
    if "apt" in text or "apt-get" in text:
        return "assumption"
    if "error" in text or "failed" in text or "not found" in text:
        return "tool_error"
    if "repeated" in text or "again" in text or "same" in text:
        return "repeated_call"
    if "ignored" in text or "not consulted" in text:
        return "context_ignored"
    if "success" in text or "worked" in text:
        return "success"
    return "other"


# ---------------------------------------------------------------------------
# Behavior compliance — deterministic Python, not LLM
# ---------------------------------------------------------------------------

def _load_behavior_rules() -> list[str]:
    """
    Load promoted rules from BEHAVIOR.md.
    Multi-line bullet items are joined into a single rule string.
    """
    if not BEHAVIOR_MD.exists():
        return []

    rules: list[str] = []
    current: list[str] = []
    in_rules_section = False

    for line in BEHAVIOR_MD.read_text(encoding="utf-8").splitlines():
        if line.strip() == "## Rules":
            in_rules_section = True
            continue
        if in_rules_section and line.startswith("## "):
            break
        if not in_rules_section:
            continue
        if line.strip().startswith("-") and not line.strip().startswith("- ["):
            # New bullet — save previous if any
            if current:
                rules.append(" ".join(current))
            current = [line.strip().lstrip("-").strip()]
        elif current and line.strip() and not line.strip().startswith("#"):
            # Continuation line of current bullet
            current.append(line.strip())

    if current:
        rules.append(" ".join(current))

    return [r for r in rules if r]


# ---------------------------------------------------------------------------
# Rule signal definitions
# ---------------------------------------------------------------------------
# Each rule type maps to:
#   trigger_types  — fact_type values that make the rule applicable
#   signal_terms   — terms in fact text that indicate a violation
#   negative_terms — terms that indicate NOT_APPLICABLE (rule doesn't apply)
# ---------------------------------------------------------------------------

RULE_SIGNALS: list[dict] = [
    {
        "match":         "package installation",
        "trigger_types": {"assumption"},
        "signal_terms":  {"apt", "apt-get", "brew", "pacman", "apk", "yum"},
        "negative_terms": set(),
        "description":   "Package manager check",
    },
    {
        "match":         "tool call returns",
        "trigger_types": {"repeated_call", "tool_error"},
        "signal_terms":  {"retry", "same command", "again", "repeated", "variation"},
        "negative_terms": set(),
        "description":   "Repeated failed tool call check",
    },
    {
        "match":         "configuration paths",
        "trigger_types": {"tool_error", "assumption"},
        "signal_terms":  {"config", "path", "not found", "enumerate", "guess"},
        "negative_terms": {"benchmark", "fio", "disk", "mount"},
        "description":   "Config path enumeration check",
    },
    {
        "match":         "specific evidence question",
        "trigger_types": {"tool_call", "repeated_call"},
        "signal_terms":  {"without stating", "no question", "blind", "repeated"},
        "negative_terms": set(),
        "description":   "Evidence-driven tool call check",
    },
]


def _match_rule_signal(rule: str) -> dict | None:
    """Find the signal definition that matches a rule string."""
    rule_lower = rule.lower()
    for sig in RULE_SIGNALS:
        if sig["match"] in rule_lower:
            return sig
    return None


def _check_compliance(report: dict, rules: list[str]) -> list[dict]:
    """
    Deterministically check behavior rule compliance.

    Uses explicit signal definitions per rule type rather than generic
    keyword matching. Returns NOT_APPLICABLE when the rule's trigger
    conditions are not present in the session at all.

    Statuses:
      VIOLATED           — rule applies and evidence of violation found
      NO_VIOLATION_DETECTED — rule applies, no violation found
      NOT_APPLICABLE     — rule trigger conditions absent from session
    """
    if not rules:
        return []

    facts = report.get("observable_facts", [])
    compliance = []

    for rule in rules:
        sig = _match_rule_signal(rule)

        if sig is None:
            # Unknown rule type — fall back to NOT_APPLICABLE
            compliance.append({
                "rule":    rule,
                "status":  "NOT_APPLICABLE",
                "reason":  "No signal definition for this rule type",
                "evidence": [],
            })
            continue

        # Check if any fact has a trigger type
        trigger_facts = [
            f for f in facts
            if f.get("fact_type") in sig["trigger_types"]
        ]

        if not trigger_facts:
            compliance.append({
                "rule":    rule,
                "status":  "NOT_APPLICABLE",
                "reason":  f"No facts of type {sig['trigger_types']} in session",
                "evidence": [],
            })
            continue

        # Check for negative terms — if present, rule doesn't apply
        all_fact_text = " ".join(f.get("fact", "").lower() for f in trigger_facts)
        if sig["negative_terms"] and any(t in all_fact_text for t in sig["negative_terms"]):
            compliance.append({
                "rule":    rule,
                "status":  "NOT_APPLICABLE",
                "reason":  "Session context excludes this rule",
                "evidence": [],
            })
            continue

        # Check for violation signal terms
        violations = [
            f for f in trigger_facts
            if sig["signal_terms"] and any(t in f.get("fact", "").lower() for t in sig["signal_terms"])
        ]

        # For package manager rule: check specifically for wrong package manager
        if sig["match"] == "package installation":
            pkg_violations = [
                f for f in trigger_facts
                if any(t in f.get("fact", "").lower() for t in sig["signal_terms"])
                or any(t in f.get("evidence", "").lower() for t in sig["signal_terms"])
            ]
            violations = pkg_violations

        if violations:
            compliance.append({
                "rule":     rule,
                "status":   "VIOLATED",
                "evidence": [v["fact"] for v in violations],
            })
        else:
            compliance.append({
                "rule":     rule,
                "status":   "NO_VIOLATION_DETECTED",
                "evidence": [],
            })

    return compliance


# ---------------------------------------------------------------------------
# Workflow decisions — Python, not LLM
# ---------------------------------------------------------------------------

def _workflow_suggestions(report: dict, compliance: list[dict]) -> dict:
    """
    Decide what to do next based on counts and thresholds.
    The LLM never sees this logic.
    """
    suggestions = []

    outcome = report.get("outcome", {}).get("status", "unknown")
    lessons = report.get("possible_lessons", [])
    mistakes = report.get("possible_mistakes", [])
    high_severity = [m for m in mistakes if m.get("severity") == "high"]
    strong_lessons = [l for l in lessons if l.get("confidence", 0) >= 70]
    violations = [c for c in compliance if c["status"] == "VIOLATED"]

    if outcome in ("success", "partial") and lessons:
        suggestions.append("run `python capture.py` — session may contain reusable knowledge")

    if high_severity:
        suggestions.append(
            f"review {len(high_severity)} high-severity mistake(s) before next session"
        )

    if strong_lessons:
        suggestions.append(
            f"consider adding {len(strong_lessons)} lesson(s) with confidence >= 70 to BEHAVIOR.md"
        )

    if violations:
        suggestions.append(
            f"{len(violations)} behavior rule(s) were violated — review compliance section"
        )

    if not suggestions:
        suggestions.append("no action required")

    return {
        "outcome":       outcome,
        "mistake_count": len(mistakes),
        "lesson_count":  len(lessons),
        "violation_count": len(violations),
        "suggestions":   suggestions,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _save_report(
    report:     dict,
    compliance: list[dict],
    workflow:   dict,
    session_name: str,
    dry_run:    bool = False,
) -> Path | None:
    """Write report.json and report.md to investigations/YYYY-MM-DD/<session_id>/"""

    full_report = {
        "schema_version":  1,
        "created_at":      datetime.now(timezone.utc).isoformat(),
        "session":         session_name,
        "investigation":   report,
        "compliance":      compliance,
        "workflow":        workflow,
    }

    if dry_run:
        print()
        print(DIVIDER)
        print("DRY RUN — full report JSON:")
        print(DIVIDER)
        print(json.dumps(full_report, indent=2, ensure_ascii=False))
        return None

    today      = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    session_id = session_name.replace(".jsonl", "")[:36]
    out_dir    = INVESTIGATIONS_DIR / today / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "report.json"
    json_path.write_text(
        json.dumps(full_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    md_path = out_dir / "report.md"
    md_path.write_text(
        _render_markdown(full_report),
        encoding="utf-8",
    )

    return out_dir


def _render_markdown(full_report: dict) -> str:
    r   = full_report["investigation"]
    c   = full_report["compliance"]
    w   = full_report["workflow"]
    ts  = full_report["created_at"]
    ses = full_report["session"]

    lines = [
        f"# Investigation Report",
        f"",
        f"> Session: `{ses}`  ",
        f"> Generated: {ts}",
        f"",
        f"## Goal",
        f"",
        f"{r.get('goal', {}).get('stated', '?')}  ",
        f"Type: `{r.get('goal', {}).get('type', '?')}`",
        f"",
        f"## Outcome",
        f"",
        f"**{r.get('outcome', {}).get('status', '?').upper()}** — {r.get('outcome', {}).get('reason', '?')}",
        f"",
        f"## Observable Facts",
        f"",
    ]

    for f in r.get("observable_facts", []):
        lines += [
            f"- **[{f.get('fact_type', '?')}]** {f.get('fact', '?')}",
            f"  *Evidence: {f.get('evidence', '?')}*",
            f"",
        ]

    lines += ["## Possible Mistakes", ""]
    for m in r.get("possible_mistakes", []):
        lines += [
            f"- **[{m.get('severity', '?').upper()}]** {m.get('mistake', '?')}",
            f"  *Evidence: {m.get('evidence', '?')}*",
            f"",
        ]

    lines += ["## Possible Lessons", ""]
    for l in r.get("possible_lessons", []):
        lines += [
            f"- **[{l.get('confidence', 0)}/100]** {l.get('lesson', '?')}",
            f"  *Addresses: {l.get('addresses', '?')}*",
            f"  *Evidence: {l.get('evidence', '?')}*",
            f"",
        ]

    if r.get("what_worked"):
        lines += ["## What Worked", "", r["what_worked"], ""]

    lines += ["## Behavior Compliance", ""]
    if c:
        for item in c:
            status = "✓" if item["status"] == "NO_VIOLATION_DETECTED" else "✗"
            lines += [f"- {status} {item['rule']}"]
            if item["evidence"]:
                for e in item["evidence"]:
                    lines += [f"  - Evidence: {e}"]
        lines.append("")
    else:
        lines += ["_No behavior rules loaded._", ""]

    lines += ["## Next Steps", ""]
    for s in w.get("suggestions", []):
        lines.append(f"- {s}")
    lines.append("")

    return "\n".join(lines)


def _print_summary(report: dict, compliance: list[dict], workflow: dict) -> None:
    """Print a concise human-readable summary."""
    r = report

    print()
    goal   = r.get("goal", {})
    outcome = r.get("outcome", {})
    print(f"  Goal    : {goal.get('stated', '?')}")
    print(f"  Outcome : {outcome.get('status', '?').upper()} — {outcome.get('reason', '?')}")

    facts = r.get("observable_facts", [])
    if facts:
        print(f"\n  Observable facts ({len(facts)}):")
        for f in facts:
            print(f"    [{f.get('fact_type', '?')}] {f.get('fact', '?')}")
            print(f"      ↳ {f.get('evidence', '?')}")

    mistakes = r.get("possible_mistakes", [])
    if mistakes:
        print(f"\n  Possible mistakes ({len(mistakes)}):")
        for m in mistakes:
            print(f"    [{m.get('severity', '?').upper()}] {m.get('mistake', '?')}")

    lessons = r.get("possible_lessons", [])
    if lessons:
        print(f"\n  Possible lessons ({len(lessons)}):")
        for l in lessons:
            print(f"    [{l.get('confidence', 0)}/100] {l.get('lesson', '?')}")

    if compliance:
        violations = [c for c in compliance if c["status"] == "VIOLATED"]
        na         = [c for c in compliance if c["status"] == "NOT_APPLICABLE"]
        applicable = [c for c in compliance if c["status"] != "NOT_APPLICABLE"]
        passed     = len(applicable) - len(violations)
        print(f"\n  Behavior compliance: {passed}/{len(applicable)} applicable rules followed"
              f"  ({len(na)} not applicable)")
        for v in violations:
            print(f"    ✗ VIOLATED:  {v['rule'][:80]}")
        for n in na:
            print(f"    —  N/A:      {n['rule'][:80]}")

    print(f"\n  Next steps:")
    for s in workflow.get("suggestions", []):
        print(f"    → {s}")
    print()


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------

def cmd_investigate(
    cfg:          OpenClaw,
    client:       Ollama,
    session_name: str | None = None,
    dry_run:      bool = False,
    max_turns:    int  = 30,
) -> None:

    print(DIVIDER)
    print("investigate.py — Session Investigation Analyzer")
    print(DIVIDER)

    # ── Resolve session ──────────────────────────────────────────────────
    session = cfg.get_session(session_name) if session_name else cfg.latest_session()
    print(f"  Session  : {session.name}")

    # ── Parse ────────────────────────────────────────────────────────────
    conversation = SessionParser(session).conversation()
    if not conversation:
        print("  ⚠ Session is empty.")
        return

    extractor = Extractor()
    payload   = extractor.build_payload(conversation=conversation, model=cfg.primary_model)
    turns     = payload.get("turns", [])
    print(f"  Turns    : {len(turns)}")

    if not turns:
        print("  ⚠ No turns found.")
        return

    # ── Load behavior rules ───────────────────────────────────────────────
    rules = _load_behavior_rules()
    print(f"  Rules    : {len(rules)} loaded from BEHAVIOR.md")

    # ── Call Ollama ───────────────────────────────────────────────────────
    model_name = cfg.primary_model.split("/", 1)[-1] if "/" in cfg.primary_model else cfg.primary_model
    print()
    print(DIVIDER)
    print("Analyzing …")
    print(DIVIDER)

    # Optionally trim session to avoid context overflow
    if max_turns and len(turns) > max_turns:
        analysis_turns = turns[-max_turns:]
        log.info("Session trimmed from %d to %d turns (use --no-trim or --max-turns 0 to disable)",
                 len(turns), len(analysis_turns))
    else:
        analysis_turns = turns
        if len(turns) > 30:
            log.info("Sending full session (%d turns) — model may struggle with very long context", len(turns))

    try:
        report = client.json(
            model  = model_name,
            system = SYSTEM_PROMPT,
            user   = _build_prompt(analysis_turns),
        )
    except OllamaError as e:
        log.error("Ollama error: %s", e)
        sys.exit(1)
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        log.error("Could not parse model response as JSON: %s", e)
        log.error("The model returned prose instead of JSON.")
        sys.exit(1)

    # ── Schema validation + normalisation ────────────────────────────────
    # Models sometimes return a different structure. Normalise to our schema.
    report = _normalise_report(report)

    # ── Deterministic compliance + workflow ───────────────────────────────
    compliance = _check_compliance(report, rules)
    workflow   = _workflow_suggestions(report, compliance)

    # ── Print + save ──────────────────────────────────────────────────────
    _print_summary(report, compliance, workflow)

    out = _save_report(report, compliance, workflow, session.name, dry_run=dry_run)

    if out:
        print(DIVIDER)
        print(f"  ✓  Report saved to {out}/")
        print(f"     report.json  — full structured data")
        print(f"     report.md    — human-readable")
        print(DIVIDER)
        print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze a completed OpenClaw session for failures and lessons.",
    )
    parser.add_argument("--session",       metavar="NAME",
                        help="Session to analyze (default: latest)")
    parser.add_argument("--dry-run",       action="store_true",
                        help="Print report, write nothing")
    parser.add_argument("--list-sessions", action="store_true",
                        help="List available sessions and exit")
    parser.add_argument("--no-trim",       action="store_true",
                        help="Send full session to model without trimming (may fail on very long sessions)")
    parser.add_argument("--max-turns",     type=int, default=30, metavar="N",
                        help="Max turns to send for analysis (default: 30, use 0 for all)")

    args = parser.parse_args()

    try:
        cfg    = OpenClaw()
        client = Ollama()
    except FileNotFoundError as e:
        log.error("%s", e)
        sys.exit(1)

    if args.list_sessions:
        sessions = cfg.session_files()
        print(f"{len(sessions)} session(s) in {cfg.sessions_dir}\n")
        for i, s in enumerate(sessions, 1):
            ts      = datetime.fromtimestamp(s.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            size_kb = s.stat().st_size // 1024
            print(f"  [{i:>3}]  {ts}  {size_kb:>6} KB  {s.name}")
        return

    if not client.ping():
        log.error("Ollama unreachable at %s", client.host)
        sys.exit(1)

    max_turns = 0 if args.no_trim else args.max_turns
    cmd_investigate(cfg=cfg, client=client,
                    session_name=args.session, dry_run=args.dry_run,
                    max_turns=max_turns)


if __name__ == "__main__":
    main()
