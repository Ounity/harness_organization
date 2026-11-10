# Contributing

Thanks for helping. A few rules keep the project small and safe.

## Rules

- **Conventional commits** (`feat:`, `fix:`, `docs:`, `test:`, `chore:`...).
- **No AI attribution** in commits, PRs or files (no `Co-Authored-By` lines for tools).
- **Tests required** for behavior changes. Bug fixes need a regression test.
- **Stdlib only, Python 3.9+**: `bin/company-cost` must not import third-party packages or use syntax newer than 3.9.
- Never log or print the token. Never put real tokens, hosts or personal names in the repo; use `https://ingest.example.com` and `hro_REDACTED`.

## Before opening a PR

```bash
cd plugins/company-cost && python3 -m unittest discover
cd ../.. && claude plugin validate plugins/company-cost && claude plugin validate .
```

## Releases

Bump `VERSION` in `plugins/company-cost/bin/company-cost`, `version` in `plugin.json` and in the marketplace entry (a test enforces they match), update `CHANGELOG.md`, then tag `vX.Y.Z`. The marketplace currently uses a relative plugin source; it may later pin `{"source": "github", "repo": "Ounity/harness_organization", "ref": "vX.Y.Z"}`.

## Security

Report vulnerabilities privately; see `SECURITY.md`.
