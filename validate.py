#!/usr/bin/env python3
"""
validate.py — Golden Dataset Validator

Runs extraction on controlled fixtures and compares results to expected output.
Never crashes — every failure mode produces a report entry.

Usage:
    python validate.py                          # run all cases
    python validate.py --unit                   # unit cases only
    python validate.py --integration            # integration cases only
    python validate.py --case unit/001-configuration
    python validate.py --verbose                # show full diffs
    python validate.py --fail-fast              # stop at first FAIL
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from config    import ROOT, SETTINGS
from extractor import Extractor, extract_structured_facts
from knowledge import KnowledgeCandidate, ALLOWED_CATEGORIES, normalise_category
from ollama    import Ollama, OllamaError
from parser    import SessionParser
from prompts   import SYSTEM_PROMPT

log = logging.getLogger(__name__)

GOLDEN_DIR = ROOT / "golden"
REPORT_PATH = GOLDEN_DIR / "report.txt"

DIVIDER     = "─" * 60
DIVIDER_FAT = "═" * 60


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class Status(str, Enum):
    PASS    = "PASS"
    FAIL    = "FAIL"
    PENDING = "PENDING"
    INVALID = "INVALID"

    def symbol(self) -> str:
        return {
            Status.PASS:    "✓",
            Status.FAIL:    "✗",
            Status.PENDING: "…",
            Status.INVALID: "!",
        }[self]


# ---------------------------------------------------------------------------
# Case result
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name:     str
    status:   Status
    detail:   str  = ""
    advisory: bool = False


@dataclass
class CaseResult:
    case_id:   str                          # e.g. "unit/001-configuration"
    status:    Status
    score:     float | None = None          # 0.0–1.0, None if not executed
    checks:    list[CheckResult] = field(default_factory=list)
    reason:    str = ""                     # short status reason for PENDING/INVALID
    error:     str = ""                     # exception text if any
    debug_dir: Path | None = None           # set on FAIL — path to debug artifacts


# ---------------------------------------------------------------------------
# Meta loader
# ---------------------------------------------------------------------------

def _load_meta(case_dir: Path) -> dict | None:
    """Load and minimally validate meta.yaml. Returns None on failure."""
    meta_path = case_dir / "meta.yaml"
    if not meta_path.exists():
        return None
    try:
        data = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        # Required keys
        for key in ("expected_category", "confidence_min", "confidence_max"):
            if key not in data:
                return None
        return data
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_checks(checks: list[CheckResult]) -> float:
    """
    Score is based on required checks only; advisory failures are excluded.
    Category is blocking (0.0 if a required category check failed).
    Remaining required checks contribute equally to the remaining 100%.
    """
    required = [c for c in checks if not c.advisory]

    cat_check = next((c for c in required if c.name == "category"), None)
    if cat_check and cat_check.status == Status.FAIL:
        return 0.0

    scorable = [c for c in required if c.name != "category"]
    if not scorable:
        return 1.0

    passed = sum(1 for c in scorable if c.status == Status.PASS)
    return passed / len(scorable)


def _get_check_severity(meta: dict, check_name: str) -> str:
    """Return 'required' or 'advisory' for a check name. Default: 'required'."""
    checks_cfg = meta.get("checks") or {}
    entry = checks_cfg.get(check_name) or {}
    severity = entry.get("severity", "required")
    return severity if severity in ("required", "advisory") else "required"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_category(
    candidates: list[KnowledgeCandidate],
    expected: str,
) -> CheckResult:
    if not candidates:
        return CheckResult("category", Status.FAIL, "no candidates returned")
    found = candidates[0].category
    norm  = normalise_category(expected)
    if found == norm:
        return CheckResult("category", Status.PASS, found)
    return CheckResult("category", Status.FAIL, f"got '{found}', expected '{norm}'")


def _check_confidence(
    candidates: list[KnowledgeCandidate],
    conf_min: int,
    conf_max: int,
) -> CheckResult:
    if not candidates:
        return CheckResult("confidence", Status.FAIL, "no candidates")
    score = candidates[0].confidence
    if conf_min <= score <= conf_max:
        return CheckResult(
            "confidence", Status.PASS,
            f"{score}  (range: {conf_min}–{conf_max})"
        )
    return CheckResult(
        "confidence", Status.FAIL,
        f"{score} out of range {conf_min}–{conf_max}"
    )


def _check_terms(
    candidates: list[KnowledgeCandidate],
    expected_terms: list[str],
) -> CheckResult:
    if not expected_terms:
        return CheckResult("expected_terms", Status.PASS, "N/A")

    # Search across all candidates' summaries and notes
    full_text = " ".join(
        f"{c.summary} {c.notes} {' '.join(c.tags)}"
        for c in candidates
    ).lower()

    found   = [t for t in expected_terms if t.lower() in full_text]
    missing = [t for t in expected_terms if t.lower() not in full_text]
    ratio   = len(found) / len(expected_terms)

    status = Status.PASS if ratio == 1.0 else Status.FAIL
    detail = f"{len(found)} / {len(expected_terms)}"
    if missing:
        detail += f"  missing: {', '.join(missing)}"

    return CheckResult("expected_terms", status, detail)


def _check_forbidden_terms(
    candidates: list[KnowledgeCandidate],
    forbidden_terms: list[str],
) -> CheckResult:
    """
    Faithfulness check: none of the forbidden_terms should appear
    in any candidate's summary, notes, or commands.
    These are terms the model may hallucinate from general knowledge
    that were never present in the session fixture.
    """
    if not forbidden_terms:
        return CheckResult("forbidden_terms", Status.PASS, "N/A")

    full_text = " ".join(
        f"{c.summary} {c.notes} {' '.join(c.commands)}"
        for c in candidates
    ).lower()

    found = [t for t in forbidden_terms if t.lower() in full_text]

    if found:
        return CheckResult(
            "forbidden_terms", Status.FAIL,
            f"hallucinated terms found: {', '.join(found)}"
        )
    return CheckResult("forbidden_terms", Status.PASS, "none found")


_ENTITY_FIELD_MAP = {
    "models":  lambda c: c.models,
    "files":   lambda c: c.files,
    "tools":   lambda c: c.tool_calls,
    "plugins": lambda c: c.plugins,
}


def _check_entities(
    candidates: list[KnowledgeCandidate],
    expected_entities: dict,
) -> CheckResult:
    if not expected_entities:
        return CheckResult("expected_entities", Status.PASS, "N/A")

    all_missing: list[str] = []
    total = 0

    for sub_key, entities in expected_entities.items():
        if not entities:
            continue
        getter = _ENTITY_FIELD_MAP.get(sub_key)
        if getter is None:
            continue
        collected = {v.lower() for c in candidates for v in getter(c)}
        for entity in entities:
            total += 1
            e = entity.lower()
            # Accept exact match or suffix match so that "AGENTS.md" matches
            # "~/.openclaw/workspace/AGENTS.md" (parser extracts full paths)
            if not any(item == e or item.endswith("/" + e) for item in collected):
                all_missing.append(f"{sub_key}:{entity}")

    if total == 0:
        return CheckResult("expected_entities", Status.PASS, "N/A")

    found_count = total - len(all_missing)
    status = Status.PASS if not all_missing else Status.FAIL
    detail = f"{found_count} / {total}"
    if all_missing:
        detail += f"  missing: {', '.join(all_missing)}"

    return CheckResult("expected_entities", status, detail)


def _check_forbidden_entities(
    candidates: list[KnowledgeCandidate],
    forbidden_entities: dict,
) -> CheckResult:
    if not forbidden_entities:
        return CheckResult("forbidden_entities", Status.PASS, "N/A")

    found: list[str] = []

    for sub_key, entities in forbidden_entities.items():
        if not entities:
            continue
        getter = _ENTITY_FIELD_MAP.get(sub_key)
        if getter is None:
            continue
        collected = {v.lower() for c in candidates for v in getter(c)}
        for entity in entities:
            if entity.lower() in collected:
                found.append(f"{sub_key}:{entity}")

    if found:
        return CheckResult(
            "forbidden_entities", Status.FAIL,
            f"forbidden entities found: {', '.join(found)}"
        )
    return CheckResult("forbidden_entities", Status.PASS, "none found")


def _check_commands(
    candidates: list[KnowledgeCandidate],
    expected_commands: list[str],
) -> CheckResult:
    if not expected_commands:
        return CheckResult("commands", Status.PASS, "N/A")

    extracted = set()
    for c in candidates:
        extracted.update(c.commands)

    overlap = sum(1 for cmd in expected_commands if cmd in extracted)
    ratio   = overlap / len(expected_commands)

    status = Status.PASS if ratio >= 0.5 else Status.FAIL
    detail = f"{overlap} / {len(expected_commands)}  ({ratio:.0%} overlap)"
    return CheckResult("commands", status, detail)


def _check_spurious_category(
    candidates: list[KnowledgeCandidate],
) -> CheckResult:
    bad = [c.category for c in candidates if c.category not in ALLOWED_CATEGORIES]
    if not bad:
        return CheckResult("spurious_category", Status.PASS, "all categories valid")
    return CheckResult(
        "spurious_category", Status.FAIL,
        f"invalid categories: {', '.join(set(bad))}"
    )


def _check_max_candidates(
    candidates: list[KnowledgeCandidate],
    max_candidates: int | None,
) -> CheckResult:
    if max_candidates is None:
        return CheckResult("max_candidates", Status.PASS, "N/A")
    n = len(candidates)
    if n <= max_candidates:
        return CheckResult(
            "max_candidates", Status.PASS,
            f"{n} ≤ {max_candidates}"
        )
    return CheckResult(
        "max_candidates", Status.FAIL,
        f"got {n} candidates, expected ≤ {max_candidates} (merge rule)"
    )


# ---------------------------------------------------------------------------
# Run one case
# ---------------------------------------------------------------------------

def _save_debug_artifacts(
    case_id:   str,
    user_text: str,
    raw:       dict,
    checks:    list[CheckResult],
) -> Path:
    """
    Write debug artifacts for a FAIL case.
    Returns the directory where they were written.

    Files written:
        golden/debug/<case_id>/<timestamp>/
            user_prompt.txt     exact payload sent to Ollama
            response.json       raw JSON returned by the model
            checks.txt          per-check results
    """
    ts        = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug      = case_id.replace("/", "_")
    debug_dir = GOLDEN_DIR / "debug" / slug / ts
    debug_dir.mkdir(parents=True, exist_ok=True)

    (debug_dir / "user_prompt.txt").write_text(user_text, encoding="utf-8")
    (debug_dir / "response.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    check_lines = [f"case: {case_id}", f"timestamp: {ts}", ""]
    for c in checks:
        sym = c.status.symbol()
        check_lines.append(f"{sym}  {c.name:<20} {c.status.value:<8} {c.detail}")
    (debug_dir / "checks.txt").write_text("\n".join(check_lines), encoding="utf-8")

    return debug_dir


def run_case(
    case_dir: Path,
    client:   Ollama,
    verbose:  bool = False,
) -> CaseResult:

    case_id = "/".join(case_dir.parts[-2:])   # e.g. "unit/001-configuration"

    # ---- Validate case structure -----------------------------------------
    session_path  = case_dir / "session.jsonl"
    expected_path = case_dir / "expected.md"
    meta          = _load_meta(case_dir)

    if not session_path.exists():
        return CaseResult(
            case_id = case_id,
            status  = Status.PENDING,
            reason  = "session.jsonl not present",
        )

    if not expected_path.exists():
        return CaseResult(
            case_id = case_id,
            status  = Status.INVALID,
            reason  = "expected.md missing",
        )

    if meta is None:
        return CaseResult(
            case_id = case_id,
            status  = Status.INVALID,
            reason  = "meta.yaml missing or invalid",
        )

    # ---- Parse + extract -------------------------------------------------
    try:
        conversation = SessionParser(session_path).conversation()
        extractor    = Extractor()
        payload      = extractor.build_payload(conversation=conversation, model=None)
        user_text    = json.dumps(payload, ensure_ascii=False, indent=2)

        raw = client.json(
            model  = _model_name(),
            system = SYSTEM_PROMPT,
            user   = user_text,
        )
        candidates = [
            KnowledgeCandidate.from_dict(d)
            for d in raw.get("knowledge", [])
        ]

        # Merge deterministically extracted facts (Deterministic First Principle)
        facts = extract_structured_facts(conversation)
        for candidate in candidates:
            candidate.commands   = facts["commands"]
            candidate.files      = facts["files"]
            candidate.tool_calls = facts["tools"]
            if not facts["ask_llm_for_models"]:
                candidate.models = facts["models"]

    except OllamaError as e:
        return CaseResult(
            case_id = case_id,
            status  = Status.FAIL,
            score   = 0.0,
            reason  = "Ollama error",
            error   = str(e),
        )
    except Exception as e:
        return CaseResult(
            case_id = case_id,
            status  = Status.INVALID,
            reason  = "unexpected error during extraction",
            error   = str(e),
        )

    # ---- Run checks ------------------------------------------------------
    checks: list[CheckResult] = []

    checks.append(_check_category(
        candidates,
        meta["expected_category"],
    ))
    checks.append(_check_confidence(
        candidates,
        int(meta["confidence_min"]),
        int(meta["confidence_max"]),
    ))
    checks.append(_check_terms(
        candidates,
        meta.get("expected_terms", []),
    ))
    checks.append(_check_forbidden_terms(
        candidates,
        meta.get("forbidden_terms", []),
    ))
    checks.append(_check_commands(
        candidates,
        meta.get("expected_commands", []),
    ))
    checks.append(_check_entities(
        candidates,
        meta.get("expected_entities", {}),
    ))
    checks.append(_check_forbidden_entities(
        candidates,
        meta.get("forbidden_entities", {}),
    ))
    checks.append(_check_spurious_category(candidates))
    checks.append(_check_max_candidates(
        candidates,
        meta.get("max_candidates"),
    ))

    # Mark advisory failures; they are visible but do not cause overall FAIL
    for check in checks:
        if check.status == Status.FAIL:
            if _get_check_severity(meta, check.name) == "advisory":
                check.advisory = True

    score = _score_checks(checks)
    required_failed = any(c.status == Status.FAIL and not c.advisory for c in checks)
    status = Status.FAIL if required_failed else Status.PASS

    # On FAIL: save debug artifacts automatically
    debug_dir: Path | None = None
    if status == Status.FAIL:
        try:
            debug_dir = _save_debug_artifacts(
                case_id   = case_id,
                user_text = user_text,
                raw       = raw,
                checks    = checks,
            )
        except Exception as e:
            log.warning("Could not write debug artifacts: %s", e)

    return CaseResult(
        case_id   = case_id,
        status    = status,
        score     = score,
        checks    = checks,
        debug_dir = debug_dir,
    )


def _model_name() -> str:
    """Get the configured model name, stripping provider prefix."""
    from openclaw import OpenClaw
    try:
        cfg   = OpenClaw()
        model = cfg.primary_model
        return model.split("/", 1)[-1] if "/" in model else model
    except Exception:
        # Fallback: read from settings directly
        return SETTINGS.get("ollama", {}).get("model", "llama3.2")


# ---------------------------------------------------------------------------
# Discover cases
# ---------------------------------------------------------------------------

def discover_cases(
    only_unit:        bool = False,
    only_integration: bool = False,
    only_case:        str  | None = None,
) -> list[Path]:
    """Return sorted list of case directories matching the filter."""
    dirs: list[Path] = []

    if only_case:
        p = GOLDEN_DIR / only_case
        if p.is_dir():
            dirs.append(p)
        else:
            log.error("Case not found: %s", p)
        return dirs

    if not only_integration:
        unit_dir = GOLDEN_DIR / "unit"
        if unit_dir.exists():
            dirs += sorted(d for d in unit_dir.iterdir() if d.is_dir())

    if not only_unit:
        int_dir = GOLDEN_DIR / "integration"
        if int_dir.exists():
            dirs += sorted(d for d in int_dir.iterdir() if d.is_dir())

    return dirs


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def _render_check_line(check: CheckResult) -> str:
    if check.advisory and check.status == Status.FAIL:
        return f"  ⚠  {check.name:<20} (advisory)   {check.detail}"
    sym = check.status.symbol()
    return f"  {sym}  {check.name:<20} {check.status.value:<8} {check.detail}"


def _render_report(
    results:  list[CaseResult],
    verbose:  bool,
    elapsed:  float,
) -> str:
    lines: list[str] = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines += [
        DIVIDER_FAT,
        "  openclaw-knowledge — Golden Dataset Report",
        f"  {ts}",
        DIVIDER_FAT,
        "",
    ]

    # Group by family
    unit_results = [r for r in results if r.case_id.startswith("unit/")]
    int_results  = [r for r in results if r.case_id.startswith("integration/")]

    for family_label, family in [("Unit", unit_results), ("Integration", int_results)]:
        if not family:
            continue

        lines.append(f"  {family_label}")
        lines.append(DIVIDER)

        for r in family:
            sym = r.status.symbol()
            score_str = f"  {r.score:.0%}" if r.score is not None else ""
            advisory_count = sum(1 for c in r.checks if c.advisory and c.status == Status.FAIL)
            advisory_str = (
                f"  ({advisory_count} advisory {'note' if advisory_count == 1 else 'notes'})"
                if advisory_count else ""
            )
            lines.append(f"  {sym}  {r.case_id:<40} {r.status.value}{score_str}{advisory_str}")

            if r.reason:
                lines.append(f"       {r.reason}")
            if r.error and verbose:
                lines.append(f"       ERROR: {r.error}")
            if r.debug_dir:
                lines.append(f"       debug → {r.debug_dir}")

            advisory_fails = [c for c in r.checks if c.advisory and c.status == Status.FAIL]
            show_all = verbose or r.status == Status.FAIL
            if r.checks and (show_all or advisory_fails):
                for check in r.checks:
                    if show_all or (check.advisory and check.status == Status.FAIL):
                        lines.append(_render_check_line(check))

        lines.append("")

    # Summary
    executed = [r for r in results if r.status in (Status.PASS, Status.FAIL)]
    passed   = [r for r in results if r.status == Status.PASS]
    failed   = [r for r in results if r.status == Status.FAIL]
    pending  = [r for r in results if r.status == Status.PENDING]
    invalid  = [r for r in results if r.status == Status.INVALID]

    global_score = (
        sum(r.score for r in executed) / len(executed)
        if executed else None
    )

    lines += [DIVIDER_FAT, ""]
    lines.append(f"  Executed   {len(executed)}")
    lines.append(f"  Passed     {len(passed)}")
    lines.append(f"  Failed     {len(failed)}")
    if pending:
        lines.append(f"  Pending    {len(pending)}")
    if invalid:
        lines.append(f"  Invalid    {len(invalid)}")
    lines.append("")

    if global_score is not None:
        lines.append(f"  Global     {global_score:.0%}  (executed cases only)")
    else:
        lines.append("  Global     N/A  (no cases executed)")

    if pending:
        lines.append(f"  Coverage   {len(executed)} / {len(results)} cases active")

    lines.append(f"  Duration   {elapsed:.1f}s")
    lines.append("")
    lines.append(DIVIDER_FAT)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:

    parser = argparse.ArgumentParser(
        description="Run the openclaw-knowledge Golden Dataset validation."
    )
    parser.add_argument("--unit",        action="store_true", help="Run unit cases only")
    parser.add_argument("--integration", action="store_true", help="Run integration cases only")
    parser.add_argument("--case",        metavar="ID",        help="Run a single case (e.g. unit/001-configuration)")
    parser.add_argument("--verbose",     action="store_true", help="Show full check output for every case")
    parser.add_argument("--fail-fast",   action="store_true", help="Stop at first FAIL")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s  %(message)s")

    client = Ollama()

    if not client.ping():
        print("✗  Ollama is not reachable. Start it with: systemctl --user start ollama")
        sys.exit(1)

    cases = discover_cases(
        only_unit        = args.unit,
        only_integration = args.integration,
        only_case        = args.case,
    )

    if not cases:
        print("No cases found.")
        sys.exit(0)

    results:  list[CaseResult] = []
    start     = datetime.now(timezone.utc).timestamp()
    fail_fast = args.fail_fast

    for case_dir in cases:
        case_id = "/".join(case_dir.parts[-2:])
        print(f"  running  {case_id} …", end="", flush=True)

        result = run_case(case_dir, client, verbose=args.verbose)
        results.append(result)

        sym = result.status.symbol()
        score_str = f"  {result.score:.0%}" if result.score is not None else ""
        advisory_count = sum(1 for c in result.checks if c.advisory and c.status == Status.FAIL)
        advisory_str = (
            f"  ({advisory_count} advisory {'note' if advisory_count == 1 else 'notes'})"
            if advisory_count else ""
        )
        print(f"\r  {sym}  {case_id:<40} {result.status.value}{score_str}{advisory_str}")

        if result.reason or result.error:
            msg = result.error if result.error else result.reason
            print(f"       {msg}")

        for check in result.checks:
            if check.advisory and check.status == Status.FAIL:
                print(_render_check_line(check))

        if fail_fast and result.status == Status.FAIL:
            print("\n  [--fail-fast] stopping at first FAIL.")
            break

    elapsed = datetime.now(timezone.utc).timestamp() - start

    report = _render_report(results, verbose=args.verbose, elapsed=elapsed)

    # Always write report.txt
    REPORT_PATH.write_text(report, encoding="utf-8")

    # Print summary to stdout
    print()
    print(report)
    print(f"  Report written to {REPORT_PATH}")

    # Exit code: 0 if all executed cases pass, 1 if any FAIL or INVALID
    bad = any(r.status in (Status.FAIL, Status.INVALID) for r in results)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
