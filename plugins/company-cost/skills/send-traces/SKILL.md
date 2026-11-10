---
name: send-traces
description: Turn the traces feature on or off (OTEL cost export, session events and redacted prompt excerpt).
disable-model-invocation: true
argument-hint: on|off
allowed-tools: Bash(company-cost send-traces:*), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/bin/company-cost" send-traces:*)
---

# company-cost send-traces

Switch the **traces** feature. It covers the OTEL cost export (9 managed keys in `~/.claude/settings.json`), session start/heartbeat/end events (project, branch, cwd hash) and the redacted prompt excerpt (200 characters at most; `COMPANY_COST_NO_PROMPTS=1` disables it). It never sends OpenSpec files.

## Rules

- The argument is `on` or `off` (also accepted: `status`). If `$ARGUMENTS` is empty, the action is `on`.
- NEVER print, repeat or ask for the token; the command already hides it.
- NEVER edit `settings.json` by hand.
- Use `--force` ONLY after the user has explicitly said yes to overriding existing values.
- Requires `/company-cost:setup` first (URL and token).

## Steps

1. Run, using the argument (`on|off`, default `on`):

   ```bash
   company-cost send-traces $ARGUMENTS
   ```

   If `company-cost` is not found on PATH, use:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/bin/company-cost" send-traces $ARGUMENTS
   ```

2. If it says nothing is configured, tell the user to run `/company-cost:setup`.
3. If it reports that `settings.json` already defines different values, show the key names (never values) and ask whether to override. Only on an explicit yes, re-run adding `--force`. `off` restores the old values.
4. On success tell the user to Restart Claude Code: the OTEL environment is read at startup. Then suggest `/company-cost:status`.
