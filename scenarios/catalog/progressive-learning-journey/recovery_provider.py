#!/usr/bin/env python3
"""Offline recovery fixture: real commands, distinct approved practice work.

The normal catalog fake remains untouched. This transport reuses its public
report builders and substitutes an eleven-slice proposal only in pool mode.
It never imports the application or reads/writes the runner's state.json.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
from pathlib import Path


def practice_source(available):
    return f'''import lessons
import progress

AVAILABLE = {available}


def prompt(number):
    if not 2 <= number <= AVAILABLE:
        raise KeyError(number)
    return f"What is {{number}} + {{number}}?"


def exercise(path, number, answer):
    prompt(number)
    if answer != str(number + number):
        return False
    progress.save(path, f"exercise-{{number}}")
    return True


def recommend(path):
    done = set(progress.completed(path))
    return next((number for number in range(2, AVAILABLE + 1)
                 if f"exercise-{{number}}" not in done), None)
'''


def project_checks():
    header = '''import tempfile
import unittest
from pathlib import Path

import practice
import progress


def prove_exercise(test, number):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "progress.json"
        test.assertEqual(practice.prompt(number), f"What is {number} + {number}?")
        test.assertFalse(practice.exercise(path, number, "wrong"))
        test.assertEqual(progress.completed(path), [])
        test.assertTrue(practice.exercise(path, number, str(number + number)))
        test.assertEqual(progress.completed(Path(str(path))), [f"exercise-{number}"])
        test.assertTrue(practice.exercise(path, number, str(number + number)))
        test.assertEqual(progress.completed(path), [f"exercise-{number}"])
'''
    for number in range(2, 12):
        header += f'''\n\nclass Exercise{number:02d}(unittest.TestCase):
    def test_distinct_exercise_survives_reopen(self):
        prove_exercise(self, {number})
'''
    header += '''\n\nclass Product(unittest.TestCase):
    def test_every_approved_exercise_advances_durable_recommendation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            for number in range(2, 12):
                self.assertEqual(practice.recommend(Path(str(path))), number)
                self.assertTrue(practice.exercise(path, number, str(number + number)))
            self.assertEqual(progress.completed(path), [f"exercise-{number}" for number in range(2, 12)])
            self.assertIsNone(practice.recommend(path))
'''
    return header


def main():
    spec = importlib.util.spec_from_file_location("catalog_fake", os.environ["SCENARIO_BASE_FAKE_CODEX"])
    fake = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fake)
    fixture = fake.CONFIG.get("recovery_fixture", "pools")
    original_proposal, original_contract, original_task = fake.progressive_proposal, fake.contract, fake.progressive_task
    original_progressive_report, original_report = fake.progressive_report, fake.report_for

    def proposal(later=False, done=None):
        if fixture != "pools":
            return original_proposal(later, done)
        done = list(done or [])
        rows = [original_proposal()["slices"][0]]
        for number in range(2, 12):
            checks = [{"id": f"EXERCISE_{number:02d}", "relation": "contributes_to",
                       "criterion_ids": ["C1"],
                       "method": f"python3 -m unittest test_pool_journey.Exercise{number:02d}"}]
            if number == 11:
                checks.append({"id": "PRODUCT", "relation": "fully_verify", "criterion_ids": ["C1"],
                               "method": fake.CHECK})
            rows.append({"id": f"S{number}", "intended_result": f"Answer practice exercise {number} and reopen its durable result",
                         "criterion_ids": ["C1"], "paths": fake.PATHS, "depends_on": [f"S{number - 1}"],
                         "tentative": True, "checks": checks})
        if later:
            rows = [row for row in rows if row["id"] not in done]
            rows[0]["tentative"] = False
        return {"version": 1, "needed_because": "Each newly approved practice exercise is independently useful before full course proof",
                "shared_decisions": ["Retain lessons.py/progress.py and add bounded practice.py exercises with cumulative durable proof"],
                "outstanding_criteria": [], "done_slices": done, "slices": rows}

    def contract(final=False):
        body = original_contract(final)
        if fixture == "pools":
            body["acceptance_criteria"][0]["criterion"] = "Original lessons and all ten approved practice exercises persist and advance recommendations after reopening"
        return body

    def task(later=False, proposal=None):
        value = original_task(later, proposal)
        if fixture == "pools":
            # The normal fixture's S4 is validation-only after a product edit;
            # these approved S4/S11 entries are distinct implementation work.
            value["kind"] = "implement"
        return value

    def progressive_report(stage, data, common):
        packet = data.get("progressive_revision") or {}
        if fixture == "review_nonzero" and stage == "astra_finalize" and packet.get("phase") == "review":
            marker = Path(os.environ["SCENARIO_FAKE_CONFIG"]).with_suffix(".review-failed")
            if not marker.exists():
                marker.touch()
                fake.emit({"type": "turn.failed", "error": "Offline transport interrupted before reporting",
                           "usage": {"input_tokens": 7, "output_tokens": 3}})
                raise SystemExit(1)
        if fixture == "pools" and stage == "terra" and data.get("progressive_verification"):
            head = data["progressive_verification"]["active_slice"]
            number = int(head["id"].removeprefix("S"))
            reference = Path(fake.CONFIG["reference"])
            for relative in fake.PATHS:
                shutil.copy2(reference / relative, Path.cwd() / relative)
            if number == 1:
                seed_slice = Path(os.environ["SCENARIO_BASE_FAKE_CODEX"]).parents[1] / "catalog" / "progressive-learning-journey" / "slices" / "S1" / "lessons.py"
                shutil.copy2(seed_slice, Path.cwd() / "lessons.py")
            (Path.cwd() / "practice.py").write_text(practice_source(number))
            commands = data["progressive_verification"]["required_commands"]
            results = [fake.run_verify(command) for command in commands]
            return {**common, "summary": head["intended_result"], "changed_files": fake.PATHS,
                    "commands_run": commands, "results": [f"exit {code}" for code, _ in results],
                    "remaining_risks": [], "evidence_refs": [ref for _, ref in results],
                    "addressed_requirements": ["C1"], "untested_behavior": [], "recommended_checks": commands}
        return original_progressive_report(stage, data, common)

    def report(stage, data):
        if fixture == "review_repair" and data.get("report_repair") and (data.get("original") or {}).get("stage") == "astra_finalize":
            # Repair only the malformed boolean of the same completed review.
            content = data["rejected_report"]["content"]
            value = json.loads(json.dumps(content))
            value["accepted"] = True
            return value
        value = original_report(stage, data)
        if (fixture == "review_repair" and stage == "astra_finalize"
                and (data.get("progressive_revision") or {}).get("phase") == "review"):
            value["accepted"] = "true"
        return value

    fake.progressive_proposal, fake.contract, fake.progressive_task = proposal, contract, task
    fake.progressive_report, fake.report_for = progressive_report, report
    return fake.main()


if __name__ == "__main__":
    raise SystemExit(main())
