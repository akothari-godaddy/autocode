"""tools/autocode_multicomponent.py: batching/ownership logic, and one end-to-end
build+integrate through the real CLI with a scripted, per-component fake model."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from . import autocode_multicomponent as mc

HERE = Path(__file__).resolve().parent
FAKE_PROVIDER = HERE / "fixtures" / "multicomponent_fake.py"
FIXTURE_OPTIONS = ("--engine", "codex", "--joint-planning", "--astra-model", "gpt-6-astra",
                   "--terra-model", "gpt-5.6-terra", "--sol-model", "gpt-5.6-sol", "--completion-model",
                   "gpt-6-astra", "--glm-model", "gpt-5.6-sol", "--plan-reviewer-model", "gpt-6-astra")


def component(id, *, depends_on=(), publishes=(), consumes=()):
    return {"id": id, "description": f"the {id} component", "requirements": ["R1"],
            "depends_on": list(depends_on), "publishes_contracts": list(publishes),
            "consumes_contracts": list(consumes)}


def architecture(*components, contracts_dir):
    return mc.Architecture(components={c["id"]: mc.Component(
        id=c["id"], description=c["description"], requirements=tuple(c["requirements"]),
        depends_on=tuple(c["depends_on"]), publishes_contracts=tuple(c["publishes_contracts"]),
        consumes_contracts=tuple(c["consumes_contracts"])) for c in components}, contracts_dir=contracts_dir)


class BatchingTests(unittest.TestCase):
    def test_independent_components_share_one_batch(self):
        arch = architecture(component("alpha"), component("beta"), contracts_dir=Path("."))
        self.assertEqual([["alpha", "beta"]], [[c.id for c in batch] for batch in arch.batches()])

    def test_a_chain_is_sequenced_into_separate_batches(self):
        arch = architecture(component("a"), component("b", depends_on=["a"]), component("c", depends_on=["b"]),
                            contracts_dir=Path("."))
        self.assertEqual([["a"], ["b"], ["c"]], [[c.id for c in batch] for batch in arch.batches()])

    def test_independent_and_dependent_components_mix(self):
        arch = architecture(component("a"), component("b"), component("c", depends_on=["a", "b"]),
                            contracts_dir=Path("."))
        self.assertEqual([["a", "b"], ["c"]], [[c.id for c in batch] for batch in arch.batches()])

    def test_a_cycle_is_refused(self):
        arch = architecture(component("a", depends_on=["b"]), component("b", depends_on=["a"]), contracts_dir=Path("."))
        with self.assertRaisesRegex(mc.ArchitectureError, "cycle"):
            arch.batches()

    def test_an_unknown_dependency_is_refused_at_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "components.json").write_text(json.dumps([component("a", depends_on=["ghost"])]))
            with self.assertRaisesRegex(mc.ArchitectureError, "unknown"):
                mc.Architecture.load(directory)


class BriefTests(unittest.TestCase):
    def test_brief_embeds_the_contract_schema_and_ownership_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            contracts = Path(tmp) / "contracts"
            contracts.mkdir()
            (contracts / "greeting.schema.json").write_text('{"type": "object", "properties": {}}')
            arch = architecture(component("writer", publishes=["greeting"]),
                                component("reader", depends_on=["writer"], consumes=["greeting"]),
                                contracts_dir=contracts)
            brief = mc.component_brief(arch.components["reader"], arch)
            self.assertIn("Implement the reader component", brief)
            self.assertIn('"type": "object"', brief)
            self.assertIn("Own only the directory components/reader/", brief)
            self.assertIn("Do not implement or stub another component's directory", brief)


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.test", *args],
                   cwd=cwd, check=True, capture_output=True, text=True)


class BuildAndIntegrateTests(unittest.TestCase):
    """Two independent components, built through the real CLI with a scripted model."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="multicomponent-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "base")
        bindir = self.root / "bin"
        bindir.mkdir()
        shutil.copy2(FAKE_PROVIDER, bindir / "codex")
        (bindir / "codex").chmod(0o755)
        self.manifest = self.root / "manifest.json"
        self.env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "AUTOCODE_HOME": str(self.root / "registry"),
                    "PYTHONDONTWRITEBYTECODE": "1", "FAKE_MANIFEST": str(self.manifest)}
        self.arch = architecture(component("alpha"), component("beta"), contracts_dir=self.root / "contracts")

    def write_manifest(self, **overrides):
        base = {"alpha": {"description": "the alpha component", "file": "components/alpha/message.txt",
                          "content": "from alpha\n", "check": "test -f components/alpha/message.txt"},
                "beta": {"description": "the beta component", "file": "components/beta/message.txt",
                        "content": "from beta\n", "check": "test -f components/beta/message.txt"}}
        base.update(overrides)
        self.manifest.write_text(json.dumps(base))

    def build(self, *, auto_approve=True, max_advances=30):
        return mc.MultiComponentBuild(self.repo, self.arch, options=FIXTURE_OPTIONS, env=self.env, timeout=300,
                                      max_advances=max_advances).build(auto_approve=auto_approve)

    def test_two_independent_components_build_and_integrate(self):
        # Batching itself (independent components share one ThreadPoolExecutor batch)
        # is proven deterministically in BatchingTests; concurrent execution within a
        # batch is then standard-library ThreadPoolExecutor behavior, not re-proven here
        # by a timing assertion, which would be fragile rather than informative.
        self.write_manifest()
        build = mc.MultiComponentBuild(self.repo, self.arch, options=FIXTURE_OPTIONS, env=self.env, timeout=300)
        results = build.build(auto_approve=True)
        for cid in ("alpha", "beta"):
            self.assertTrue(results[cid].ready_to_integrate, results[cid].error)
            self.assertEqual("TASK_COMPLETE", results[cid].view["status"])
            self.assertTrue((results[cid].workspace / "components" / cid / "message.txt").is_file())

        target = self.repo / "integration"
        git(self.repo, "worktree", "add", str(target), "HEAD")
        outcome = build.integrate(target)
        self.assertEqual({"alpha", "beta"}, set(outcome["integrated"]))
        self.assertIsNone(outcome["failed"])
        self.assertEqual("from alpha\n", (target / "components" / "alpha" / "message.txt").read_text())
        self.assertEqual("from beta\n", (target / "components" / "beta" / "message.txt").read_text())
        # The unrelated original worktree at HEAD never received either component's file:
        # git worktree add above checked out plain HEAD, so this also confirms integrate()
        # did the copying, not something upstream of it.
        self.assertFalse((self.repo / "components").exists())

    def test_a_component_that_writes_outside_its_own_directory_is_refused_at_integration(self):
        self.write_manifest(beta={"description": "the beta component", "file": "shared/leak.txt",
                                  "content": "leaked\n", "check": "test -f shared/leak.txt"})
        results = self.build()
        self.assertTrue(results["alpha"].ready_to_integrate)
        self.assertTrue(results["beta"].ready_to_integrate)  # the run itself succeeds; only integration refuses it

        build = mc.MultiComponentBuild(self.repo, self.arch, options=FIXTURE_OPTIONS, env=self.env)
        build.results = results
        target = self.repo / "integration"
        git(self.repo, "worktree", "add", str(target), "HEAD")
        outcome = build.integrate(target)
        self.assertEqual("beta", outcome["failed"])
        self.assertIn("outside components/beta/", outcome["detail"])
        # alpha, processed first alphabetically, is still applied; beta's leak is not.
        self.assertEqual(["alpha"], outcome["integrated"])
        self.assertTrue((target / "components" / "alpha" / "message.txt").is_file())
        self.assertFalse((target / "shared").exists())


if __name__ == "__main__":
    unittest.main()
