# Security policy

## Reporting a vulnerability

Please do NOT open a public issue. Use GitHub's private reporting:
**Security tab > Report a vulnerability** on https://github.com/Ounity/harness_organization.

Include the plugin version (`company-cost status` prints it), your OS, and steps to reproduce.

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Scope

In scope:

- Token handling (storage, logging, exposure in process lists or output)
- The settings merge performed by `setup` / `uninstall` (data loss, wrong file modes, symlink or path issues)
- Redaction bypass in prompt excerpts
- Contents of the local queue and data directory
- Sending credentials to an unintended host (redirects, non-HTTPS endpoints)

Out of scope: vulnerabilities in the ingest service you point the plugin at, and in Claude Code itself.

## Known, documented limitations

The token is stored in clear text in `~/.claude/settings.json` and in a mode-600 sidecar file. `setup` always rewrites `settings.json` with owner-only permissions (group/other bits are removed). See the README.
