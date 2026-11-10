"""Optional environment label: user_ref format `agent:env=<environment>/name=<name>`."""
import json
import os
import time
import unittest

from tests.test_agent_name import AgentHookCase, write_json
from tests.test_company_cost import EnvCase, cc, fake_token, slurp

URL = "https://ingest.example.com"
OPT_ENV = "CLAUDE_PLUGIN_OPTION_ENVIRONMENT"
OPT_AGENT = "CLAUDE_PLUGIN_OPTION_AGENT_NAME"


class TestEnvironmentValidation(unittest.TestCase):
    def test_valid(self):
        for v in ("dev", "staging", "prod", "eu-west_1", "prod:blue", "v1.2", "Pre Prod"):
            self.assertEqual(cc.validate_environment(v), (v, None))
        self.assertEqual(cc.validate_environment("  prod  "), ("prod", None))

    def test_invalid(self):
        for v in ("", "   ", "x" * 49, "a\nb", "a\x00b", "a​b", "a/b", "a=b", "/", "prod/", "a;b", "a'b",
                  "password:hunter2", "hro_" + "Ab3dE6gH9k" * 4, "Ａbc", None, 3):
            clean, err = cc.validate_environment(v)
            self.assertIsNone(clean, repr(v))
            self.assertTrue(err, repr(v))

    def test_slash_rejected_in_agent_name(self):
        self.assertIsNotNone(cc.validate_agent_name("a/b")[1])


class TestFormat(unittest.TestCase):
    def test_four_cases(self):
        self.assertEqual(cc.agent_user_ref("planner", "prod"), "agent:env=prod/name=planner")
        self.assertEqual(cc.agent_user_ref("planner", None), "agent:name=planner")
        self.assertEqual(cc.agent_user_ref(None, "prod"), "agent:env=prod")
        self.assertIsNone(cc.agent_user_ref(None, None))

    def test_max_length(self):
        long_name = ("agent " * 11)[:48]
        long_env = ("stage " * 11)[:48]
        self.assertLessEqual(len(cc.agent_user_ref(long_name, long_env)), 200)

    def test_payload_helper(self):
        ident = {"key": "k", "name": "n", "branch": "b", "repo_display": "r"}
        for agent, env, exp in (("a", "e", "agent:env=e/name=a"), ("a", None, "agent:name=a"), (None, "e", "agent:env=e")):
            p = cc._session_payload("start", "s", ident, "/x", None, None, agent=agent, environment=env)
            self.assertEqual(p["user_ref"], exp)
        self.assertNotIn("user_ref", cc._session_payload("start", "s", ident, "/x", None, None))


class TestEnvironmentSetup(EnvCase):
    def side(self):
        return json.loads(slurp(self.sidecar))

    def test_store_keep_update_clear(self):
        self.setup_only(extra=["--environment", " staging "])
        self.assertEqual(self.side()["environment"], "staging")
        self.setup_only()
        self.assertEqual(self.side()["environment"], "staging")
        self.setup_only(extra=["--environment", "prod"])
        self.assertEqual(self.side()["environment"], "prod")
        self.setup_only(extra=["--environment", ""])
        self.assertNotIn("environment", self.side())

    def test_independent_of_agent(self):
        self.setup_only(extra=["--agent", "planner", "--environment", "dev"])
        self.setup_only(extra=["--environment", ""])
        self.assertEqual(self.side()["agent_name"], "planner")
        self.assertNotIn("environment", self.side())
        self.setup_only(extra=["--environment", "dev", "--agent", ""])
        self.assertEqual(self.side()["environment"], "dev")
        self.assertNotIn("agent_name", self.side())

    def test_unset_has_no_key(self):
        self.setup_only()
        self.assertNotIn("environment", self.side())

    def test_leading_dash_via_equals(self):
        self.setup_only(extra=["--environment=-x"])
        self.assertEqual(self.side()["environment"], "-x")

    def test_invalid_rejected_untouched(self):
        self.write_settings({"model": "m"})
        for bad in ("x" * 49, "a/b", "a;b", "bad\nenv", "password:hunter2", "  "):
            rc, out, err = self.run_cli(["setup", "--url", URL, "--token-stdin", "--environment", bad],
                                        stdin=fake_token() + "\n")
            self.assertEqual(rc, 1, repr(bad))
            self.assertIn("environment", err.lower())
            self.assertFalse(os.path.exists(self.sidecar))
            self.assertEqual(self.read_settings(), {"model": "m"})

    def test_survives_send_traces_cycle(self):
        self.setup_only(extra=["--environment", "prod"])
        self.run_cli(["send-traces", "on"])
        self.run_cli(["send-traces", "off"])
        self.assertEqual(self.side()["environment"], "prod")


class TestEnvironmentResolution(EnvCase):
    def test_fallback_and_precedence(self):
        os.environ[OPT_ENV] = "fromenv"
        self.assertEqual(cc.resolve_environment({}), "fromenv")
        self.assertEqual(cc.resolve_environment({"environment": "sidecar"}), "sidecar")
        os.environ[OPT_ENV] = "bad/env"
        self.assertIsNone(cc.resolve_environment({}))
        self.assertIsNone(cc.resolve_environment({"environment": 5}))
        del os.environ[OPT_ENV]
        self.assertIsNone(cc.resolve_environment(None))

    def test_user_ref_for(self):
        self.assertIsNone(cc.resolve_user_ref({}))
        os.environ[OPT_ENV] = "dev"
        self.assertEqual(cc.resolve_user_ref({}), "agent:env=dev")
        self.assertEqual(cc.resolve_user_ref({"agent_name": "p"}), "agent:env=dev/name=p")


class TestEnvironmentStatus(EnvCase):
    def test_status_and_doctor(self):
        self.setup_only(extra=["--agent", "planner", "--environment", "prod"])
        for cmd in ("status", "doctor"):
            out = self.run_cli([cmd])[1]
            self.assertIn("Environment: prod", out)
            self.assertIn("Agent name: planner", out)
            self.assertIn("agent:env=prod/name=planner", out)

    def test_status_variants(self):
        self.setup_only()
        out = self.run_cli(["status"])[1]
        self.assertIn("Environment: not set", out)
        self.setup_only(extra=["--environment", "dev"])
        out = self.run_cli(["status"])[1]
        self.assertIn("Agent name: not set", out)
        self.assertIn("agent:env=dev", out)

    def test_status_env_fallback(self):
        self.setup_only()
        os.environ[OPT_ENV] = "staging"
        self.assertIn("Environment: staging", self.run_cli(["status"])[1])


class TestEnvironmentPayloads(AgentHookCase):
    def conf(self, agent, env, features=None):
        side = {"features": features or {"costs": True, "openspec": False}, "version": "0.3.0"}
        if agent:
            side["agent_name"] = agent
        if env:
            side["environment"] = env
        write_json(os.path.join(self.cfg, "settings.company-cost.json"), side)

    def test_four_combinations_on_all_events(self):
        for i, (agent, env, exp) in enumerate((("planner", "prod", "agent:env=prod/name=planner"),
                                               ("planner", None, "agent:name=planner"),
                                               (None, "prod", "agent:env=prod"),
                                               (None, None, None))):
            self.server.requests[:] = []
            self.conf(agent, env)
            self.full_session()
            events = self.sessions()
            self.assertEqual([e["event"] for e in events], ["start", "heartbeat", "heartbeat", "end"])
            for e in events:
                if exp is None:
                    self.assertNotIn("user_ref", e)
                else:
                    self.assertEqual(e["user_ref"], exp)
            for r in self.server.requests:
                if not r["path"].endswith("/session"):
                    self.assertNotIn("user_ref", json.dumps(r["body"]))

    def test_env_option_fallback(self):
        self.conf(None, None)
        self.assertSilent(self.hook("SessionStart", self.base(), env=self.env(**{OPT_ENV: "dev", OPT_AGENT: "p"})))
        self.assertEqual(self.sessions()[-1]["user_ref"], "agent:env=dev/name=p")
        self.conf("side", "prod")
        self.assertSilent(self.hook("Stop", self.base(), env=self.env(**{OPT_ENV: "dev", OPT_AGENT: "p"})))
        self.assertEqual(self.sessions()[-1]["user_ref"], "agent:env=prod/name=side")

    def test_openspec_only_start_and_no_leak_to_sync(self):
        os.makedirs(os.path.join(self.repo, "openspec", "changes", "foo"))
        with open(os.path.join(self.repo, "openspec", "changes", "foo", "proposal.md"), "w") as f:
            f.write("# p")
        self.write_openspec_shim(json.dumps({"changes": [{"name": "foo", "completedTasks": 1, "totalTasks": 3,
                                                          "status": "in-progress",
                                                          "lastModified": "2025-01-01T00:00:00Z"}]}))
        self.conf(None, "staging", {"costs": False, "openspec": True})
        self.full_session(env=self.env(COMPANY_COST_NO_SYNC=None))
        deadline = time.time() + 20
        while time.time() < deadline and "snapshot" not in self.server.paths():
            time.sleep(0.1)
        time.sleep(0.5)
        sessions = self.sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["user_ref"], "agent:env=staging")
        others = [r for r in self.server.requests if not r["path"].endswith("/session")]
        self.assertTrue(others)
        for r in others:
            self.assertNotIn("user_ref", json.dumps(r["body"]))

    def test_queued_replay_keeps_value(self):
        self.conf("planner", "prod")
        self.server.responder = lambda r: (500, {}, {})
        self.assertSilent(self.hook("SessionEnd", self.base()))
        self.assertIn("agent:env=prod/name=planner", json.dumps(self.queue_items()))
        self.conf("other", "dev")
        self.server.responder = self.server.default_responder
        self.server.requests[:] = []
        self.clear_backoff()
        self.assertSilent(self.hook("SessionStart", self.base()))
        by_event = dict((b["event"], b) for b in self.sessions())
        self.assertEqual(by_event["end"]["user_ref"], "agent:env=prod/name=planner")
        self.assertEqual(by_event["start"]["user_ref"], "agent:env=dev/name=other")


if __name__ == "__main__":
    unittest.main()
