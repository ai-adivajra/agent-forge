#!/usr/bin/env python3
"""
validate_retrieval.py — Retrieval Quality Validator

MEASUREMENT TOOL ONLY. This file measures the behaviour of the existing
retrieval pipeline (Searcher from search.py). It does not implement BM25,
TF-IDF, cross-encoders, or any new ranking algorithm. Changes to retrieval
quality must happen in search.py / vector_store.py and will be reflected
automatically the next time this runner executes.

Usage:
    python validate_retrieval.py                   # full campaign
    python validate_retrieval.py --case retrieval/001-specific-identifier
    python validate_retrieval.py --runs 10          # multi-run mode
"""

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from pathlib     import Path

import yaml

from search import Searcher, SearchError
from config import ROOT

GOLDEN_DIR     = ROOT / "golden" / "retrieval"
DEBUG_DIR      = ROOT / "golden" / "debug" / "retrieval"
DIVIDER        = "─" * 60
DIVIDER_FAT    = "═" * 60
NOT_FOUND_RANK = 4   # penalty rank when a top-3 assertion is not satisfied


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CaseSpec:
    case_id:                  str
    query:                    str
    expected_rank_1_contains: str | None    = None
    expected_top_3_contains:  list[str]     = field(default_factory=list)
    never_rank_1:             list[str]     = field(default_factory=list)
    never_top_3:              list[str]     = field(default_factory=list)
    known_failure:            dict | None   = None


@dataclass
class Assertion:
    kind:   str        # rank_1 | top_3 | never_rank_1 | never_top_3
    value:  str        # substring being checked
    passed: bool
    rank:   int | None # observed rank for top_3 assertions; None otherwise
    detail: str


@dataclass
class RunResult:
    case_id:       str
    passed:        bool
    known_failure: bool
    assertions:    list[Assertion]
    top3_titles:   list[str]
    top3_scores:   list[float]
    error:         str = ""


# ---------------------------------------------------------------------------
# Case loading
# ---------------------------------------------------------------------------

def _load_cases(only_case: str | None) -> list[CaseSpec]:
    if not GOLDEN_DIR.exists():
        sys.exit(f"golden/retrieval/ not found at {GOLDEN_DIR}")
    if only_case:
        name = only_case.removeprefix("retrieval/")
        path = GOLDEN_DIR / f"{name}.yaml"
        if not path.exists():
            sys.exit(f"Case not found: {path}")
        return [_parse_spec(path)]
    return [_parse_spec(p) for p in sorted(GOLDEN_DIR.glob("*.yaml"))]


def _parse_spec(path: Path) -> CaseSpec:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return CaseSpec(
        case_id                  = data["case_id"],
        query                    = data["query"],
        expected_rank_1_contains = data.get("expected_rank_1_contains"),
        expected_top_3_contains  = list(data.get("expected_top_3_contains") or []),
        never_rank_1             = list(data.get("never_rank_1") or []),
        never_top_3              = list(data.get("never_top_3") or []),
        known_failure            = data.get("known_failure"),
    )


# ---------------------------------------------------------------------------
# Single run
# ---------------------------------------------------------------------------

def _run_case(spec: CaseSpec, searcher: Searcher) -> RunResult:
    try:
        results = searcher.search(spec.query, top_k=3)
    except SearchError as e:
        return RunResult(spec.case_id, False, bool(spec.known_failure), [], [], [], str(e))

    titles  = [r.title for r in results]
    scores  = [r.score for r in results]
    t_lower = [t.lower() for t in titles]
    top1    = t_lower[0] if t_lower else ""

    assertions: list[Assertion] = []

    # expected_rank_1_contains
    if spec.expected_rank_1_contains:
        needle = spec.expected_rank_1_contains.lower()
        ok     = needle in top1
        assertions.append(Assertion(
            "rank_1", spec.expected_rank_1_contains, ok,
            1 if ok else None,
            f"rank-1: '{titles[0] if titles else '(no results)'}'",
        ))

    # expected_top_3_contains
    for term in spec.expected_top_3_contains:
        needle = term.lower()
        rank   = next((i + 1 for i, t in enumerate(t_lower) if needle in t), None)
        assertions.append(Assertion(
            "top_3", term, rank is not None, rank,
            f"found at rank {rank}" if rank else f"not in top-3  titles={titles}",
        ))

    # never_rank_1
    for term in spec.never_rank_1:
        violated = term.lower() in top1
        assertions.append(Assertion(
            "never_rank_1", term, not violated, None,
            f"rank-1 was '{titles[0] if titles else '(none)'}'",
        ))

    # never_top_3
    for term in spec.never_top_3:
        needle = term.lower()
        rank   = next((i + 1 for i, t in enumerate(t_lower) if needle in t), None)
        assertions.append(Assertion(
            "never_top_3", term, rank is None, None,
            f"appeared at rank {rank}" if rank else "not in top-3 ✓",
        ))

    passed = all(a.passed for a in assertions)
    r = RunResult(spec.case_id, passed, bool(spec.known_failure), assertions, titles, scores)

    if not passed and not spec.known_failure:
        _write_debug(spec, results, assertions)

    return r


# ---------------------------------------------------------------------------
# Debug artifacts
# ---------------------------------------------------------------------------

def _write_debug(spec: CaseSpec, results, assertions: list[Assertion]) -> None:
    ts   = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = spec.case_id.replace("/", "_")
    d    = DEBUG_DIR / slug / ts
    d.mkdir(parents=True, exist_ok=True)

    (d / "query.txt").write_text(spec.query, encoding="utf-8")
    (d / "results.json").write_text(
        json.dumps(
            [{"rank": i + 1, "title": r.title, "score": round(r.score, 4)}
             for i, r in enumerate(results)],
            indent=2,
        ),
        encoding="utf-8",
    )
    lines = [f"case: {spec.case_id}", ""]
    for a in assertions:
        sym = "✓" if a.passed else "✗"
        lines.append(f"{sym}  [{a.kind}]  '{a.value}'  —  {a.detail}")
    (d / "report.txt").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _slug(case_id: str) -> str:
    return case_id.split("/", 1)[-1]


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n // d}%)" if d else "N/A"


def _render_report(cases: list[CaseSpec], runs: dict[str, list[RunResult]], n: int) -> str:
    lines: list[str] = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines += [DIVIDER_FAT, "  Retrieval Quality Report", f"  {ts}", DIVIDER_FAT, ""]

    real_passes = real_fails = known_count = 0
    rank1_pass = rank1_total = top3_pass = top3_total = 0
    nr1_viol = nr1_total = nt3_viol = nt3_total = 0
    avg_ranks: dict[tuple[str, str], list[int]] = {}

    for spec in cases:
        case_runs = runs[spec.case_id]
        n_pass    = sum(1 for r in case_runs if r.passed)
        known     = bool(spec.known_failure)
        error     = next((r.error for r in case_runs if r.error), "")

        # Per-case status line
        if n == 1:
            r     = case_runs[0]
            sym   = "✓" if r.passed else ("⚠" if known else "✗")
            label = "PASS" if r.passed else ("FAIL (known)" if known else "FAIL")
        else:
            sym   = "✓" if n_pass == n else ("⚠" if known else ("◑" if n_pass > 0 else "✗"))
            label = f"{n_pass}/{n} ({100 * n_pass // n}%)" + (" — known" if known else "")
        lines.append(f"  {sym}  {_slug(spec.case_id):<40} {label}")

        if error:
            lines.append(f"       error: {error}")

        # Assertion detail for failed cases (last run)
        last = case_runs[-1]
        if not last.passed:
            for a in last.assertions:
                marker = "⚠" if known else "✗" if not a.passed else "✓"
                if not a.passed:
                    lines.append(f"     {marker}  [{a.kind}] '{a.value}'  —  {a.detail}")

        # Aggregate counters
        if known:
            known_count += 1
        elif n_pass == n:
            real_passes += 1
        else:
            real_fails += 1

        for rr in case_runs:
            for a in rr.assertions:
                if a.kind == "rank_1":
                    rank1_total += 1
                    if a.passed: rank1_pass += 1
                elif a.kind == "top_3":
                    top3_total += 1
                    if a.passed: top3_pass += 1
                    key = (spec.case_id, a.value)
                    avg_ranks.setdefault(key, []).append(a.rank or NOT_FOUND_RANK)
                elif a.kind == "never_rank_1":
                    nr1_total += 1
                    if not a.passed: nr1_viol += 1
                elif a.kind == "never_top_3":
                    nt3_total += 1
                    if not a.passed: nt3_viol += 1

    lines += ["", DIVIDER]
    lines.append(f"  Cases run         : {len(cases)}")
    lines.append(f"  Passed            : {real_passes}")
    lines.append(f"  Failed (real)     : {real_fails}")
    lines.append(f"  Failed (known)    : {known_count}")
    lines.append(f"  Top-1 accuracy    : {_pct(rank1_pass, rank1_total)}")
    lines.append(f"  Top-3 recall      : {_pct(top3_pass, top3_total)}")
    never_total = nr1_total + nt3_total
    never_viol  = nr1_viol  + nt3_viol
    lines.append(f"  False positive rate breakdown:")
    lines.append(f"    never_rank_1 assertions executed : {nr1_total}  (violated: {nr1_viol})")
    lines.append(f"    never_top_3  assertions executed : {nt3_total}  (violated: {nt3_viol})")
    lines.append(f"    Total: {_pct(never_viol, never_total)}")

    if avg_ranks:
        lines.append("")
        lines.append("  Average rank  (top-3 assertions across all runs; 4 = not found)")
        for (case_id, term), ranks in avg_ranks.items():
            avg   = sum(ranks) / len(ranks)
            sigma = math.sqrt(sum((r - avg) ** 2 for r in ranks) / len(ranks))
            if n > 1:
                stability = f", stable" if sigma == 0.0 else ""
                lines.append(f"    {_slug(case_id)}  '{term}'  →  {avg:.2f}  (σ={sigma:.2f}{stability})")
            else:
                lines.append(f"    {_slug(case_id)}  '{term}'  →  {avg:.2f}")

    lines += ["", DIVIDER_FAT]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Validate retrieval quality against golden cases.")
    ap.add_argument("--case", metavar="ID",
                    help="Run one case (e.g. retrieval/001-specific-identifier)")
    ap.add_argument("--runs", type=int, default=1, metavar="N",
                    help="Repeat the campaign N times (default: 1)")
    args = ap.parse_args()

    cases = _load_cases(args.case)
    if not cases:
        print("No cases found.")
        sys.exit(0)

    try:
        searcher = Searcher()
    except Exception as e:
        sys.exit(f"Failed to initialise Searcher: {e}")

    runs: dict[str, list[RunResult]] = {c.case_id: [] for c in cases}

    for run_idx in range(args.runs):
        prefix = f"[{run_idx + 1}/{args.runs}] " if args.runs > 1 else ""
        tty = sys.stdout.isatty()
        for spec in cases:
            slug = _slug(spec.case_id)
            if tty:
                print(f"  {prefix}running  {slug} …", end="", flush=True)
            result = _run_case(spec, searcher)
            runs[spec.case_id].append(result)
            sym = "✓" if result.passed else ("⚠" if result.known_failure else "✗")
            line = f"  {prefix}{sym}  {slug}"
            print(f"\r{line}\033[K" if tty else line)

    report = _render_report(cases, runs, args.runs)
    print()
    print(report)

    any_real_fail = any(
        not all(r.passed for r in runs[spec.case_id])
        for spec in cases
        if not spec.known_failure
    )
    sys.exit(1 if any_real_fail else 0)


if __name__ == "__main__":
    main()
