"""Regression tests for the third review round (F1-F7)."""
import json
import os
import re
import unittest
from unittest import mock

from tests.test_company_cost import EnvCase, cc

URL = "https://ingest.example.com"
PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_skill(name):
    with open(os.path.join(PLUGIN, "skills", name, "SKILL.md"), "r", encoding="utf-8") as f:
        return f.read()


class R3Case(EnvCase):
    def side(self):
        return self.read_side()

    def write_side(self, obj):
        with open(self.sidecar, "w") as f:
            json.dump(obj, f)


class TestF1OffPersistsFlagFirst(R3Case):
    def test_invalid_json_settings_still_turns_flag_off(self):
        self.setup_ok()
        self.write_settings("{not json")
        rc, out, err = self.run_cli(["send-traces", "off"])
        self.assertEqual(rc, 1)
        self.assertIn("OTEL", err)
        self.assertIn("send-traces off", err)
        self.assertFalse(self.side()["features"]["costs"])

    def test_env_not_an_object_still_turns_flag_off(self):
        self.setup_ok()
        self.write_settings({"env": "oops"})
        rc, out, err = self.run_cli(["send-traces", "off"])
        self.assertEqual(rc, 1)
        self.assertFalse(self.side()["features"]["costs"])

    def test_rerun_after_fixing_settings_cleans_keys(self):
        self.setup_ok()
        good = self.read_settings()
        self.write_settings("{not json")
        self.run_cli(["send-traces", "off"])
        self.write_settings(good)
        rc, _, err = self.run_cli(["send-traces", "off"])
        self.assertEqual(rc, 0, err)
        self.assertNotIn("env", self.read_settings())
        self.assertNotIn("managed_keys", self.side())


class TestF2QueueSanitized(R3Case):
    def flush(self, flags):
        sent = []

        def fake(cfg, suffix, payload, timeout):
            sent.append((suffix, payload))
            return "ok", cc.Resp(200)
        with mock.patch.object(cc, "deliver", fake):
            cc.flush_queue({"url": URL, "token": "t"}, flags=flags)
        return sent

    def test_change_fields_stripped_when_openspec_off(self):
        cc.queue_append("session", {"event": "heartbeat", "session_id": "s", "change_name": "secret",
                                    "change_source": "command"})
        cc.queue_append("prompt", {"session_id": "s", "excerpt": "x", "change_name": "secret"})
        sent = self.flush({"costs": True, "openspec": False})
        self.assertEqual([s for s, _ in sent], ["session", "prompt"])
        for _, payload in sent:
            self.assertNotIn("change_name", payload)
            self.assertNotIn("change_source", payload)
        self.assertEqual(sent[0][1]["event"], "heartbeat")

    def test_change_fields_kept_when_openspec_on(self):
        cc.queue_append("session", {"event": "heartbeat", "session_id": "s", "change_name": "c",
                                    "change_source": "command"})
        sent = self.flush({"costs": True, "openspec": True})
        self.assertEqual(sent[0][1]["change_name"], "c")

    def test_openspec_only_start_is_sanitized_too_when_openspec_off_is_impossible(self):
        cc.queue_append("session", {"event": "heartbeat", "session_id": "s"})
        self.assertEqual(self.flush({"costs": False, "openspec": True}), [])


class TestF3NoImplicitOptIn(R3Case):
    def test_env_only_no_sidecar_is_off(self):
        self.assertEqual(cc.feature_flags(cc.read_sidecar()), {"costs": False, "openspec": False})

    def test_corrupt_sidecar_is_off(self):
        with open(self.sidecar, "w") as f:
            f.write("{nope")
        self.assertEqual(cc.feature_flags(cc.read_sidecar()), {"costs": False, "openspec": False})

    def test_legacy_sidecar_is_on(self):
        self.write_side({"url": URL, "token": "t", "version": "0.1.0"})
        self.assertEqual(cc.feature_flags(cc.read_sidecar()), {"costs": True, "openspec": True})

    def test_non_dict_features_is_off(self):
        self.assertEqual(cc.feature_flags({"url": URL, "features": []}), {"costs": False, "openspec": False})


class TestF4EnvRoundTrip(R3Case):
    def test_empty_env_object_round_trips(self):
        original = {"env": {}, "x": 1}
        self.write_settings(original)
        self.setup_only()
        self.assertEqual(self.run_cli(["send-traces", "on"])[0], 0)
        self.assertEqual(self.run_cli(["send-traces", "off"])[0], 0)
        self.assertEqual(self.read_settings(), original)

    def test_missing_env_is_removed(self):
        self.write_settings({"x": 1})
        self.setup_only()
        self.run_cli(["send-traces", "on"])
        self.run_cli(["send-traces", "off"])
        self.assertEqual(self.read_settings(), {"x": 1})


class TestF5SkillArguments(unittest.TestCase):
    def test_arguments_validated_and_never_spliced_raw(self):
        for name in ("send-traces", "send-openspec"):
            text = read_skill(name)
            self.assertIn("exactly `on`, `off` or `status`", text, name)
            self.assertIn("stop and ask the user", text, name)
            self.assertIn("never take `--force`", text.lower().replace("never take", "never take"), name)
            for block in re.findall(r"```bash\n(.*?)```", text, re.S):
                self.assertNotIn("$ARGUMENTS", block, name)


class TestF6ForceHint(R3Case):
    def test_setup_conflict_hint_points_to_send_traces(self):
        self.setup_only()
        side = self.side()
        side["features"]["costs"] = True
        self.write_side(side)
        self.write_settings({"env": {"OTEL_LOGS_EXPORTER": "console"}})
        rc, out, err = self.run_cli(["setup", "--url", URL, "--token-stdin"],
                                    stdin="hro_" + "Ab3dE6gH9k" * 4 + "xyz\n")
        self.assertEqual(rc, 1)
        self.assertNotIn("Re-run with --force", err)
        self.assertIn("send-traces", err)
        self.assertIn("--force", err)

    def test_send_traces_skill_documents_force_rule(self):
        text = read_skill("send-traces")
        self.assertIn("--force", text)
        self.assertIn("explicit yes", text)
        self.assertIn("never comes from `$ARGUMENTS`", text)


class TestF7Atomicity(R3Case):
    def test_failed_settings_write_leaves_flag_off(self):
        self.setup_only()
        self.write_settings({"env": {"OTEL_LOGS_EXPORTER": "console"}})
        with mock.patch.object(cc, "_write_settings", side_effect=OSError("disk full")):
            rc, out, err = self.run_cli(["send-traces", "on", "--force"])
        self.assertEqual(rc, 1)
        self.assertFalse(self.side()["features"]["costs"])
        self.assertEqual(self.read_settings(), {"env": {"OTEL_LOGS_EXPORTER": "console"}})

    def test_success_keeps_exact_uninstall(self):
        original = {"env": {"OTEL_LOGS_EXPORTER": "console"}}
        self.write_settings(original)
        self.setup_only()
        self.assertEqual(self.run_cli(["send-traces", "on", "--force"])[0], 0)
        side = self.side()
        self.assertTrue(side["features"]["costs"])
        self.assertEqual(side["previous"], {"OTEL_LOGS_EXPORTER": "console"})
        self.assertEqual(self.run_cli(["uninstall"])[0], 0)
        self.assertEqual(self.read_settings(), original)

    def test_held_sidecar_lock_blocks_writers(self):
        self.setup_only()
        before = open(self.sidecar).read()
        lk = cc.FileLock(os.path.join(self.cfg, "settings.company-cost.lock"), wait=0)
        self.assertTrue(lk.acquire())
        self.addCleanup(lk.release)
        with mock.patch.object(cc, "SIDECAR_LOCK_WAIT", 0.05):
            rc, out, err = self.run_cli(["send-openspec", "on"])
        self.assertEqual(rc, 1)
        self.assertIn("another", err)
        self.assertEqual(open(self.sidecar).read(), before)

    def test_lock_released_after_command(self):
        self.setup_only()
        self.assertEqual(self.run_cli(["send-openspec", "on"])[0], 0)
        self.assertFalse(os.path.exists(os.path.join(self.cfg, "settings.company-cost.lock")))


if __name__ == "__main__":
    unittest.main()
