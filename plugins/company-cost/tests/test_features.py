"""Feature switches (costs / openspec): sidecar flags, send-costs, send-openspec, setup, status."""
import json
import os
import stat
import unittest

from tests.test_company_cost import EnvCase, cc, fake_token, slurp

URL = "https://ingest.example.com"


class FeatureCase(EnvCase):
    def side(self):
        return json.loads(slurp(self.sidecar))

    def write_side(self, obj):
        with open(self.sidecar, "w") as f:
            json.dump(obj, f)

    def costs(self, *args):
        return self.run_cli(["send-costs"] + list(args))

    def openspec(self, *args):
        return self.run_cli(["send-openspec"] + list(args))


class TestFeatureFlags(FeatureCase):
    def test_flags_helper(self):
        self.assertEqual(cc.feature_flags({}), {"costs": True, "openspec": True})  # legacy
        self.assertEqual(cc.feature_flags({"features": {"costs": True, "openspec": False}}),
                         {"costs": True, "openspec": False})
        self.assertEqual(cc.feature_flags({"features": {}}), {"costs": False, "openspec": False})
        self.assertEqual(cc.feature_flags({"features": {"costs": "yes"}}), {"costs": False, "openspec": False})
        self.assertEqual(cc.feature_flags({"features": "junk"}), {"costs": True, "openspec": True})

    def test_legacy_sidecar_means_both_enabled(self):
        self.write_side({"url": URL, "token": "t", "version": "0.1.0", "managed_keys": sorted(cc.MANAGED_ENV_KEYS)})
        self.assertEqual(cc.feature_flags(cc.read_sidecar()), {"costs": True, "openspec": True})
        rc, out, _ = self.run_cli(["status"])
        self.assertIn("costs: enabled", out)
        self.assertIn("openspec: enabled", out)

    def test_setup_leaves_features_off(self):
        tok = self.setup_ok()
        sc = self.side()
        self.assertEqual(sc["features"], {"costs": False, "openspec": False})
        self.assertEqual(sc["version"], cc.VERSION)
        self.assertEqual((sc["url"], sc["token"]), (URL, tok))
        self.assertEqual(stat.S_IMODE(os.stat(self.sidecar).st_mode), 0o600)

    def test_setup_does_not_touch_settings(self):
        original = {"model": "m", "env": {"OTEL_LOGS_EXPORTER": "console"}}
        self.write_settings(original)
        rc, out, err = self.run_cli(["setup", "--url", URL, "--token-stdin"], stdin=fake_token() + "\n")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.read_settings(), original)  # no conflict, no write
        self.assertFalse(os.path.exists(self.bak))
        self.assertIn("/company-cost:send-costs", out)
        self.assertIn("/company-cost:send-openspec", out)
        os.unlink(self.settings)
        self.setup_ok()
        self.assertFalse(os.path.exists(self.settings))

    def test_setup_output_never_has_token(self):
        tok = fake_token()
        rc, out, err = self.run_cli(["setup", "--url", URL, "--token-stdin"], stdin=tok + "\n")
        self.assertNotIn(tok, out + err)

    def test_setup_rerun_keeps_flags_and_refreshes_env_when_costs_on(self):
        self.setup_ok(token="tok-one")
        self.assertEqual(self.costs("on")[0], 0)
        self.assertEqual(self.openspec("on")[0], 0)
        self.setup_ok(token="tok-two")
        self.assertEqual(self.side()["features"], {"costs": True, "openspec": True})
        self.assertIn("tok-two", self.read_settings()["env"]["OTEL_EXPORTER_OTLP_HEADERS"])

    def test_setup_rerun_with_costs_off_keeps_settings_clean(self):
        self.setup_ok()
        self.openspec("on")
        self.setup_ok(token="tok-two")
        self.assertEqual(self.side()["features"], {"costs": False, "openspec": True})
        self.assertFalse(os.path.exists(self.settings))

    def test_setup_over_legacy_sidecar_keeps_both_on(self):
        self.write_settings({})
        self.setup_ok()
        self.costs("on")
        sc = self.side()
        del sc["features"]
        self.write_side(sc)  # looks like a 0.1.0 install
        self.setup_ok(token="tok-two")
        self.assertEqual(self.side()["features"], {"costs": True, "openspec": True})


class TestSendCosts(FeatureCase):
    def test_on_requires_configuration(self):
        for argv in ([], ["on"]):
            rc, out, err = self.costs(*argv)
            self.assertEqual(rc, 1)
            self.assertIn("/company-cost:setup", err)
        self.assertFalse(os.path.exists(self.settings))
        self.assertFalse(os.path.exists(self.sidecar))

    def test_default_action_is_on(self):
        self.setup_ok()
        rc, out, err = self.costs()
        self.assertEqual(rc, 0, err)
        self.assertEqual(sorted(self.read_settings()["env"]), sorted(cc.MANAGED_ENV_KEYS))
        self.assertEqual(self.side()["features"], {"costs": True, "openspec": False})
        self.assertIn("Restart Claude Code", out)

    def test_on_writes_exactly_the_old_setup_result(self):
        tok = self.setup_ok(URL + "/")
        self.costs("on")
        env = self.read_settings()["env"]
        self.assertEqual(len(env), 9)
        self.assertEqual(env["OTEL_EXPORTER_OTLP_ENDPOINT"], "https://ingest.example.com/v1/otlp")
        self.assertEqual(env["OTEL_EXPORTER_OTLP_HEADERS"], "Authorization=Bearer " + tok)
        self.assertEqual(env[cc.SENTINEL_KEY], "1")
        sc = self.side()
        self.assertEqual(sc["managed_keys"], sorted(cc.MANAGED_ENV_KEYS))
        self.assertTrue(sc["env_was_missing"])
        for p in (self.settings, self.sidecar):
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)

    def test_on_backup_first_kept_and_idempotent(self):
        self.write_settings({"model": "first"})
        self.setup_ok()
        self.costs("on")
        first = slurp(self.bak)
        self.assertEqual(json.loads(first), {"model": "first"})
        snap = self.read_settings()
        self.costs("on")
        self.assertEqual(self.read_settings(), snap)
        self.assertEqual(slurp(self.bak), first)

    def test_on_off_roundtrip_is_exact(self):
        originals = ({"model": "m", "hooks": {"A": [1]}, "env": {"FOO": "bar"}}, {"model": "m"}, {})
        for original in originals:
            for p in (self.settings, self.sidecar, self.bak):
                if os.path.exists(p):
                    os.unlink(p)
            self.write_settings(original)
            before = slurp(self.settings)
            self.setup_ok()
            self.costs("on")
            self.assertNotEqual(self.read_settings(), original)
            rc, out, err = self.costs("off")
            self.assertEqual(rc, 0, err)
            self.assertEqual(self.read_settings(), original)
            self.assertEqual(json.loads(before), original)

    def test_off_removes_env_and_keeps_url_token(self):
        tok = self.setup_ok()
        self.write_settings({"model": "m"})
        self.costs("on")
        self.costs("off")
        self.assertEqual(self.read_settings(), {"model": "m"})
        sc = self.side()
        self.assertEqual((sc["url"], sc["token"]), (URL, tok))
        self.assertEqual(sc["features"]["costs"], False)
        for k in ("managed_keys", "previous", "env_was_missing"):
            self.assertNotIn(k, sc)
        self.assertEqual(cc.load_config(), {"url": URL, "token": tok})

    def test_foreign_keys_conflict_without_force_changes_nothing(self):
        original = {"env": {"OTEL_LOGS_EXPORTER": "console", "KEEP": "1"}}
        self.write_settings(original)
        self.setup_ok()
        rc, out, err = self.costs("on")
        self.assertEqual(rc, 1)
        self.assertIn("OTEL_LOGS_EXPORTER", err)
        self.assertEqual(self.read_settings(), original)
        self.assertFalse(os.path.exists(self.bak))
        self.assertEqual(self.side()["features"]["costs"], False)

    def test_force_then_off_restores_previous(self):
        original = {"env": {"OTEL_LOGS_EXPORTER": "console", "KEEP": "1"}, "model": "m"}
        self.write_settings(original)
        self.setup_ok()
        self.assertEqual(self.costs("on", "--force")[0], 0)
        self.assertEqual(self.read_settings()["env"]["OTEL_LOGS_EXPORTER"], "otlp")
        self.assertEqual(self.side()["previous"], {"OTEL_LOGS_EXPORTER": "console"})
        self.assertEqual(self.costs("off")[0], 0)
        self.assertEqual(self.read_settings(), original)

    def test_off_spares_hand_edited_values(self):
        self.setup_ok()
        self.costs("on")
        s = self.read_settings()
        s["env"]["EXTRA"] = "mine"
        self.write_settings(s)
        self.costs("off")
        self.assertEqual(self.read_settings(), {"env": {"EXTRA": "mine"}})

    def test_invalid_settings_refused_on_and_off(self):
        self.setup_ok()
        self.write_settings("{broken")
        for a in ("on", "off"):
            self.assertEqual(self.costs(a)[0], 1)
            self.assertEqual(slurp(self.settings), "{broken")
        self.assertEqual(self.side()["features"]["costs"], False)

    def test_off_is_idempotent_and_safe_unconfigured(self):
        self.assertEqual(self.costs("off")[0], 0)
        self.assertFalse(os.path.exists(self.sidecar))
        self.setup_ok()
        self.assertEqual(self.costs("off")[0], 0)
        self.assertEqual(self.costs("off")[0], 0)

    def test_off_does_not_touch_openspec_flag(self):
        self.setup_ok()
        self.costs("on")
        self.openspec("on")
        self.costs("off")
        self.assertEqual(self.side()["features"], {"costs": False, "openspec": True})

    def test_bad_argument(self):
        self.setup_ok()
        rc, _, _ = self.costs("maybe")
        self.assertEqual(rc, 2)

    def test_token_never_printed(self):
        tok = self.setup_ok()
        for a in ("on", "status", "off"):
            rc, out, err = self.costs(a)
            self.assertNotIn(tok, out + err)

    def test_status_subcommand(self):
        self.setup_ok()
        self.assertIn("costs: disabled", self.costs("status")[1])
        self.costs("on")
        out = self.costs("status")[1]
        self.assertIn("costs: enabled", out)
        self.assertIn("prompt excerpt", out)
        self.assertEqual(self.side()["features"]["costs"], True)  # status is read-only

    def test_uninstall_removes_everything_even_when_off(self):
        self.write_settings({"model": "m"})
        self.setup_ok()
        self.costs("on")
        self.costs("off")
        self.assertEqual(self.run_cli(["uninstall"])[0], 0)
        self.assertEqual(self.read_settings(), {"model": "m"})
        self.assertFalse(os.path.exists(self.sidecar))
        self.assertFalse(os.path.exists(self.bak))

    def test_uninstall_after_on_restores(self):
        original = {"model": "m"}
        self.write_settings(original)
        self.setup_ok()
        self.costs("on")
        self.run_cli(["uninstall"])
        self.assertEqual(self.read_settings(), original)


class TestSendOpenspec(FeatureCase):
    def test_on_requires_configuration(self):
        rc, out, err = self.openspec("on")
        self.assertEqual(rc, 1)
        self.assertIn("/company-cost:setup", err)
        self.assertFalse(os.path.exists(self.sidecar))

    def test_flag_flips_only(self):
        self.write_settings({"model": "m"})
        self.setup_ok()
        before = slurp(self.settings)
        rc, out, err = self.openspec()  # default on
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.side()["features"], {"costs": False, "openspec": True})
        self.assertEqual(slurp(self.settings), before)
        self.assertFalse(os.path.exists(self.bak))
        self.assertEqual(self.openspec("off")[0], 0)
        self.assertEqual(self.side()["features"], {"costs": False, "openspec": False})
        self.assertEqual(slurp(self.settings), before)
        self.assertEqual(self.side()["version"], cc.VERSION)

    def test_on_leaves_costs_alone(self):
        self.setup_ok()
        self.costs("on")
        self.openspec("on")
        self.openspec("off")
        self.assertEqual(self.side()["features"], {"costs": True, "openspec": False})
        self.assertEqual(len(self.read_settings()["env"]), 9)

    def test_status_subcommand_mentions_what_is_sent(self):
        self.setup_ok()
        out = self.openspec("status")[1]
        self.assertIn("openspec: disabled", out)
        self.openspec("on")
        out = self.openspec("status")[1]
        self.assertIn("openspec: enabled", out)
        self.assertIn("sync", out)

    def test_openspec_only_mentions_registration_exception(self):
        self.setup_ok()
        self.openspec("on")
        self.assertIn("registration", self.openspec("status")[1])
        self.assertIn("registration", self.run_cli(["status"])[1])
        self.costs("on")
        self.assertNotIn("registration", self.run_cli(["status"])[1])

    def test_off_when_unconfigured_is_noop(self):
        self.assertEqual(self.openspec("off")[0], 0)
        self.assertFalse(os.path.exists(self.sidecar))

    def test_bad_argument(self):
        self.assertEqual(self.openspec("x")[0], 2)


class TestStatusDoctor(FeatureCase):
    def test_status_both_off_after_setup(self):
        self.setup_ok()
        rc, out, _ = self.run_cli(["status"])
        self.assertIn("costs: disabled", out)
        self.assertIn("openspec: disabled", out)
        self.assertIn("/company-cost:send-costs", out)
        self.assertIn("/company-cost:send-openspec", out)
        self.assertNotIn("MISSING", out)

    def test_status_costs_on_checks_managed_keys(self):
        self.setup_ok()
        self.costs("on")
        out = self.run_cli(["status"])[1]
        self.assertIn("costs: enabled", out)
        self.assertIn("Managed keys: present", out)
        s = self.read_settings()
        del s["env"]["OTEL_LOGS_EXPORTER"]
        self.write_settings(s)
        self.assertIn("MISSING", self.run_cli(["status"])[1])

    def test_doctor_lists_features(self):
        self.setup_ok()
        self.openspec("on")
        out = self.run_cli(["doctor"])[1]
        self.assertIn("costs: disabled", out)
        self.assertIn("openspec: enabled", out)


if __name__ == "__main__":
    unittest.main()
