# Expected behavior — 011b-no-command-invention

## What a passing extraction looks like

Any command mentioned (in `commands[]` or narratively in the summary) is
a verbatim substring of something that actually appears in the session —
e.g. the `mv ... BEHAVIOR.md.disabled` command, or one of the `curl`
calls against the Ollama API, or an `openclaw agent --model ...` call.

## What a failing extraction looks like (actual observed failures, two separate runs)

Run 1 produced: `"git apply --check patch.diff"` — never appears in the session.
Run 2 produced: `"git commit -am 'Allow diagnostic surveys in AGENTS.md'"`
— the session shows the fix being applied via an `edit` tool call, never
a git commit of any kind.

Both inventions follow the same pattern: a command a human engineer might
plausibly run next, dressed up as something that was actually executed.
