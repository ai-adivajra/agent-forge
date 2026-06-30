#!/usr/bin/env python3
"""
update_behavior.py — Add a candidate lesson to BEHAVIOR.md.

Usage:
    python update_behavior.py "lesson text"
    python update_behavior.py "lesson text" --promote   # promote to Rules section
"""
import argparse
from pathlib import Path

BEHAVIOR_MD = Path.home() / ".openclaw" / "workspace" / "BEHAVIOR.md"
DIVIDER = "─" * 70


def add_candidate(lesson: str) -> None:
    content = BEHAVIOR_MD.read_text(encoding="utf-8")
    marker  = "## Candidates"
    if marker not in content:
        print(f"ERROR: '{marker}' section not found in BEHAVIOR.md")
        raise SystemExit(1)

    # Find insertion point — after the Candidates heading and its comment
    lines   = content.splitlines()
    insert  = None
    in_cand = False
    for i, line in enumerate(lines):
        if line.strip() == marker:
            in_cand = True
            continue
        if in_cand and line.startswith("## "):
            insert = i
            break

    if insert is None:
        print("ERROR: Could not find end of Candidates section")
        raise SystemExit(1)

    new_line = f"<!-- [1/3 sessions] {lesson} -->"
    lines.insert(insert, new_line)
    lines.insert(insert, "")

    BEHAVIOR_MD.write_text("\n".join(lines), encoding="utf-8")
    print(DIVIDER)
    print(f"✓  Added to BEHAVIOR.md Candidates:")
    print(f"   {new_line}")
    print(DIVIDER)


def promote_to_rules(lesson: str) -> None:
    content = BEHAVIOR_MD.read_text(encoding="utf-8")
    marker  = "## Rules"
    if marker not in content:
        print(f"ERROR: '{marker}' section not found in BEHAVIOR.md")
        raise SystemExit(1)

    lines  = content.splitlines()
    insert = None
    in_rules = False
    for i, line in enumerate(lines):
        if line.strip() == marker:
            in_rules = True
            continue
        if in_rules and line.startswith("## "):
            insert = i
            break

    if insert is None:
        print("ERROR: Could not find end of Rules section")
        raise SystemExit(1)

    new_line = f"- {lesson}"
    lines.insert(insert, "")
    lines.insert(insert, new_line)

    BEHAVIOR_MD.write_text("\n".join(lines), encoding="utf-8")
    print(DIVIDER)
    print(f"✓  Promoted to BEHAVIOR.md Rules:")
    print(f"   {new_line}")
    print(DIVIDER)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add a lesson to BEHAVIOR.md"
    )
    parser.add_argument("lesson", help="The lesson text to add")
    parser.add_argument("--promote", action="store_true",
                        help="Promote directly to Rules (use only after 3+ sessions)")
    args = parser.parse_args()

    if args.promote:
        promote_to_rules(args.lesson)
    else:
        add_candidate(args.lesson)


if __name__ == "__main__":
    main()
