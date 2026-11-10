# company-cost

A Claude Code plugin that reports **cost telemetry** and **OpenSpec progress** from your sessions to an *ingest service* that you (or your organization) operate. Nothing is sent until you switch on one of two independent features: **traces** and **openspec** (see [Choose what is sent](#choose-what-is-sent)).

- Status: `0.3.0`, license Apache-2.0
- Platforms: **macOS, Linux, WSL**. Native Windows is **not supported** in this release.

## What it collects

What is collected depends on which features you enabled.

| Collected | Details |
|-----------|---------|
| Cost telemetry (traces) | Claude Code's own OpenTelemetry events, sent to `<URL>/v1/otlp` |
| Session metadata (traces) | session id, project key (normalized git remote), branch name, hash of the working directory. Outside a git repo the folder name is sent as the project name |
| Change name and source (openspec) | the OpenSpec change inferred from your command, branch or most recent change |
| Prompt excerpts (traces) | at most 200 characters per prompt, secrets redacted (see below) |
| OpenSpec files (openspec) | `*.md` under your repo's `openspec/` (or `specs/`) directory, excluding `changes/archive` |

**Privacy statement:** with only **traces** enabled, no OpenSpec file ever leaves your machine (and no `openspec` command is run). With only **openspec** enabled, no cost telemetry, heartbeat, end event or prompt excerpt is sent; the single exception is one session `start` event per session, used as project registration (see below). With neither enabled the hooks do nothing.

**Not collected:** full prompts, model responses, source code outside the OpenSpec directory, file paths of your machine (only a hash of the working directory).

### Prompt excerpts: read this

Excerpts are redacted (keys, bearer tokens, URLs with credentials, long random strings...) but redaction is best effort. **Excerpts are personal data visible to whoever operates the configured service.** Tell the people whose sessions are reported. The recommended retention for excerpts is **30 days** (enforced by the service, not by this plugin).

### Opt-outs

| Variable | Effect |
|----------|--------|
| `COMPANY_COST_NO_PROMPTS=1` | never send prompt excerpts (traces) |
| `COMPANY_COST_NO_SYNC=1` | never sync OpenSpec files (openspec) |

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

In Claude Code run `/company-cost:setup`. It asks for the service URL (for example `https://ingest.example.com`) and your token (`hro_REDACTED`) and stores them in `settings.company-cost.json` (mode 600). **Setup does not enable anything**: it does not touch `settings.json` and nothing is sent. It prints the next steps: `/company-cost:send-traces` and `/company-cost:send-openspec`. It also asks, optionally, for the [agent name and environment](#agent-name-and-environment).

Equivalent from a terminal (the token goes through stdin, never argv):

```bash
printf '%s\n' "$TOKEN" | company-cost setup --url https://ingest.example.com --token-stdin
```

Add `--agent 'NAME'` and/or `--environment 'ENV'` (or the `--agent=NAME` form) to set the optional labels; see [Agent name and environment](#agent-name-and-environment).

Plain `http://` to a non-local host is refused unless `--allow-insecure` is passed. Re-running `setup` with a new token keeps your feature switches; if traces is on it also refreshes the token in `settings.json`.

## Agent name and environment

Two optional labels that tell the service which agent this installation monitors and which environment (dev, staging, prod...) it runs in, so usage can be attributed.

- **What they are**: labels, not filters. All sessions are still monitored; the labels only travel with them.
- **Set them**: `/company-cost:setup`, or `company-cost setup --agent 'Code Reviewer' --environment 'prod'`. For each option, re-running `setup` with it replaces the value, `""` clears it, and omitting it keeps the stored value. The plugin options `AGENT_NAME` and `ENVIRONMENT` (env `CLAUDE_PLUGIN_OPTION_AGENT_NAME` / `CLAUDE_PLUGIN_OPTION_ENVIRONMENT`) are used only when the sidecar has no value; the sidecar wins.
- **Rules (both)**: 1 to 64 characters after trimming; letters, digits, space and `. _ - :`. `/` is NOT allowed because it is the delimiter below. Control characters and anything the prompt redaction would alter (secret-looking text) are rejected; setup exits 1 without changing anything.
- **Where they are sent**: in the `user_ref` field of every `session` event (`start`, `heartbeat`, `end`, and the lone `start` of openspec-only mode). Prompt, snapshot and sync payloads do not carry it. Queued session events keep the value they were built with.

| Agent name | Environment | `user_ref` |
|------------|-------------|------------|
| `planner` | `prod` | `agent:prod/planner` |
| `planner` | not set | `agent:planner` |
| not set | `prod` | `agent:prod/` |
| not set | not set | omitted |

`agent:` is a fixed prefix, even when only the environment is set (that is the chosen format). A consumer can split the part after the prefix on the first `/`: if there is one, the left side is the environment and the right side the name (possibly empty); if not, the whole value is the name.

- **Visibility**: plain text visible to whoever operates the service. They are not secrets; do not put one in them.
- **Caution**: this reuses the service's optional `user_ref` field. The field's purpose on the server is not documented in this repo. If your service uses `user_ref` for end-user identity, tell its operator before you set either value.
- `/company-cost:status` and `company-cost doctor` show `Agent name`, `Environment` and the exact `user_ref` that will be sent. `uninstall` removes them with the sidecar.

## Choose what is sent

Two independent, persistent switches. `on` is the default action when no argument is given.

| Feature | What is sent | Endpoints | Enable / disable |
|---------|--------------|-----------|------------------|
| **traces** (OTEL cost export, session events, prompt excerpt) | the 9 OTEL keys written to `settings.json` (Claude Code then exports cost events); session `start` / `heartbeat` / `end` (project, branch, cwd hash); redacted prompt excerpt (200 chars max; `COMPANY_COST_NO_PROMPTS=1` still applies) | `/v1/otlp`, `/v1/ingest/plugin/session`, `/v1/ingest/plugin/prompt` | `/company-cost:send-traces [on\|off]` or `company-cost send-traces [on\|off\|status]` |
| **openspec** (change tracking and file mirror) | change inference (command, branch, recent) so `change_name` / `change_source` are sent; the OpenSpec file mirror; the change snapshot (`COMPANY_COST_NO_SYNC=1` still applies) | `/v1/ingest/plugin/sync/plan`, `sync/files`, `snapshot` (plus the registration `start` event below) | `/company-cost:send-openspec [on\|off]` or `company-cost send-openspec [on\|off\|status]` |

"Traces" covers the OTEL cost export, the session events and the prompt excerpt (the excerpt travels with the traces).

**send-traces on** writes these keys into the `env` block of `~/.claude/settings.json` (or `$CLAUDE_CONFIG_DIR`): `CLAUDE_CODE_ENABLE_TELEMETRY`, `OTEL_LOGS_EXPORTER`, `OTEL_METRICS_EXPORTER`, `OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_LOGS_EXPORT_INTERVAL`, `OTEL_METRICS_INCLUDE_REPOSITORY`, `COMPANY_COST_MANAGED` (marker). `settings.json` is rewritten atomically with owner-only permissions (mode 600; group/other bits are removed because the token lives there), after a one-time backup `settings.json.company-cost.bak` (kept until uninstall). If you already have different values for those keys it stops and lists them; `/company-cost:send-traces` adds `--force` only after you explicitly say yes (it is never taken from the skill argument, which must be exactly `on`, `off` or `status`), and `send-traces off` (or uninstall) restores the old ones. The CLI form is `company-cost send-traces on --force`. **Restart Claude Code** after `send-traces on` or `off`: the OTEL environment is read at startup.

**send-traces off** first records the flag as off, then removes exactly those keys and restores the previous values (an original empty `env` object stays; `env` is deleted only if setup created it). If `settings.json` is invalid JSON or `env` is not an object, the flag is still off, the command exits 1 and tells you the OTEL keys remain: fix the file and run `send-traces off` again; URL and token stay in the sidecar, so you can switch it on again without running `setup`. **send-openspec on|off** only flips a flag (hooks read it on every event, no restart needed). Both require `setup` first; `send-traces on` without a configured URL/token fails with exit code 1 and points to `/company-cost:setup`.

### Registration exception when only openspec is on

The server answers `409 unknown_project` to `sync/plan` unless a session event for that project was received first. So with **openspec on and traces off**, the plugin sends exactly one session `start` event per session (project, branch, cwd hash, change name/source) as project registration, and then runs change inference, sync and snapshot. It sends no heartbeat, no `end` event and no prompt excerpt, and no OTEL keys are written.

### Hook behavior per combination

| traces | openspec | Hooks do |
|--------|----------|----------|
| off | off | nothing (silent no-op, no network, no subprocess) |
| on | off | start / heartbeat / end + excerpt, **without** change fields; no `openspec` command, no sync, no snapshot |
| off | on | only the registration `start`, then inference, sync, snapshot |
| on | on | everything (same as 0.1.0) |

> OTEL variables placed in a repository's `.claude/settings*.json` are **ignored** by Claude Code. They must live in user settings, managed settings or your shell, which is what `send-traces on` does.

### Upgrading from 0.1.0

Existing installs keep working exactly as before: a sidecar without a `features` entry is treated as **both features enabled**. Run `/company-cost:send-traces off` or `/company-cost:send-openspec off` to narrow it. New installs start with both off.

## Verify

1. Run `/company-cost:send-traces`, restart Claude Code, then `/company-cost:status`: it should show `traces: enabled` and `Managed keys: present (9)`.
2. Send a prompt, then check `/company-cost:status` again: `Last ok` should appear and `Queue` should be 0.
3. Check at your service that cost events arrive.

Honest caveat: Claude Code's docs do not state that the OTLP exporter appends `/v1/logs` to `OTEL_EXPORTER_OTLP_ENDPOINT` (it is standard OpenTelemetry behavior for `http/json`). The plugin sets the endpoint to `<URL>/v1/otlp`, so your service should receive `<URL>/v1/otlp/v1/logs`. Step 3 is the real confirmation; do it on first run.

## Status and diagnostics

- `/company-cost:status` (or `company-cost status`): URL, each feature enabled/disabled with what it sends (and the registration exception when openspec runs alone), whether keys are present, last success, last error, backoff, queue size, last sync. The token is never printed.
- `company-cost doctor`: offline checks (python, git, openspec, directories, feature switches).

## Uninstall (order matters)

1. `/company-cost:uninstall` inside Claude Code (restores your previous values, then deletes the sidecar with the feature switches, the settings backup and the plugin data directory; works whether the features are on or off)
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

Do not include `COMPANY_COST_MANAGED`. **Warning:** the token in a managed file is readable by every local user of the machine. Hooks still need the URL and token: users run `/company-cost:setup`, or provide the plugin options `RAG_URL` / `RAG_TOKEN`. Hooks only act for enabled features: after `setup` run `/company-cost:send-traces` (identical managed values are not a conflict) and/or `/company-cost:send-openspec`. With only the plugin options (no sidecar) both features are treated as enabled, as in 0.1.0.

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
| `/v1/ingest/plugin/session` | session start / heartbeat / end (optional `user_ref`: `agent:<environment>/<name>`, see [Agent name and environment](#agent-name-and-environment)) |
| `/v1/ingest/plugin/prompt` | redacted prompt excerpt |
| `/v1/ingest/plugin/snapshot` | OpenSpec change snapshot |
| `/v1/ingest/plugin/sync/plan` | receives file manifest, returns `{"upload": [paths], "delete": [paths]}` |
| `/v1/ingest/plugin/sync/files` | receives file contents and deletions |

The plugin never follows redirects. 401 is reported as a rejected token and pauses delivery for about 15 minutes (a successful `setup` clears it; queued items are kept; queued items of a feature you disabled are dropped, not sent); 429, 5xx and network errors are queued and retried with backoff. Other 4xx responses are dropped.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Not configured` | run `/company-cost:setup` |
| Nothing is sent after setup | expected: enable `/company-cost:send-traces` and/or `/company-cost:send-openspec` |
| `409 unknown_project` on sync | the session `start` event did not arrive yet; it is queued and retried |
| `Managed keys: MISSING` | re-run `/company-cost:send-traces`, then restart |
| `token rejected` | get a new token, re-run `/company-cost:setup` |
| send-traces lists conflicting keys | review them; run `/company-cost:send-traces` and answer yes to override (or `company-cost send-traces on --force`) |
| `refusing plain http://` | use `https://` (or `--allow-insecure` for a trusted network) |
| Queue keeps growing | check URL, network, `status` last error |
| No cost events at the service | restart Claude Code; confirm the service accepts `<URL>/v1/otlp/v1/logs` |
| `python3` not found | install Python 3.9+ |

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). Licensed under [Apache-2.0](LICENSE).
