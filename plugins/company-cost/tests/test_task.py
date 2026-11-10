"""Task label, prefixed-field user_ref format and the per-session COMPANY_COST_TASK override."""
import itertools
import json
import os
import time
import unittest

from tests.test_agent_name import AgentHookCase, write_json
from tests.test_company_cost import EnvCase, cc, fake_token, slurp

URL = "https://ingest.example.com"
TASK_ENV = "COMPANY_COST_TASK"
TASK_OPT = "CLAUDE_PLUGIN_OPTION_TASK"


def expected(agent, env, task):
    parts = []
    if env:
        parts.append("env=" + env)
    if agent:
        parts.append("name=" + agent)
    if task:
        parts.append("task=" + task)
    return "agent:" + "/".join(parts) if parts else None


COMBOS = list(itertools.product((None, "mi-agente"), (None, "prod"), (None, "deploy")))


class TestTaskValidation(unittest.TestCase):
    def test_valid(self):
        for v in ("deploy", "summarize docs", "step-1", "node_2", "v1.2", "a:b"):
            self.assertEqual(cc.validate_task(v), (v, None))
        self.assertEqual(cc.validate_task("  deploy  "), ("deploy", None))
        self.assertEqual(cc.validate_task("t" * 0 + ("task " * 10)[:48])[1], None)

    def test_invalid(self):
        for v in ("", "  ", "x" * 49, "a\nb", "a\x00b", "a​b", "a/b", "a=b", "a;b", "a'b",
                  "password:hunter2", "hro_" + "Ab3dE6gH9k" * 4, None, 3):
            clean, err = cc.validate_task(v)
            self.assertIsNone(clean, repr(v))
            self.assertTrue(err, repr(v))

    def test_delimiters_rejected_everywhere_including_lookalikes(self):
        bads = ("a/b", "a=b", "a／b", "a＝b", "a⁄b", "a∕b", "a≠b", "a⧸b")
        for fn in (cc.validate_agent_name, cc.validate_environment, cc.validate_task):
            for v in bads:
                self.assertIsNone(fn(v)[0], (fn.__name__, v))
        self.assertIsNone(cc.validate_task("Ｄeploy")[0])  # NFKC-unstable


class TestFormat(unittest.TestCase):
    def test_eight_combinations(self):
        for agent, env, task in COMBOS:
            self.assertEqual(cc.agent_user_ref(agent, env, task), expected(agent, env, task), (agent, env, task))

    def test_documented_examples(self):
        self.assertEqual(cc.agent_user_ref("mi-agente", "prod", "deploy"), "agent:env=prod/name=mi-agente/task=deploy")
        self.assertEqual(cc.agent_user_ref("mi-agente"), "agent:name=mi-agente")
        self.assertEqual(cc.agent_user_ref(None, "prod", "deploy"), "agent:env=prod/task=deploy")
        self.assertEqual(cc.agent_user_ref(None, None, "deploy"), "agent:task=deploy")
        self.assertIsNone(cc.agent_user_ref())

    def test_parse_rule_roundtrip(self):
        ref = cc.agent_user_ref("a b", "prod", "x:y")
        self.assertTrue(ref.startswith("agent:"))
        got = dict(p.split("=", 1) for p in ref[len("agent:"):].split("/"))
        self.assertEqual(got, {"env": "prod", "name": "a b", "task": "x:y"})

    def test_worst_case_length(self):
        self.assertEqual(cc.LABEL_MAX, 48)
        v = ("word " * 10)[:48]
        self.assertEqual(len(v), 48)
        for fn in (cc.validate_agent_name, cc.validate_environment, cc.validate_task):
            self.assertEqual(fn(v), (v, None))
        ref = cc.agent_user_ref(v, v, v)
        self.assertEqual(len(ref), 6 + 4 + 48 + 1 + 5 + 48 + 1 + 5 + 48)
        self.assertLessEqual(len(ref), 200)

    def test_payload_helper(self):
        ident = {"key": "k", "name": "n", "branch": "b", "repo_display": "r"}
        p = cc._session_payload("start", "s", ident, "/x", None, None, agent="a", environment="e", task="t")
        self.assertEqual(p["user_ref"], "agent:env=e/name=a/task=t")
        self.assertNotIn("user_ref", cc._session_payload("start", "s", ident, "/x", None, None))


class TestTaskSetup(EnvCase):
    def side(self):
        return json.loads(slurp(self.sidecar))

    def test_store_keep_update_clear(self):
        self.setup_only(extra=["--task", " deploy "])
        self.assertEqual(self.side()["task"], "deploy")
        self.setup_only()
        self.assertEqual(self.side()["task"], "deploy")
        self.setup_only(extra=["--task=review"])
        self.assertEqual(self.side()["task"], "review")
        self.setup_only(extra=["--task", ""])
        self.assertNotIn("task", self.side())

    def test_independent_of_other_labels(self):
        self.setup_only(extra=["--agent", "a", "--environment", "e", "--task", "t"])
        self.setup_only(extra=["--task", ""])
        sc = self.side()
        self.assertEqual((sc["agent_name"], sc["environment"]), ("a", "e"))
        self.assertNotIn("task", sc)

    def test_unset_has_no_key(self):
        self.setup_only()
        self.assertNotIn("task", self.side())

    def test_invalid_rejected_untouched(self):
        self.write_settings({"model": "m"})
        for bad in ("x" * 49, "a/b", "a=b", "a;b", "bad\nt", "password:hunter2", "  "):
            rc, out, err = self.run_cli(["setup", "--url", URL, "--token-stdin", "--task", bad],
                                        stdin=fake_token() + "\n")
            self.assertEqual(rc, 1, repr(bad))
            self.assertIn("task", err.lower())
            self.assertFalse(os.path.exists(self.sidecar))
            self.assertEqual(self.read_settings(), {"model": "m"})

    def test_agent_and_env_reject_equals_in_setup(self):
        for flag in ("--agent", "--environment"):
            rc, _, err = self.run_cli(["setup", "--url", URL, "--token-stdin", flag, "a=b"], stdin=fake_token() + "\n")
            self.assertEqual(rc, 1)

    def test_survives_send_traces_cycle(self):
        self.setup_only(extra=["--task", "t1"])
        self.run_cli(["send-traces", "on"])
        self.run_cli(["send-traces", "off"])
        self.assertEqual(self.side()["task"], "t1")


class TestTaskPrecedence(EnvCase):
    def test_matrix(self):
        for var, sidecar, opt in itertools.product((None, "", "fromvar", "bad/var"), (None, "side", "bad=side"),
                                                   (None, "opt", "bad opt;")):
            for k in (TASK_ENV, TASK_OPT):
                os.environ.pop(k, None)
            if var is not None:
                os.environ[TASK_ENV] = var
            if opt:
                os.environ[TASK_OPT] = opt
            side = {"task": sidecar} if sidecar else {}
            if var:
                # set to a non-empty value: valid wins, invalid omits the task (no fall-through)
                want = (var, "env var COMPANY_COST_TASK") if cc.validate_task(var)[0] else (None, None)
            else:
                want = (None, None)
                for value, src in ((sidecar, "sidecar"), (opt, "plugin option")):
                    if value and cc.validate_task(value)[0]:
                        want = (value, src)
                        break
            self.assertEqual(cc.resolve_task_source(side), want, (var, sidecar, opt))
            self.assertEqual(cc.resolve_task(side), want[0])

    def test_user_ref_uses_task(self):
        os.environ[TASK_ENV] = "node-1"
        self.assertEqual(cc.resolve_user_ref({"agent_name": "a", "environment": "e", "task": "default"}),
                         "agent:env=e/name=a/task=node-1")


class TestTaskStatus(EnvCase):
    def test_status_and_doctor(self):
        self.setup_only(extra=["--agent", "mi-agente", "--environment", "prod", "--task", "deploy"])
        for cmd in ("status", "doctor"):
            out = self.run_cli([cmd])[1]
            self.assertIn("Task: deploy", out)
            self.assertIn("sidecar", out)
            self.assertIn("agent:env=prod/name=mi-agente/task=deploy", out)

    def test_status_sources(self):
        self.setup_only()
        self.assertIn("Task: not set", self.run_cli(["status"])[1])
        os.environ[TASK_OPT] = "optional"
        out = self.run_cli(["status"])[1]
        self.assertIn("Task: optional", out)
        self.assertIn("plugin option", out)
        os.environ[TASK_ENV] = "fromvar"
        out = self.run_cli(["status"])[1]
        self.assertIn("Task: fromvar", out)
        self.assertIn("COMPANY_COST_TASK", out)
        self.assertIn("agent:task=fromvar", out)
        os.environ[TASK_ENV] = "bad/var"
        out = self.run_cli(["status"])[1]
        self.assertIn("COMPANY_COST_TASK is set but invalid", out)
        self.assertIn("task omitted", out)
        self.assertNotIn("Task: optional", out)
        self.assertIn("user_ref sent: none", out)
        self.assertIn("COMPANY_COST_TASK is set but invalid", self.run_cli(["doctor"])[1])
        os.environ[TASK_ENV] = ""
        self.assertIn("Task: optional", self.run_cli(["status"])[1])


class TestTaskHooks(AgentHookCase):
    def conf(self, agent=None, env=None, task=None, features=None):
        side = {"features": features or {"costs": True, "openspec": False}, "version": "0.3.0"}
        for k, v in (("agent_name", agent), ("environment", env), ("task", task)):
            if v:
                side[k] = v
        write_json(os.path.join(self.cfg, "settings.company-cost.json"), side)

    def test_eight_combinations_all_events(self):
        for agent, env, task in COMBOS:
            self.server.requests[:] = []
            self.conf(agent, env, task)
            self.full_session()
            events = self.sessions()
            self.assertEqual([e["event"] for e in events], ["start", "heartbeat", "heartbeat", "end"])
            for e in events:
                want = expected(agent, env, task)
                if want is None:
                    self.assertNotIn("user_ref", e)
                else:
                    self.assertEqual(e["user_ref"], want)
            for r in self.server.requests:
                if not r["path"].endswith("/session"):
                    self.assertNotIn("user_ref", json.dumps(r["body"]))

    def test_env_var_overrides_in_start_heartbeat_end(self):
        self.conf("mi-agente", "prod", "default")
        self.full_session(env=self.env(**{TASK_ENV: "summarize"}))
        for e in self.sessions():
            self.assertEqual(e["user_ref"], "agent:env=prod/name=mi-agente/task=summarize")

    def test_precedence_in_hooks_and_invalid_env_var_omits(self):
        self.conf(task="side")
        steps = (({TASK_ENV: "var", TASK_OPT: "opt"}, "agent:task=var"),
                 ({TASK_ENV: "bad/var", TASK_OPT: "opt"}, None),  # invalid env var: omitted, no fall-through
                 ({TASK_ENV: "", TASK_OPT: "opt"}, "agent:task=side"),  # empty means not provided
                 ({TASK_OPT: "opt"}, "agent:task=side"))
        for extra, want in steps:
            self.server.requests[:] = []
            self.assertSilent(self.hook("Stop", self.base(), env=self.env(**extra)))
            ses = self.sessions()[-1]
            if want is None:
                self.assertNotIn("user_ref", ses, extra)
            else:
                self.assertEqual(ses["user_ref"], want, extra)
        self.conf()
        # invalid values in the sidecar / plugin option keep falling through
        write_json(os.path.join(self.cfg, "settings.company-cost.json"),
                   {"features": {"costs": True, "openspec": False}, "task": "bad=side"})
        self.server.requests[:] = []
        self.assertSilent(self.hook("Stop", self.base(), env=self.env(**{TASK_OPT: "opt"})))
        self.assertEqual(self.sessions()[-1]["user_ref"], "agent:task=opt")

    def test_invalid_env_var_values_at_hook_level(self):
        self.conf("mi-agente", "prod", "default")
        base_ref = "agent:env=prod/name=mi-agente"
        cases = (("100KB", "x" * 100000, None), ("non-utf8", b"\xff\xfe", None),
                 ("sk", "sk-" + "A1b2C3d4" * 6, None), ("ghp", "ghp_" + "A1b2C3d4" * 5, None),
                 ("slash", "a/b", None), ("equals", "a=b", None),
                 ("empty", "", base_ref + "/task=default"))
        for label, value, want in cases:
            for event in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"):
                self.server.requests[:] = []
                extra = {"prompt": "hi"} if event == "UserPromptSubmit" else {}
                p = self.hook(event, self.base(**extra), env=self.env(**{TASK_ENV: value}))
                self.assertSilent(p)
                for ses in self.sessions():
                    self.assertEqual(ses["user_ref"], want or base_ref, (label, event))
                self.assertTrue(self.sessions(), (label, event))

    def test_later_hook_without_env_var_falls_back(self):
        self.conf(task="default")
        self.assertSilent(self.hook("SessionStart", self.base(), env=self.env(**{TASK_ENV: "node"})))
        self.assertSilent(self.hook("Stop", self.base()))
        self.assertEqual([e["user_ref"] for e in self.sessions()], ["agent:task=node", "agent:task=default"])

    def test_openspec_only_start_and_no_leak(self):
        os.makedirs(os.path.join(self.repo, "openspec", "changes", "foo"))
        with open(os.path.join(self.repo, "openspec", "changes", "foo", "proposal.md"), "w") as f:
            f.write("# p")
        self.write_openspec_shim(json.dumps({"changes": [{"name": "foo", "completedTasks": 1, "totalTasks": 3,
                                                          "status": "in-progress",
                                                          "lastModified": "2025-01-01T00:00:00Z"}]}))
        self.conf(env="staging", features={"costs": False, "openspec": True})
        self.full_session(env=self.env(COMPANY_COST_NO_SYNC=None, **{TASK_ENV: "lc-node"}))
        deadline = time.time() + 20
        while time.time() < deadline and "snapshot" not in self.server.paths():
            time.sleep(0.1)
        time.sleep(0.5)
        sessions = self.sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["user_ref"], "agent:env=staging/task=lc-node")
        others = [r for r in self.server.requests if not r["path"].endswith("/session")]
        self.assertTrue(others)
        for r in others:
            self.assertNotIn("user_ref", json.dumps(r["body"]))
            self.assertNotIn("lc-node", json.dumps(r["body"]))

    def test_prompt_has_no_label(self):
        self.conf("a", "e", "t")
        self.full_session(env=self.env(**{TASK_ENV: "node"}))
        prompts = [r["body"] for r in self.server.requests if r["path"].endswith("/prompt")]
        self.assertEqual(len(prompts), 1)
        self.assertNotIn("node", json.dumps(prompts[0]))
        self.assertNotIn("user_ref", prompts[0])

    def test_queued_replay_keeps_value_as_built(self):
        self.conf("planner", "prod", "t1")
        self.server.responder = lambda r: (500, {}, {})
        self.assertSilent(self.hook("SessionEnd", self.base(), env=self.env(**{TASK_ENV: "node-a"})))
        self.assertIn("agent:env=prod/name=planner/task=node-a", json.dumps(self.queue_items()))
        self.server.responder = self.server.default_responder
        self.server.requests[:] = []
        self.clear_backoff()
        self.assertSilent(self.hook("SessionStart", self.base(), env=self.env(**{TASK_ENV: "node-b"})))
        by_event = dict((b["event"], b) for b in self.sessions())
        self.assertEqual(by_event["end"]["user_ref"], "agent:env=prod/name=planner/task=node-a")
        self.assertEqual(by_event["start"]["user_ref"], "agent:env=prod/name=planner/task=node-b")

    def test_invalid_env_var_silent(self):
        self.conf()
        p = self.hook("SessionStart", self.base(), env=self.env(**{TASK_ENV: "bad\nvalue"}))
        self.assertSilent(p)
        self.assertNotIn("user_ref", self.sessions()[0])


if __name__ == "__main__":
    unittest.main()
