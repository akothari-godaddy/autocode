"""The serial assignment boundary measures the whole assignment, from saved snapshots."""
import json
from pathlib import Path
import tempfile
import unittest

import autocode_assignment as assignment


class RetainedChangesTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def snapshot(self, name, files):
        path = self.dir / name
        path.write_text(json.dumps({"head": "h", "files": files, "revision": name}))
        return str(path)

    def attempt(self, started, before, after, **extra):
        return {"stage": "terra", "iteration": 1, "task_id": "task-1", "started_at": started,
                "before_ref": before, "after_ref": after, **extra}

    def test_an_earlier_attempts_edit_counts_against_a_retry_that_changed_nothing(self):
        start = self.snapshot("a.before", {"src/a.py": "1", "notes.txt": "n"})
        left = self.snapshot("a.after", {"src/a.py": "2", "notes.txt": "stray"})
        first = self.attempt("t1", start, left, changed_files=["notes.txt", "src/a.py"])
        retry = self.attempt("t2", left, self.snapshot("b.after", {"src/a.py": "2", "notes.txt": "stray"}),
                             changed_files=[])
        self.assertEqual(["notes.txt"], assignment.outside(["src"], [first], retry))

    def test_a_deleted_path_is_a_change(self):
        start = self.snapshot("a.before", {"src/a.py": "1", "keep.txt": "k"})
        first = self.attempt("t1", start, self.snapshot("a.after", {"src/a.py": "2"}))
        self.assertEqual(["keep.txt"], assignment.outside(["src/"], [], first))

    def test_the_archived_copy_of_an_attempt_is_found_when_another_copy_is_stale(self):
        archived = self.snapshot("archived.before", {"src/a.py": "1"})
        stale = self.attempt("t1", str(self.dir / "moved.before"), None)
        rejected = dict(stale, before_ref=archived, rejected=True)
        retry = self.attempt("t2", archived, self.snapshot("b.after", {"src/a.py": "2"}))
        self.assertEqual([], assignment.outside(["src"], [stale, rejected], retry))

    def test_an_unreadable_start_is_missing_evidence_not_an_empty_delta(self):
        first = self.attempt("t1", str(self.dir / "gone"), None)
        retry = self.attempt("t2", str(self.dir / "gone"), self.snapshot("b.after", {}), changed_files=[])
        self.assertIsNone(assignment.outside(["src"], [first], retry))

    def test_a_new_compiled_program_a_build_left_behind_is_not_a_scope_violation(self):
        # A live Go run's Builder ran `go build .`, which wrote ./policy; the stage was refused for it.
        workspace = Path(tempfile.mkdtemp())
        (workspace / "policy").write_bytes(b"\x7fELF\x02\x01\x01" + b"\0" * 64)
        (workspace / "notes.txt").write_text("stray")
        (workspace / "script.sh").write_text("#!/bin/sh\necho hi\n")
        start = self.snapshot("a.before", {"src/a.go": "1"})
        after = self.snapshot("a.after", {"src/a.go": "2", "policy": "executable:x", "notes.txt": "n",
                                          "script.sh": "executable:y"})
        first = self.attempt("t1", start, after)
        self.assertEqual(["notes.txt", "script.sh"], assignment.outside(["src"], [], first, workspace=workspace))
        # Without the workspace nothing can be read, so nothing is excused.
        self.assertEqual(["notes.txt", "policy", "script.sh"], assignment.outside(["src"], [], first))

    def test_changing_an_existing_executable_still_counts(self):
        workspace = Path(tempfile.mkdtemp())
        (workspace / "tool").write_bytes(b"\x7fELF" + b"\0" * 64)
        start = self.snapshot("a.before", {"src/a.go": "1", "tool": "executable:old"})
        first = self.attempt("t1", start, self.snapshot("a.after", {"src/a.go": "2", "tool": "executable:new"}))
        self.assertEqual(["tool"], assignment.outside(["src"], [], first, workspace=workspace))

    def test_a_first_attempt_without_snapshots_falls_back_to_its_measured_delta(self):
        only = {"stage": "terra", "changed_files": ["src/a.py", "other.txt"]}
        self.assertEqual(["other.txt"], assignment.outside(["src"], [], only))


if __name__ == "__main__":
    unittest.main()
