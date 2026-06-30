#!/usr/bin/env python3
"""
prime.py — Prime an OpenClaw session with relevant knowledge.

Architecture:
  Pass 1  — broad embedding search (top_k * 3 candidates)
  Policy  — RetrievalPolicy decides routing mode and confidence
  Pass 2  — optional filtered search based on policy decision
  Cutoff  — score-relative cutoff removes low-relevance noise
  Render  — structured Engineering Context written to knowledge.md

Routing modes (decided by RetrievalPolicy):
  FOCUSED     — one dominant domain, single-domain retrieval
  HYBRID      — two domains with comparable scores, both included
  EXPLORATORY — ambiguous intent, all domains, broad retrieval

Usage:
    python prime.py "skill_workshop apply"     # explicit query
    python prime.py --auto                     # derive from latest session
    python prime.py --top 5                    # limit results (default: 5)
    python prime.py --domain openclaw          # force domain
    python prime.py --cutoff 0.6               # score cutoff (fraction of top, default: 0.55)
    python prime.py --check                    # verify configuration
    python prime.py --dry-run "query"          # show output, no files written
    python prime.py --feedback good            # record feedback on last priming
    python prime.py --feedback bad --note "wrong domain"
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime    import datetime, timezone
from enum        import Enum
from pathlib     import Path

sys.path.insert(0, str(Path(__file__).parent))

from search  import Searcher, SearchError
from inject  import Injector
from config  import SETTINGS, expand
from ollama  import Ollama

log     = logging.getLogger(__name__)
DIVIDER = "─" * 70

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

ALLOWED_DOMAINS = {
    "openclaw", "workstation", "gaming",
    "datacenter", "development", "general",
}


# ---------------------------------------------------------------------------
# Note metadata helpers  (read from file; DB fields used when available)
# ---------------------------------------------------------------------------

def _read_frontmatter_field(path: str, field_name: str, default: str = "") -> str:
    try:
        p = Path(path)
        if not p.exists():
            return default
        in_fm = False
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip() == "---":
                if not in_fm:
                    in_fm = True
                    continue
                else:
                    break
            if in_fm and line.startswith(f"{field_name}:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return default


def _note_domain(r) -> str:
    """Get domain from SearchResult (DB) or fallback to frontmatter."""
    if hasattr(r, "domain") and r.domain:
        return r.domain
    return _read_frontmatter_field(r.path, "domain", "general")


def _note_type(r) -> str:
    """Get type from SearchResult (DB) or fallback to frontmatter."""
    if hasattr(r, "type") and r.type:
        return r.type
    return _read_frontmatter_field(r.path, "type", "general")


# ---------------------------------------------------------------------------
# RetrievalPolicy
# ---------------------------------------------------------------------------

class RoutingMode(Enum):
    FOCUSED     = "focused"      # single dominant domain
    HYBRID      = "hybrid"       # two comparable domains
    EXPLORATORY = "exploratory"  # broad, no clear domain signal


@dataclass
class PolicyDecision:
    mode:            RoutingMode
    active_domains:  list[str]
    confidence:      float          # 0.0 – 1.0
    reason:          str
    score_cutoff:    float          # absolute score threshold for result trimming


@dataclass
class RetrievalPolicy:
    """
    Decides how to route a retrieval request based on pass-1 evidence.

    Parameters
    ----------
    dominance_ratio : float
        If top_score >= ratio * second_score, the top domain dominates.
        Default 1.4 (top must be 40% better than second).
    hybrid_threshold : float
        Score-weighted domain share required to join a HYBRID retrieval.
        Default 0.25 (domain must account for >=25% of total score).
    cutoff_fraction : float
        Results with score < cutoff_fraction * top_score are dropped.
        Default 0.55 (drop anything scoring less than 55% of the best).
    force_domain : str | None
        Override all inference and restrict to this domain.
    """

    dominance_ratio:  float     = 1.4
    hybrid_threshold: float     = 0.25
    cutoff_fraction:  float     = 0.55
    force_domain:     str | None = None

    def decide(self, results: list) -> PolicyDecision:
        """
        Analyse pass-1 results and return a PolicyDecision.

        Decision tree:
          1. If force_domain is set → FOCUSED on that domain, confidence 1.0
          2. If top result dominates (score >= ratio * second) → FOCUSED
          3. If 2 domains each hold >= hybrid_threshold share → HYBRID
          4. Otherwise → EXPLORATORY
        """
        if not results:
            return PolicyDecision(
                mode           = RoutingMode.EXPLORATORY,
                active_domains = [],
                confidence     = 0.0,
                reason         = "no results",
                score_cutoff   = 0.0,
            )

        top_score = results[0].score
        cutoff    = top_score * self.cutoff_fraction

        # ── Force override ───────────────────────────────────────────────
        if self.force_domain:
            return PolicyDecision(
                mode           = RoutingMode.FOCUSED,
                active_domains = [self.force_domain],
                confidence     = 1.0,
                reason         = f"forced by --domain {self.force_domain}",
                score_cutoff   = cutoff,
            )

        # Exclude 'general' domain from signal
        signal = [(r, _note_domain(r)) for r in results
                  if _note_domain(r) != "general"]

        if not signal:
            return PolicyDecision(
                mode           = RoutingMode.EXPLORATORY,
                active_domains = [],
                confidence     = 0.0,
                reason         = "all results are domain=general",
                score_cutoff   = cutoff,
            )

        top_result, top_domain = signal[0]

        # ── Dominance rule ───────────────────────────────────────────────
        if len(signal) >= 2:
            second_score = signal[1][0].score
            if second_score > 0 and top_result.score >= self.dominance_ratio * second_score:
                confidence = min(1.0, top_result.score / (second_score * self.dominance_ratio))
                return PolicyDecision(
                    mode           = RoutingMode.FOCUSED,
                    active_domains = [top_domain],
                    confidence     = round(confidence, 3),
                    reason         = (
                        f"top score {top_result.score:.3f} dominates "
                        f"second {second_score:.3f} "
                        f"(ratio {top_result.score/second_score:.2f} >= {self.dominance_ratio})"
                    ),
                    score_cutoff   = cutoff,
                )

        # ── Score-weighted domain shares ─────────────────────────────────
        domain_scores: dict[str, float] = {}
        total = sum(r.score for r, _ in signal)
        for r, d in signal:
            domain_scores[d] = domain_scores.get(d, 0.0) + r.score

        qualifying = {
            d: s / total
            for d, s in domain_scores.items()
            if s / total >= self.hybrid_threshold
        }

        if len(qualifying) == 1:
            d, share = next(iter(qualifying.items()))
            return PolicyDecision(
                mode           = RoutingMode.FOCUSED,
                active_domains = [d],
                confidence     = round(share, 3),
                reason         = f"{d} holds {share:.0%} of score weight",
                score_cutoff   = cutoff,
            )

        if len(qualifying) >= 2:
            domains = sorted(qualifying, key=lambda d: -domain_scores[d])[:2]
            conf    = min(qualifying[d] for d in domains)
            return PolicyDecision(
                mode           = RoutingMode.HYBRID,
                active_domains = domains,
                confidence     = round(conf, 3),
                reason         = (
                    f"two domains qualify: "
                    + ", ".join(f"{d} ({qualifying[d]:.0%})" for d in domains)
                ),
                score_cutoff   = cutoff,
            )

        # ── Exploratory fallback ─────────────────────────────────────────
        return PolicyDecision(
            mode           = RoutingMode.EXPLORATORY,
            active_domains = [],
            confidence     = 0.0,
            reason         = "no domain exceeds hybrid threshold",
            score_cutoff   = cutoff,
        )


# ---------------------------------------------------------------------------
# Two-pass retrieval
# ---------------------------------------------------------------------------

def retrieve(
    query:        str,
    top_k:        int,
    policy:       RetrievalPolicy,
) -> tuple[list, PolicyDecision, dict]:
    """
    Full retrieval pipeline.

    Returns (final_results, decision, stats).
    """
    searcher = Searcher()

    # Pass 1 — broad
    broad   = searcher.search(query, top_k=top_k * 3)
    decision = policy.decide(broad)

    stats: dict = {
        "pass1_count":   len(broad),
        "pass1_domains": _domain_counts(broad),
        "pass1_types":   _type_counts(broad),
        "routing_mode":  decision.mode.value,
        "routing_confidence": decision.confidence,
        "routing_reason": decision.reason,
        "active_domains": decision.active_domains,
        "score_cutoff":  decision.score_cutoff,
        "pass2_applied": False,
    }

    candidates = broad

    # Pass 2 — domain filter (FOCUSED or HYBRID)
    if decision.active_domains:
        filtered = [
            r for r in broad
            if _note_domain(r) in decision.active_domains
        ]
        if filtered:
            stats["pass2_applied"] = True
            candidates = filtered

    # Score cutoff — drop noise below fraction of top score
    if candidates and decision.score_cutoff > 0:
        candidates = [r for r in candidates if r.score >= decision.score_cutoff]

    results = candidates[:top_k]
    stats["final_count"] = len(results)

    return results, decision, stats


def _domain_counts(results: list) -> dict:
    counts: dict[str, int] = {}
    for r in results:
        d = _note_domain(r)
        counts[d] = counts.get(d, 0) + 1
    return counts


def _type_counts(results: list) -> dict:
    counts: dict[str, int] = {}
    for r in results:
        t = _note_type(r)
        counts[t] = counts.get(t, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Retrieval log
# ---------------------------------------------------------------------------

def _append_retrieval_log(query: str, stats: dict, log_path: Path) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts":    datetime.now(timezone.utc).isoformat(),
            "query": query,
            **stats,
        }
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        log.warning("Could not write retrieval log: %s", e)


def _last_retrieval_log(log_path: Path) -> dict | None:
    """Return the last non-feedback entry from the retrieval log."""
    if not log_path.exists():
        return None
    last = None
    try:
        for line in log_path.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if entry.get("type") != "feedback":
                last = entry
    except Exception:
        pass
    return last


def _append_feedback(
    feedback:  str,
    note:      str,
    log_path:  Path,
) -> str | None:
    """Append a feedback event correlated to the last retrieval. Returns query or None."""
    last = _last_retrieval_log(log_path)
    if not last:
        return None
    entry = {
        "ts":       datetime.now(timezone.utc).isoformat(),
        "type":     "feedback",
        "feedback": feedback,
        "note":     note,
        "query":    last.get("query", ""),
        "correlated_ts": last.get("ts", ""),
    }
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return last.get("query", "")
    except Exception as e:
        log.warning("Could not write feedback: %s", e)
        return None


# ---------------------------------------------------------------------------
# Query derivation
# ---------------------------------------------------------------------------

def _derive_query_from_session() -> str | None:
    try:
        from openclaw import OpenClaw
        from parser   import SessionParser, UserMessage
    except ImportError:
        log.warning("openclaw or parser module not found")
        return None

    try:
        cfg     = OpenClaw()
        session = cfg.latest_session()
    except FileNotFoundError as e:
        log.warning("Could not find latest session: %s", e)
        return None

    conversation  = SessionParser(session).conversation()
    user_messages = [
        e.text for e in conversation
        if isinstance(e, UserMessage) and e.text.strip()
    ]
    if not user_messages:
        return None

    recent = user_messages[-3:]
    joined = "\n".join(f"- {m[:300]}" for m in recent)

    client = Ollama()
    if not client.ping():
        log.warning("Ollama unreachable — cannot derive query")
        return None

    model  = SETTINGS.get("ollama", {}).get("chat_model", "qwen3:14b")
    prompt = (
        "The following are recent messages from an engineering session.\n"
        "Summarise the core technical topic in ONE short phrase (5–10 words).\n"
        "The phrase will be used as a search query for a knowledge base.\n"
        "Reply with only the phrase — no punctuation, no explanation.\n\n"
        f"{joined}"
    )
    try:
        response = client.chat(model=model, user=prompt)
        query    = response.strip().strip("\"'").strip()
        if query:
            log.info("Derived query: %s", query)
            return query
    except Exception as e:
        log.warning("Query derivation failed: %s", e)
    return None


# ---------------------------------------------------------------------------
# Context renderer
# ---------------------------------------------------------------------------

def render_engineering_context(
    query:    str,
    results:  list,
    decision: PolicyDecision,
) -> str:
    now         = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    mode_label  = decision.mode.value.upper()
    domains_str = ", ".join(f"`{d}`" for d in decision.active_domains) if decision.active_domains else "all"
    conf_pct    = f"{decision.confidence:.0%}"

    lines: list[str] = [
        "# Engineering Context",
        "",
        f"> **Query:** {query}",
        f"> **Generated:** {now}",
        f"> **Routing:** {mode_label} · Domains: {domains_str} · Confidence: {conf_pct}",
        f"> **Sources:** {len(results)}",
        "",
        "---",
        "",
        "> ⚠ **Instructions for the agent**",
        "> Before forming any hypothesis, check the sections below.",
        "> Negative knowledge lists conclusions that have already been",
        "> disproven — do not repeat them without new contradicting evidence.",
        "",
        "---",
        "",
    ]

    if not results:
        lines += ["_No relevant knowledge found for this query._", "", "Proceed without prior context.", ""]
        return "\n".join(lines)

    troubleshooting = [r for r in results if r.category == "Troubleshooting"]
    workflows       = [r for r in results if r.category == "Workflow"]
    negative        = [r for r in results if getattr(r, "refuted", False)]
    other           = [
        r for r in results
        if r.category not in ("Troubleshooting", "Workflow")
        and not getattr(r, "refuted", False)
    ]

    for section_title, items in [
        ("## Relevant Observations", troubleshooting + other),
        ("## Relevant Procedures",   workflows),
    ]:
        if items:
            lines += [section_title, ""]
            for r in items:
                d = _note_domain(r)
                t = _note_type(r)
                lines += [
                    f"### {r.title}",
                    f"> {r.category} · {d} · {t} · {r.confidence}/100 · score {r.score:.2f}",
                    "",
                    r.summary,
                    "",
                ]

    if negative:
        lines += [
            "## Negative Knowledge — Do Not Repeat These Errors",
            "",
            "> These hypotheses have been **disproven** in past sessions.",
            "> Do not conclude these without strong new contradicting evidence.",
            "",
        ]
        for r in negative:
            lines += [
                f"### ~~{r.title}~~",
                f"> Refuted · confidence in refutation: {r.confidence}/100",
                "",
                r.summary,
                "",
            ]
    else:
        lines += ["## Negative Knowledge", "", "_No disproven hypotheses on record._", ""]

    lines += [
        "## Open Hypotheses",
        "",
        "_No open hypotheses recorded yet._",
        "",
        "---",
        "",
        f"_Context generated by prime.py · {now}_",
        "",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------

def cmd_check() -> None:
    print(DIVIDER)
    print("prime.py — configuration check")
    print(DIVIDER)

    client = Ollama()
    ok = client.ping()
    print(f"  Ollama       {'✓' if ok else '✗'}  {client.host}")
    if not ok:
        print("  → systemctl --user start ollama")

    from index.vector_store import VectorStore
    idx_cfg = SETTINGS.get("index", {})
    db_path = expand(idx_cfg.get("db", "~/.openclaw/embeddings.sqlite"))
    if db_path.exists():
        store = VectorStore(db_path)
        stats = store.stats()
        print(f"  Index        ✓  {stats.total_notes} note(s)  {db_path}")
        for cat, n in stats.by_category.items():
            print(f"               {cat}: {n}")
    else:
        print(f"  Index        ✗  not found at {db_path}")
        print("  → python build_index.py")

    inject_cfg  = SETTINGS.get("inject", {})
    context_dir = expand(inject_cfg.get("context_dir", "~/.openclaw/workspace/context"))
    print(f"  Context dir  {'✓' if context_dir.exists() else '○'}  {context_dir}")

    kf = context_dir / inject_cfg.get("knowledge_file", "knowledge.md")
    if kf.exists():
        print(f"  knowledge.md ✓  {len(kf.read_text(encoding='utf-8'))} chars")
    else:
        print("  knowledge.md ○  not yet generated")

    print(DIVIDER)


def cmd_prime(
    query:          str,
    top_k:          int   = 5,
    dry_run:        bool  = False,
    force_domain:   str | None = None,
    cutoff_fraction: float = 0.55,
) -> None:

    print(DIVIDER)
    print("prime.py — priming session context")
    print(DIVIDER)
    print(f"  Query    : {query}")
    print(f"  Top-K    : {top_k}")
    print(f"  Cutoff   : {cutoff_fraction:.0%} of top score")
    if force_domain:
        print(f"  Domain   : {force_domain} (forced)")
    if dry_run:
        print("  Mode     : DRY RUN")
    print()

    policy = RetrievalPolicy(
        cutoff_fraction = cutoff_fraction,
        force_domain    = force_domain,
    )

    print("  Retrieving …", end="", flush=True)
    try:
        results, decision, stats = retrieve(query, top_k, policy)
    except SearchError as e:
        print(f"\n  ✗  {e}")
        sys.exit(1)

    print(f"\r  [{decision.mode.value.upper()}] confidence={decision.confidence:.0%}  "
          f"domains={decision.active_domains or 'all'}  "
          f"cutoff={decision.score_cutoff:.3f}")
    print(f"  Reason: {decision.reason}")
    print()

    if stats["pass1_domains"]:
        print("  Pass-1 domain distribution:")
        for d, c in sorted(stats["pass1_domains"].items(), key=lambda x: -x[1]):
            print(f"    {d:<15} {c}")
    print()

    for i, r in enumerate(results, 1):
        d = _note_domain(r)
        t = _note_type(r)
        refuted = " [REFUTED]" if getattr(r, "refuted", False) else ""
        print(f"  [{i}]  {r.score:.4f}  {r.title}  ({d} · {t}){refuted}")

    print()
    context_text = render_engineering_context(query, results, decision)
    char_count   = len(context_text)
    tok_est      = char_count // 4
    print(f"  Context : {char_count} chars (~{tok_est} tokens)")

    if dry_run:
        print()
        print(DIVIDER)
        print("DRY RUN — would write:")
        print(DIVIDER)
        print(context_text)
        return

    # Write files
    inject_cfg  = SETTINGS.get("inject", {})
    context_dir = expand(inject_cfg.get("context_dir", "~/.openclaw/workspace/context"))
    context_dir.mkdir(parents=True, exist_ok=True)

    kf = context_dir / inject_cfg.get("knowledge_file", "knowledge.md")
    kf.write_text(context_text, encoding="utf-8")

    injector         = Injector()
    injection_result = injector.inject(question=query, results=results)

    log_path = Path("logs/retrieval") / f"{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl"
    _append_retrieval_log(query, stats, log_path)

    print()
    print(DIVIDER)
    print(f"  ✓  Engineering context written ({char_count} chars, ~{tok_est} tokens)")
    print(f"     {kf}")
    print(f"     {injection_result.retrieval_path}")
    print(f"     {log_path}")
    print(DIVIDER)
    print()


def cmd_feedback(feedback: str, note: str) -> None:
    log_path = Path("logs/retrieval") / f"{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl"
    query = _append_feedback(feedback, note, log_path)
    if query:
        print(f"  ✓  Feedback recorded: {feedback}")
        print(f"     Query: {query}")
        if note:
            print(f"     Note:  {note}")
    else:
        print("  ✗  No previous retrieval found to correlate feedback with.")
        print(f"     Log expected at: {log_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prime an OpenClaw session with relevant knowledge.",
    )
    parser.add_argument("query", nargs="?",
                        help="Search query (required unless --auto, --check, or --feedback)")
    parser.add_argument("--auto",     action="store_true",
                        help="Derive query from latest session")
    parser.add_argument("--top",      type=int, default=5, metavar="K",
                        help="Max results to inject (default: 5)")
    parser.add_argument("--domain",   choices=sorted(ALLOWED_DOMAINS), metavar="DOMAIN",
                        help=f"Force domain: {', '.join(sorted(ALLOWED_DOMAINS))}")
    parser.add_argument("--cutoff",   type=float, default=0.55, metavar="FRAC",
                        help="Score cutoff as fraction of top score (default: 0.55)")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Show output without writing files")
    parser.add_argument("--check",    action="store_true",
                        help="Verify configuration and exit")
    parser.add_argument("--feedback", choices=["good", "bad", "partial"],
                        help="Record feedback on last priming run")
    parser.add_argument("--note",     default="",
                        help="Optional note to attach to feedback")

    args = parser.parse_args()

    if args.check:
        cmd_check()
        return

    if args.feedback:
        cmd_feedback(args.feedback, args.note)
        return

    query: str | None = args.query

    if not query and args.auto:
        print("  Deriving query from latest session …")
        query = _derive_query_from_session()
        if not query:
            print("  ✗  Could not derive query. Pass one explicitly.")
            sys.exit(1)

    if not query:
        parser.error("Provide a query or use --auto.")

    cmd_prime(
        query           = query,
        top_k           = args.top,
        dry_run         = args.dry_run,
        force_domain    = args.domain,
        cutoff_fraction = args.cutoff,
    )


if __name__ == "__main__":
    main()
