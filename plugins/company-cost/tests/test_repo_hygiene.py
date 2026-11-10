"""Repository hygiene checks: packaging, manifests, docs, forbidden strings."""
import json
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(PLUGIN))
SCRIPT = os.path.join(PLUGIN, "bin", "company-cost")

SKIP_DIRS = {".git", ".atl", "__pycache__", "tests"}
SKIP_FILES = {"CONTRIBUTING.md", "ci.yml", ".DS_Store", "icon.png"}
FORBIDDEN = ("acme", "192.168", "gitea", "pedro", "garcia", "unlicensed", "co-authored-by")
ALL_SKILLS = ("setup", "status", "uninstall", "send-traces", "send-openspec")
ALLOWED_USERCONFIG_KEYS = {"type", "title", "description", "required", "default", "sensitive",
                           "multiple", "min", "max"}


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def iter_text_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn in SKIP_FILES:
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    yield path, f.read()
            except (UnicodeDecodeError, OSError):
                continue


def script_version():
    with open(SCRIPT, "r", encoding="utf-8") as f:
        m = re.search(r'^VERSION = "([^"]+)"', f.read(), re.M)
    return m.group(1)


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return None
    out = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out


class ForbiddenContent(unittest.TestCase):
    def test_forbidden_strings(self):
        hits = []
        for path, text in iter_text_files():
            low = text.lower()
            for bad in FORBIDDEN:
                if bad in low:
                    hits.append("%s: %s" % (os.path.relpath(path, ROOT), bad))
        self.assertEqual(hits, [])

    def test_no_real_looking_tokens(self):
        pat = re.compile(r"hro_[A-Za-z0-9_-]{20,}")
        hits = [os.path.relpath(p, ROOT) for p, t in iter_text_files() if pat.search(t)]
        self.assertEqual(hits, [])


class Manifests(unittest.TestCase):
    def setUp(self):
        self.plugin = load(os.path.join(PLUGIN, ".claude-plugin", "plugin.json"))
        self.market = load(os.path.join(ROOT, ".claude-plugin", "marketplace.json"))

    def test_versions_equal(self):
        self.assertEqual(self.plugin["version"], script_version())
        for entry in self.market["plugins"]:
            if entry.get("name") == self.plugin["name"] and "version" in entry:
                self.assertEqual(entry["version"], script_version())

    def test_plugin_fields(self):
        self.assertEqual(self.plugin["name"], "company-cost")
        self.assertEqual(self.plugin["license"], "Apache-2.0")
        self.assertTrue(self.plugin["homepage"].startswith("https://"))
        icon = self.plugin["icon"]
        self.assertTrue(os.path.isfile(os.path.join(PLUGIN, icon)))

    def test_userconfig_keys(self):
        cfg = self.plugin["userConfig"]
        self.assertEqual(set(cfg), {"RAG_URL", "RAG_TOKEN", "AGENT_NAME"})
        for name, opt in cfg.items():
            for key in ("type", "title", "description"):
                self.assertIn(key, opt, name)
            self.assertLessEqual(set(opt), ALLOWED_USERCONFIG_KEYS, name)
        self.assertTrue(cfg["RAG_TOKEN"].get("sensitive"))
        agent = cfg["AGENT_NAME"]
        self.assertEqual(agent["type"], "string")
        self.assertFalse(agent.get("required"))
        self.assertFalse(agent.get("sensitive"))

    def test_marketplace(self):
        self.assertEqual(self.market["name"], "ounity-plugins")
        self.assertIn("name", self.market["owner"])
        self.assertTrue(self.market["plugins"])
        for entry in self.market["plugins"]:
            self.assertIn("name", entry)
            self.assertNotIn("icon", entry)
            self.assertTrue(entry["source"].startswith("./"))
            self.assertTrue(os.path.isdir(os.path.join(ROOT, entry["source"])))

    def test_all_json_valid(self):
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in {".git", ".atl"}]
            for fn in filenames:
                if fn.endswith(".json"):
                    load(os.path.join(dirpath, fn))


class Hooks(unittest.TestCase):
    def test_hooks(self):
        hooks = load(os.path.join(PLUGIN, "hooks", "hooks.json"))["hooks"]
        expected = {"SessionStart": 15, "UserPromptSubmit": 8, "Stop": 8, "SessionEnd": 5}
        self.assertEqual(set(hooks), set(expected))
        for event, timeout in expected.items():
            cmds = [h for group in hooks[event] for h in group["hooks"]]
            self.assertEqual(len(cmds), 1)
            h = cmds[0]
            self.assertEqual(h["type"], "command")
            self.assertEqual(h["timeout"], timeout)
            self.assertIn('"${CLAUDE_PLUGIN_ROOT}/bin/company-cost"', h["command"])
            self.assertTrue(h["command"].endswith("hook " + event))

    def test_timeouts_exceed_http_budget(self):
        with open(SCRIPT, "r", encoding="utf-8") as f:
            src = f.read()

        def const(name):
            return float(re.search(r"^%s = ([0-9.]+)" % name, src, re.M).group(1))
        hooks = load(os.path.join(PLUGIN, "hooks", "hooks.json"))["hooks"]

        def timeout(ev):
            return hooks[ev][0]["hooks"][0]["timeout"]
        # worst case = pre-send deadline (git + openspec) + HTTP sends + queue flush
        self.assertGreater(timeout("SessionStart"), const("DEADLINE_SESSION_START")
                           + const("TIMEOUT_SESSION_START") + const("QUEUE_FLUSH_BUDGET"))
        self.assertGreater(timeout("UserPromptSubmit"), const("DEADLINE_USER_PROMPT")
                           + const("TIMEOUT_HEARTBEAT") + const("TIMEOUT_PROMPT"))
        self.assertGreater(timeout("Stop"), const("DEADLINE_STOP")
                           + const("TIMEOUT_HEARTBEAT") + const("QUEUE_FLUSH_BUDGET"))
        self.assertGreater(timeout("SessionEnd"), const("DEADLINE_SESSION_END") + const("TIMEOUT_SESSION_END"))


class Skills(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(PLUGIN, "skills", name, "SKILL.md"), "r", encoding="utf-8") as f:
            return f.read()

    def test_frontmatter(self):
        for name in ALL_SKILLS:
            fm = frontmatter(self.read(name))
            self.assertIsNotNone(fm, name)
            self.assertEqual(fm["name"], name)
            self.assertTrue(fm["description"])
        for name in ("setup", "uninstall", "send-traces", "send-openspec"):
            self.assertEqual(frontmatter(self.read(name))["disable-model-invocation"], "true")

    def test_skill_dirs_match(self):
        dirs = sorted(d for d in os.listdir(os.path.join(PLUGIN, "skills"))
                      if os.path.isdir(os.path.join(PLUGIN, "skills", d)))
        self.assertEqual(dirs, sorted(ALL_SKILLS))

    def test_feature_skills_arguments_and_restart(self):
        for name in ("send-traces", "send-openspec"):
            text = self.read(name)
            self.assertIn("$ARGUMENTS", text, name)
            self.assertIn("on|off", text, name)
            self.assertIn("Bash(company-cost %s:*)" % name, text)
            self.assertIn('Bash(python3 "${CLAUDE_PLUGIN_ROOT}/bin/company-cost" %s:*)' % name, text)
            self.assertIn("NEVER print", text, name)
        self.assertIn("Restart Claude Code", self.read("send-traces"))

    def test_feature_skills_validate_arguments(self):
        for name in ("send-traces", "send-openspec"):
            text = self.read(name)
            self.assertIn("exactly `on`, `off` or `status`", text, name)
            self.assertIn("stop and ask the user", text, name)
            self.assertIn("Never take `--force`", text, name)
            for block in re.findall(r"```bash\n(.*?)```", text, re.S):
                self.assertNotIn("$ARGUMENTS", block, name)
        self.assertIn("never comes from `$ARGUMENTS`", self.read("send-traces"))

    def test_no_bare_python_allowed_tools(self):
        for name in ALL_SKILLS:
            self.assertNotIn("python3:*", self.read(name), name)

    def test_setup_uses_stdin_token(self):
        text = self.read("setup")
        self.assertIn("--token-stdin", text)
        self.assertIsNone(re.search(r"--token(?![-\w])", text.replace("`--token`", "")))

    def test_setup_agent_name_quoting_guidance(self):
        text = self.read("setup")
        self.assertIn("--agent='<NAME>'", text)  # single-quoted, attached with `=`
        self.assertIn("A-Z a-z 0-9", text)  # allowed character set spelled out
        self.assertIn("refuse", text.lower())
        self.assertIn("optional", text.lower())
        self.assertNotIn('--agent "', text)  # never double-quoted (shell expansion)

    def test_uninstall_order(self):
        text = self.read("uninstall")
        positions = [text.index(s) for s in (
            "claude plugin uninstall company-cost@ounity-plugins",
            "claude plugin marketplace remove ounity-plugins",
            "Restart Claude Code",
            "Revoke the token")]
        self.assertEqual(positions, sorted(positions))


class RepoFiles(unittest.TestCase):
    def test_required_files(self):
        for rel in ("LICENSE", "README.md", "SECURITY.md", "CHANGELOG.md", "CONTRIBUTING.md",
                    "CODE_OF_CONDUCT.md", ".gitignore", ".github/workflows/ci.yml",
                    ".github/dependabot.yml", ".github/pull_request_template.md"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, rel)), rel)

    def test_license_is_apache(self):
        with open(os.path.join(ROOT, "LICENSE"), "r", encoding="utf-8") as f:
            self.assertIn("Apache License", f.read(500))

    def test_ci_actions_pinned_by_sha(self):
        with open(os.path.join(ROOT, ".github", "workflows", "ci.yml"), "r", encoding="utf-8") as f:
            uses = re.findall(r"uses:\s*(\S+)", f.read())
        self.assertTrue(uses)
        for u in uses:
            self.assertRegex(u, r"@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
