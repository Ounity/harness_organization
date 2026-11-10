"""Regression tests for the second code-review round (F1-F10)."""
import json
import os
import re
import threading
import time
import unittest
from unittest import mock

from tests.test_company_cost import EnvCase, cc, fake_token

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestF1F2F3Redaction(unittest.TestCase):
    def test_F1_identifier_with_underscore(self):
        for text, secret in (("DB_PASSWORD=hunter2", "hunter2"),
                             ("export GITHUB_TOKEN=abc123", "abc123"),
                             ("AWS_SECRET_ACCESS_KEY=short", "short"),
                             ("my.api_key: zzz9", "zzz9"),
                             ("AWS_ACCESS_KEY_ID=AKIDabc", "AKIDabc")):
            with self.subTest(text):
                out = cc.redact_excerpt(text)
                self.assertNotIn(secret, out)
                self.assertIn("[REDACTED]", out)

    def test_F2_quoted_values(self):
        for text, leak in (("password: 'my secret phrase'", "phrase"),
                           ('pwd = "two words here" x', "words"),
                           ("DB_PASSWORD='a b c'", "b c"),
                           ("password: 'unterminated secret phrase", "phrase")):
            with self.subTest(text):
                out = cc.redact_excerpt(text)
                self.assertNotIn(leak, out)
                self.assertNotIn("secret phrase'", out)

    def test_F2_trailing_text_kept(self):
        self.assertIn("after", cc.redact_excerpt("password: 'a b' after"))

    def test_F3_secret_straddling_source_window(self):
        # whitespace collapses after the cut, so the old 2000 cut left a fragment in the excerpt
        out = cc.redact_excerpt(" " * 1990 + "Qw3rTy0pAs" * 4)
        self.assertNotIn("Qw3rTy0pAs", out)
        out = cc.redact_excerpt(" " * 1995 + "sk-" + "Q" * 40)
        self.assertNotIn("sk-", out)
        out = cc.redact_excerpt("\x00" * 1990 + "password: 'alpha beta gamma delta'")
        self.assertNotIn("gamma", out)
        self.assertNotIn("alpha", out)

    def test_F3_source_window_is_larger(self):
        self.assertGreaterEqual(cc.EXCERPT_SOURCE_CHARS, 8000)

    def test_F3_perf_adversarial(self):
        samples = ["a" * 8000, "token" * 1600, "password " * 900, "a_" * 4000, "k=" * 4000,
                   "'" * 8000, "password: '" * 800, "a-" * 4000, "word " * 1600]
        for s in samples:
            for n in (2000, 8000):
                t0 = time.perf_counter()
                cc.redact_excerpt(s[:n])
                dt = time.perf_counter() - t0
                self.assertLess(dt, 0.05, "%r n=%d %.3fs" % (s[:10], n, dt))

    def test_originals_unmodified_and_in_order(self):
        self.assertTrue(cc.REDACTION_PATTERNS[11].pattern.startswith("[A-Za-z0-9+/_=]{32,}"))
        self.assertTrue(cc.REDACTION_PATTERNS[9].pattern.startswith(r"\b(?:password|passwd|pwd|secret|api"))


class TestF4LockFailure(EnvCase):
    def hold(self, name):
        cc.ensure_data_dir()
        lk = cc.FileLock(os.path.join(cc.data_dir(), name), stale=1000, wait=0)
        self.assertTrue(lk.acquire())
        self.addCleanup(lk.release)

    def test_mutate_state_skips_when_lock_not_held(self):
        self.hold("state.lock")
        called = []
        with mock.patch.object(cc, "FileLock", lambda p, stale, wait: cc.FileLock.__new__(cc.FileLock)
                               if False else _fast_lock(p, stale)):
            cc.mutate_state(lambda st: called.append(1) or st.__setitem__("x", 1))
        self.assertEqual(called, [])
        self.assertFalse(os.path.exists(os.path.join(cc.data_dir(), "state.json")))

    def test_session_meta_update_skips_when_lock_not_held(self):
        self.hold("sessions.lock")
        with mock.patch.object(cc, "FileLock", lambda p, stale, wait: _fast_lock(p, stale)):
            cc.session_meta_update("s1", change="c")
        self.assertFalse(os.path.exists(os.path.join(cc.data_dir(), "sessions.json")))


_RealLock = cc.FileLock


def _fast_lock(path, stale):
    return _RealLock(path, stale=stale, wait=0.05)


class TestF5Lock(EnvCase):
    def setUp(self):
        super().setUp()
        os.makedirs(self.data, exist_ok=True)
        self.path = os.path.join(self.data, "t.lock")

    def test_refresh_updates_mtime(self):
        lk = cc.FileLock(self.path, stale=10, wait=0)
        self.assertTrue(lk.acquire())
        old = time.time() - 100
        os.utime(self.path, (old, old))
        lk.refresh()
        self.assertGreater(os.stat(self.path).st_mtime, time.time() - 5)
        lk.release()

    def test_refreshed_during_recheck_is_not_taken_over(self):
        a = cc.FileLock(self.path, stale=10, wait=0)
        self.assertTrue(a.acquire())
        old = time.time() - 100
        os.utime(self.path, (old, old))
        real_sleep = time.sleep

        def sleeper(s):
            a.refresh()  # live holder refreshes while the contender re-checks
            real_sleep(0)
        renames = []
        real_rename = os.rename
        with mock.patch.object(cc.time, "sleep", sleeper), \
                mock.patch.object(cc.os, "rename", lambda *x: renames.append(x) or real_rename(*x)):
            b = cc.FileLock(self.path, stale=10, wait=0.1)
            self.assertFalse(b.acquire())
        self.assertEqual(renames, [])
        with open(self.path) as f:
            self.assertEqual(f.read(), a.token)

    def test_stale_unchanged_is_taken_over(self):
        a = cc.FileLock(self.path, stale=10, wait=0)
        self.assertTrue(a.acquire())
        old = time.time() - 100
        os.utime(self.path, (old, old))
        b = cc.FileLock(self.path, stale=10, wait=1.0)
        self.assertTrue(b.acquire())
        b.release()

    def test_threads_mutual_exclusion(self):
        counter = [0]
        errors = []

        def work():
            for _ in range(20):
                lk = cc.FileLock(self.path, stale=30, wait=5)
                if not lk.acquire():
                    errors.append("noacq")
                    continue
                v = counter[0]
                time.sleep(0.0005)
                counter[0] = v + 1
                lk.release()
        ts = [threading.Thread(target=work) for _ in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errors, [])
        self.assertEqual(counter[0], 120)


class TestF6SyncDeletes(EnvCase):
    def setUp(self):
        super().setUp()
        os.makedirs(self.data, exist_ok=True)
        with open(cc.sidecar_path(), "w") as f:
            json.dump({"features": {"costs": True, "openspec": True}}, f)
        self.root = os.path.join(self.tmp, "repo", "openspec")
        os.makedirs(os.path.join(self.root, "specs"))
        self.write("specs/ok.md", "ok")
        self.write("specs/big.md", "x" * (cc.MAX_FILE_BYTES + 10))
        with open(os.path.join(self.root, "specs", "bad.md"), "wb") as f:
            f.write(b"\xff\xfe\xfa")
        os.symlink(os.path.join(self.root, "specs", "ok.md"), os.path.join(self.root, "specs", "link.md"))
        self.calls = []

    def write(self, rel, text):
        with open(os.path.join(self.root, *rel.split("/")), "w") as f:
            f.write(text)

    def run_sync(self, delete):
        ident = {"key": "k", "name": "n", "repo_display": "k", "branch": "main", "toplevel": os.path.dirname(self.root)}

        def fake_deliver(cfg, suffix, payload, timeout):
            self.calls.append((suffix, payload))
            if suffix == "sync/plan":
                return "ok", cc.Resp(200, None, {"upload": ["specs/ok.md"], "delete": delete})
            return "ok", cc.Resp(200, None, {})
        with mock.patch.object(cc, "load_config", lambda: {"url": "http://localhost", "token": "t"}), \
                mock.patch.object(cc, "project_identity", lambda cwd: ident), \
                mock.patch.object(cc, "openspec_list", lambda cwd: {"root": self.root, "changes": []}), \
                mock.patch.object(cc, "deliver", fake_deliver):
            cc.run_sync(os.path.dirname(self.root))
        dels = []
        for s, p in self.calls:
            if s == "sync/files":
                dels.extend(p["delete"])
        return dels

    def test_skipped_local_files_never_deleted(self):
        dels = self.run_sync(["specs/big.md", "specs/bad.md", "specs/link.md", "specs/gone.md"])
        self.assertEqual(dels, ["specs/gone.md"])

    def test_truncated_manifest_sends_no_deletes(self):
        self.write("specs/z.md", "z")  # second collectable file: beyond the cap of 1
        with mock.patch.object(cc, "MAX_MANIFEST", 1):
            dels = self.run_sync(["specs/gone.md"])
        self.assertEqual(dels, [])


class TestF7SyncFailure(EnvCase):
    def setUp(self):
        super().setUp()
        os.makedirs(self.data, exist_ok=True)
        with open(cc.sidecar_path(), "w") as f:
            json.dump({"features": {"costs": True, "openspec": True}}, f)
        self.root = os.path.join(self.tmp, "repo", "openspec")
        os.makedirs(os.path.join(self.root, "specs"))
        with open(os.path.join(self.root, "specs", "a.md"), "w") as f:
            f.write("a")
        self.key = "k|main"
        cc.mutate_state(lambda st: st.__setitem__("sync", {self.key: time.time()}))

    def run_sync(self, fail_suffix, changes=()):
        ident = {"key": "k", "name": "n", "repo_display": "k", "branch": "main", "toplevel": os.path.dirname(self.root)}

        def fake_deliver(cfg, suffix, payload, timeout):
            if suffix == fail_suffix:
                cc.mutate_state(lambda st: st.__setitem__("last_error", "network"))
                return "retry", cc.Resp(0)
            if suffix == "sync/plan":
                return "ok", cc.Resp(200, None, {"upload": ["specs/a.md"], "delete": []})
            return "ok", cc.Resp(200, None, {})
        with mock.patch.object(cc, "load_config", lambda: {"url": "http://localhost", "token": "t"}), \
                mock.patch.object(cc, "project_identity", lambda cwd: ident), \
                mock.patch.object(cc, "openspec_list", lambda cwd: {"root": self.root, "changes": list(changes)}), \
                mock.patch.object(cc, "deliver", fake_deliver):
            cc.run_sync(os.path.dirname(self.root))
        return cc.load_state()

    def test_failure_does_not_record_success(self):
        for suffix in ("sync/plan", "sync/files", "snapshot"):
            with self.subTest(suffix):
                cc.mutate_state(lambda st: (st.pop("last_sync", None), st.__setitem__("sync", {self.key: time.time()})))
                st = self.run_sync(suffix, changes=[{"name": "c"}])
                self.assertNotIn("last_sync", st)
                self.assertNotIn(self.key, st.get("sync", {}))

    def test_success_records(self):
        st = self.run_sync(None, changes=[{"name": "c"}])
        self.assertIn("last_sync", st)
        self.assertIn(self.key, st["sync"])


class TestF8Heartbeat(EnvCase):
    def hb(self):
        return {"event": "heartbeat", "session_id": "s"}

    def test_heartbeat_not_queued_in_backoff(self):
        cc.mutate_state(lambda st: st.__setitem__("backoff_until", time.time() + 100))
        cfg = {"url": "http://localhost", "token": "t"}
        cc.send(cfg, "session", {"event": "start", "session_id": "s"}, 1)
        for _ in range(5):
            cc.send(cfg, "session", self.hb(), 1)
        cc.send(cfg, "prompt", {"session_id": "s", "excerpt": "x"}, 1)
        with open(cc._queue_path()) as f:
            text = f.read()
        self.assertNotIn("heartbeat", text)
        self.assertEqual(cc.queue_size(), 2)

    def test_heartbeat_not_queued_on_retry(self):
        with mock.patch.object(cc, "deliver", lambda *a: ("retry", cc.Resp(0))):
            cc.send({"url": "u", "token": "t"}, "session", self.hb(), 1)
        self.assertEqual(cc.queue_size(), 0)


class TestF10ParseCommand(EnvCase):
    def test_long_kebab_name_accepted(self):
        name = "add-very-long-change-name-for-the-user-profile-page"
        self.assertGreaterEqual(len(name), 32)
        self.assertEqual(cc.parse_command("/opsx:apply " + name), name)
        self.assertEqual(cc.parse_command("/opsx:apply " + "a" * 150), "a" * 150)

    def test_secret_families_rejected(self):
        for tok in ("sk-abcdef123456", "ghp_" + "a1B2c3D4e5F6g7H8i9", "AKIA" + "ABCDEFGH12345678",
                    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc", fake_token()):
            with self.subTest(tok):
                self.assertEqual(cc.parse_command("/opsx:apply " + tok), "")

    def test_dead_code_removed(self):
        self.assertFalse(hasattr(cc, "session_cache_get"))


class TestF9Skills(unittest.TestCase):
    def fm(self, name):
        with open(os.path.join(PLUGIN, "skills", name, "SKILL.md"), encoding="utf-8") as f:
            return re.search(r"^allowed-tools:\s*(.*)$", f.read(), re.M).group(1)

    def test_no_bare_python_wildcard(self):
        for n in ("setup", "status", "uninstall", "send-traces", "send-openspec"):
            self.assertNotIn("python3:*", self.fm(n), n)
            self.assertNotRegex(self.fm(n), r"Bash\(python3 :?\*\)")

    def test_scoped_commands(self):
        self.assertIn("Bash(company-cost status)", self.fm("status"))
        self.assertIn('Bash(python3 "${CLAUDE_PLUGIN_ROOT}/bin/company-cost" status)', self.fm("status"))
        self.assertIn("Bash(company-cost uninstall)", self.fm("uninstall"))
        for n in ("send-traces", "send-openspec"):
            self.assertIn("Bash(company-cost %s:*)" % n, self.fm(n))
        s = self.fm("setup")
        self.assertIn("Bash(printf:*)", s)
        self.assertIn("Bash(company-cost setup:*)", s)
        self.assertIn('Bash(python3 "${CLAUDE_PLUGIN_ROOT}/bin/company-cost" setup:*)', s)


if __name__ == "__main__":
    unittest.main()
