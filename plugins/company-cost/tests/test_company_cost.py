"""Unit tests for bin/company-cost (stdlib unittest, no network)."""
import ast
import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from importlib.machinery import SourceFileLoader
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(PLUGIN_ROOT, "bin", "company-cost")


def load_module():
    sys.dont_write_bytecode = True
    loader = SourceFileLoader("company_cost_under_test", SCRIPT)
    spec = importlib.util.spec_from_loader("company_cost_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


cc = load_module()

ENV_KEYS = ("CLAUDE_CONFIG_DIR", "CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_OPTION_RAG_URL",
            "CLAUDE_PLUGIN_OPTION_RAG_TOKEN", "COMPANY_COST_NO_PROMPTS", "COMPANY_COST_NO_SYNC",
            "CLAUDE_PLUGIN_OPTION_AGENT_NAME")


def slurp(path):
    with open(path) as f:
        return f.read()


def fake_token():
    return "hro_" + ("Ab3dE6gH9k" * 4) + "xyz"  # 4 + 43 chars


class EnvCase(unittest.TestCase):
    """Isolated config dir, data dir and environment."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cc-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = os.path.join(self.tmp, "cfg")
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(self.cfg)
        patcher = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": self.cfg, "CLAUDE_PLUGIN_DATA": self.data})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ENV_KEYS[2:]:
            os.environ.pop(k, None)
        self.settings = os.path.join(self.cfg, "settings.json")
        self.sidecar = os.path.join(self.cfg, "settings.company-cost.json")
        self.bak = os.path.join(self.cfg, "settings.json.company-cost.bak")

    def write_settings(self, obj, mode=None):
        with open(self.settings, "w") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj))
        if mode:
            os.chmod(self.settings, mode)

    def read_settings(self):
        with open(self.settings) as f:
            return json.load(f)

    def run_cli(self, argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin)), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            try:
                rc = cc.main(argv)
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()

    def setup_only(self, url="https://ingest.example.com", token=None, extra=()):
        token = token or fake_token()
        rc, out, err = self.run_cli(["setup", "--url", url, "--token-stdin"] + list(extra), stdin=token + "\n")
        self.assertEqual(rc, 0, err)
        return token

    def setup_ok(self, url="https://ingest.example.com", token=None, extra=()):
        """Connection + `send-traces on`: the 0.1.0 outcome (managed keys in settings.json)."""
        token = self.setup_only(url, token)
        rc, out, err = self.run_cli(["send-traces", "on"] + list(extra))
        self.assertEqual(rc, 0, err)
        return token

    def read_side(self):
        with open(self.sidecar) as f:
            return json.load(f)

    def all_data_text(self):
        chunks = []
        for base, _, files in os.walk(self.data):
            for fn in files:
                with open(os.path.join(base, fn), "rb") as f:
                    chunks.append(f.read().decode("utf-8", "replace"))
        return "\n".join(chunks)


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com"] + list(args),
                          cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True).stdout.decode().strip()


# ---------------------------------------------------------------------------
class TestIdentityAndVersion(unittest.TestCase):
    def test_version_constant(self):
        self.assertEqual(cc.VERSION, "0.3.0")

    def test_version_consistency_with_plugin_json(self):
        pj = os.path.join(PLUGIN_ROOT, ".claude-plugin", "plugin.json")
        if not os.path.exists(pj):
            self.skipTest("plugin.json not present")
        with open(pj) as f:
            self.assertEqual(json.load(f)["version"], cc.VERSION)

    def test_only_stdlib_imports(self):
        with open(SCRIPT) as f:
            tree = ast.parse(f.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                names.add((node.module or "").split(".")[0])
        stdlib = getattr(sys, "stdlib_module_names", None)
        if stdlib is None:
            stdlib = {"argparse", "getpass", "hashlib", "json", "os", "re", "shutil", "subprocess",
                      "sys", "time", "urllib"}
        self.assertTrue(names)
        self.assertEqual(sorted(n for n in names if n not in stdlib), [])

    def test_no_urlopen_outside_custom_opener(self):
        with open(SCRIPT) as f:
            src = f.read()
        self.assertNotIn("urlopen(", src)
        self.assertNotIn("shell=True", src)

    def test_no_py310_syntax(self):
        with open(SCRIPT) as f:
            tree = ast.parse(f.read(), feature_version=(3, 9))
        for node in ast.walk(tree):
            self.assertFalse(type(node).__name__ == "Match")


# ---------------------------------------------------------------------------
class TestRemoteNormalization(unittest.TestCase):
    def test_R01_remote_normalization_table(self):
        table = {
            "https://github.com/Owner/Repo.git": "github.com/Owner/Repo",
            "git@github.com:Owner/Repo.git": "github.com/Owner/Repo",
            "ssh://git@Host.Example.com:2222/team/app.git": "host.example.com/team/app",
            "https://user:tok@HOST.com:8443/a/b/": "host.com/a/b",
            "ssh://host.example.com/a/b": "host.example.com/a/b",
            "host.example.com:a/b.git": "host.example.com/a/b",
        }
        for raw, want in table.items():
            self.assertEqual(cc.normalize_remote(raw), want, raw)

    def test_R01_remote_normalization_invalid(self):
        for raw in ("", "   ", "not a url", "/local/path", "https://host", None):
            self.assertIsNone(cc.normalize_remote(raw), raw)

    def test_R01_never_keeps_credentials(self):
        n = cc.normalize_remote("https://user:s3cret@github.com/o/r.git")
        self.assertNotIn("s3cret", n)
        self.assertNotIn("@", n)


class TestProjectIdentity(unittest.TestCase):
    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git not available")
        self.tmp = tempfile.mkdtemp(prefix="cc-id-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def repo(self, name="proj"):
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        git(d, "init", "-q", "-b", "main")
        return d

    def test_R01_origin_remote_identity(self):
        d = self.repo()
        git(d, "remote", "add", "other", "git@x.example.com:a/other.git")
        git(d, "remote", "add", "origin", "https://u:p@git.example.com/team/app.git")
        ident = cc.project_identity(d)
        self.assertEqual(ident["key"], "git.example.com/team/app")
        self.assertEqual(ident["name"], "app")
        self.assertEqual(ident["branch"], "main")
        self.assertNotIn("p@", json.dumps(ident))

    def test_R01_first_remote_when_no_origin(self):
        d = self.repo()
        git(d, "remote", "add", "up", "git@x.example.com:a/b.git")
        self.assertEqual(cc.project_identity(d)["key"], "x.example.com/a/b")

    def test_R01_root_commit_fallback(self):
        d = self.repo("folder-name")
        git(d, "commit", "-q", "--allow-empty", "-m", "init")
        root = git(d, "rev-list", "--max-parents=0", "HEAD")
        ident = cc.project_identity(d)
        self.assertEqual(ident["key"], "git-root-" + root[:16])
        self.assertEqual(ident["name"], "folder-name")

    def test_R01_folder_fallback_without_git(self):
        d = os.path.join(self.tmp, "plain")
        os.makedirs(d)
        with mock.patch.object(cc, "_run", return_value=None):
            ident = cc.project_identity(d)
        self.assertEqual(ident["key"], "plain")
        self.assertEqual(ident["branch"], "")

    def test_R01_detached_head(self):
        d = self.repo()
        git(d, "commit", "-q", "--allow-empty", "-m", "init")
        git(d, "checkout", "-q", "--detach")
        sha = git(d, "rev-parse", "--short=7", "HEAD")
        self.assertEqual(cc.project_identity(d)["branch"], "detached-" + sha)

    def test_cwd_hash(self):
        h = cc.cwd_hash("/a/b")
        self.assertEqual(len(h), 16)
        int(h, 16)


# ---------------------------------------------------------------------------
class TestRedaction(unittest.TestCase):
    def assertRedacted(self, text, secret):
        out = cc.redact_excerpt(text)
        self.assertNotIn(secret, out)
        self.assertIn("[REDACTED]", out)
        self.assertLessEqual(len(out), 200)

    def test_R11_families(self):
        cases = {
            "pem": ("key -----BEGIN RSA PRIVATE KEY-----\nMIIEvQIBADANBgkq\n-----END RSA PRIVATE KEY----- ok", "MIIEvQ"),
            "url_creds": ("clone https://deploy:hunter22@host.example.com/x", "hunter22"),
            "slack": ("xoxb-1234567890-abcdefghij", "1234567890"),
            "google": ("AIza" + "A1b2C3d4E5" * 3, "A1b2C3d4E5"),
            "gh_pat": ("github_pat_" + "Ab12Cd34Ef56Gh78", "Ab12Cd34Ef56Gh78"),
            "sk": ("use sk-abcdef123456 now", "abcdef123456"),
            "ghp": ("ghp_" + "a1B2c3D4e5F6g7H8i9", "a1B2c3D4e5F6g7H8i9"),
            "aws": ("AKIA" + "ABCDEFGH12345678", "ABCDEFGH12345678"),
            "bearer": ("Authorization: Bearer abc.def-ghi_jkl", "abc.def-ghi_jkl"),
            "kv_eq": ("password=hunter2", "hunter2"),
            "kv_colon": ("api_key: s3cr3tvalue", "s3cr3tvalue"),
            "jwt": ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc", "eyJhbGciOiJIUzI1NiJ9"),
            "long_blob": ("blob " + "Qw3rTy0pAs" * 4, "Qw3rTy0pAs"),
            "hro": ("my token " + fake_token(), "Ab3dE6gH9k"),
        }
        for name, (text, secret) in cases.items():
            with self.subTest(name):
                self.assertRedacted(text, secret)

    def test_R11_hro_plus_43_chars(self):
        out = cc.redact_excerpt("here: " + fake_token())
        self.assertEqual(out, "here: [REDACTED]")

    def test_R11_password_is_hunter2(self):
        out = cc.redact_excerpt("the password is hunter2 ok")
        self.assertNotIn("hunter2", out)

    def test_R11_pem_without_end(self):
        out = cc.redact_excerpt("x -----BEGIN PRIVATE KEY-----\nAAAA\nBBBB trailing")
        self.assertNotIn("AAAA", out)
        self.assertNotIn("trailing", out)

    def test_R11_secret_straddling_the_cut(self):
        text = "a " * 75 + "sk-" + "B" * 40 + " tail"
        out = cc.redact_excerpt(text)
        self.assertNotIn("sk-B", out)
        self.assertNotIn("BBBBB", out)
        self.assertLessEqual(len(out), 200)

    def test_R11_secret_straddling_source_cut(self):
        text = "x" * 1 + " " + "y " * 990 + "token=" + "Z" * 50
        out = cc.redact_excerpt(text)
        self.assertNotIn("ZZZZ", out)

    def test_R11_length_cap(self):
        self.assertLessEqual(len(cc.redact_excerpt("word " * 1000)), 200)
        self.assertEqual(cc.redact_excerpt(""), "")
        self.assertEqual(cc.redact_excerpt(None), "")

    def test_R11_control_chars_and_whitespace(self):
        out = cc.redact_excerpt("a\x00b\x1b[31m\n\n\tc\x7fd   e")
        self.assertNotRegex(out, r"[\x00-\x1f\x7f]")
        self.assertNotIn("  ", out)

    def test_R11_secret_split_by_control_char_not_leaked(self):
        out = cc.redact_excerpt("password\x00is\x00hunter2")
        self.assertNotIn("hunter2", out)

    def test_R11_unicode(self):
        out = cc.redact_excerpt("hola ñandú 中文 \U0001f600 password=über")
        self.assertIn("中文", out)
        self.assertNotIn("über", out)

    def test_R11_performance_2000_chars(self):
        samples = [
            "a" * 5000, "word " * 1000, "-" * 3000, "/" * 3000, "=" * 3000,
            "ab12" * 1000, "http://" * 400, " " * 3000,
            "-----BEGIN KEY-----" * 150, "Bearer " * 300, "password " * 300,
        ]
        for s in samples:
            t0 = time.perf_counter()
            cc.redact_excerpt(s)
            dt = time.perf_counter() - t0
            self.assertLess(dt, 0.05, "%r... took %.3fs" % (s[:12], dt))

    def test_R11_patterns_order_preserved(self):
        src = [p.pattern for p in cc.REDACTION_PATTERNS[:12]]
        self.assertTrue(src[0].startswith("-----BEGIN"))
        self.assertTrue(src[-1].startswith("[A-Za-z0-9+/_=]{32,}"))


# ---------------------------------------------------------------------------
class TestValidation(unittest.TestCase):
    def test_R02_url_matrix(self):
        ok = {
            "https://x": "https://x",
            "https://ingest.example.com/": "https://ingest.example.com",
            "http://localhost:8000": "http://localhost:8000",
            "http://127.0.0.1:1/": "http://127.0.0.1:1",
            "http://[::1]": "http://[::1]",
            "https://ingest.example.com/base/": "https://ingest.example.com/base",
        }
        for raw, want in ok.items():
            self.assertEqual(cc.validate_url(raw), (want, None), raw)

    def test_R02_url_rejections(self):
        for raw in ("http://evil.com", "ingest.example.com", "ftp://x", "https://", "", "https://a b",
                    "https://u:p@host", "https://h/?q=1", "http://localhost.evil.com", None):
            clean, err = cc.validate_url(raw)
            self.assertIsNone(clean, raw)
            self.assertTrue(err)

    def test_R02_allow_insecure(self):
        self.assertEqual(cc.validate_url("http://evil.com", True), ("http://evil.com", None))

    def test_R02_token_validation(self):
        self.assertIsNone(cc.validate_token("abc"))
        for bad in ("", "a b", "a,b", "a\tb", None):
            self.assertTrue(cc.validate_token(bad), bad)


# ---------------------------------------------------------------------------
class TestChangeInference(EnvCase):
    def test_command_parsing(self):
        table = {
            "/opsx:apply my-change": "my-change",
            "/opsx:explore   foo.bar now": "foo.bar",
            "  /opsx:new add_thing\nmore": "add_thing",
            "/openspec apply my-change": "my-change",
            "/openspec-apply my-change": "my-change",
            "/openspec my-change": "my-change",
            "/opsx:apply": "",
            "hello /opsx:apply x": "",
            "/opsx:apply ../../etc": "",
            "/opsx:apply --force": "",
            "": "",
        }
        for raw, want in table.items():
            self.assertEqual(cc.parse_command(raw), want, raw)
        self.assertEqual(cc.parse_command(None), "")

    def test_command_cache_and_priority(self):
        ident = {"toplevel": "", "branch": ""}
        with mock.patch.object(cc, "openspec_list", return_value={"root": None, "changes": []}):
            self.assertEqual(cc.resolve_change("s1", self.tmp, ident, "/opsx:apply abc"), ("abc", "command"))
            self.assertEqual(cc.resolve_change("s1", self.tmp, ident, "plain"), ("abc", "command"))
            self.assertEqual(cc.resolve_change("s2", self.tmp, ident, "plain"), ("", ""))

    def test_session_cache_capped_at_50(self):
        for i in range(60):
            cc.session_cache_set("sid%d" % i, "c%d" % i)
        with open(os.path.join(self.data, "sessions.json")) as f:
            cache = json.load(f)
        self.assertEqual(len(cache), 50)
        self.assertIn("sid59", cache)
        self.assertNotIn("sid0", cache)

    def test_branch_beats_recent(self):
        root = os.path.join(self.tmp, "repo", "openspec")
        os.makedirs(os.path.join(root, "changes", "feat-x"))
        listing = {"root": None, "changes": [{"name": "other", "lastModified": "2030-01-01", "completedTasks": 0,
                                              "totalTasks": 0, "status": ""}]}
        ident = {"toplevel": os.path.join(self.tmp, "repo"), "branch": "feat-x"}
        with mock.patch.object(cc, "openspec_list", return_value=listing):
            self.assertEqual(cc.resolve_change("s", self.tmp, ident), ("feat-x", "branch"))
            ident["branch"] = "nomatch"
            self.assertEqual(cc.resolve_change("s", self.tmp, ident), ("other", "recent"))

    def test_recent_picks_max_last_modified(self):
        listing = {"root": None, "changes": [
            {"name": "old", "lastModified": "2024-01-01T00:00:00Z"},
            {"name": "new", "lastModified": "2025-06-01T00:00:00Z"},
            {"name": "undated", "lastModified": ""}]}
        with mock.patch.object(cc, "openspec_list", return_value=listing):
            self.assertEqual(cc.resolve_change("s", self.tmp, {"toplevel": "", "branch": ""}), ("new", "recent"))

    def test_tolerant_openspec_parsing(self):
        for garbage in ("", "garbage", "[1,2,3]", "{\"changes\": 5}", "null", "{\"changes\":[null,{\"x\":1}]}"):
            self.assertEqual(cc.parse_openspec_list(garbage), {"root": None, "changes": []}, garbage)
        good = cc.parse_openspec_list(json.dumps({"root": {"path": "/r"}, "changes": [
            {"name": "a", "completedTasks": 2, "totalTasks": 5, "status": "in-progress",
             "lastModified": "2025-01-01T00:00:00Z"}]}))
        self.assertEqual(good["root"], "/r")
        self.assertEqual(good["changes"][0]["totalTasks"], 5)
        listed = cc.parse_openspec_list(json.dumps([{"name": "z"}]))
        self.assertEqual(listed["changes"][0]["name"], "z")


# ---------------------------------------------------------------------------
class TestFileCollection(EnvCase):
    def make_root(self):
        top = os.path.join(self.tmp, "repo")
        root = os.path.join(top, "openspec")
        os.makedirs(os.path.join(root, "changes", "c1"))
        os.makedirs(os.path.join(root, "changes", "archive", "old"))
        os.makedirs(os.path.join(root, "specs", "s1"))
        return top, root

    def put(self, path, content="x", mode="w"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode) as f:
            f.write(content)

    def test_collect_rules(self):
        top, root = self.make_root()
        self.put(os.path.join(root, "changes", "c1", "proposal.md"), "héllo")
        self.put(os.path.join(root, "changes", "archive", "old", "p.md"), "archived")
        self.put(os.path.join(root, "specs", "s1", "spec.md"), "spec")
        self.put(os.path.join(root, "specs", "s1", "notes.txt"), "nope")
        self.put(os.path.join(root, "big.md"), "a" * (cc.MAX_FILE_BYTES + 1))
        self.put(os.path.join(root, "bad.md"), b"\xff\xfe\xfa", "wb")
        outside = os.path.join(self.tmp, "outside.md")
        self.put(outside, "secret outside")
        os.symlink(outside, os.path.join(root, "link.md"))
        outdir = os.path.join(self.tmp, "outdir")
        self.put(os.path.join(outdir, "inner.md"), "inner")
        os.symlink(outdir, os.path.join(root, "linkdir"))
        files, skipped = cc.collect_files(root)
        paths = [f["path"] for f in files]
        self.assertEqual(paths, ["changes/c1/proposal.md", "specs/s1/spec.md"])
        self.assertEqual(skipped, 3)  # big, non-utf8, symlinked file
        import hashlib
        self.assertEqual(files[0]["sha256"], hashlib.sha256("héllo".encode("utf-8")).hexdigest())
        self.assertEqual(files[0]["content"], "héllo")

    def test_manifest_cap(self):
        top, root = self.make_root()
        for i in range(cc.MAX_MANIFEST + 3):
            self.put(os.path.join(root, "specs", "s1", "f%05d.md" % i), "x")
        files, skipped = cc.collect_files(root)
        self.assertEqual(len(files), cc.MAX_MANIFEST)
        self.assertEqual(skipped, 3)

    def test_root_resolution(self):
        top, root = self.make_root()
        git_top = top
        self.assertEqual(cc.resolve_openspec_root(top, git_top), (root, 0))

    def test_specs_dir_fallback(self):
        top = os.path.join(self.tmp, "repo2")
        os.makedirs(os.path.join(top, "specs", "changes"))
        self.assertEqual(cc.resolve_openspec_root(top, top)[0], os.path.join(top, "specs"))

    def test_symlinked_root_refused(self):
        top = os.path.join(self.tmp, "repo3")
        real = os.path.join(self.tmp, "elsewhere", "openspec")
        os.makedirs(os.path.join(real, "changes"))
        os.makedirs(top)
        os.symlink(real, os.path.join(top, "openspec"))
        root, skipped = cc.resolve_openspec_root(top, top)
        self.assertIsNone(root)
        self.assertEqual(skipped, 1)

    def test_cli_root_outside_git_toplevel_refused(self):
        top = os.path.join(self.tmp, "repo4")
        os.makedirs(top)
        outside = os.path.join(self.tmp, "other", "openspec")
        os.makedirs(os.path.join(outside, "changes"))
        root, skipped = cc.resolve_openspec_root(top, top, cli_root=outside)
        self.assertIsNone(root)
        self.assertEqual(skipped, 1)

    def test_batches_split_by_count(self):
        files = [{"path": "f%d.md" % i, "content": "x"} for i in range(120)]
        batches = cc.plan_batches(files, [])
        self.assertEqual([len(b["files"]) for b in batches], [50, 50, 20])

    def test_batches_split_by_bytes_and_delete_first_only(self):
        files = [{"path": "f%d.md" % i, "content": "é" * 300000} for i in range(4)]  # 600000 bytes each
        batches = cc.plan_batches(files, ["gone1.md", "gone2.md"])
        self.assertEqual([len(b["files"]) for b in batches], [2, 2])
        self.assertEqual(batches[0]["delete"], ["gone1.md", "gone2.md"])
        self.assertEqual(batches[1]["delete"], [])
        for b in batches:
            self.assertLessEqual(sum(len(f["content"].encode("utf-8")) for f in b["files"]), cc.BATCH_BYTES)

    def test_delete_only_batch_and_cap(self):
        batches = cc.plan_batches([], ["d%d.md" % i for i in range(700)])
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]["delete"]), 500)
        self.assertEqual(cc.plan_batches([], []), [])

    def test_sync_lock(self):
        cc.ensure_data_dir()
        lock = cc.acquire_sync_lock("abc")
        self.assertIsNotNone(lock)
        self.assertIsNone(cc.acquire_sync_lock("abc"))
        lock.release()
        again = cc.acquire_sync_lock("abc")
        self.assertIsNotNone(again)
        again.held = False  # simulate crash: lock file stays
        old = time.time() - 700
        os.utime(again.path, (old, old))
        taken = cc.acquire_sync_lock("abc")
        self.assertIsNotNone(taken)
        taken.release()
        self.assertFalse(os.path.exists(taken.path))


# ---------------------------------------------------------------------------
class TestQueue(EnvCase):
    def lines(self):
        with open(os.path.join(self.data, "queue.jsonl"), "rb") as f:
            return [json.loads(x) for x in f.read().split(b"\n") if x]

    def test_R05_item_cap_oldest_dropped(self):
        for i in range(cc.QUEUE_MAX_ITEMS + 100):
            cc.queue_append("session", {"n": i})
        items = self.lines()
        self.assertEqual(len(items), cc.QUEUE_MAX_ITEMS)
        self.assertEqual(items[0]["payload"]["n"], 100)
        self.assertEqual(items[-1]["payload"]["n"], cc.QUEUE_MAX_ITEMS + 99)

    def test_R05_byte_cap_oldest_dropped(self):
        for i in range(300):
            cc.queue_append("prompt", {"n": i, "pad": "x" * 2000})
        path = os.path.join(self.data, "queue.jsonl")
        self.assertLessEqual(os.path.getsize(path), cc.QUEUE_MAX_BYTES)
        items = self.lines()
        self.assertEqual(items[-1]["payload"]["n"], 299)
        self.assertGreater(items[0]["payload"]["n"], 0)
        self.assertEqual([i["payload"]["n"] for i in items], sorted(i["payload"]["n"] for i in items))

    def test_R05_concurrent_threads(self):
        def work(t):
            for i in range(25):
                cc.queue_append("session", {"t": t, "i": i})
        threads = [threading.Thread(target=work, args=(t,)) for t in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        items = self.lines()
        self.assertEqual(len(items), 200)
        self.assertEqual(len(set((x["payload"]["t"], x["payload"]["i"]) for x in items)), 200)

    def test_R05_concurrent_processes(self):
        code = ("import importlib.util,sys;from importlib.machinery import SourceFileLoader as L;"
                "sys.dont_write_bytecode=True;"
                "l=L('m',%r);s=importlib.util.spec_from_loader('m',l);m=importlib.util.module_from_spec(s);"
                "l.exec_module(m);p=int(sys.argv[1])\n"
                "for i in range(25): m.queue_append('session',{'p':p,'i':i})" % SCRIPT)
        procs = [subprocess.Popen([sys.executable, "-c", code, str(p)], env=dict(os.environ)) for p in range(4)]
        for p in procs:
            self.assertEqual(p.wait(60), 0)
        items = self.lines()
        self.assertEqual(len(items), 100)
        self.assertEqual(len(set((x["payload"]["p"], x["payload"]["i"]) for x in items)), 100)

    def test_R05_claim_restore_preserves_order_and_concurrent_appends(self):
        for n in "abc":
            cc.queue_append("session", {"n": n})
        claim, lines = cc._queue_claim()
        self.assertEqual(len(lines), 3)
        self.assertFalse(os.path.exists(os.path.join(self.data, "queue.jsonl")))
        cc.queue_append("session", {"n": "d"})  # appended while claimed
        cc._queue_restore(claim, lines[1:])  # a was sent; b, c unsent
        self.assertEqual([i["payload"]["n"] for i in self.lines()], ["b", "c", "d"])
        self.assertFalse(os.path.exists(claim))

    def test_R05_orphan_claim_adopted(self):
        cc.ensure_data_dir()
        orphan = os.path.join(self.data, "queue.claim.1.dead")
        with open(orphan, "wb") as f:
            f.write(b'{"path":"session","payload":{"n":"old"},"ts":1}\n')
        old = time.time() - 4000
        os.utime(orphan, (old, old))
        cc.queue_append("session", {"n": "new"})
        claim, lines = cc._queue_claim()
        self.assertEqual([json.loads(x)["payload"]["n"] for x in lines], ["old", "new"])
        self.assertFalse(os.path.exists(orphan))

    def cfg_obj(self):
        return {"url": "https://ingest.example.com", "token": fake_token()}

    def test_R06_429_queued_with_retry_after(self):
        with mock.patch.object(cc, "http_post", return_value=cc.Resp(429, 7)):
            out = cc.send(self.cfg_obj(), "session", {"event": "start"}, 1.0)
        self.assertEqual(out, "retry")
        self.assertEqual(len(self.lines()), 1)
        st = cc.load_state()
        self.assertEqual(st["last_error"], "http_429")
        self.assertAlmostEqual(st["backoff_until"] - time.time(), 7, delta=2)

    def test_R06_5xx_and_network_queue_and_backoff(self):
        for status in (500, 503, 0):
            shutil.rmtree(self.data, True)
            with mock.patch.object(cc, "http_post", return_value=cc.Resp(status)):
                self.assertEqual(cc.send(self.cfg_obj(), "session", {"e": 1}, 1.0), "retry")
            self.assertEqual(len(self.lines()), 1)
            self.assertTrue(cc.in_backoff(cc.load_state()))
            with mock.patch.object(cc, "http_post") as hp:  # backoff: no network, but queued
                self.assertEqual(cc.send(self.cfg_obj(), "prompt", {"e": 2}, 1.0), "backoff")
                hp.assert_not_called()
            self.assertEqual(len(self.lines()), 2)

    def test_R06_401_recorded_not_queued_no_retry(self):
        with mock.patch.object(cc, "http_post", return_value=cc.Resp(401)) as hp:
            self.assertEqual(cc.send(self.cfg_obj(), "session", {"e": 1}, 1.0), "auth")
            self.assertEqual(hp.call_count, 1)
        self.assertEqual(cc.load_state()["last_error"], "http_401")
        self.assertFalse(os.path.exists(os.path.join(self.data, "queue.jsonl")))

    def test_R06_other_4xx_dropped(self):
        for status in (400, 404, 409, 413, 422):
            with mock.patch.object(cc, "http_post", return_value=cc.Resp(status)):
                self.assertEqual(cc.send(self.cfg_obj(), "session", {"e": 1}, 1.0), "drop")
            self.assertFalse(cc.in_backoff(cc.load_state()))
        self.assertFalse(os.path.exists(os.path.join(self.data, "queue.jsonl")))

    def test_R06_3xx_counts_as_dropped(self):
        with mock.patch.object(cc, "http_post", return_value=cc.Resp(307)):
            self.assertEqual(cc.send(self.cfg_obj(), "session", {"e": 1}, 1.0), "drop")
        self.assertFalse(os.path.exists(os.path.join(self.data, "queue.jsonl")))

    def test_R06_success_clears_error(self):
        cc.mutate_state(lambda s: s.update(last_error="http_401"))
        with mock.patch.object(cc, "http_post", return_value=cc.Resp(200)):
            self.assertEqual(cc.send(self.cfg_obj(), "session", {"e": 1}, 1.0), "ok")
        st = cc.load_state()
        self.assertIsNone(st["last_error"])
        self.assertTrue(st["last_ok"])

    def test_R06_flush_in_order_and_keeps_unsent_on_429(self):
        for n in range(5):
            cc.queue_append("session", {"n": n})
        seen = []

        def fake(cfg, suffix, payload, timeout):
            seen.append(payload["n"])
            return cc.Resp(429, 5) if payload["n"] == 2 else cc.Resp(200)
        with mock.patch.object(cc, "http_post", side_effect=fake):
            cc.flush_queue(self.cfg_obj())
        self.assertEqual(seen, [0, 1, 2])
        self.assertEqual([i["payload"]["n"] for i in self.lines()], [2, 3, 4])

    def test_R06_flush_drops_4xx_items_and_continues(self):
        for n in range(3):
            cc.queue_append("session", {"n": n})
        codes = {0: 200, 1: 422, 2: 200}
        with mock.patch.object(cc, "http_post", side_effect=lambda c, s, p, t: cc.Resp(codes[p["n"]])):
            cc.flush_queue(self.cfg_obj())
        self.assertFalse(os.path.exists(os.path.join(self.data, "queue.jsonl")))

    def test_flush_skipped_during_backoff(self):
        cc.queue_append("session", {"n": 1})
        cc.mutate_state(lambda s: s.update(backoff_until=time.time() + 50))
        with mock.patch.object(cc, "http_post") as hp:
            cc.flush_queue(self.cfg_obj())
            hp.assert_not_called()

    def test_flush_respects_budget(self):
        for n in range(20):
            cc.queue_append("session", {"n": n})

        def slow(cfg, suffix, payload, timeout):
            time.sleep(0.2)
            return cc.Resp(200)
        t0 = time.monotonic()
        with mock.patch.object(cc, "http_post", side_effect=slow):
            cc.flush_queue(self.cfg_obj(), budget=0.7)
        self.assertLess(time.monotonic() - t0, 1.5)
        remaining = self.lines()
        self.assertTrue(0 < len(remaining) < 20)
        self.assertEqual(remaining[0]["payload"]["n"], 20 - len(remaining))

    def test_queue_never_contains_token(self):
        tok = fake_token()
        with mock.patch.object(cc, "http_post", return_value=cc.Resp(500)):
            cc.send({"url": "https://ingest.example.com", "token": tok}, "session", {"event": "start"}, 1.0)
        self.assertNotIn(tok, self.all_data_text())


# ---------------------------------------------------------------------------
class TestSetupUninstall(EnvCase):
    URL = "https://ingest.example.com"

    def test_R03_exactly_nine_keys_and_values(self):
        tok = self.setup_ok(self.URL + "/")
        env = self.read_settings()["env"]
        self.assertEqual(sorted(env), sorted(cc.MANAGED_ENV_KEYS))
        self.assertEqual(len(env), 9)
        self.assertEqual(env["OTEL_EXPORTER_OTLP_ENDPOINT"], "https://ingest.example.com/v1/otlp")
        self.assertEqual(env["OTEL_EXPORTER_OTLP_HEADERS"], "Authorization=Bearer " + tok)
        self.assertEqual(env["OTEL_EXPORTER_OTLP_PROTOCOL"], "http/json")
        self.assertEqual(env["OTEL_LOGS_EXPORT_INTERVAL"], "5000")
        self.assertEqual(env["COMPANY_COST_MANAGED"], "1")

    def test_R03_never_sets_prompt_or_tool_detail_logging(self):
        self.setup_ok()
        text = slurp(self.settings) + slurp(self.sidecar)
        self.assertNotIn("OTEL_LOG_USER_PROMPTS", text)
        self.assertNotIn("OTEL_LOG_TOOL_DETAILS", text)

    def test_R03_other_keys_untouched(self):
        self.write_settings({"model": "x", "hooks": {"A": [1]}, "env": {"FOO": "bar"}, "permissions": {"allow": []}})
        self.setup_ok()
        s = self.read_settings()
        self.assertEqual(s["model"], "x")
        self.assertEqual(s["hooks"], {"A": [1]})
        self.assertEqual(s["env"]["FOO"], "bar")
        self.assertEqual(s["permissions"], {"allow": []})
        self.assertEqual(len(s["env"]), 10)

    def test_R03_missing_and_empty_file(self):
        self.setup_ok()
        self.assertEqual(len(self.read_settings()["env"]), 9)
        os.unlink(self.settings)
        os.unlink(self.sidecar)
        self.write_settings("")
        self.setup_ok()
        self.assertEqual(len(self.read_settings()["env"]), 9)

    def test_R03_invalid_json_untouched(self):
        for bad in ("{not json", "[1,2]", "42"):
            self.write_settings(bad)
            self.setup_only(token="tok")
            self.assertEqual(self.read_side()["features"]["costs"], False)
            rc, out, err = self.run_cli(["send-traces", "on"])
            self.assertEqual(rc, 1)
            self.assertEqual(slurp(self.settings), bad)
            self.assertEqual(self.read_side()["features"]["costs"], False)
            self.assertNotIn("managed_keys", self.read_side())
            self.assertFalse(os.path.exists(self.bak))
            rc, _, _ = self.run_cli(["uninstall"])
            self.assertEqual(rc, 1)
            self.assertEqual(slurp(self.settings), bad)

    def test_R03_env_not_dict_refused(self):
        self.write_settings({"env": "oops"})
        self.setup_only(token="tok")
        rc, _, err = self.run_cli(["send-traces", "on"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.read_settings(), {"env": "oops"})

    def test_R03_conflict_without_force_changes_nothing(self):
        original = {"env": {"OTEL_LOGS_EXPORTER": "console", "KEEP": "1"}}
        self.write_settings(original)
        self.setup_only(token="tok")
        rc, out, err = self.run_cli(["send-traces", "on"])
        self.assertEqual(rc, 1)
        self.assertIn("OTEL_LOGS_EXPORTER", err)
        self.assertEqual(self.read_settings(), original)
        self.assertNotIn("managed_keys", self.read_side())
        self.assertFalse(os.path.exists(self.bak))

    def test_R03_force_then_uninstall_restores_previous(self):
        original = {"env": {"OTEL_LOGS_EXPORTER": "console", "KEEP": "1"}, "model": "m"}
        self.write_settings(original)
        self.setup_ok(extra=["--force"])
        self.assertEqual(self.read_settings()["env"]["OTEL_LOGS_EXPORTER"], "otlp")
        self.assertEqual(json.loads(slurp(self.sidecar))["previous"], {"OTEL_LOGS_EXPORTER": "console"})
        rc, out, err = self.run_cli(["uninstall"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.read_settings(), original)

    def test_R03_equal_preexisting_values_restored(self):
        self.write_settings({"env": {"OTEL_LOGS_EXPORTER": "otlp"}})
        self.setup_ok()
        self.run_cli(["uninstall"])
        self.assertEqual(self.read_settings(), {"env": {"OTEL_LOGS_EXPORTER": "otlp"}})

    def test_R03_uninstall_removes_empty_env_and_files(self):
        self.write_settings({"model": "m"})
        self.setup_ok()
        cc.ensure_data_dir()
        legacy = os.path.join(self.cfg, "plugins", "data", "company-cost-ounity-plugins")
        os.makedirs(legacy)
        rc, out, err = self.run_cli(["uninstall"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.read_settings(), {"model": "m"})
        for p in (self.sidecar, self.bak, legacy):
            self.assertFalse(os.path.exists(p), p)

    def test_R03_uninstall_twice_and_without_setup(self):
        self.setup_ok()
        self.assertEqual(self.run_cli(["uninstall"])[0], 0)
        self.assertEqual(self.run_cli(["uninstall"])[0], 0)
        os.unlink(self.settings)
        self.assertEqual(self.run_cli(["uninstall"])[0], 0)

    def test_R03_sidecar_lost_sentinel_present_uses_defaults(self):
        self.write_settings({"env": {"KEEP": "1"}})
        self.setup_ok()
        os.unlink(self.sidecar)
        self.assertEqual(self.run_cli(["uninstall"])[0], 0)
        self.assertEqual(self.read_settings(), {"env": {"KEEP": "1"}})

    def test_R03_sentinel_absent_without_sidecar_removes_nothing(self):
        env = dict((k, "mine") for k in cc.MANAGED_ENV_KEYS if k != cc.SENTINEL_KEY)
        self.write_settings({"env": dict(env)})
        self.run_cli(["uninstall"])
        self.assertEqual(self.read_settings(), {"env": env})

    def test_R03_setup_idempotent_and_keeps_first_backup(self):
        self.write_settings({"model": "first"})
        self.setup_ok()
        first_bak = slurp(self.bak)
        self.assertEqual(json.loads(first_bak), {"model": "first"})
        snapshot = self.read_settings()
        self.setup_ok()
        self.assertEqual(self.read_settings(), snapshot)
        self.assertEqual(slurp(self.bak), first_bak)
        self.assertEqual(json.loads(slurp(self.sidecar))["previous"], {})

    def test_R03_token_change_on_rerun(self):
        self.setup_ok(token="tok-one")
        self.setup_ok(token="tok-two")
        self.assertIn("tok-two", self.read_settings()["env"]["OTEL_EXPORTER_OTLP_HEADERS"])

    def test_R03_modes(self):
        self.setup_ok()
        for p in (self.settings, self.sidecar):
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600, p)
        os.unlink(self.settings)
        os.unlink(self.sidecar)
        self.write_settings({"model": "x"}, mode=0o644)
        self.setup_ok()
        self.assertEqual(stat.S_IMODE(os.stat(self.settings).st_mode), 0o600)  # group/other bits stripped
        self.assertEqual(stat.S_IMODE(os.stat(self.bak).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.sidecar).st_mode), 0o600)

    def test_R03_sidecar_contents(self):
        tok = self.setup_ok()
        sc = json.loads(slurp(self.sidecar))
        self.assertEqual(sc["managed_keys"], sorted(cc.MANAGED_ENV_KEYS))
        self.assertTrue(sc["env_was_missing"])
        self.assertEqual(sc["url"], self.URL)
        self.assertEqual(sc["token"], tok)
        self.assertEqual(sc["version"], cc.VERSION)

    def test_R03_claude_config_dir_respected(self):
        other = os.path.join(self.tmp, "other-cfg")
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": other}):
            self.assertEqual(self.run_cli(["setup", "--url", self.URL, "--token-stdin"], stdin="t\n")[0], 0)
            self.assertEqual(self.run_cli(["send-traces", "on"])[0], 0)
        self.assertTrue(os.path.exists(os.path.join(other, "settings.json")))
        self.assertFalse(os.path.exists(self.settings))

    def test_R02_setup_url_validation_via_cli(self):
        for url, want in (("https://x", 0), ("http://localhost:8000", 0), ("http://[::1]", 0),
                          ("http://evil.com", 1), ("nourl", 1), ("https://x/api/", 0)):
            rc, _, err = self.run_cli(["setup", "--url", url, "--token-stdin"], stdin="t\n")
            self.assertEqual(rc, want, (url, err))
        rc, _, err = self.run_cli(["setup", "--url", "http://evil.com", "--token-stdin", "--allow-insecure"],
                                  stdin="t\n")
        self.assertEqual(rc, 0, err)

    def test_R02_trailing_slash_stripped_and_path_kept(self):
        self.setup_ok("https://x.example.com/base/")
        self.assertEqual(self.read_settings()["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"],
                         "https://x.example.com/base/v1/otlp")

    def test_R02_bad_tokens_refused(self):
        for tok in ("", "a b", "a,b"):
            rc, _, _ = self.run_cli(["setup", "--url", self.URL, "--token-stdin"], stdin=tok + "\n")
            self.assertEqual(rc, 1, repr(tok))
        self.assertFalse(os.path.exists(self.settings))

    def test_R04_token_never_echoed(self):
        tok = fake_token()
        rc, out, err = self.run_cli(["setup", "--url", self.URL, "--token-stdin"], stdin=tok + "\n")
        self.assertNotIn(tok, out + err)
        rc, out, err = self.run_cli(["setup", "--url", self.URL, "--token", tok])
        self.assertEqual(rc, 0)
        self.assertNotIn(tok, out)
        self.assertIn("argv", err)  # warns about argv exposure
        self.assertNotIn(tok, err)
        rc, out, err = self.run_cli(["uninstall"])
        self.assertNotIn(tok, out + err)

    def test_windows_is_refused(self):
        with mock.patch.object(cc, "_is_windows", return_value=True):
            rc, out, err = self.run_cli(["setup", "--url", self.URL, "--token-stdin"], stdin="t\n")
        self.assertEqual(rc, 1)
        self.assertIn("macOS/Linux/WSL", err)
        self.assertFalse(os.path.exists(self.settings))

    def test_uninstall_next_steps_with_marketplace(self):
        self.setup_ok()
        os.makedirs(os.path.join(self.cfg, "plugins"))
        with open(os.path.join(self.cfg, "plugins", "installed_plugins.json"), "w") as f:
            json.dump({"version": 2, "plugins": {"company-cost@my-market": [{"scope": "user"}]}}, f)
        rc, out, err = self.run_cli(["uninstall"])
        self.assertIn("claude plugin uninstall company-cost@my-market", out)
        self.assertIn("claude plugin marketplace remove my-market", out)
        order = [out.index(s) for s in ("1.", "2.", "3.", "4.", "5.")]
        self.assertEqual(order, sorted(order))

    def test_uninstall_next_steps_placeholder(self):
        rc, out, err = self.run_cli(["uninstall"])
        self.assertIn("company-cost@<marketplace>", out)


# ---------------------------------------------------------------------------
class TestConfigAndStatus(EnvCase):
    def test_no_config_no_default_url(self):
        self.assertIsNone(cc.load_config())

    def test_env_config(self):
        os.environ["CLAUDE_PLUGIN_OPTION_RAG_URL"] = "https://ingest.example.com/"
        os.environ["CLAUDE_PLUGIN_OPTION_RAG_TOKEN"] = "tok"
        self.assertEqual(cc.load_config(), {"url": "https://ingest.example.com", "token": "tok"})

    def test_sidecar_config(self):
        tok = fake_token()
        self.run_cli(["setup", "--url", "https://ingest.example.com", "--token-stdin"], stdin=tok)
        self.assertEqual(cc.load_config(), {"url": "https://ingest.example.com", "token": tok})

    def test_insecure_remote_env_ignored(self):
        os.environ["CLAUDE_PLUGIN_OPTION_RAG_URL"] = "http://evil.com"
        os.environ["CLAUDE_PLUGIN_OPTION_RAG_TOKEN"] = "tok"
        self.assertIsNone(cc.load_config())

    def test_R04_status_never_prints_token(self):
        tok = self.setup_ok()
        cc.mutate_state(lambda s: s.update(last_ok=time.time()))
        cc.queue_append("session", {"event": "start"})
        rc, out, err = self.run_cli(["status"])
        self.assertEqual(rc, 0)
        self.assertNotIn(tok, out + err)
        self.assertIn("set (hidden)", out)
        self.assertIn("Queue: 1", out)
        self.assertIn("/v1/ingest/plugin/*", out)
        self.assertIn("Managed keys: present", out)

    def test_status_variants(self):
        rc, out, _ = self.run_cli(["status"])
        self.assertIn("Not configured", out)
        self.setup_ok()
        self.assertIn("No events seen yet", self.run_cli(["status"])[1])
        cc.mutate_state(lambda s: s.update(last_error="http_401"))
        self.assertIn("token rejected: re-run /company-cost:setup", self.run_cli(["status"])[1])
        s = self.read_settings()
        s["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"] = "https://other.example.com/v1/otlp"
        self.write_settings(s)
        self.assertIn("WARNING", self.run_cli(["status"])[1])

    def test_doctor_offline(self):
        rc, out, _ = self.run_cli(["doctor"])
        self.assertEqual(rc, 0)
        self.assertIn("python:", out)


# ---------------------------------------------------------------------------
class TestReviewFixes(EnvCase):
    URL = "https://ingest.example.com"

    def cfg_obj(self):
        return {"url": self.URL, "token": fake_token()}

    # 3 redaction extras
    def test_redaction_extras(self):
        cases = [
            ("key_sk-abcdefghijkl1234", "abcdefghijkl1234"),
            ('{"password":"hunter2"}', "hunter2"),
            ('"token": "abc"', '"abc"'),
            ('{"client_secret": "zzz999"}', "zzz999"),
            ("sk-abc\u200bdefghijklmnop", "defghijklmnop"),
            ("\uff53\uff4b-abcdefghijklmnop", "abcdefghijklmnop"),
            ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ]
        for text, secret in cases:
            with self.subTest(text):
                out = cc.redact_excerpt(text)
                self.assertNotIn(secret, out)
                self.assertIn("[REDACTED]", out)
                self.assertLessEqual(len(out), 200)

    def test_redaction_extras_keep_prefix_words(self):
        self.assertEqual(cc.redact_excerpt("task-management is fine"), "task-management is fine")

    def test_redaction_extras_after_the_twelve(self):
        pats = [p.pattern for p in cc.REDACTION_PATTERNS]
        self.assertTrue(pats[0].startswith("-----BEGIN"))
        self.assertTrue(pats[11].startswith("[A-Za-z0-9+/_=]{32,}"))
        self.assertGreater(len(pats), 13)

    def test_redaction_extras_timing(self):
        for s in ('"' * 3000, '"token' * 400, "Basic " * 300, "sk-" * 800, '"password": "' * 200):
            t0 = time.perf_counter()
            out = cc.redact_excerpt(s)
            self.assertLess(time.perf_counter() - t0, 0.05, s[:10])
            self.assertLessEqual(len(out), 200)

    # 13
    def test_token_rejects_non_ascii(self):
        self.assertTrue(cc.validate_token("tok\u00e9n"))
        self.assertTrue(cc.validate_token("\uff54ok"))

    # 2
    def test_settings_written_without_group_other_bits(self):
        self.write_settings({"model": "x"}, mode=0o644)
        self.setup_ok()
        self.assertEqual(stat.S_IMODE(os.stat(self.settings).st_mode), 0o600)
        self.write_settings(self.read_settings(), mode=0o666)
        self.setup_ok(token="other-token")
        self.assertEqual(stat.S_IMODE(os.stat(self.settings).st_mode), 0o600)
        self.run_cli(["uninstall"])
        if os.path.exists(self.settings):
            self.assertEqual(stat.S_IMODE(os.stat(self.settings).st_mode) & 0o077, 0)

    # 4
    def test_uninstall_spares_other_plugins_data(self):
        base = os.path.join(self.cfg, "plugins", "data")
        keep = [os.path.join(base, n) for n in ("company-cost-extras-x", "company-cost2", "other")]
        gone = [os.path.join(base, n) for n in ("company-cost", "company-cost-ounity-plugins")]
        for d in keep + gone:
            os.makedirs(d)
        target = os.path.join(self.tmp, "elsewhere")
        os.makedirs(target)
        os.symlink(target, os.path.join(base, "company-cost-my-market"))
        self.setup_ok()
        self.assertEqual(self.run_cli(["uninstall"])[0], 0)
        for d in keep:
            self.assertTrue(os.path.isdir(d), d)
        for d in gone:
            self.assertFalse(os.path.exists(d), d)
        self.assertTrue(os.path.isdir(target))

    def test_uninstall_marketplace_from_installed_plugins(self):
        base = os.path.join(self.cfg, "plugins", "data")
        mk = os.path.join(base, "company-cost-my-market")
        os.makedirs(mk)
        with open(os.path.join(self.cfg, "plugins", "installed_plugins.json"), "w") as f:
            json.dump({"plugins": {"company-cost@my-market": [{}]}}, f)
        self.run_cli(["uninstall"])
        self.assertFalse(os.path.exists(mk))

    def test_uninstall_plugin_data_env_only_under_plugins_data(self):
        outside = os.path.join(self.tmp, "precious")
        os.makedirs(outside)
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": outside}):
            self.run_cli(["uninstall"])
        self.assertTrue(os.path.isdir(outside))
        inside = os.path.join(self.cfg, "plugins", "data", "company-cost-weird")
        os.makedirs(inside)
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": inside}):
            self.run_cli(["uninstall"])
        self.assertFalse(os.path.exists(inside))

    # 5
    def test_uninstall_hand_edited_env_keeps_user_values(self):
        self.setup_ok()
        s = self.read_settings()
        del s["env"][cc.SENTINEL_KEY]
        s["env"]["OTEL_LOGS_EXPORT_INTERVAL"] = "9999"  # hand edited
        self.write_settings(s)
        self.run_cli(["uninstall"])
        env = self.read_settings()["env"]
        self.assertEqual(env, {"OTEL_LOGS_EXPORT_INTERVAL": "9999"})

    def test_uninstall_tampered_sidecar_cannot_delete_foreign_keys(self):
        self.write_settings({"env": {"PATH": "/mine", "FOO": "bar"}, "model": "m"})
        self.setup_ok()
        sc = json.loads(slurp(self.sidecar))
        sc["managed_keys"] = sc["managed_keys"] + ["PATH", "FOO"]
        sc["previous"] = {"PATH": "/evil", "OTEL_LOGS_EXPORTER": "x"}
        with open(self.sidecar, "w") as f:
            json.dump(sc, f)
        self.run_cli(["uninstall"])
        env = self.read_settings()["env"]
        self.assertEqual(env["PATH"], "/mine")
        self.assertEqual(env["FOO"], "bar")
        self.assertEqual(env["OTEL_LOGS_EXPORTER"], "x")

    # 6
    def test_flush_processes_orphan_claim_without_queue(self):
        cc.ensure_data_dir()
        orphan = os.path.join(self.data, "queue.claim.1.dead")
        with open(orphan, "wb") as f:
            f.write(b'{"path":"session","payload":{"n":1},"ts":1}\n')
        old = time.time() - 4000
        os.utime(orphan, (old, old))
        self.assertEqual(cc.queue_size(), 1)
        seen = []
        with mock.patch.object(cc, "http_post", side_effect=lambda c, s, p, t: seen.append(p) or cc.Resp(200)):
            cc.flush_queue(self.cfg_obj())
        self.assertEqual(seen, [{"n": 1}])
        self.assertFalse(os.path.exists(orphan))
        self.assertEqual(cc.queue_size(), 0)

    # 7
    def test_restore_without_lock_keeps_claim(self):
        cc.queue_append("session", {"n": 1})
        claim, lines = cc._queue_claim()
        lock = os.path.join(self.data, "queue.lock")
        with open(lock, "w") as f:
            f.write("someone")
        with mock.patch.object(cc, "QUEUE_LOCK_WAIT", 0.05):
            self.assertFalse(cc._queue_restore(claim, lines))
        self.assertTrue(os.path.exists(claim))

    # 8
    def test_lock_release_only_when_owned(self):
        path = os.path.join(self.tmp, "x.lock")
        a = cc.FileLock(path, stale=0.0, wait=0.5)
        self.assertTrue(a.acquire())
        os.utime(path, (time.time() - 100, time.time() - 100))
        b = cc.FileLock(path, stale=30.0, wait=0.5)
        self.assertTrue(b.acquire())  # stale takeover
        a.release()  # not the owner any more: must not remove b's lock
        self.assertTrue(os.path.exists(path))
        b.release()
        self.assertFalse(os.path.exists(path))
        self.assertEqual([f for f in os.listdir(self.tmp) if ".stale." in f], [])

    # 9
    def test_401_sets_backoff_and_setup_clears_it(self):
        with mock.patch.object(cc, "http_post", return_value=cc.Resp(401)):
            self.assertEqual(cc.send(self.cfg_obj(), "session", {"e": 1}, 1.0), "auth")
        st = cc.load_state()
        self.assertEqual(st["last_error"], "http_401")
        self.assertGreater(st["backoff_until"], time.time() + 600)
        with mock.patch.object(cc, "http_post") as hp:
            self.assertEqual(cc.send(self.cfg_obj(), "session", {"e": 2}, 1.0), "backoff")
            hp.assert_not_called()
        self.setup_ok()
        st = cc.load_state()
        self.assertIsNone(st["last_error"])
        self.assertFalse(cc.in_backoff(st))

    def test_flush_401_keeps_item_and_backs_off(self):
        cc.queue_append("session", {"n": 1})
        with mock.patch.object(cc, "http_post", return_value=cc.Resp(401)):
            cc.flush_queue(self.cfg_obj())
        self.assertEqual(cc.queue_size(), 1)
        self.assertTrue(cc.in_backoff(cc.load_state()))

    # 10
    def test_status_discovers_hook_data_dir(self):
        self.setup_ok()
        real = os.path.join(self.cfg, "plugins", "data", "company-cost-my-market")
        os.makedirs(real)
        with open(os.path.join(real, "state.json"), "w") as f:
            json.dump({"last_error": "http_500"}, f)
        decoy = os.path.join(self.cfg, "plugins", "data", "company-cost")
        os.makedirs(decoy)
        with open(os.path.join(decoy, "state.json"), "w") as f:
            json.dump({"last_error": "http_999"}, f)
        old = time.time() - 500
        os.utime(os.path.join(decoy, "state.json"), (old, old))
        os.makedirs(os.path.join(self.cfg, "plugins", "data", "company-cost-extra-empty"))
        env = dict(os.environ)
        env.pop("CLAUDE_PLUGIN_DATA")
        with mock.patch.dict(os.environ, env, clear=True):
            rc, out, err = self.run_cli(["status"])
            self.assertIn("http_500", out)
            self.assertNotIn("http_999", out)
            cc._DATA_OVERRIDE[0] = None

    # 1 latency
    def test_inference_cached_per_session_and_branch(self):
        ident = {"toplevel": "", "branch": "b"}
        listing = {"root": None, "changes": [{"name": "r", "lastModified": "2030-01-01"}]}
        with mock.patch.object(cc, "openspec_list", return_value=listing) as ol:
            self.assertEqual(cc.resolve_change("s", self.tmp, ident), ("r", "recent"))
            self.assertEqual(cc.resolve_change("s", self.tmp, ident), ("r", "recent"))
            self.assertEqual(ol.call_count, 1)
            ident["branch"] = "other"
            cc.resolve_change("s", self.tmp, ident)
            self.assertEqual(ol.call_count, 2)
            ent = json.loads(slurp(os.path.join(self.data, "sessions.json")))["s"]
            ent["infer"]["ts"] = time.time() - 1000
            with open(os.path.join(self.data, "sessions.json"), "w") as f:
                json.dump({"s": ent}, f)
            cc.resolve_change("s", self.tmp, ident)
            self.assertEqual(ol.call_count, 3)

    def test_session_end_sends_first_without_openspec(self):
        ident = {"key": "k", "name": "n", "repo_display": "n", "branch": "b", "toplevel": ""}
        cc.session_meta_update("sid", ident=ident, infer={"change": "c", "source": "recent", "branch": "b", "ts": 1})
        with open(cc.sidecar_path(), "w") as f:
            json.dump({"features": {"costs": True, "openspec": True}}, f)
        sent = []
        with mock.patch.object(cc, "openspec_list", side_effect=AssertionError("no openspec")), \
                mock.patch.object(cc, "project_identity", side_effect=AssertionError("no git")), \
                mock.patch.object(cc, "http_post", side_effect=lambda c, s, p, t: sent.append((s, p, t)) or cc.Resp(200)):
            os.environ["CLAUDE_PLUGIN_OPTION_RAG_URL"] = self.URL
            os.environ["CLAUDE_PLUGIN_OPTION_RAG_TOKEN"] = "tok"
            cc.run_hook("SessionEnd", {"session_id": "sid", "cwd": self.tmp})
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1]["event"], "end")
        self.assertEqual(sent[0][1]["change_name"], "c")
        self.assertEqual(sent[0][1]["project_key"], "k")
        self.assertEqual(sent[0][2], cc.TIMEOUT_SESSION_END)

    def test_run_clamped_by_deadline(self):
        cc._DEADLINE[0] = time.monotonic() - 1
        try:
            self.assertIsNone(cc._run(["git", "--version"]))
        finally:
            cc._DEADLINE[0] = None
        self.assertIsNotNone(cc._run(["git", "--version"]))


if __name__ == "__main__":
    unittest.main()
