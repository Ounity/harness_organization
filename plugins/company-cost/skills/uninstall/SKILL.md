---
name: uninstall
description: Remove company-cost settings and local data, then list the remaining uninstall steps.
disable-model-invocation: true
allowed-tools: Bash(company-cost uninstall), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/bin/company-cost" uninstall)
---

# company-cost uninstall

This is step 1 of the uninstall order. Run:

```bash
company-cost uninstall
```

If `company-cost` is not found on PATH, use:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/bin/company-cost" uninstall
```

It restores any previous values in the `env` block of `settings.json`, removes the plugin's keys, the sidecar file, the backup and the local data directory.

Then list the remaining steps to the user, in this order:

1. `/company-cost:uninstall` (done)
2. `claude plugin uninstall company-cost@ounity-plugins`
3. `claude plugin marketplace remove ounity-plugins`
4. Restart Claude Code
5. Revoke the token at the ingest service

Use the marketplace name printed by the command if it differs from `ounity-plugins`.
