---
name: send-openspec
description: Turn the OpenSpec feature on or off (change tracking, OpenSpec file mirror and snapshot).
disable-model-invocation: true
argument-hint: on|off
allowed-tools: Bash(company-cost send-openspec:*), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/bin/company-cost" send-openspec:*)
---

# company-cost send-openspec

Switch the **openspec** feature. It enables change inference (command, branch, recent) so `change_name`/`change_source` are sent, the OpenSpec markdown mirror (`sync/plan`, `sync/files`) and the change snapshot. `COMPANY_COST_NO_SYNC=1` still disables the mirror. It does not touch `settings.json`.

If traces is OFF, the server needs the project registered before it accepts a sync: exactly one session `start` event per session is sent for that purpose (no heartbeat, end or prompt excerpt). Mention this to the user when they enable openspec alone.

## Rules

- The argument is `on` or `off` (also accepted: `status`). If `$ARGUMENTS` is empty, the action is `on`.
- NEVER print, repeat or ask for the token; the command already hides it.
- Requires `/company-cost:setup` first (URL and token).

## Steps

1. Run, using the argument (`on|off`, default `on`):

   ```bash
   company-cost send-openspec $ARGUMENTS
   ```

   If `company-cost` is not found on PATH, use:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/bin/company-cost" send-openspec $ARGUMENTS
   ```

2. If it says nothing is configured, tell the user to run `/company-cost:setup`.
3. On success, confirm the new state. No restart is needed (hooks read the flag on every event). Suggest `/company-cost:status`.
