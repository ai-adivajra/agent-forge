# Expected behavior — 013-summary-entity-consistency

## What a passing extraction looks like

If the summary says "A rule in AGENTS.md prevented models from making
tool calls...", the `files` field must contain the exact string
`"AGENTS.md"` (not a path-prefixed or otherwise altered version).

## What a failing extraction looks like (actual observed failure)

The real capture.py run produced a summary explicitly discussing
AGENTS.md at length:

> "A rule in AGENTS.md prevented models from making tool calls if they
> couldn't answer the first question..."

But the structured `files` field contained:

```json
"files": ["/path/to/AGENTS.md", "/path/to/patch.diff"]
```

`/path/to/AGENTS.md` is not the same string as `AGENTS.md` — a downstream
consumer doing an exact match against `files` (e.g. "show me all notes
that reference this specific file") would silently miss this note,
despite the summary being substantively about that exact file. This is
a quieter failure than outright invention (case 008) because the summary
itself often reads as correct and trustworthy — only a field-level
comparison surfaces the inconsistency.
