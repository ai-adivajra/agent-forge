#!/usr/bin/env python3
"""
profile_session.py — Session performance profiler.

Reads an OpenClaw trajectory file and produces a performance report:
LLM call count, token counts, generation time, and context composition.

Schema confirmed by inspecting real trajectory files in
~/.openclaw/agents/main/sessions/. Key event types used:
  session.started   – session/turn start timestamp
  trace.metadata    – injected files, tool list (systemPromptReport)
  context.compiled  – fallback for tool count / system prompt chars
  prompt.submitted  – user prompt text
  model.completed   – per-call usage via messagesSnapshot
  session.ended     – turn end timestamp

Usage:
    python profile_session.py                       # latest session
    python profile_session.py --session <name>      # specific session
    python profile_session.py --session <name> --json
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from openclaw import OpenClaw


# ─── Data model ──────────────────────────────────────────────────────────────

class LLMCall:
    """One LLM completion (one assistant entry in messagesSnapshot)."""

    def __init__(
        self,
        start_ms: int,
        end_ms: int,
        input_tokens: int,
        output_tokens: int,
        stop_reason: str,
    ):
        self.start_ms     = start_ms
        self.end_ms       = end_ms
        self.duration_s   = max((end_ms - start_ms) / 1000, 0) if (end_ms and start_ms) else 0
        self.input_tokens  = input_tokens
        self.output_tokens = output_tokens
        self.stop_reason   = stop_reason
        self.tokens_per_sec = (
            output_tokens / self.duration_s if self.duration_s > 0 else 0
        )


class Turn:
    """One user turn (one runId group, one model.completed event)."""

    def __init__(self, run_id: str, prompt: str, calls: list[LLMCall]):
        self.run_id = run_id
        self.prompt = prompt
        self.calls  = calls

    @property
    def total_input(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def total_output(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    @property
    def total_duration_s(self) -> float:
        return sum(c.duration_s for c in self.calls)


class ContextInfo:
    """System-prompt composition from trace.metadata systemPromptReport."""

    def __init__(
        self,
        system_prompt_chars: int,
        injected_files: list[dict],
        tool_count: int,
        source: str,
    ):
        self.system_prompt_chars = system_prompt_chars
        self.injected_files      = injected_files   # [{name, injectedChars, truncated}]
        self.tool_count          = tool_count
        self.source              = source            # "systemPromptReport" | "context.compiled"


class SessionProfile:
    """Full session profile extracted from one trajectory file."""

    def __init__(
        self,
        session_id: str,
        model_id: str,
        provider: str,
        start_ts: str,
        end_ts: str,
        turns: list[Turn],
        context: Optional[ContextInfo],
    ):
        self.session_id = session_id
        self.model_id   = model_id
        self.provider   = provider
        self.start_ts   = start_ts
        self.end_ts     = end_ts
        self.turns      = turns
        self.context    = context

    @property
    def all_calls(self) -> list[LLMCall]:
        return [c for t in self.turns for c in t.calls]

    @property
    def total_calls(self) -> int:
        return len(self.all_calls)

    @property
    def total_input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.all_calls)

    @property
    def total_output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.all_calls)

    @property
    def total_duration_s(self) -> float:
        try:
            t0 = _parse_iso(self.start_ts)
            t1 = _parse_iso(self.end_ts)
            return max(t1 - t0, 0)
        except (ValueError, TypeError):
            return sum(t.total_duration_s for t in self.turns)

    @property
    def avg_tokens_per_sec(self) -> float:
        gen_time = sum(c.duration_s for c in self.all_calls if c.duration_s > 0)
        if gen_time <= 0:
            return 0.0
        return self.total_output_tokens / gen_time


# ─── Parser ──────────────────────────────────────────────────────────────────

class TrajectoryParser:

    def __init__(self, path: Path):
        self.path = path

    def parse(self) -> SessionProfile:
        if not self.path.exists():
            raise FileNotFoundError(
                f"Trajectory file not found: {self.path}\n"
                "Make sure the session ID is correct and the session has completed."
            )

        events = self._load_events()
        if not events:
            raise ValueError(f"Trajectory file is empty or contains no valid JSON lines: {self.path}")

        # Session-level identifiers come from the first event.
        first     = events[0]
        session_id = first.get("sessionId", self.path.name.split(".")[0])
        model_id   = first.get("modelId", "unknown")
        provider   = first.get("provider", "unknown")

        starts = [e for e in events if e.get("type") == "session.started"]
        ends   = [e for e in events if e.get("type") == "session.ended"]
        start_ts = starts[0]["ts"] if starts else first.get("ts", "")
        end_ts   = ends[-1]["ts"]  if ends   else events[-1].get("ts", "")

        context = self._extract_context(events)

        # Split events into per-turn lists on each session.started boundary.
        # Turns within one session can share the same runId, so runId grouping
        # is unreliable; session.started always marks a new turn boundary.
        turns_events = self._split_turns(events)

        turns: list[Turn] = []
        prev_end_ms = 0
        for turn_events in turns_events:
            turn = self._extract_turn(turn_events, prev_end_ms)
            if turn is not None:
                turns.append(turn)
                if turn.calls:
                    prev_end_ms = turn.calls[-1].end_ms

        return SessionProfile(
            session_id=session_id,
            model_id=model_id,
            provider=provider,
            start_ts=start_ts,
            end_ts=end_ts,
            turns=turns,
            context=context,
        )

    # ------------------------------------------------------------------

    def _load_events(self) -> list[dict]:
        events: list[dict] = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return events

    def _extract_context(self, events: list[dict]) -> Optional[ContextInfo]:
        # Prefer systemPromptReport (has actual file names and tool list).
        for e in events:
            if e.get("type") != "trace.metadata":
                continue
            try:
                spr    = e["data"]["prompting"]["systemPromptReport"]
                sp     = spr.get("systemPrompt", {})
                files  = spr.get("injectedWorkspaceFiles", [])
                tools  = spr.get("tools", {})
                return ContextInfo(
                    system_prompt_chars=sp.get("chars", sp.get("originalChars", 0)),
                    injected_files=files,
                    tool_count=len(tools.get("entries", [])),
                    source="systemPromptReport",
                )
            except (KeyError, TypeError):
                continue

        # Fallback: context.compiled has chars and tool count but not file names.
        for e in events:
            if e.get("type") != "context.compiled":
                continue
            try:
                d   = e["data"]
                sp  = d.get("systemPrompt", {})
                return ContextInfo(
                    system_prompt_chars=sp.get("originalChars", sp.get("chars", 0)),
                    injected_files=[],
                    tool_count=len(d.get("tools", [])),
                    source="context.compiled",
                )
            except (KeyError, TypeError):
                continue

        return None

    def _split_turns(self, events: list[dict]) -> list[list[dict]]:
        """Split flat event list into per-turn sublists on session.started."""
        turns: list[list[dict]] = []
        current: list[dict] = []
        for e in events:
            if e.get("type") == "session.started" and current:
                turns.append(current)
                current = []
            current.append(e)
        if current:
            turns.append(current)
        return turns

    def _extract_turn(
        self, run_events: list[dict], prev_end_ms: int
    ) -> Optional[Turn]:
        run_id = run_events[0].get("runId", "unknown") if run_events else "unknown"
        mc = next((e for e in run_events if e.get("type") == "model.completed"), None)
        if mc is None:
            return None

        ps = next((e for e in run_events if e.get("type") == "prompt.submitted"), None)
        ss = next((e for e in run_events if e.get("type") == "session.started"), None)
        prompt_text: str = ps["data"].get("prompt", "") if ps else ""

        snapshot = mc.get("data", {}).get("messagesSnapshot", [])

        # Anchor at the user message closest to prompt.submitted.ts.
        # Snapshots are not simple appends — OpenClaw may restructure prior
        # messages, so we can't rely on a running length offset.
        # Fallback 1: first user message after prev_end_ms (no prompt.submitted).
        # Fallback 2: index 0 (first turn, no prior state).
        anchor_ts_str = (ps or ss or {}).get("ts")
        anchor_idx: Optional[int] = None

        if anchor_ts_str:
            anchor_ms = int(_parse_iso(anchor_ts_str) * 1000)
            best_delta = float("inf")
            for i, msg in enumerate(snapshot):
                if msg.get("role") == "user" and msg.get("timestamp"):
                    delta = abs(msg["timestamp"] - anchor_ms)
                    if delta < best_delta:
                        best_delta = delta
                        anchor_idx = i
            if best_delta > 5_000:   # >5 s: no reliable match
                anchor_idx = None

        if anchor_idx is None and prev_end_ms > 0:
            for i, msg in enumerate(snapshot):
                if msg.get("role") == "user" and (msg.get("timestamp") or 0) > prev_end_ms:
                    anchor_idx = i
                    break

        if anchor_idx is None:
            anchor_idx = 0

        new_messages = snapshot[anchor_idx:]

        calls: list[LLMCall] = []
        for i, msg in enumerate(new_messages):
            if msg.get("role") != "assistant":
                continue

            prev_msg  = new_messages[i - 1] if i > 0 else None
            usage     = msg.get("usage") or {}
            end_ms    = msg.get("timestamp", 0)
            start_ms  = prev_msg.get("timestamp", end_ms) if prev_msg else end_ms

            calls.append(LLMCall(
                start_ms=start_ms,
                end_ms=end_ms,
                input_tokens=usage.get("input", 0),
                output_tokens=usage.get("output", 0),
                stop_reason=msg.get("stopReason", "unknown"),
            ))

        return Turn(run_id=run_id, prompt=prompt_text, calls=calls) if calls else None


# ─── Formatting helpers ───────────────────────────────────────────────────────

def _parse_iso(ts: str) -> float:
    """Parse ISO-8601 UTC string to epoch seconds."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def _fmt_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s"


def _fmt_offset(ms: int, t0_ms: int) -> str:
    """Return MM:SS offset from t0."""
    delta_s = max((ms - t0_ms) / 1000, 0)
    m, s    = divmod(int(delta_s), 60)
    return f"{m:02d}:{s:02d}"


def _approx_tokens(chars: int) -> int:
    return chars // 4


# ─── Human-readable report ────────────────────────────────────────────────────

_W   = 64
_SEP = "═" * _W


def _print_report(profile: SessionProfile) -> None:
    print(_SEP)
    print(f"  Session Profile — {profile.session_id}")
    print(_SEP)

    # Summary block
    L = 24  # label column width
    print(f"  {'Model':<{L}}: {profile.model_id} ({profile.provider})")
    print(f"  {'LLM calls':<{L}}: {profile.total_calls:,}")
    if len(profile.turns) > 1:
        print(f"  {'User turns':<{L}}: {len(profile.turns):,}")
    print(f"  {'Total duration':<{L}}: {_fmt_duration(profile.total_duration_s)}")
    print(f"  {'Input tokens (total)':<{L}}: {profile.total_input_tokens:,}")
    print(f"  {'Output tokens (total)':<{L}}: {profile.total_output_tokens:,}")
    avg = profile.avg_tokens_per_sec
    print(f"  {'Tokens/sec (avg)':<{L}}: {avg:.1f}" if avg > 0 else f"  {'Tokens/sec (avg)':<{L}}: N/A")

    # Context composition
    ctx = profile.context
    if ctx:
        print()
        src_label = "trace.metadata" if ctx.source == "systemPromptReport" else ctx.source
        print(f"  Context composition (from {src_label}):")
        if ctx.system_prompt_chars:
            approx = _approx_tokens(ctx.system_prompt_chars)
            print(f"    {'System prompt':<20}: {ctx.system_prompt_chars:,} chars (~{approx:,} tokens)")
        if ctx.injected_files:
            names = ", ".join(f["name"] for f in ctx.injected_files)
            print(f"    {'Files injected':<20}: {names}")
            truncated = [f["name"] for f in ctx.injected_files if f.get("truncated")]
            if truncated:
                print(f"    ⚠ Truncated         : {', '.join(truncated)}")
        else:
            print(f"    {'Files injected':<20}: N/A (not in trajectory)")
        print(f"    {'Tools available':<20}: {ctx.tool_count}")

    # Per-call breakdown
    all_calls = profile.all_calls
    print()
    if not all_calls:
        print("  Per-call breakdown: no LLM call data found in trajectory.")
    else:
        print("  Per-call breakdown:")
        t0_ms    = all_calls[0].start_ms
        call_num = 1

        for turn in profile.turns:
            if len(profile.turns) > 1:
                snippet = (turn.prompt[:58] + "…") if len(turn.prompt) > 58 else turn.prompt
                print(f"    ── \"{snippet}\"")

            for call in turn.calls:
                start_off = _fmt_offset(call.start_ms, t0_ms)
                end_off   = _fmt_offset(call.end_ms,   t0_ms)
                dur       = _fmt_duration(call.duration_s)
                tps       = f"{call.tokens_per_sec:.1f}" if call.tokens_per_sec > 0 else "N/A"
                stop      = call.stop_reason or "?"
                print(
                    f"    [{call_num:3d}] {start_off} → {end_off}"
                    f"  ({dur:>8s})"
                    f"  in={call.input_tokens:,}  out={call.output_tokens:,}"
                    f"  tok/s={tps}  [{stop}]"
                )
                call_num += 1

    # GPU note — always shown explicitly rather than silently omitted.
    print()
    print("  ⚠  GPU/VRAM utilization not available — trajectory files do not")
    print("     record live hardware state. Use a separate live-sampling tool")
    print("     (e.g. `watch -n5 rocm-smi` or `ollama ps`) run DURING a session")
    print("     if hardware correlation is needed.")
    print(_SEP)


# ─── JSON output ─────────────────────────────────────────────────────────────

def _profile_to_dict(profile: SessionProfile) -> dict:
    ctx = profile.context
    return {
        "session_id":          profile.session_id,
        "model":               profile.model_id,
        "provider":            profile.provider,
        "start_ts":            profile.start_ts,
        "end_ts":              profile.end_ts,
        "total_duration_s":    round(profile.total_duration_s, 2),
        "llm_calls":           profile.total_calls,
        "user_turns":          len(profile.turns),
        "total_input_tokens":  profile.total_input_tokens,
        "total_output_tokens": profile.total_output_tokens,
        "avg_tokens_per_sec":  round(profile.avg_tokens_per_sec, 2),
        "context": {
            "source":              ctx.source if ctx else None,
            "system_prompt_chars": ctx.system_prompt_chars if ctx else None,
            "tool_count":          ctx.tool_count if ctx else None,
            "injected_files": [
                {
                    "name":           f.get("name"),
                    "injected_chars": f.get("injectedChars"),
                    "truncated":      f.get("truncated"),
                }
                for f in (ctx.injected_files if ctx else [])
            ],
        },
        "turns": [
            {
                "run_id": t.run_id,
                "prompt": t.prompt,
                "calls": [
                    {
                        "start_ms":     c.start_ms,
                        "end_ms":       c.end_ms,
                        "duration_s":   round(c.duration_s, 3),
                        "input_tokens":  c.input_tokens,
                        "output_tokens": c.output_tokens,
                        "tokens_per_sec": round(c.tokens_per_sec, 2),
                        "stop_reason":   c.stop_reason,
                    }
                    for c in t.calls
                ],
            }
            for t in profile.turns
        ],
        "gpu_note": (
            "GPU/VRAM utilization is not recorded in trajectory files. "
            "Use a live-sampling tool (e.g. rocm-smi, ollama ps) run "
            "concurrently during a session to collect hardware metrics."
        ),
    }


# ─── Trajectory path resolution ──────────────────────────────────────────────

def _trajectory_path(session_file: Path) -> Path:
    """
    Given a session .jsonl path, return the companion .trajectory.jsonl path.
    e.g. /…/abc123.jsonl  →  /…/abc123.trajectory.jsonl
    """
    stem = session_file.name
    if stem.endswith(".jsonl"):
        stem = stem[: -len(".jsonl")]
    return session_file.parent / f"{stem}.trajectory.jsonl"


# ─── Entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Profile an OpenClaw session from its trajectory file."
    )
    ap.add_argument("--session", metavar="NAME",
                    help="Session name or partial ID (default: latest session)")
    ap.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON instead of the human report")
    args = ap.parse_args()

    try:
        oc = OpenClaw()
        session_file = oc.get_session(args.session) if args.session else oc.latest_session()
        traj_path    = _trajectory_path(session_file)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        profile = TrajectoryParser(traj_path).parse()
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(_profile_to_dict(profile), indent=2))
    else:
        _print_report(profile)


if __name__ == "__main__":
    main()
