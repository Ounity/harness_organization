# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/). Releases are tagged `vX.Y.Z`.

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
