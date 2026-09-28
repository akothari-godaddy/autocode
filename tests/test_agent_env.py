"""Agents and runner-executed tests do not see credential-like environment variables."""
import json
from pathlib import Path
import sys
import unittest

from . import test_subprocess
import autocode_agent_env as agent_env
import autocode_verify as verify


class ScrubTests(unittest.TestCase):
    def test_credentials_are_withheld_and_ordinary_settings_kept(self):
        env = {"PATH": "/usr/bin", "HOME": "/home/u", "LANG": "C.UTF-8", "PYTHONPATH": "/src",
               "GIT_AUTHOR_NAME": "Ada", "XAUTHORITY": "/tmp/x", "KEYTIMEOUT": "1",
               "GITHUB_TOKEN": "t", "GH_TOKEN": "t", "AWS_ACCESS_KEY_ID": "a", "AWS_SECRET_ACCESS_KEY": "s",
               "AWS_SESSION_TOKEN": "t", "OPENAI_API_KEY": "k", "STRIPE_KEY": "k", "NPM_CONFIG__AUTH": "a",
               "SSH_AUTH_SOCK": "/tmp/agent", "DB_PASSWORD": "p", "GOOGLE_APPLICATION_CREDENTIALS": "/c.json",
               "AZURE_CLIENT_SECRET": "s", "MYAPIKEY": "k", "github_pat": "p"}
        kept = agent_env.scrubbed(env)
        self.assertEqual({"PATH", "HOME", "LANG", "PYTHONPATH", "GIT_AUTHOR_NAME", "XAUTHORITY", "KEYTIMEOUT"},
                         set(kept))
        self.assertEqual(sorted(set(env) - set(kept)), agent_env.withheld(env))

    def test_a_url_with_an_embedded_password_is_withheld_whatever_its_name(self):
        env = {"DATABASE_URL": "postgres://app:hunter2@db/prod", "SERVICE": "https://example.test/api",
               "MIRROR": "https://user@example.test/"}
        self.assertEqual(["DATABASE_URL"], agent_env.withheld(env))

    def test_proxies_and_autocode_settings_are_kept(self):
        env = {"HTTPS_PROXY": "http://u:p@proxy:3128", "https_proxy": "http://u:p@proxy:3128",
               "NO_PROXY": "localhost", "AUTOCODE_CAPTURE_CONTEXT": "{}", "AUTOCODE_FIXTURE_OPENAI_AUTH": "oauth"}
        self.assertEqual(env, agent_env.scrubbed(env))

    def test_git_config_variables_are_kept_or_withheld_as_one_set(self):
        env = {"GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "credential.interactive", "GIT_CONFIG_VALUE_0": "false",
               "GIT_CONFIG_KEY_1": "url.https://github.com/.insteadOf", "GIT_CONFIG_VALUE_1": "git@github.com:"}
        self.assertEqual(env, agent_env.scrubbed(env))
        env["GIT_CONFIG_VALUE_1"] = "https://bot:hunter2@github.com/"
        self.assertEqual({}, agent_env.scrubbed(env))

    def test_token_counts_are_not_credentials(self):
        self.assertEqual([], agent_env.withheld({"MAX_THINKING_TOKENS": "8000", "CONTEXT_TOKENS": "1"}))

    def test_named_variables_pass_through(self):
        env = {"ZHIPU_API_KEY": "k", "GITHUB_TOKEN": "t", agent_env.PASS_VARIABLE: " ZHIPU_API_KEY , "}
        self.assertEqual({"ZHIPU_API_KEY", agent_env.PASS_VARIABLE}, set(agent_env.scrubbed(env)))
        self.assertEqual(["GITHUB_TOKEN"], agent_env.withheld(env))

    def test_runner_executed_test_commands_get_the_scrubbed_environment(self):
        environment = verify.test_environment(Path("/tree"), {"PATH": "/usr/bin", "GH_TOKEN": "t"})
        self.assertNotIn("GH_TOKEN", environment)
        self.assertEqual("/usr/bin", environment["PATH"])


class ProviderEnvironmentTests(unittest.TestCase):
    """A real CLI run: the provider process never receives the withheld variables."""
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def record_provider_environment(self):
        bin_dir = self.root / "fixture-bin"
        (bin_dir / "codex").rename(bin_dir / "codex-fixture")
        wrapper = bin_dir / "codex"
        wrapper.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n"
            "if sys.argv[1:2] == ['exec']:  # a role launch, not the runner's own login check\n"
            "    with open(os.environ['AUTOCODE_TEST_ENV_LOG'], 'a') as log:\n"
            "        log.write(json.dumps(sorted(os.environ)) + '\\n')\n"
            "fixture = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'codex-fixture')\n"
            "os.execv(sys.executable, [sys.executable, fixture, *sys.argv[1:]])\n")
        wrapper.chmod(0o755)
        log = self.root / "provider-env.jsonl"
        self.env["AUTOCODE_TEST_ENV_LOG"] = str(log)
        return log

    def test_provider_launches_without_credentials_unless_passed_through(self):
        log = self.record_provider_environment()
        self.env.update({"AUTOCODE_FIXTURE_MODE": "no-human", "GITHUB_TOKEN": "fixture-token",
                         "FIXTURE_DB_PASSWORD": "fixture-password", "FIXTURE_API_KEY": "fixture-key",
                         agent_env.PASS_VARIABLE: "FIXTURE_API_KEY"})
        self.launch(["Build greeting", "--chat"], 0, answers="CLI\nyes\n")
        _, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        launches = [set(json.loads(line)) for line in log.read_text().splitlines()]
        self.assertTrue(launches)
        for names in launches:
            self.assertNotIn("GITHUB_TOKEN", names)
            self.assertNotIn("FIXTURE_DB_PASSWORD", names)
            self.assertIn("FIXTURE_API_KEY", names)
        stage = next(row for row in state["stages"] if row.get("stage") == "terra")
        self.assertIn("GITHUB_TOKEN", stage["withheld_env"])
        self.assertNotIn("FIXTURE_API_KEY", stage["withheld_env"])


if __name__ == "__main__":
    unittest.main()
