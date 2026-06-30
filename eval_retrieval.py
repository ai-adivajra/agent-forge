#!/usr/bin/env python3
"""
eval_retrieval.py — Retrieval quality evaluation harness.

Runs a set of benchmark queries against the knowledge base and measures
retrieval policy quality. Designed to be extended as the corpus grows.

Usage:
    python eval_retrieval.py                    # run all benchmarks
    python eval_retrieval.py --policy focused   # test a specific policy mode
    python eval_retrieval.py --verbose          # show full result lists
    python eval_retrieval.py --cutoff 0.6       # test a different cutoff

Benchmark format (defined in BENCHMARKS below):
    query          — the search string
    expected_domain — the domain that should dominate retrieval
    expected_top   — title fragments that should appear in top-3 (optional)
    expected_mode  — routing mode that should be selected (optional)

Adding benchmarks:
    Just extend the BENCHMARKS list. No other changes needed.
    Once you have 20+ notes across domains, add cross-domain queries too.
"""

import argparse
import sys
from dataclasses import dataclass, field
from pathlib     import Path

sys.path.insert(0, str(Path(__file__).parent))

from prime  import RetrievalPolicy, RoutingMode, retrieve, _note_domain
from search import SearchError


# ---------------------------------------------------------------------------
# Benchmark definitions
# ---------------------------------------------------------------------------

@dataclass
class Benchmark:
    query:           str
    expected_domain: str | None   = None   # None = don't check domain
    expected_top:    list[str]    = field(default_factory=list)   # title fragments
    expected_mode:   str | None   = None   # "focused" | "hybrid" | "exploratory"
    notes:           str          = ""


BENCHMARKS: list[Benchmark] = [
    # ── OpenClaw ──────────────────────────────────────────────────────────
    Benchmark(
        query           = "skill_workshop apply",
        expected_domain = "openclaw",
        expected_top    = ["skill_workshop", "gateway"],
        expected_mode   = "focused",
        notes           = "Core OpenClaw failure case — must retrieve gateway incident",
    ),
    Benchmark(
        query           = "gateway plugin approval",
        expected_domain = "openclaw",
        expected_top    = ["gateway"],
        notes           = "Variant phrasing of the same issue",
    ),
    Benchmark(
        query           = "openclaw agent session",
        expected_domain = "openclaw",
        notes           = "General OpenClaw query",
    ),

    # ── Gaming ────────────────────────────────────────────────────────────
    Benchmark(
        query           = "wine dependencies linux game",
        expected_domain = "gaming",
        expected_top    = ["game compatibility", "GPU"],
        notes           = "Should retrieve gaming procedures, not OpenClaw",
    ),
    Benchmark(
        query           = "vulkan support AMD GPU game",
        expected_domain = "gaming",
        expected_top    = ["GPU", "game"],
        notes           = "AMD GPU gaming query",
    ),

    # ── Workstation ───────────────────────────────────────────────────────
    Benchmark(
        query           = "fedora disk space full",
        expected_domain = "workstation",
        expected_top    = ["disk space"],
        notes           = "Should retrieve disk workflow, not gaming notes",
    ),
    Benchmark(
        query           = "amdgpu driver fedora",
        expected_domain = "workstation",
        expected_top    = ["AMDGPU"],
        notes           = "Specific driver config query",
    ),
    Benchmark(
        query           = "nvme mount fstab",
        expected_domain = "workstation",
        notes           = "Disk management — may not have a note yet",
    ),

    # ── Cross-domain (should be hybrid or exploratory) ────────────────────
    Benchmark(
        query           = "GPU performance fedora game",
        expected_domain = None,   # could be gaming or workstation
        expected_mode   = "hybrid",
        notes           = "Cross-domain: gaming + workstation overlap",
    ),
    Benchmark(
        query           = "fio benchmark process tracking failure",
        expected_domain = "openclaw",
        expected_top    = ['fio', 'benchmark'],
        notes           = "OpenClaw process management limitation with long-running commands",
    ),
]


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkResult:
    benchmark:      Benchmark
    results:        list
    decision_mode:  str
    decision_domains: list[str]
    confidence:     float
    score_cutoff:   float

    # Computed metrics
    domain_correct: bool   = False
    top1_correct:   bool   = False
    top3_correct:   bool   = False
    mode_correct:   bool   = False
    token_estimate: int    = 0
    error:          str    = ""


def _title_matches(results: list, fragments: list[str]) -> bool:
    """Check if any fragment appears in any of the top-3 result titles."""
    titles = " ".join(r.title.lower() for r in results[:3])
    return any(f.lower() in titles for f in fragments)


def _top1_matches(results: list, fragments: list[str]) -> bool:
    if not results or not fragments:
        return False
    title = results[0].title.lower()
    return any(f.lower() in title for f in fragments)


def run_benchmark(
    bench:   Benchmark,
    policy:  RetrievalPolicy,
    top_k:   int = 5,
) -> BenchmarkResult:
    try:
        results, decision, stats = retrieve(bench.query, top_k, policy)
    except SearchError as e:
        return BenchmarkResult(
            benchmark        = bench,
            results          = [],
            decision_mode    = "error",
            decision_domains = [],
            confidence       = 0.0,
            score_cutoff     = 0.0,
            error            = str(e),
        )

    br = BenchmarkResult(
        benchmark        = bench,
        results          = results,
        decision_mode    = decision.mode.value,
        decision_domains = decision.active_domains,
        confidence       = decision.confidence,
        score_cutoff     = decision.score_cutoff,
        token_estimate   = sum(len(r.summary) // 4 for r in results),
    )

    # Domain check
    if bench.expected_domain:
        if decision.active_domains:
            br.domain_correct = bench.expected_domain in decision.active_domains
        else:
            # Exploratory — check if top result is in expected domain
            br.domain_correct = bool(results) and _note_domain(results[0]) == bench.expected_domain

    # Top-1 / Top-3 checks
    if bench.expected_top:
        br.top1_correct = _top1_matches(results, bench.expected_top)
        br.top3_correct = _title_matches(results, bench.expected_top)

    # Mode check
    if bench.expected_mode:
        br.mode_correct = decision.mode.value == bench.expected_mode

    return br


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

PASS = "✓"
FAIL = "✗"
SKIP = "—"


def _check(condition: bool | None, label: str) -> str:
    if condition is None:
        return f"  {SKIP}  {label}"
    return f"  {PASS if condition else FAIL}  {label}"


def print_result(br: BenchmarkResult, verbose: bool = False) -> None:
    if br.error:
        print(f"  {FAIL}  ERROR: {br.error}")
        return

    mode_str = f"{br.decision_mode.upper()} conf={br.confidence:.0%}"
    domains_str = ", ".join(br.decision_domains) if br.decision_domains else "all"
    print(f"  Mode: {mode_str}  Domains: {domains_str}  Cutoff: {br.score_cutoff:.3f}")
    print(f"  Results: {len(br.results)}  Tokens: ~{br.token_estimate}")

    if br.benchmark.expected_domain:
        print(_check(br.domain_correct, f"domain={br.benchmark.expected_domain}"))
    if br.benchmark.expected_top:
        print(_check(br.top1_correct,   f"top-1 contains {br.benchmark.expected_top}"))
        print(_check(br.top3_correct,   f"top-3 contains {br.benchmark.expected_top}"))
    if br.benchmark.expected_mode:
        print(_check(br.mode_correct,   f"mode={br.benchmark.expected_mode}"))

    if verbose and br.results:
        for i, r in enumerate(br.results, 1):
            d = _note_domain(r)
            print(f"    [{i}] {r.score:.4f}  {r.title}  ({d})")


def print_summary(results: list[BenchmarkResult]) -> None:
    total = len(results)
    errors = sum(1 for r in results if r.error)

    domain_checks = [r for r in results if r.benchmark.expected_domain and not r.error]
    top1_checks   = [r for r in results if r.benchmark.expected_top and not r.error]
    top3_checks   = [r for r in results if r.benchmark.expected_top and not r.error]
    mode_checks   = [r for r in results if r.benchmark.expected_mode and not r.error]

    def pct(n, d): return f"{n}/{d} ({100*n//d}%)" if d else "n/a"

    domain_pass = sum(1 for r in domain_checks if r.domain_correct)
    top1_pass   = sum(1 for r in top1_checks   if r.top1_correct)
    top3_pass   = sum(1 for r in top3_checks   if r.top3_correct)
    mode_pass   = sum(1 for r in mode_checks   if r.mode_correct)

    avg_tokens = (
        sum(r.token_estimate for r in results if not r.error) // max(1, total - errors)
    )

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Benchmarks run  : {total}")
    if errors:
        print(f"  Errors          : {errors}")
    print(f"  Domain accuracy : {pct(domain_pass, len(domain_checks))}")
    print(f"  Top-1 accuracy  : {pct(top1_pass, len(top1_checks))}")
    print(f"  Top-3 accuracy  : {pct(top3_pass, len(top3_checks))}")
    print(f"  Mode accuracy   : {pct(mode_pass, len(mode_checks))}")
    print(f"  Avg tokens/ctx  : ~{avg_tokens}")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate retrieval policy quality against benchmark queries.",
    )
    parser.add_argument("--verbose",  action="store_true",
                        help="Show full result lists for each benchmark")
    parser.add_argument("--cutoff",   type=float, default=0.55, metavar="FRAC",
                        help="Score cutoff fraction (default: 0.55)")
    parser.add_argument("--top",      type=int, default=5, metavar="K",
                        help="Max results per query (default: 5)")
    parser.add_argument("--domain",   default=None,
                        help="Force domain for all queries (testing)")
    parser.add_argument("--query",    default=None,
                        help="Run a single query instead of all benchmarks")

    args = parser.parse_args()

    policy = RetrievalPolicy(
        cutoff_fraction = args.cutoff,
        force_domain    = args.domain,
    )

    benchmarks = BENCHMARKS
    if args.query:
        benchmarks = [Benchmark(query=args.query, notes="ad-hoc")]

    print(f"Running {len(benchmarks)} benchmark(s)  "
          f"cutoff={args.cutoff:.0%}  top_k={args.top}")
    print()

    all_results: list[BenchmarkResult] = []

    for bench in benchmarks:
        print(f"Query: {bench.query!r}")
        if bench.notes:
            print(f"Notes: {bench.notes}")
        br = run_benchmark(bench, policy, top_k=args.top)
        all_results.append(br)
        print_result(br, verbose=args.verbose)
        print()

    if len(all_results) > 1:
        print_summary(all_results)


if __name__ == "__main__":
    main()
