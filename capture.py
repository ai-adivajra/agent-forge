#!/usr/bin/env python3
"""
capture.py — OpenClaw Knowledge Capture

Reads the latest (or a named) OpenClaw session, extracts reusable technical
knowledge via a local Ollama model, and saves the results as Obsidian notes.

Usage:
    python capture.py                       # process the latest session
    python capture.py --session <name>      # process a specific session (partial match)
    python capture.py --dry-run             # extract but do not save
    python capture.py --debug-prompt        # dump prompts and request to logs/debug/
    python capture.py --list-sessions       # list available sessions and exit
    python capture.py --check               # verify config and Ollama, then exit
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from openclaw  import OpenClaw
from parser    import SessionParser
from extractor import Extractor
from knowledge import KnowledgeCandidate
from kb        import KnowledgeBase
from prompts   import SYSTEM_PROMPT
from ollama    import Ollama, OllamaError
from config    import SETTINGS, ROOT

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level  = logging.INFO,
    format = "%(levelname)s  %(message)s",
)
log = logging.getLogger(__name__)

DIVIDER = "─" * 70


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_model_prefix(model: str) -> str:
    """
    OpenClaw stores model as 'provider/name' (e.g. 'ollama/llama3.2').
    Ollama only wants 'name'.
    """
    return model.split("/", 1)[-1] if "/" in model else model


def _print_candidates(candidates: list[KnowledgeCandidate]) -> None:
    for i, c in enumerate(candidates, 1):
        print(f"\n[{i}] {c.title}  (confidence: {c.confidence}/100)")
        print(f"    Category : {c.category}")
        if c.reason:
            print(f"    Reason   : {c.reason}")
        print(f"    Summary  : {c.summary}")
        if c.tool_calls:
            print(f"    Tools    : {', '.join(c.tool_calls)}")
        if c.commands:
            print(f"    Commands : {', '.join(c.commands)}")
        if c.files:
            print(f"    Files    : {', '.join(c.files)}")
        if c.models:
            print(f"    Models   : {', '.join(c.models)}")
        if c.plugins:
            print(f"    Plugins  : {', '.join(c.plugins)}")
        if c.tags:
            print(f"    Tags     : {', '.join(c.tags)}")


def _sizeof(text: str) -> str:
    """Return a human-readable byte size for a string."""
    size = len(text.encode("utf-8"))
    if size < 1024:
        return f"{size} B"
    return f"{size / 1024:.1f} KB"


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------

def cmd_check(cfg: OpenClaw, client: Ollama) -> None:
    print(DIVIDER)
    print("OpenClaw-Knowledge — configuration check")
    print(DIVIDER)

    summary = cfg.summary()
    for k, v in summary.items():
        print(f"  {k:<20} {v}")

    print()
    print(f"  Ollama host          {client.host}")

    if client.ping():
        print("  Ollama status        ✓ reachable")
        models = client.list_models()
        print(f"  Local models         {', '.join(models) if models else '(none)'}")
    else:
        print("  Ollama status        ✗ NOT reachable")
        print("  → Run: systemctl --user start ollama")

    print()
    session = cfg.latest_session()
    print(f"  Latest session       {session.name}")
    print(f"  Session count        {len(cfg.session_files())}")
    print(DIVIDER)


def cmd_list_sessions(cfg: OpenClaw) -> None:
    sessions = cfg.session_files()
    print(f"{len(sessions)} session(s) in {cfg.sessions_dir}\n")
    for i, s in enumerate(sessions, 1):
        ts = datetime.fromtimestamp(s.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        size_kb = s.stat().st_size // 1024
        print(f"  [{i:>3}]  {ts}  {size_kb:>6} KB  {s.name}")


def cmd_debug_prompt(
    cfg:          OpenClaw,
    client:       Ollama,
    session_name: str | None = None,
) -> None:
    """
    Build the exact system prompt, user prompt, and request JSON that would
    be sent to Ollama, then write them to logs/debug/ without calling the API.
    """

    # ---- Resolve session ------------------------------------------------
    if session_name:
        session = cfg.get_session(session_name)
    else:
        session = cfg.latest_session()

    # ---- Parse + build payload ------------------------------------------
    conversation = SessionParser(session).conversation()
    extractor    = Extractor()
    payload      = extractor.build_payload(
        conversation = conversation,
        model        = cfg.primary_model,
    )

    model_name = _strip_model_prefix(cfg.primary_model)
    user_text  = json.dumps(payload, ensure_ascii=False, indent=2)

    request_dict = client.build_request(
        model  = model_name,
        system = SYSTEM_PROMPT,
        user   = user_text,
    )

    # ---- Write files ----------------------------------------------------
    ts        = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    debug_dir = ROOT / "logs" / "debug" / ts
    debug_dir.mkdir(parents=True, exist_ok=True)

    files = {
        "system_prompt.txt": SYSTEM_PROMPT,
        "user_prompt.txt":   user_text,
        "request.json":      json.dumps(request_dict, ensure_ascii=False, indent=2),
    }

    print(DIVIDER)
    print("Debug prompt dump")
    print(DIVIDER)
    print(f"  Session  : {session.name}")
    print(f"  Model    : {model_name}")
    print(f"  Turns    : {len(payload.get('turns', []))}")
    print()

    for name, content in files.items():
        path = debug_dir / name
        path.write_text(content, encoding="utf-8")
        print(f"  ✓  {path}  ({_sizeof(content)})")

    print()
    print(f"  system_prompt  {_sizeof(SYSTEM_PROMPT)}")
    print(f"  user_prompt    {_sizeof(user_text)}")
    print(f"  total payload  {_sizeof(json.dumps(request_dict))}")
    print(DIVIDER)


def cmd_capture(
    cfg:          OpenClaw,
    client:       Ollama,
    session_name: str | None = None,
    dry_run:      bool = False,
    debug_prompt: bool = False,
) -> None:

    print(DIVIDER)
    print("OpenClaw Knowledge Capture")
    print(DIVIDER)
    print(f"  Model         : {cfg.primary_model}")
    print(f"  Ollama host   : {client.host}")
    print()

    # ---- Session --------------------------------------------------------
    if session_name:
        session = cfg.get_session(session_name)
    else:
        session = cfg.latest_session()

    print(f"  Session : {session.name}")
    print()

    # ---- Parse ----------------------------------------------------------
    conversation = SessionParser(session).conversation()

    print(f"  {len(conversation)} event(s) parsed")
    print()

    if not conversation:
        print("  ⚠ Session is empty — nothing to extract.")
        return

    # ---- Build payload --------------------------------------------------
    extractor = Extractor()
    payload   = extractor.build_payload(
        conversation = conversation,
        model        = cfg.primary_model,
    )

    turns = payload.get("turns", [])
    print(f"  {len(turns)} turn(s) in payload")
    print()

    model_name = _strip_model_prefix(cfg.primary_model)
    user_text  = json.dumps(payload, ensure_ascii=False, indent=2)

    # ---- Optional: dump debug files before calling Ollama ---------------
    if debug_prompt:
        request_dict = client.build_request(
            model  = model_name,
            system = SYSTEM_PROMPT,
            user   = user_text,
        )
        ts        = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        debug_dir = ROOT / "logs" / "debug" / ts
        debug_dir.mkdir(parents=True, exist_ok=True)

        (debug_dir / "system_prompt.txt").write_text(SYSTEM_PROMPT,  encoding="utf-8")
        (debug_dir / "user_prompt.txt"  ).write_text(user_text,       encoding="utf-8")
        (debug_dir / "request.json"     ).write_text(
            json.dumps(request_dict, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        print(f"  [debug] Prompt files written to {debug_dir}")
        print(f"          system_prompt  {_sizeof(SYSTEM_PROMPT)}")
        print(f"          user_prompt    {_sizeof(user_text)}")
        print()

    # ---- Call Ollama ----------------------------------------------------
    print(DIVIDER)
    print("Extracting knowledge …")
    print(DIVIDER)

    try:
        result = client.json(
            model  = model_name,
            system = SYSTEM_PROMPT,
            user   = user_text,
        )
    except OllamaError as e:
        log.error("Ollama error: %s", e)
        sys.exit(1)

    raw_candidates = result.get("knowledge", [])

    print(f"\n  {len(raw_candidates)} knowledge candidate(s) returned")

    if not raw_candidates:
        print("\n  ⚠ No reusable knowledge found in this session.")
        return

    # ---- Deserialise ----------------------------------------------------
    raw_conf       = SETTINGS.get("knowledge", {}).get("confidence", 0.75)
    min_confidence = int(raw_conf * 100) if raw_conf <= 1.0 else int(raw_conf)
    max_candidates = SETTINGS.get("knowledge", {}).get("max_candidates", 10)

    candidates = [
        KnowledgeCandidate.from_dict(d, source_session=session.name)
        for d in raw_candidates[:max_candidates]
    ]

    _print_candidates(candidates)

    above_threshold = [c for c in candidates if c.confidence >= min_confidence]

    print(f"\n  {len(above_threshold)} candidate(s) above confidence threshold ({min_confidence}/100)")

    if not above_threshold:
        print("  Nothing saved.")
        return

    # ---- Save -----------------------------------------------------------
    if dry_run:
        print("\n  [DRY RUN] Would save the candidates above — not writing any files.")
        return

    kb    = KnowledgeBase(cfg.knowledge_dir)
    saved = kb.save_all(above_threshold, min_confidence=min_confidence)

    print()
    print(DIVIDER)
    print(f"Saved {len(saved)} note(s) to {cfg.knowledge_dir}")
    print(DIVIDER)

    for path in saved:
        print(f"  ✓  {path.relative_to(cfg.knowledge_dir)}")

    print()
    print(f"Total notes in knowledge base: {kb.count()}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:

    parser = argparse.ArgumentParser(
        description="Extract reusable knowledge from OpenClaw sessions.",
    )
    parser.add_argument(
        "--session",
        metavar="NAME",
        help="Partial name of the session file to process (default: latest)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run extraction but do not write any files",
    )
    parser.add_argument(
        "--debug-prompt",
        action="store_true",
        help="Dump system_prompt.txt, user_prompt.txt, request.json to logs/debug/ and exit",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="List available sessions and exit",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Show config and verify Ollama is reachable, then exit",
    )

    args = parser.parse_args()

    try:
        cfg    = OpenClaw()
        client = Ollama()
    except FileNotFoundError as e:
        log.error("%s", e)
        sys.exit(1)

    if args.check:
        cmd_check(cfg, client)
        return

    if args.list_sessions:
        cmd_list_sessions(cfg)
        return

    # --debug-prompt alone: dump and exit without calling Ollama
    if args.debug_prompt and not any([args.dry_run]):
        cmd_debug_prompt(cfg, client, session_name=args.session)
        return

    cmd_capture(
        cfg          = cfg,
        client       = client,
        session_name = args.session,
        dry_run      = args.dry_run,
        debug_prompt = args.debug_prompt,
    )


if __name__ == "__main__":
    main()
