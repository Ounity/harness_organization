# company-cost

A Claude Code plugin that reports **cost telemetry** and **OpenSpec progress** from your sessions to an *ingest service* that you (or your organization) operate.

- Status: `0.1.0`, license Apache-2.0
- Platforms: **macOS, Linux, WSL**. Native Windows is **not supported** in this release.

## What it collects

| Collected | Details |
|-----------|---------|
| Cost telemetry | Claude Code's own OpenTelemetry events, sent to `<URL>/v1/otlp` |
| Session metadata | session id, project key (normalized git remote), branch name, change name, hash of the working directory. Outside a git repo the folder name is sent as the project name |
| Prompt excerpts | at most 200 characters per prompt, secrets redacted (see below) |
| OpenSpec files | `*.md` under your repo's `openspec/` (or `specs/`) directory, excluding `changes/archive` |

**Not collected:** full prompts, model responses, source code outside the OpenSpec directory, file paths of your machine (only a hash of the working directory).

### Prompt excerpts: read this

Excerpts are redacted (keys, bearer tokens, URLs with credentials, long random strings...) but redaction is best effort. **Excerpts are personal data visible to whoever operates the configured service.** Tell the people whose sessions are reported. The recommended retention for excerpts is **30 days** (enforced by the service, not by this plugin).

### Opt-outs

| Variable | Effect |
|----------|--------|
| `COMPANY_COST_NO_PROMPTS=1` | never send prompt excerpts |
| `COMPANY_COST_NO_SYNC=1` | never sync OpenSpec files |

Set them in your shell or in the `env` block of your settings.

## Requirements

- Python 3.9+ (standard library only)
- git
- `openspec` CLI (optional, improves change detection)
- A reachable ingest service and a token (see [Server contract](#minimum-server-contract))

## Install

```bash
claude plugin marketplace add Ounity/harness_organization
claude plugin install company-cost@ounity-plugins
```

## Setup

In Claude Code run `/company-cost:setup`. It asks for the service URL (for example `https://ingest.example.com`) and your token (`hro_REDACTED`), then writes the telemetry settings into your **user** settings (`~/.claude/settings.json`, or `$CLAUDE_CONFIG_DIR`). Restart Claude Code afterwards.

Equivalent from a terminal (the token goes through stdin, never argv):

```bash
printf '%s\n' "$TOKEN" | company-cost setup --url https://ingest.example.com --token-stdin
```

What `setup` writes into the `env` block of `settings.json` (9 keys):
`CLAUDE_CODE_ENABLE_TELEMETRY`, `OTEL_LOGS_EXPORTER`, `OTEL_METRICS_EXPORTER`, `OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_LOGS_EXPORT_INTERVAL`, `OTEL_METRICS_INCLUDE_REPOSITORY`, `COMPANY_COST_MANAGED` (marker used by uninstall).

`settings.json` is always rewritten with owner-only permissions (mode 600; group/other bits are removed because the token lives there). It also creates `settings.company-cost.json` (mode 600, holds URL and token for the hooks and records previous values) and a one-time backup `settings.json.company-cost.bak` (kept until you run uninstall, which deletes it). If you already have different values for those keys it stops; `--force` overrides them, and uninstall restores the old ones. Plain `http://` to a non-local host is refused unless `--allow-insecure` is passed.

> OTEL variables placed in a repository's `.claude/settings*.json` are **ignored** by Claude Code. They must live in user settings, managed settings or your shell, which is what `setup` does.

## Verify

1. Restart Claude Code, run `/company-cost:status`: it should show `Managed keys: present (9)`.
2. Send a prompt, then check `/company-cost:status` again: `Last ok` should appear and `Queue` should be 0.
3. Check at your service that cost events arrive.

Honest caveat: Claude Code's docs do not state that the OTLP exporter appends `/v1/logs` to `OTEL_EXPORTER_OTLP_ENDPOINT` (it is standard OpenTelemetry behavior for `http/json`). The plugin sets the endpoint to `<URL>/v1/otlp`, so your service should receive `<URL>/v1/otlp/v1/logs`. Step 3 is the real confirmation; do it on first run.

## Status and diagnostics

- `/company-cost:status` (or `company-cost status`): URL, whether keys are present, last success, last error, backoff, queue size, last sync. The token is never printed.
- `company-cost doctor`: offline checks (python, git, openspec, directories).

## Uninstall (order matters)

1. `/company-cost:uninstall` inside Claude Code (restores your previous values, then deletes the sidecar, the settings backup and the plugin data directory)
2. `claude plugin uninstall company-cost@ounity-plugins`
3. `claude plugin marketplace remove ounity-plugins`
4. Restart Claude Code
5. Revoke the token at the service

### Manual cleanup (if you removed the plugin first)

Run `python3 plugins/company-cost/bin/company-cost uninstall` from a clone of this repo, or by hand:

- In `~/.claude/settings.json` delete these keys from `env`: `CLAUDE_CODE_ENABLE_TELEMETRY`, `OTEL_LOGS_EXPORTER`, `OTEL_METRICS_EXPORTER`, `OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_LOGS_EXPORT_INTERVAL`, `OTEL_METRICS_INCLUDE_REPOSITORY`, `COMPANY_COST_MANAGED` (restore any values you had before; `settings.company-cost.json` lists them under `previous`)
- Delete `~/.claude/settings.company-cost.json` and `~/.claude/settings.json.company-cost.bak`
- Delete the data directory `~/.claude/plugins/data/company-cost*`
- Revoke the token at the service

(Use `$CLAUDE_CONFIG_DIR` instead of `~/.claude` if you set it.)

## Managed rollout (organizations)

Deploy a managed settings file: macOS `/Library/Application Support/ClaudeCode/managed-settings.json`, Linux/WSL `/etc/claude-code/managed-settings.json` (Windows path `C:\Program Files\ClaudeCode\managed-settings.json` exists in Claude Code but this plugin does not support Windows).

```json
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_METRICS_EXPORTER": "none",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "https://ingest.example.com/v1/otlp",
    "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer hro_REDACTED",
    "OTEL_LOGS_EXPORT_INTERVAL": "5000",
    "OTEL_METRICS_INCLUDE_REPOSITORY": "true"
  },
  "extraKnownMarketplaces": {
    "ounity-plugins": {
      "source": { "source": "github", "repo": "Ounity/harness_organization" }
    }
  },
  "enabledPlugins": { "company-cost@ounity-plugins": true }
}
```

Do not include `COMPANY_COST_MANAGED`. **Warning:** the token in a managed file is readable by every local user of the machine. Hooks still need the URL and token: users run `/company-cost:setup`, or provide the plugin options `RAG_URL` / `RAG_TOKEN`.

## Known limitations

- The token is stored in **clear text** in `settings.json` (`OTEL_EXPORTER_OTLP_HEADERS`) and in the sidecar (mode 600). Anyone who can read your home directory can read it. Use a revocable, least-privilege token.
- No native Windows support.
- Hooks are best effort and silent: failures never block Claude Code. Heartbeats are never queued (they would evict start/end/prompt items). Only network errors, HTTP 5xx and HTTP 429 are queued (max 500 items / 256 KB) and retried with backoff. A 401, other 4xx responses, `SessionEnd` delivery failures and the OpenSpec `sync/*` calls are not queued.
- Hook latency is bounded: git/OpenSpec inference has a wall-clock budget per event, is cached for 60 s per session, and `SessionEnd` never runs `openspec`.
- `status` and `doctor` find the hooks' data directory under `~/.claude/plugins/data/company-cost*` (newest one with state) when `CLAUDE_PLUGIN_DATA` is not set.
- CI: `gitleaks-action` needs a `GITLEAKS_LICENSE` secret when the repo belongs to an organization.
- Redaction is pattern based and cannot guarantee that no secret is sent.
- Sync safety: a `delete` answer from the service is never forwarded for a path that still exists locally (oversized, non-UTF-8, symlinked or beyond the 5000-file cap), and no deletes are sent at all when the manifest was truncated. A failed sync is not recorded as done, so the next `Stop` retries it.

## Minimum server contract

Authentication: `Authorization: Bearer hro_...` on every request. All plugin requests are `POST` with JSON.

| Endpoint | Purpose |
|----------|---------|
| `/v1/otlp` (+ `/v1/logs` appended by the exporter) | OTLP http/json log events (cost) |
| `/v1/ingest/plugin/session` | session start / heartbeat / end |
| `/v1/ingest/plugin/prompt` | redacted prompt excerpt |
| `/v1/ingest/plugin/snapshot` | OpenSpec change snapshot |
| `/v1/ingest/plugin/sync/plan` | receives file manifest, returns `{"upload": [paths], "delete": [paths]}` |
| `/v1/ingest/plugin/sync/files` | receives file contents and deletions |

The plugin never follows redirects. 401 is reported as a rejected token and pauses delivery for about 15 minutes (a successful `setup` clears it; queued items are kept); 429, 5xx and network errors are queued and retried with backoff. Other 4xx responses are dropped.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Not configured` | run `/company-cost:setup` |
| `Managed keys: MISSING` | re-run `/company-cost:setup`, then restart |
| `token rejected` | get a new token, re-run `/company-cost:setup` |
| setup lists conflicting keys | review them; re-run with `--force` only if you accept overriding |
| `refusing plain http://` | use `https://` (or `--allow-insecure` for a trusted network) |
| Queue keeps growing | check URL, network, `status` last error |
| No cost events at the service | restart Claude Code; confirm the service accepts `<URL>/v1/otlp/v1/logs` |
| `python3` not found | install Python 3.9+ |

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). Licensed under [Apache-2.0](LICENSE).
