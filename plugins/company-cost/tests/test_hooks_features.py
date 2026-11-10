"""Hook gating matrix: which endpoints are hit (and what they carry) per feature combination."""
import json
import os
import time
import unittest

from tests.test_hooks_fake_server import HookCase, slurp

COMBOS = {
    "neither": {"costs": False, "openspec": False},
    "costs": {"costs": True, "openspec": False},
    "openspec": {"costs": False, "openspec": True},
    "both": {"costs": True, "openspec": True},
}
CHANGE_FIELDS = ("change_name", "change_source")


class GateCase(HookCase):
    def setUp(self):
        super().setUp()
        self.calls = os.path.join(self.tmp, "openspec.calls")

    def set_features(self, name):
        with open(os.path.join(self.cfg, "settings.company-cost.json"), "w") as f:
            json.dump({"features": COMBOS[name], "version": "0.2.0"}, f)

    def recording_shim(self):
        out = os.path.join(self.tmp, "openspec.out")
        with open(out, "w") as f:
            f.write(json.dumps({"changes": [{"name": "foo", "completedTasks": 1, "totalTasks": 3,
                                             "status": "in-progress", "lastModified": "2025-01-01T00:00:00Z"}]}))
        p = os.path.join(self.shim, "openspec")
        with open(p, "w") as f:
            f.write('#!/bin/sh\necho called >> "%s"\n/bin/cat "%s"\n' % (self.calls, out))
        os.chmod(p, 0o755)

    def populate(self):
        os.makedirs(os.path.join(self.repo, "openspec", "changes", "foo"))
        with open(os.path.join(self.repo, "openspec", "changes", "foo", "proposal.md"), "w") as f:
            f.write("# proposal")

    def openspec_calls(self):
        return len(slurp(self.calls).splitlines()) if os.path.exists(self.calls) else 0

    def run_session(self, prompt="hello world /opsx:apply foo"):
        env = self.env(COMPANY_COST_NO_SYNC=None)
        for ev, extra in (("SessionStart", {}), ("UserPromptSubmit", {"prompt": prompt}),
                          ("Stop", {}), ("SessionEnd", {})):
            self.assertSilent(self.hook(ev, self.base(**extra), env=env))

    def settle(self, seconds=1.5):
        time.sleep(seconds)

    def sync_paths(self):
        return [p for p in self.server.paths() if p.startswith("sync/") or p == "snapshot"]


class TestGatingMatrix(GateCase):
    def prepare(self, combo):
        self.populate()
        self.recording_shim()
        self.set_features(combo)

    def test_neither_is_silent_noop(self):
        self.prepare("neither")
        self.run_session()
        self.settle()
        self.assertEqual(self.server.requests, [])
        self.assertEqual(self.openspec_calls(), 0)
        self.assertFalse(os.path.exists(os.path.join(self.data, "queue.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(self.data, "state.json")))

    def test_costs_only(self):
        self.prepare("costs")
        self.run_session()
        self.settle()
        self.assertEqual(self.server.paths(), ["session", "session", "prompt", "session", "session"])
        self.assertEqual([r["body"]["event"] for r in self.server.requests if "event" in r["body"]],
                         ["start", "heartbeat", "heartbeat", "end"])
        for r in self.server.requests:
            for f in CHANGE_FIELDS:
                self.assertNotIn(f, r["body"], r["path"])
        self.assertEqual(set(self.server.requests[2]["body"]), {"session_id", "excerpt"})
        self.assertEqual(self.openspec_calls(), 0, "no openspec subprocess")
        self.assertEqual(self.sync_paths(), [])

    def test_costs_only_still_honours_no_prompts(self):
        self.prepare("costs")
        self.assertSilent(self.hook("UserPromptSubmit", self.base(prompt="hi"),
                                    env=self.env(COMPANY_COST_NO_PROMPTS="1")))
        self.assertEqual(self.server.paths(), ["session"])

    def test_openspec_only_sends_only_start_plus_sync(self):
        self.prepare("openspec")
        self.run_session()
        deadline = time.time() + 20
        while time.time() < deadline and "snapshot" not in self.server.paths():
            time.sleep(0.1)
        self.settle(0.5)
        paths = self.server.paths()
        self.assertEqual(paths[0], "session")
        self.assertEqual(paths.count("session"), 1, paths)
        self.assertNotIn("prompt", paths)
        sess = self.server.requests[0]["body"]
        self.assertEqual(sess["event"], "start")
        self.assertIn("change_name", sess)
        self.assertIn("change_source", sess)
        self.assertEqual(sess["project_key"], "git.example.com/team/app")
        self.assertLess(paths.index("session"), paths.index("sync/plan"), "registration precedes sync")
        for needed in ("sync/plan", "sync/files", "snapshot"):
            self.assertIn(needed, paths)
        self.assertGreaterEqual(self.openspec_calls(), 1)

    def test_openspec_only_no_sync_env_honoured(self):
        self.prepare("openspec")
        self.assertSilent(self.hook("SessionStart", self.base()))  # env has COMPANY_COST_NO_SYNC=1
        self.settle()
        self.assertEqual(self.server.paths(), ["session"])

    def test_both_behaves_like_0_1_0(self):
        self.prepare("both")
        self.run_session()
        deadline = time.time() + 20
        while time.time() < deadline and "snapshot" not in self.server.paths():
            time.sleep(0.1)
        self.settle(0.5)
        paths = self.server.paths()
        self.assertEqual([p for p in paths if p in ("session", "prompt")],
                         ["session", "session", "prompt", "session", "session"])
        for needed in ("sync/plan", "sync/files", "snapshot"):
            self.assertIn(needed, paths)
        for r in self.server.requests:
            if "event" in r["body"]:
                self.assertIn("change_name", r["body"])
                self.assertIn("change_source", r["body"])
        self.assertEqual(self.server.requests[-1]["body"]["change_name"], "foo")

    def test_legacy_sidecar_without_features_runs_everything(self):
        self.populate()
        self.recording_shim()
        with open(os.path.join(self.cfg, "settings.company-cost.json"), "w") as f:
            json.dump({"version": "0.1.0", "url": self.server.url}, f)
        self.run_session()
        self.settle()
        self.assertIn("prompt", self.server.paths())
        self.assertIn("sync/plan", self.server.paths())

    def test_env_only_config_without_sidecar_sends_nothing(self):
        # plugin-option-only users have no sidecar: no opt-in, so no feature is on
        os.unlink(os.path.join(self.cfg, "settings.company-cost.json"))
        self.populate()
        self.recording_shim()
        self.run_session()
        self.settle()
        self.assertEqual(self.server.requests, [])
        self.assertEqual(self.openspec_calls(), 0)

    def test_corrupt_sidecar_sends_nothing(self):
        with open(os.path.join(self.cfg, "settings.company-cost.json"), "w") as f:
            f.write("{not json")
        self.run_session()
        self.settle()
        self.assertEqual(self.server.requests, [])

    def test_legacy_0_1_0_sidecar_runs_everything(self):
        with open(os.path.join(self.cfg, "settings.company-cost.json"), "w") as f:
            json.dump({"url": self.server.url, "token": self.token, "version": "0.1.0"}, f)
        self.run_session()
        self.settle()
        self.assertIn("prompt", self.server.paths())


class TestGatingFailures(GateCase):
    def test_openspec_only_start_queued_on_500_not_prompts(self):
        self.recording_shim()
        self.set_features("openspec")
        self.server.responder = lambda r: (500, {}, {})
        self.run_session()
        self.settle()
        items = self.queue_items()
        self.assertEqual([i["path"] for i in items], ["session"])
        self.assertEqual(items[0]["payload"]["event"], "start")

    def test_disabled_feature_items_in_queue_are_not_sent(self):
        self.set_features("costs")
        os.makedirs(self.data, exist_ok=True)
        with open(os.path.join(self.data, "queue.jsonl"), "w") as f:
            for suffix, payload in (("snapshot", {"project_key": "k", "snapshot": {"changes": []}}),
                                    ("session", {"event": "start", "session_id": "q"})):
                f.write(json.dumps({"path": suffix, "payload": payload, "ts": time.time()}) + "\n")
        self.assertSilent(self.hook("SessionStart", self.base()))
        self.assertEqual(self.server.paths().count("snapshot"), 0)
        self.assertEqual(self.server.paths().count("session"), 2)  # queued start + new start
        self.assertEqual(self.queue_items(), [])

    def test_costs_off_drops_queued_prompts(self):
        self.set_features("openspec")
        os.makedirs(self.data, exist_ok=True)
        with open(os.path.join(self.data, "queue.jsonl"), "w") as f:
            f.write(json.dumps({"path": "prompt", "payload": {"session_id": "q", "excerpt": "old"},
                                "ts": time.time()}) + "\n")
        self.assertSilent(self.hook("SessionStart", self.base()))
        self.assertNotIn("prompt", self.server.paths())
        self.assertEqual(self.queue_items(), [])


if __name__ == "__main__":
    unittest.main()
