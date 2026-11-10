---
name: setup
description: Configure company-cost telemetry (ingest URL and token) in your user settings.
disable-model-invocation: true
allowed-tools: Bash(printf:*), Bash(company-cost setup:*), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/bin/company-cost" setup:*)
---

# company-cost setup

Configure Claude Code to report cost telemetry to the ingest service.

## Rules

- NEVER pass the token with `--token` (it would appear in the process list).
- NEVER edit `settings.json` by hand; only `company-cost setup` may write it.
- NEVER repeat, echo or quote the token back to the user after receiving it.
- Use `--force` ONLY after the user has explicitly said yes to overriding existing values.

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

3. If the command reports that `settings.json` already defines different values, show the listed key names (never values) and ask whether to override. Only on an explicit yes, re-run the same command adding `--force`. Old values are saved and restored on uninstall.
4. If it refuses a plain `http://` URL to a non-local host, explain that `https://` is required; do not add `--allow-insecure` unless the user explicitly insists.
5. On success, tell the user to RESTART Claude Code so the telemetry settings take effect, then run `/company-cost:status`.
