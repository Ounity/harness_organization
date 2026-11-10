---
name: status
description: Show company-cost configuration and delivery status (URL, last success, errors, queue).
allowed-tools: Bash(company-cost status), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/bin/company-cost" status)
---

# company-cost status

Run:

```bash
company-cost status
```

If `company-cost` is not found on PATH, use:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/bin/company-cost" status
```

The command locates the data directory used by the hooks on its own (newest `company-cost*` directory under the Claude config dir), so run it as shown without setting variables.

Summarize the output for the user: configured or not, whether managed keys are present, last successful delivery, last error, queue size. NEVER print or ask for the token; the command already hides it.

If it says "Not configured", suggest `/company-cost:setup`. If the last error is a rejected token, suggest re-running `/company-cost:setup` with a new token.
