# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/). Releases are tagged `vX.Y.Z`.

## [0.2.0] - 2026-11-10

### Added
- Two independent, persistent feature switches: **traces** (OTEL cost export, session events, redacted prompt excerpt) and **openspec** (change inference, OpenSpec file mirror, snapshot).
- Skills `/company-cost:send-traces` and `/company-cost:send-openspec`, and CLI `company-cost send-traces [on|off|status]` / `company-cost send-openspec [on|off|status]` (`on` is the default).
- Sidecar `features` object; `status` and `doctor` show each feature and what it sends.
- Hook gating: with both features off every hook is a silent no-op; with traces only, no change fields are sent and no `openspec` command, sync or snapshot runs.
- openspec-only mode sends exactly one session `start` event per session as project registration (the server rejects `sync/plan` with `409 unknown_project` otherwise); no heartbeat, end or prompt excerpt.

### Changed
- BREAKING-ISH: `setup` now only stores the connection (URL, token) and no longer writes `settings.json` or enables anything. It prints the next steps. Run `/company-cost:send-traces` to get the old `setup` behavior (same 9 keys, backup, `--force` rule, restore on `off`/uninstall).
- Queued items that belong to a disabled feature are dropped instead of sent.
- `send-traces off` keeps URL and token in the sidecar; `uninstall` still removes everything.

- A missing, empty or unparseable sidecar (for example plugin-option-only configuration) now means both features OFF: nothing is sent without an explicit opt-in.
- `send-traces off` persists the flag first, so a broken `settings.json` can no longer leave traces on (exit 1 tells you the OTEL keys remain). It restores `env` exactly, keeping an original empty object.
- Queued items are sanitized when flushed: `change_name` / `change_source` are stripped once openspec is off.
- `send-traces on` writes `settings.json` before flipping the flag, and sidecar changes are serialized with a lock.
- The `send-traces` / `send-openspec` skills only accept the exact words `on`, `off` or `status` and never take `--force` from their argument; the conflict hint points to `send-traces on --force` after an explicit yes.

### Migration from 0.1.0
Existing installs keep working: a sidecar with `url` and without a `features` key (written by 0.1.0) is treated as both features enabled. Use `send-traces off` / `send-openspec off` to narrow it.

## [0.1.0] - 2026-11-10

Initial public release.

### Added
- `company-cost` plugin: session, heartbeat, prompt-excerpt and OpenSpec sync reporting through Claude Code hooks.
- Skills `/company-cost:setup`, `/company-cost:status`, `/company-cost:uninstall`.
- OTLP telemetry configuration written to user settings, with reversible uninstall.
- Offline queue with backoff for failed deliveries.

### Changed (stricter than the private predecessor)
- Plain `http://` to a non-local host is refused unless `--allow-insecure` is passed.
- HTTP 429 is retried (honoring `Retry-After`) instead of dropped.
- HTTP 401 is surfaced in `status` as a rejected token.
- No dependence on `fcntl`; locking uses portable exclusive file creation.
- Redirects are never followed, so the bearer token does not leave the configured origin.

### Security and robustness
- `settings.json` is always written with mode `existing & 600` (token protection).
- Redaction: JSON-quoted secrets, `Basic` credentials, prefix keys without word boundary, zero-width characters stripped and NFKC normalization before matching.
- Redaction also covers identifiers containing a secret word (`DB_PASSWORD=...`, `GITHUB_TOKEN=...`) and quoted values with spaces; secrets are redacted over an 8000-character window before the excerpt is cut.
- Heartbeats are not queued while offline; state/session writes are skipped when their lock cannot be taken; stale lock takeover re-checks the holder, and long holders refresh the lock.
- Sync never forwards server `delete` entries for files that exist locally but were skipped, nor any delete when the manifest was truncated; a failed sync is not recorded as successful.
- Skills' `allowed-tools` are scoped to the exact plugin commands; long kebab-case change names are accepted.
- Uninstall only removes exact plugin data dirs (never other plugins' dirs), ignores symlinks, only trusts the 9 known env keys and keeps hand-edited values.
- Lock takeover is race-safe and owner-checked; orphan queue claims are flushed and counted; a restore without the lock no longer deletes the claim.
- HTTP 401 pauses delivery for ~15 minutes until a new `setup`; `SessionEnd` skips `openspec`; per-event wall-clock budgets and a 60 s inference cache bound hook latency; `SessionEnd` hook timeout raised to 5 s.
- `status`/`doctor` locate the hooks' data directory; tokens must be ASCII.

### Not supported
- Native Windows (use WSL).
