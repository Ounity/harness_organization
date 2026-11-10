"""Optional agent name: validation, sidecar semantics, env fallback, `user_ref` transport, status."""
import json
import os
import time
import unittest

from tests.test_company_cost import ENV_KEYS, EnvCase, cc, fake_token, slurp
from tests.test_hooks_fake_server import HookCase

URL = "https://ingest.example.com"
OPT = "CLAUDE_PLUGIN_OPTION_AGENT_NAME"


def write_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)


class TestValidation(unittest.TestCase):
    def ok(self, raw, expected=None):
        clean, err = cc.validate_agent_name(raw)
        self.assertIsNone(err, raw)
        self.assertEqual(clean, raw.strip() if expected is None else expected)

    def bad(self, raw):
        clean, err = cc.validate_agent_name(raw)
        self.assertIsNone(clean, repr(raw))
        self.assertTrue(err and isinstance(err, str), repr(raw))

    def test_valid(self):
        for name in ("planner", "Code Reviewer 2", "a", "team/agent-1", "svc.v2_beta:x", "9", "Agénte ñandú"):
            self.ok(name)
        self.ok("  padded  ", "padded")
        longest = ("agent " * 11)[:64]
        self.assertEqual(len(longest), 64)
        self.ok(longest)

    def test_empty_and_whitespace_only(self):
        self.bad("")
        self.bad("   ")

    def test_too_long(self):
        self.bad("x" * 65)
        self.bad(" " + "x" * 65 + " ")

    def test_control_chars(self):
        for raw in ("a\nb", "a\tb", "a\x00b", "a\x1bb", "a\x7fb", "a\rb", "a​b", "a‮b"):
            self.bad(raw)

    def test_disallowed_chars(self):
        for raw in ("a;b", "a'b", 'a"b', "a$(x)", "a`b`", "a|b", "a&b", "a\\b", "a<b", "a=b", "a@b", "a#b",
                    "a*b", "emoji \U0001F600"):
            self.bad(raw)

    def test_secret_like(self):
        for raw in ("hro_" + "Ab3dE6gH9k" * 4, "password:hunter2", "api_key:abc123", "sk-ant-api03-" + "A1b2" * 8,
                    "ghp_" + "A1b2C3d4" * 5, "AKIA" + "ABCDEFGHIJKLMNOP"):
            self.bad(raw)

    def test_non_string(self):
        self.bad(None)
        self.bad(5)

    def test_unicode_is_nfkc_stable_only(self):
        self.ok("Ünïcode")
        self.bad("Ａgent")  # fullwidth letter: NFKC would change it (redaction check differs)

    def test_user_ref_under_limit(self):
        self.assertLessEqual(len(cc.agent_user_ref("x" * 64)), 200)
        self.assertEqual(cc.agent_user_ref("planner"), "agent:planner")


class TestSetupSemantics(EnvCase):
    def side(self):
        return json.loads(slurp(self.sidecar))

    def test_setup_stores_agent_name(self):
        self.setup_only(extra=["--agent", "  Planner 1  "])
        self.assertEqual(self.side()["agent_name"], "Planner 1")

    def test_setup_without_agent_has_no_key(self):
        self.setup_only()
        self.assertNotIn("agent_name", self.side())

    def test_rerun_without_agent_keeps_value(self):
        self.setup_only(extra=["--agent", "planner"])
        self.setup_only()
        self.assertEqual(self.side()["agent_name"], "planner")

    def test_rerun_with_agent_updates(self):
        self.setup_only(extra=["--agent", "planner"])
        self.setup_only(extra=["--agent", "reviewer"])
        self.assertEqual(self.side()["agent_name"], "reviewer")

    def test_empty_agent_clears(self):
        self.setup_only(extra=["--agent", "planner"])
        self.setup_only(extra=["--agent", ""])
        self.assertNotIn("agent_name", self.side())

    def test_leading_dash_via_equals(self):
        self.setup_only(extra=["--agent=-dash"])
        self.assertEqual(self.side()["agent_name"], "-dash")

    def test_survives_send_traces_cycle(self):
        self.setup_only(extra=["--agent", "planner"])
        self.assertEqual(self.run_cli(["send-traces", "on"])[0], 0)
        self.assertEqual(self.side()["agent_name"], "planner")
        self.assertEqual(self.run_cli(["send-traces", "off"])[0], 0)
        self.assertEqual(self.side()["agent_name"], "planner")
        self.setup_only(token="tok-two")  # re-run with traces possibly on: still kept
        self.assertEqual(self.side()["agent_name"], "planner")

    def test_rerun_with_traces_on_keeps_and_updates(self):
        self.setup_only()
        self.run_cli(["send-traces", "on"])
        self.setup_only(extra=["--agent", "late"])
        self.assertEqual(self.side()["agent_name"], "late")

    def test_invalid_rejected_without_touching_anything(self):
        original = {"model": "m"}
        self.write_settings(original)
        for bad in ("x" * 65, "a;b", "bad\nname", "password:hunter2", "   "):
            rc, out, err = self.run_cli(["setup", "--url", URL, "--token-stdin", "--agent", bad],
                                        stdin=fake_token() + "\n")
            self.assertEqual(rc, 1, repr(bad))
            self.assertIn("agent", err.lower())
            self.assertFalse(os.path.exists(self.sidecar), repr(bad))
            self.assertEqual(self.read_settings(), original)
            self.assertNotIn("hunter2", out + err)

    def test_invalid_does_not_change_existing(self):
        self.setup_only(extra=["--agent", "planner"])
        rc, _, _ = self.run_cli(["setup", "--url", URL, "--token-stdin", "--agent", "a;b"], stdin=fake_token() + "\n")
        self.assertEqual(rc, 1)
        self.assertEqual(self.side()["agent_name"], "planner")


class TestResolution(EnvCase):
    def test_env_fallback_when_sidecar_has_none(self):
        os.environ[OPT] = "From Env"
        self.assertEqual(cc.resolve_agent_name({}), "From Env")
        self.assertEqual(cc.resolve_agent_name(None), "From Env")

    def test_sidecar_wins(self):
        os.environ[OPT] = "From Env"
        self.assertEqual(cc.resolve_agent_name({"agent_name": "sidecar"}), "sidecar")

    def test_unset(self):
        self.assertIsNone(cc.resolve_agent_name({}))
        os.environ[OPT] = "   "
        self.assertIsNone(cc.resolve_agent_name({}))

    def test_invalid_env_ignored(self):
        os.environ[OPT] = "bad;name"
        self.assertIsNone(cc.resolve_agent_name({}))

    def test_invalid_sidecar_value_ignored_then_env(self):
        os.environ[OPT] = "env"
        self.assertEqual(cc.resolve_agent_name({"agent_name": "bad;name"}), "env")
        del os.environ[OPT]
        self.assertIsNone(cc.resolve_agent_name({"agent_name": 5}))

    def test_env_key_is_cleaned_by_harness(self):
        self.assertIn(OPT, ENV_KEYS)

    def test_session_payload_helper(self):
        ident = {"key": "k", "name": "n", "branch": "b", "repo_display": "r"}
        p = cc._session_payload("start", "s", ident, "/x", None, None, agent="planner")
        self.assertEqual(p["user_ref"], "agent:planner")
        p = cc._session_payload("start", "s", ident, "/x", None, None)
        self.assertNotIn("user_ref", p)


class TestStatus(EnvCase):
    def test_status_shows_agent(self):
        self.setup_only(extra=["--agent", "planner"])
        rc, out, _ = self.run_cli(["status"])
        self.assertIn("Agent name: planner", out)
        self.assertIn("user_ref", out)
        self.assertIn("agent:planner", out)

    def test_status_not_set(self):
        self.setup_only()
        rc, out, _ = self.run_cli(["status"])
        self.assertIn("Agent name: not set", out)

    def test_status_env_fallback(self):
        self.setup_only()
        os.environ[OPT] = "envagent"
        self.assertIn("Agent name: envagent", self.run_cli(["status"])[1])

    def test_doctor_shows_agent(self):
        self.setup_only(extra=["--agent", "planner"])
        self.assertIn("Agent name: planner", self.run_cli(["doctor"])[1])

    def test_uninstall_removes_it(self):
        self.setup_only(extra=["--agent", "planner"])
        rc, _, _ = self.run_cli(["uninstall"])
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(self.sidecar))


class AgentHookCase(HookCase):
    def configure(self, features, agent=None):
        side = {"features": features, "version": "0.3.0"}
        if agent is not None:
            side["agent_name"] = agent
        write_json(os.path.join(self.cfg, "settings.company-cost.json"), side)

    def full_session(self, env=None):
        for ev, extra in (("SessionStart", {}), ("UserPromptSubmit", {"prompt": "hello"}),
                          ("Stop", {}), ("SessionEnd", {})):
            self.assertSilent(self.hook(ev, self.base(**extra), env=env))

    def sessions(self):
        return [r["body"] for r in self.server.requests if r["path"].endswith("/session")]


class TestPayloads(AgentHookCase):
    def test_every_session_event_carries_user_ref(self):
        self.configure({"costs": True, "openspec": False}, "Planner 1")
        self.full_session()
        events = self.sessions()
        self.assertEqual([e["event"] for e in events], ["start", "heartbeat", "heartbeat", "end"])
        for e in events:
            self.assertEqual(e["user_ref"], "agent:Planner 1")
            self.assertLessEqual(len(e["user_ref"]), 200)

    def test_absent_when_unset(self):
        self.configure({"costs": True, "openspec": True})
        self.full_session()
        self.assertEqual(len(self.sessions()), 4)
        for e in self.sessions():
            self.assertNotIn("user_ref", e)

    def test_env_fallback_in_payload_and_sidecar_wins(self):
        self.configure({"costs": True, "openspec": False})
        self.assertSilent(self.hook("SessionStart", self.base(), env=self.env(**{OPT: "envagent"})))
        self.assertEqual(self.sessions()[-1]["user_ref"], "agent:envagent")
        self.configure({"costs": True, "openspec": False}, "sidecar")
        self.assertSilent(self.hook("Stop", self.base(), env=self.env(**{OPT: "envagent"})))
        self.assertEqual(self.sessions()[-1]["user_ref"], "agent:sidecar")

    def test_invalid_env_value_is_silently_ignored(self):
        self.configure({"costs": True, "openspec": False})
        self.assertSilent(self.hook("SessionStart", self.base(), env=self.env(**{OPT: "bad;name"})))
        self.assertNotIn("user_ref", self.sessions()[0])

    def test_prompt_has_no_user_ref(self):
        self.configure({"costs": True, "openspec": False}, "planner")
        self.full_session()
        prompts = [r["body"] for r in self.server.requests if r["path"].endswith("/prompt")]
        self.assertEqual(len(prompts), 1)
        self.assertNotIn("user_ref", prompts[0])
        self.assertNotIn("agent", json.dumps(prompts[0]))

    def test_openspec_only_lone_start_and_no_user_ref_elsewhere(self):
        os.makedirs(os.path.join(self.repo, "openspec", "changes", "foo"))
        write_json(os.path.join(self.tmp, "openspec.out"),
                   {"changes": [{"name": "foo", "completedTasks": 1, "totalTasks": 3,
                                 "status": "in-progress", "lastModified": "2025-01-01T00:00:00Z"}]})
        self.write_openspec_shim(slurp(os.path.join(self.tmp, "openspec.out")))
        with open(os.path.join(self.repo, "openspec", "changes", "foo", "proposal.md"), "w") as f:
            f.write("# p")
        self.configure({"costs": False, "openspec": True}, "planner")
        env = self.env(COMPANY_COST_NO_SYNC=None)
        self.full_session(env=env)
        deadline = time.time() + 20
        while time.time() < deadline and "snapshot" not in self.server.paths():
            time.sleep(0.1)
        time.sleep(0.5)
        sessions = self.sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["event"], "start")
        self.assertEqual(sessions[0]["user_ref"], "agent:planner")
        others = [r for r in self.server.requests if not r["path"].endswith("/session")]
        self.assertTrue([r for r in others if r["path"].endswith("/snapshot")])
        for r in others:
            self.assertNotIn("user_ref", json.dumps(r["body"]), r["path"])

    def test_queued_session_keeps_user_ref_as_built(self):
        self.configure({"costs": True, "openspec": False}, "planner")
        self.server.responder = lambda r: (500, {}, {})
        self.assertSilent(self.hook("SessionEnd", self.base()))
        items = self.queue_items()
        self.assertEqual(len(items), 1)
        self.assertIn("agent:planner", json.dumps(items[0]))
        # the agent changes before replay: the queued item keeps the value built at queue time
        self.configure({"costs": True, "openspec": False}, "other")
        self.server.responder = self.server.default_responder
        self.server.requests[:] = []
        self.clear_backoff()
        self.assertSilent(self.hook("SessionStart", self.base()))
        starts = [b for b in self.sessions()]
        by_event = dict((b["event"], b) for b in starts)
        self.assertEqual(by_event["end"]["user_ref"], "agent:planner")
        self.assertEqual(by_event["start"]["user_ref"], "agent:other")

    def test_queued_without_agent_stays_without(self):
        self.configure({"costs": True, "openspec": False})
        self.server.responder = lambda r: (500, {}, {})
        self.assertSilent(self.hook("SessionEnd", self.base()))
        self.assertNotIn("user_ref", json.dumps(self.queue_items()))


if __name__ == "__main__":
    unittest.main()
