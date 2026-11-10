---
name: setup
description: Save the company-cost connection (ingest URL and token). Does not enable any feature.
disable-model-invocation: true
allowed-tools: Bash(printf:*), Bash(company-cost setup:*), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/bin/company-cost" setup:*)
---

# company-cost setup

Store the ingest service URL and token. This does NOT turn anything on: nothing is sent and `settings.json` is not touched until the user runs `/company-cost:send-traces` and/or `/company-cost:send-openspec`.

## Rules

- NEVER pass the token with `--token` (it would appear in the process list).
- NEVER edit `settings.json` by hand.
- NEVER repeat, echo or quote the token back to the user after receiving it.

## Steps

1. Ask the user for the ingest service URL (for example `https://ingest.example.com`) and the token (starts with `hro_`). Wait for the answers.
2. Run, substituting the values (keep the single quotes around the token):

   ```bash
   printf '%s\n' '<TOKEN>' | company-cost setup --url "<URL>" --token-stdin
   ```

   If `company-cost` is not found on PATH, use the fallback:

   ```bash
   printf '%s\n' '<TOKEN>' | python3 "${CLAUDE_PLUGIN_ROOT}/bin/company-cost" setup --url "<URL>" --token-stdin
   ```

3. If it refuses a plain `http://` URL to a non-local host, explain that `https://` is required; do not add `--allow-insecure` unless the user explicitly insists.
4. On success, show the next steps and let the user choose:
   - `/company-cost:send-traces` : OTEL cost export, session events and redacted prompt excerpt (restart Claude Code afterwards).
   - `/company-cost:send-openspec` : OpenSpec change tracking, file mirror and snapshot.
   Then suggest `/company-cost:status`.
