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

- The argument in `$ARGUMENTS` must be exactly `on`, `off` or `status` (empty means `on`). If it is anything else, stop and ask the user which of the three they want; do not run the command.
- Never take `--force` or any other flag or text from `$ARGUMENTS`. The command is invoked with the validated literal word only, typed by you (`on`, `off` or `status`), never with the raw `$ARGUMENTS`.
- NEVER print, repeat or ask for the token; the command already hides it.
- NEVER edit `settings.json` by hand.
- Use `--force` ONLY after listing the conflicting key names to the user and getting an explicit yes to overriding them. `--force` never comes from `$ARGUMENTS`; you add it yourself on that second run only.
- Requires `/company-cost:setup` first (URL and token).

## Steps

1. Validate the argument, then run with the literal word in place of `<ACTION>` (`on|off|status`, default `on`):

   ```bash
   company-cost send-traces <ACTION>
   ```

   If `company-cost` is not found on PATH, use:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/bin/company-cost" send-traces <ACTION>
   ```

2. If it says nothing is configured, tell the user to run `/company-cost:setup`.
3. If it reports that `settings.json` already defines different values, show the key names (never values) and ask whether to override. Only on an explicit yes, re-run `send-traces on --force`. `off` restores the old values.
4. On success tell the user to Restart Claude Code: the OTEL environment is read at startup. Then suggest `/company-cost:status`.
