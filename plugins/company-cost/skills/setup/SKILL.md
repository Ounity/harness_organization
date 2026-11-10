---
name: setup
description: Save the company-cost connection (ingest URL and token). Does not enable any feature.
disable-model-invocation: true
allowed-tools: Bash(printf:*), Bash(company-cost setup:*), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/bin/company-cost" setup:*)
---

# company-cost setup

Store the ingest service URL and token, and optionally the name of the agent being monitored. This does NOT turn anything on: nothing is sent and `settings.json` is not touched until the user runs `/company-cost:send-traces` and/or `/company-cost:send-openspec`.

## Rules

- NEVER pass the token with `--token` (it would appear in the process list).
- NEVER edit `settings.json` by hand.
- The agent name is OPTIONAL user text that ends up inside a shell command. Only accept names of 1 to 64 characters made of the characters `A-Z a-z 0-9`, space, `.`, `_`, `-`, `:`, `/` (accented letters are fine too). If the name contains anything else (quotes, `$`, backticks, `;`, `|`, `&`, `<`, `>`, `\`, newlines, ...), REFUSE it, say which rule it broke and ask for another one; never "fix" it silently and never run the command with it.
- Pass the agent name only if the user provided one, always single-quoted and attached with `=` as `--agent='<NAME>'`. Never use double quotes for it. The name is a label visible to whoever operates the ingest service: never put secrets in it.
- NEVER repeat, echo or quote the token back to the user after receiving it.

## Steps

1. Ask the user for the ingest service URL (for example `https://ingest.example.com`) and the token (starts with `hro_`). Then ask (optional) for the name of the agent being monitored; the user may skip it. Wait for the answers.
2. Run, substituting the values (keep the single quotes around the token):

   ```bash
   printf '%s\n' '<TOKEN>' | company-cost setup --url "<URL>" --token-stdin
   ```

   If (and only if) the user gave a valid agent name, append `--agent='<NAME>'` (single quotes around the name) to the command, for example `... --token-stdin --agent='Code Reviewer'`. Re-running setup without `--agent` keeps the stored name; `--agent=''` clears it.

   If `company-cost` is not found on PATH, use the fallback:

   ```bash
   printf '%s\n' '<TOKEN>' | python3 "${CLAUDE_PLUGIN_ROOT}/bin/company-cost" setup --url "<URL>" --token-stdin
   ```

3. If it refuses a plain `http://` URL to a non-local host, explain that `https://` is required; do not add `--allow-insecure` unless the user explicitly insists.
4. On success, show the next steps and let the user choose:
   - `/company-cost:send-traces` : OTEL cost export, session events and redacted prompt excerpt (restart Claude Code afterwards).
   - `/company-cost:send-openspec` : OpenSpec change tracking, file mirror and snapshot.
   The agent name is only a label: all sessions are still monitored. Then suggest `/company-cost:status`.
