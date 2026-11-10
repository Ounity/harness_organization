"""Subprocess hook tests against a fake local HTTP server (no external network)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(PLUGIN_ROOT, "bin", "company-cost")
VERSION = "0.2.0"
SESSION_KEYS = {"event", "session_id", "project_key", "project_name", "branch", "repo_display",
                "change_name", "change_source", "cwd_hash"}


def make_token():
    return "hro_" + ("Ab3dE6gH9k" * 4) + "xyz"


def slurp(path):
    with open(path) as f:
        return f.read()


class FakeServer(object):
    def __init__(self, responder=None):
        self.requests = []
        self.release = threading.Event()
        self.responder = responder or self.default_responder
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = None
                req = {"method": "POST", "path": self.path, "headers": dict((k.lower(), v) for k, v in self.headers.items()),
                       "body": body}
                outer.requests.append(req)
                status, headers, resp = outer.responder(req)
                data = json.dumps(resp).encode("utf-8")
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = "http://127.0.0.1:%d" % self.port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @staticmethod
    def default_responder(req):
        if req["path"].endswith("/sync/plan"):
            files = (req["body"] or {}).get("files", [])
            return 200, {}, {"upload": [f["path"] for f in files], "delete": ["old.md"], "skipped": 0}
        return 200, {}, {"ok": True}

    def stop(self):
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()

    def paths(self):
        return [r["path"].replace("/v1/ingest/plugin/", "") for r in self.requests]


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com"] + list(args),
                          cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True).stdout.decode().strip()


class HookCase(unittest.TestCase):
    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git not available")
        self.tmp = tempfile.mkdtemp(prefix="cc-hook-")
        self.cfg = os.path.join(self.tmp, "cfg")
        self.data = os.path.join(self.tmp, "data")
        self.home = os.path.join(self.tmp, "home")
        self.shim = os.path.join(self.tmp, "shim")
        for d in (self.cfg, self.home, self.shim):
            os.makedirs(d)
        self.token = make_token()
        self.servers = []
        self.server = self.new_server()
        self.addCleanup(self.cleanup)
        self.repo = self.make_repo("proj", "https://git.example.com/team/app.git")

    def cleanup(self):
        # wait for any detached sync child to release its lock
        deadline = time.time() + 15
        while time.time() < deadline:
            locks = [f for f in (os.listdir(self.data) if os.path.isdir(self.data) else []) if f.startswith("sync-")]
            if not locks:
                break
            time.sleep(0.1)
        for s in self.servers:
            s.stop()
        shutil.rmtree(self.tmp, True)

    def new_server(self, responder=None):
        s = FakeServer(responder)
        self.servers.append(s)
        return s

    def make_repo(self, name, remote=None, commit=True):
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        git(d, "init", "-q", "-b", "main")
        if remote:
            git(d, "remote", "add", "origin", remote)
        if commit:
            git(d, "commit", "-q", "--allow-empty", "-m", "init")
        return d

    def write_openspec_shim(self, output):
        out = os.path.join(self.tmp, "openspec.out")
        with open(out, "w") as f:
            f.write(output)
        p = os.path.join(self.shim, "openspec")
        with open(p, "w") as f:
            f.write('#!/bin/sh\n/bin/cat "%s"\n' % out)
        os.chmod(p, 0o755)

    def env(self, configured=True, **extra):
        e = {
            "PATH": "/usr/bin:/bin:" + self.shim,
            "HOME": self.home,
            "CLAUDE_CONFIG_DIR": self.cfg,
            "CLAUDE_PLUGIN_DATA": self.data,
            "COMPANY_COST_NO_SYNC": "1",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
        if configured:
            e["CLAUDE_PLUGIN_OPTION_RAG_URL"] = self.server.url
            e["CLAUDE_PLUGIN_OPTION_RAG_TOKEN"] = self.token
        for k, v in extra.items():
            if v is None:
                e.pop(k, None)
            else:
                e[k] = v
        return e

    def hook(self, event, payload=None, raw=None, env=None, timeout=40):
        if raw is None:
            raw = json.dumps(payload)
        t0 = time.monotonic()
        p = subprocess.run([sys.executable, SCRIPT, "hook", event], input=raw.encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env or self.env(), timeout=timeout)
        p.elapsed = time.monotonic() - t0
        return p

    def base(self, **kw):
        d = {"session_id": "sess-1", "cwd": self.repo}
        d.update(kw)
        return d

    def assertSilent(self, proc):
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(proc.stderr, b"")

    def data_text(self):
        chunks = []
        for base, _, files in os.walk(self.data):
            for fn in files:
                with open(os.path.join(base, fn), "rb") as f:
                    chunks.append(f.read().decode("utf-8", "replace"))
        return "\n".join(chunks)

    def state(self):
        return json.loads(slurp(os.path.join(self.data, "state.json")))

    def clear_backoff(self):
        p = os.path.join(self.data, "state.json")
        st = json.loads(slurp(p))
        st["backoff_until"] = 0
        with open(p, "w") as f:
            json.dump(st, f)

    def queue_items(self):
        p = os.path.join(self.data, "queue.jsonl")
        if not os.path.exists(p):
            return []
        return [json.loads(x) for x in slurp(p).splitlines() if x.strip()]


# ---------------------------------------------------------------------------
class TestHappyPath(HookCase):
    def test_four_events(self):
        long_prompt = "/opsx:apply my-change please password is hunter2 " + "word " * 100
        procs = [
            self.hook("SessionStart", self.base()),
            self.hook("UserPromptSubmit", self.base(prompt=long_prompt)),
            self.hook("Stop", self.base()),
            self.hook("SessionEnd", self.base()),
        ]
        for p in procs:
            self.assertSilent(p)
        reqs = self.server.requests
        self.assertEqual(self.server.paths(), ["session", "session", "prompt", "session", "session"])
        for r in reqs:
            self.assertEqual(r["method"], "POST")
            self.assertTrue(r["path"].startswith("/v1/ingest/plugin/"))
            self.assertEqual(r["headers"]["authorization"], "Bearer " + self.token)
            self.assertEqual(r["headers"]["content-type"], "application/json")
            self.assertEqual(r["headers"]["user-agent"], "company-cost/" + VERSION)
        self.assertEqual([r["body"]["event"] for r in reqs if "event" in r["body"]],
                         ["start", "heartbeat", "heartbeat", "end"])
        start = reqs[0]["body"]
        self.assertEqual(set(start), SESSION_KEYS)
        self.assertNotIn("user_ref", start)
        self.assertEqual(start["session_id"], "sess-1")
        self.assertEqual(start["project_key"], "git.example.com/team/app")
        self.assertEqual(start["project_name"], "app")
        self.assertEqual(start["branch"], "main")
        self.assertEqual(len(start["cwd_hash"]), 16)
        prompt = reqs[2]["body"]
        self.assertEqual(set(prompt), {"session_id", "excerpt", "change_name"})
        self.assertLessEqual(len(prompt["excerpt"]), 200)
        self.assertNotIn("hunter2", prompt["excerpt"])
        self.assertEqual(prompt["change_name"], "my-change")
        self.assertEqual(reqs[1]["body"]["change_source"], "command")
        # change is cached per session: Stop/SessionEnd reuse it
        self.assertEqual(reqs[3]["body"]["change_name"], "my-change")
        self.assertEqual(reqs[4]["body"]["change_name"], "my-change")
        st = self.state()
        self.assertTrue(st["last_ok"])
        self.assertIsNone(st["last_error"])

    def test_no_prompts_env_skips_prompt_post(self):
        p = self.hook("UserPromptSubmit", self.base(prompt="hello there"),
                      env=self.env(COMPANY_COST_NO_PROMPTS="1"))
        self.assertSilent(p)
        self.assertEqual(self.server.paths(), ["session"])

    def test_recent_change_from_openspec_shim(self):
        self.write_openspec_shim(json.dumps({"changes": [
            {"name": "older", "lastModified": "2024-01-01T00:00:00Z"},
            {"name": "newest", "lastModified": "2025-01-01T00:00:00Z"}]}))
        self.assertSilent(self.hook("SessionStart", self.base()))
        body = self.server.requests[0]["body"]
        self.assertEqual((body["change_name"], body["change_source"]), ("newest", "recent"))

    def test_branch_change(self):
        os.makedirs(os.path.join(self.repo, "openspec", "changes", "main"))
        self.assertSilent(self.hook("SessionStart", self.base()))
        body = self.server.requests[0]["body"]
        self.assertEqual((body["change_name"], body["change_source"]), ("main", "branch"))

    def test_raw_prompt_never_on_disk_or_output(self):
        tail = "UNIQUE-TAIL-MARKER-" + "z" * 10
        secret = "sk-RAWSECRETVALUE12345"
        prompt = "please use " + secret + " " + "pad " * 80 + tail
        srv_down = self.new_server()
        srv_down.stop()
        self.servers.remove(srv_down)
        for env in (self.env(), self.env(CLAUDE_PLUGIN_OPTION_RAG_URL=srv_down.url)):
            p = self.hook("UserPromptSubmit", self.base(prompt=prompt), env=env)
            self.assertSilent(p)
            blob = self.data_text()
            for needle in (secret, tail, prompt, self.token):
                self.assertNotIn(needle, blob)
        # server-bound excerpt is redacted too
        for r in self.server.requests:
            self.assertNotIn(secret, json.dumps(r["body"]))
        self.assertTrue(self.queue_items())
        for item in self.queue_items():
            self.assertNotIn(self.token, json.dumps(item))

    def test_no_session_start_uses_cwd_default(self):
        p = subprocess.run([sys.executable, SCRIPT, "hook", "SessionEnd"], input=b'{"session_id":"s"}',
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env(), cwd=self.repo, timeout=30)
        self.assertSilent(p)
        self.assertEqual(self.server.requests[0]["body"]["project_key"], "git.example.com/team/app")


# ---------------------------------------------------------------------------
class TestFailureHandling(HookCase):
    def test_500_queues_and_backs_off(self):
        self.server.responder = lambda r: (500, {}, {})
        self.assertSilent(self.hook("SessionEnd", self.base()))
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(len(self.queue_items()), 1)
        st = self.state()
        self.assertEqual(st["last_error"], "http_500")
        self.assertGreater(st["backoff_until"], time.time() + 30)
        self.assertSilent(self.hook("Stop", self.base()))
        self.assertEqual(len(self.server.requests), 1, "no network during backoff")
        self.assertEqual(len(self.queue_items()), 1, "heartbeats are never queued")

    def test_429_honours_retry_after(self):
        self.server.responder = lambda r: (429, {"Retry-After": "9"}, {})
        self.assertSilent(self.hook("SessionEnd", self.base()))
        self.assertEqual(len(self.queue_items()), 1)
        self.assertAlmostEqual(self.state()["backoff_until"] - time.time(), 9, delta=3)

    def test_401_recorded_not_queued_status_says_rejected(self):
        self.server.responder = lambda r: (401, {}, {})
        self.assertSilent(self.hook("SessionEnd", self.base()))
        self.assertEqual(self.queue_items(), [])
        self.assertEqual(self.state()["last_error"], "http_401")
        self.assertSilent(self.hook("Stop", self.base()))
        self.assertEqual(len(self.server.requests), 1, "401 backs off (~15 min) so hooks do not hammer")
        self.assertGreater(self.state()["backoff_until"], time.time() + 600)
        self.assertEqual(self.state()["last_error"], "http_401")
        p = subprocess.run([sys.executable, SCRIPT, "status"], env=self.env(), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=30)
        out = p.stdout.decode()
        self.assertIn("token rejected", out)
        self.assertNotIn(self.token, out)

    def test_other_4xx_dropped(self):
        for code in (413, 422, 409):
            shutil.rmtree(self.data, True)
            self.server.responder = lambda r, c=code: (c, {}, {})
            self.assertSilent(self.hook("SessionEnd", self.base()))
            self.assertEqual(self.queue_items(), [], code)
            self.assertLess(self.state().get("backoff_until", 0), time.time() + 1)
            self.assertEqual(self.state()["last_error"], "http_%d" % code)

    def test_server_down(self):
        dead = self.new_server()
        dead.stop()
        env = self.env(CLAUDE_PLUGIN_OPTION_RAG_URL=dead.url)
        self.assertSilent(self.hook("SessionEnd", self.base(), env=env))
        self.assertEqual(len(self.queue_items()), 1)
        self.assertEqual(self.state()["last_error"], "network")

    def test_server_hang_stays_within_budget(self):
        def hang(req):
            self.server.release.wait(15)
            return 200, {}, {}
        self.server.responder = hang
        end = self.hook("SessionEnd", self.base())
        self.assertSilent(end)
        self.assertLess(end.elapsed, 4.0)
        shutil.rmtree(self.data, True)
        start = self.hook("SessionStart", self.base())
        self.assertSilent(start)
        self.assertLess(start.elapsed, 7.0)
        shutil.rmtree(self.data, True)
        prompt = self.hook("UserPromptSubmit", self.base(prompt="hi"))
        self.assertSilent(prompt)
        self.assertLess(prompt.elapsed, 5.0)

    def test_next_session_start_flushes_in_order(self):
        self.server.responder = lambda r: (500, {}, {})
        self.assertSilent(self.hook("UserPromptSubmit", self.base(prompt="/opsx:apply chg hello")))
        self.assertSilent(self.hook("Stop", self.base()))
        self.assertEqual(len(self.queue_items()), 1, "only the prompt is queued; heartbeats are dropped")
        self.server.requests[:] = []
        self.server.responder = FakeServer.default_responder
        self.clear_backoff()
        self.assertSilent(self.hook("SessionStart", self.base()))
        got = [(r["path"].replace("/v1/ingest/plugin/", ""), r["body"].get("event")) for r in self.server.requests]
        self.assertEqual(got, [("session", "start"), ("prompt", None)])
        self.assertEqual(self.queue_items(), [])
        self.assertEqual(self.server.requests[1]["body"]["excerpt"], "/opsx:apply chg hello")

    def test_redirect_does_not_leak_token(self):
        target = self.new_server()
        self.server.responder = lambda r: (307, {"Location": target.url + r["path"]}, {})
        self.assertSilent(self.hook("SessionEnd", self.base()))
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(target.requests, [])
        for r in target.requests:
            self.assertNotIn("authorization", r["headers"])
        self.assertEqual(self.state()["last_error"], "http_307")
        self.assertEqual(self.queue_items(), [])


# ---------------------------------------------------------------------------
class TestNoOpAndRobustness(HookCase):
    def test_missing_config_is_noop(self):
        p = self.hook("SessionStart", self.base(), env=self.env(configured=False))
        self.assertSilent(p)
        self.assertEqual(self.server.requests, [])
        self.assertFalse(os.path.exists(self.data))

    def test_garbage_stdin(self):
        for raw in ("", "not json", "[1,2]", "null", "\xff\xfe", "{" * 10000):
            self.assertSilent(self.hook("SessionStart", raw=raw))
        self.assertEqual(self.server.requests, [])

    def test_no_session_id(self):
        for payload in ({"cwd": self.repo}, {"session_id": "", "cwd": self.repo}, {"session_id": 5}):
            self.assertSilent(self.hook("SessionStart", payload))
        self.assertEqual(self.server.requests, [])

    def test_unknown_or_missing_event(self):
        self.assertSilent(self.hook("Bogus", self.base()))
        p = subprocess.run([sys.executable, SCRIPT, "hook"], input=b"{}", stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=self.env(), timeout=30)
        self.assertSilent(p)
        self.assertEqual(self.server.requests, [])

    def test_unwritable_data_dir(self):
        blocker = os.path.join(self.tmp, "blocker")
        with open(blocker, "w") as f:
            f.write("x")
        env = self.env(CLAUDE_PLUGIN_DATA=os.path.join(blocker, "sub"))
        for ev in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"):
            self.assertSilent(self.hook(ev, self.base(prompt="hi"), env=env))
        self.assertTrue(self.server.requests)

    def test_unwritable_data_dir_with_failing_server(self):
        blocker = os.path.join(self.tmp, "blocker")
        with open(blocker, "w") as f:
            f.write("x")
        self.server.responder = lambda r: (500, {}, {})
        self.assertSilent(self.hook("SessionEnd", self.base(), env=self.env(CLAUDE_PLUGIN_DATA=blocker + "/x")))

    def test_no_git_binary(self):
        env = self.env(PATH=self.shim)
        plain = os.path.join(self.tmp, "plain-folder")
        os.makedirs(plain)
        self.assertSilent(self.hook("SessionStart", self.base(cwd=plain), env=env))
        body = self.server.requests[0]["body"]
        self.assertEqual(body["project_key"], "plain-folder")
        self.assertEqual(body["branch"], "")

    def test_not_a_git_repo(self):
        plain = os.path.join(self.tmp, "nogit")
        os.makedirs(plain)
        self.assertSilent(self.hook("SessionStart", self.base(cwd=plain)))
        self.assertEqual(self.server.requests[0]["body"]["project_key"], "nogit")

    def test_nonexistent_cwd(self):
        self.assertSilent(self.hook("SessionStart", self.base(cwd=os.path.join(self.tmp, "missing"))))

    def test_detached_head(self):
        git(self.repo, "checkout", "-q", "--detach")
        sha = git(self.repo, "rev-parse", "--short=7", "HEAD")
        self.assertSilent(self.hook("SessionStart", self.base()))
        self.assertEqual(self.server.requests[0]["body"]["branch"], "detached-" + sha)

    def test_credentialed_remote_not_leaked(self):
        repo = self.make_repo("cred", "https://deployuser:SuperSecretPw@Git.Example.com:8443/Team/App.git")
        self.assertSilent(self.hook("SessionStart", self.base(cwd=repo)))
        body = self.server.requests[0]["body"]
        self.assertEqual(body["project_key"], "git.example.com/Team/App")
        blob = json.dumps(body) + self.data_text()
        self.assertNotIn("SuperSecretPw", blob)
        self.assertNotIn("deployuser", blob)

    def test_repo_without_remote_uses_root_commit(self):
        repo = self.make_repo("noremote", None)
        root = git(repo, "rev-list", "--max-parents=0", "HEAD")
        self.assertSilent(self.hook("SessionStart", self.base(cwd=repo)))
        self.assertEqual(self.server.requests[0]["body"]["project_key"], "git-root-" + root[:16])

    def test_no_openspec(self):
        env = self.env(PATH="/usr/bin:/bin")
        self.assertSilent(self.hook("SessionStart", self.base(), env=env))
        body = self.server.requests[0]["body"]
        self.assertEqual((body["change_name"], body["change_source"]), ("", ""))

    def test_garbage_openspec_output(self):
        for out in ("}{ garbage", "", "null", '{"changes": "x"}', "\x00\x01"):
            self.server.requests[:] = []
            self.write_openspec_shim(out)
            self.assertSilent(self.hook("UserPromptSubmit", self.base(session_id=out[:5] or "e", prompt="hi")))
            self.assertEqual(self.server.requests[0]["body"]["change_name"], "")

    def test_openspec_hang_is_bounded(self):
        p = os.path.join(self.shim, "openspec")
        with open(p, "w") as f:
            f.write("#!/bin/sh\n/bin/sleep 30\n")
        os.chmod(p, 0o755)
        r = self.hook("SessionEnd", self.base())
        self.assertSilent(r)
        self.assertLess(r.elapsed, 12)


# ---------------------------------------------------------------------------
class TestSyncChild(HookCase):
    def populate(self):
        os.makedirs(os.path.join(self.repo, "openspec", "changes", "foo"))
        os.makedirs(os.path.join(self.repo, "openspec", "changes", "archive", "x"))
        os.makedirs(os.path.join(self.repo, "openspec", "specs", "s"))
        for rel, text in (("changes/foo/proposal.md", "# proposal é"), ("specs/s/spec.md", "# spec"),
                          ("changes/archive/x/p.md", "archived"), ("specs/s/notes.txt", "nope")):
            with open(os.path.join(self.repo, "openspec", rel), "w", encoding="utf-8") as f:
                f.write(text)

    def wait_for(self, suffix, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if suffix in self.server.paths():
                return True
            time.sleep(0.1)
        return False

    def test_session_start_spawns_sync(self):
        self.populate()
        self.write_openspec_shim(json.dumps({"changes": [
            {"name": "foo", "completedTasks": 1, "totalTasks": 3, "status": "in-progress",
             "lastModified": "2025-01-01T00:00:00Z"}]}))
        self.assertSilent(self.hook("SessionStart", self.base(), env=self.env(COMPANY_COST_NO_SYNC=None)))
        self.assertTrue(self.wait_for("snapshot"), self.server.paths())
        plan = [r for r in self.server.requests if r["path"].endswith("/sync/plan")][0]["body"]
        self.assertEqual(plan["project_key"], "git.example.com/team/app")
        self.assertEqual(plan["branch"], "main")
        self.assertEqual([f["path"] for f in plan["files"]], ["changes/foo/proposal.md", "specs/s/spec.md"])
        self.assertTrue(all(len(f["sha256"]) == 64 for f in plan["files"]))
        files = [r for r in self.server.requests if r["path"].endswith("/sync/files")]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["body"]["delete"], ["old.md"])
        self.assertEqual({f["path"]: f["content"] for f in files[0]["body"]["files"]},
                         {"changes/foo/proposal.md": "# proposal é", "specs/s/spec.md": "# spec"})
        snap = [r for r in self.server.requests if r["path"].endswith("/snapshot")][0]["body"]
        self.assertEqual(snap["snapshot"]["changes"][0]["name"], "foo")
        self.assertEqual(snap["snapshot"]["changes"][0]["totalTasks"], 3)
        for r in self.server.requests:
            self.assertEqual(r["headers"]["authorization"], "Bearer " + self.token)
        deadline = time.time() + 10
        while time.time() < deadline and "last_sync" not in self.state():
            time.sleep(0.1)
        self.assertIn("last_sync", self.state())

    def test_sync_only_uploads_paths_from_plan(self):
        self.populate()

        def responder(req):
            if req["path"].endswith("/sync/plan"):
                return 200, {}, {"upload": ["specs/s/spec.md", "../evil.md"], "delete": [], "skipped": 1}
            return 200, {}, {}
        self.server.responder = responder
        self.assertSilent(self.hook("SessionStart", self.base(), env=self.env(COMPANY_COST_NO_SYNC=None)))
        self.assertTrue(self.wait_for("sync/files"), self.server.paths())
        files = [r for r in self.server.requests if r["path"].endswith("/sync/files")][0]["body"]
        self.assertEqual([f["path"] for f in files["files"]], ["specs/s/spec.md"])
        self.assertEqual(files["delete"], [])

    def test_stop_respects_interval(self):
        self.populate()
        self.assertSilent(self.hook("SessionStart", self.base(), env=self.env(COMPANY_COST_NO_SYNC=None)))
        self.assertTrue(self.wait_for("sync/files"))
        time.sleep(0.5)
        n = len(self.server.requests)
        self.assertSilent(self.hook("Stop", self.base(), env=self.env(COMPANY_COST_NO_SYNC=None)))
        time.sleep(1.5)
        extra = [p for p in self.server.paths()[n:] if p.startswith("sync/")]
        self.assertEqual(extra, [], "Stop within 120s must not re-sync")

    def test_stop_spawns_when_due(self):
        self.populate()
        self.assertSilent(self.hook("SessionStart", self.base(), env=self.env(COMPANY_COST_NO_SYNC=None)))
        self.assertTrue(self.wait_for("sync/files"))
        time.sleep(0.5)
        st = self.state()
        st["sync"] = dict((k, time.time() - 500) for k in st["sync"])
        with open(os.path.join(self.data, "state.json"), "w") as f:
            json.dump(st, f)
        n = len(self.server.requests)
        self.assertSilent(self.hook("Stop", self.base(), env=self.env(COMPANY_COST_NO_SYNC=None)))
        deadline = time.time() + 15
        while time.time() < deadline and not [p for p in self.server.paths()[n:] if p == "sync/plan"]:
            time.sleep(0.1)
        self.assertIn("sync/plan", self.server.paths()[n:])

    def test_no_sync_env(self):
        self.populate()
        self.assertSilent(self.hook("SessionStart", self.base()))
        time.sleep(1.0)
        self.assertEqual([p for p in self.server.paths() if p.startswith("sync/")], [])


class TestCliSubprocess(HookCase):
    def test_setup_with_token_stdin_via_real_process(self):
        env = self.env(configured=False)
        argv = [sys.executable, SCRIPT, "setup", "--url", "https://ingest.example.com", "--token-stdin"]
        self.assertNotIn(self.token, " ".join(argv))
        p = subprocess.run(argv, input=(self.token + "\n").encode(), env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn(self.token.encode(), p.stdout + p.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.cfg, "settings.json")))  # setup alone: no settings
        p = subprocess.run([sys.executable, SCRIPT, "send-traces"], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn(self.token.encode(), p.stdout + p.stderr)
        settings = json.loads(slurp(os.path.join(self.cfg, "settings.json")))
        self.assertEqual(len(settings["env"]), 9)
        # the sidecar alone now configures hooks (no env options)
        self.server.stop()
        s2 = self.new_server()
        sc_path = os.path.join(self.cfg, "settings.company-cost.json")
        sc = json.loads(slurp(sc_path))
        sc["url"] = s2.url
        sc["allow_insecure"] = False
        with open(sc_path, "w") as f:
            json.dump(sc, f)
        self.assertSilent(self.hook("SessionEnd", self.base(), env=self.env(configured=False)))
        self.assertEqual(len(s2.requests), 1)
        self.assertEqual(s2.requests[0]["headers"]["authorization"], "Bearer " + self.token)


if __name__ == "__main__":
    unittest.main()
