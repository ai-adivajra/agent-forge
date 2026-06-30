#!/usr/bin/env python3
"""
update_eval.py — Add a benchmark to eval_retrieval.py.

Usage:
    python update_eval.py "fio benchmark process tracking failure" \
        --domain openclaw \
        --top "fio,benchmark" \
        --notes "OpenClaw process management limitation"
"""
import argparse
import re
from pathlib import Path

EVAL_PATH = Path(__file__).parent / "eval_retrieval.py"
DIVIDER   = "─" * 70


def add_benchmark(
    query:   str,
    domain:  str | None,
    top:     list[str],
    mode:    str | None,
    notes:   str,
) -> None:
    content = EVAL_PATH.read_text(encoding="utf-8")

    # Find the end of BENCHMARKS list
    marker = "]  # end BENCHMARKS"
    if marker not in content:
        # Try alternative — find last Benchmark( block
        last = list(re.finditer(r'^\)', content, re.MULTILINE))
        if not last:
            print("ERROR: Could not find insertion point in eval_retrieval.py")
            raise SystemExit(1)
        # Find the closing ] of BENCHMARKS
        bench_end = content.rfind("\n]", 0, content.find("def run_benchmark"))
        if bench_end == -1:
            print("ERROR: Could not locate BENCHMARKS list end")
            raise SystemExit(1)
        insert_at = bench_end
        closing   = "\n]"
    else:
        insert_at = content.index(marker)
        closing   = marker

    # Build the new Benchmark entry
    top_str    = repr(top) if top else "[]"
    domain_str = f'    expected_domain = "{domain}",' if domain else ""
    top_entry  = f"    expected_top    = {top_str}," if top else ""
    mode_entry = f'    expected_mode   = "{mode}",' if mode else ""
    notes_str  = f'    notes           = "{notes}",' if notes else ""

    lines = ["    Benchmark(", f'        query           = "{query}",']
    if domain_str:
        lines.append(f"        expected_domain = \"{domain}\",")
    if top:
        lines.append(f"        expected_top    = {top_str},")
    if mode:
        lines.append(f"        expected_mode   = \"{mode}\",")
    if notes:
        lines.append(f"        notes           = \"{notes}\",")
    lines.append("    ),")

    new_entry = "\n" + "\n".join(lines)

    if closing == marker:
        new_content = content[:insert_at] + new_entry + "\n" + content[insert_at:]
    else:
        new_content = content[:insert_at] + new_entry + content[insert_at:]

    EVAL_PATH.write_text(new_content, encoding="utf-8")
    print(DIVIDER)
    print(f"✓  Added benchmark to eval_retrieval.py:")
    for l in lines:
        print(f"   {l}")
    print(DIVIDER)
    print("Run: python eval_retrieval.py --verbose")


def main() -> None:
    parser = argparse.ArgumentParser(description="Add a benchmark to eval_retrieval.py")
    parser.add_argument("query",     help="Search query for the benchmark")
    parser.add_argument("--domain",  default=None, help="Expected domain")
    parser.add_argument("--top",     default="",   help="Comma-separated title fragments")
    parser.add_argument("--mode",    default=None, help="Expected routing mode")
    parser.add_argument("--notes",   default="",   help="Description of the benchmark")
    args = parser.parse_args()

    top = [t.strip() for t in args.top.split(",") if t.strip()] if args.top else []
    add_benchmark(args.query, args.domain, top, args.mode, args.notes)


if __name__ == "__main__":
    main()
