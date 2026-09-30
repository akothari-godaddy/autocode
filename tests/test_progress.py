import contextlib
import io
import unittest

from autocode_progress import StageProgress, tool_description


class ProgressTests(unittest.TestCase):
    def test_action_descriptions_omit_commands_contents_and_secret_paths(self):
        self.assertEqual('running the tests', tool_description('bash', {
            'command': 'TOKEN=secret python -m pytest tests/test_demo.py'}))
        self.assertEqual('reading transport.ts', tool_description('read', {
            'filePath': '/private/user/project/src/transport.ts'}))
        self.assertEqual('reading repository files', tool_description('read', {
            'filePath': '/private/user/.env'}))
        self.assertEqual('updating jsonl.py', tool_description('edit', {
            'filePath': 'src/jsonl.py', 'newString': 'secret contents'}))

    def test_specific_work_messages(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            progress = StageProgress('terra')
            progress.activity({'activity': 'running_tool', 'detail': 'updating transport.ts'})
            progress.activity({'activity': 'waiting_for_provider', 'completed_tool_count': 1,
                               'completed_action': 'running the tests'})
        self.assertIn('INFO: Builder is updating transport.ts.', output.getvalue())
        self.assertIn('INFO: Builder finished running the tests.', output.getvalue())

    def test_human_stage_names_and_no_heartbeat_spam(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            progress = StageProgress('terra')
            progress.started()
            for _ in range(5):
                progress.activity({'activity': 'waiting_for_provider', 'completed_tool_count': 0})
            progress.activity({'activity': 'running_tool', 'completed_tool_count': 0})
            progress.activity({'activity': 'running_tool', 'completed_tool_count': 0})
            progress.activity({'activity': 'waiting_for_provider', 'completed_tool_count': 2})
            progress.finished(0)
        lines = output.getvalue().splitlines()
        self.assertEqual(4, len(lines))
        self.assertTrue(all(line.startswith('INFO: Builder') for line in lines))
        self.assertIn('completed 2 repository action(s)', lines[2])
        self.assertNotIn('waiting_for_provider', output.getvalue())

    def test_timeout_interruption_and_review_labels(self):
        for stage, label in [('requirements_gather', 'Requirements'),
                             ('glm_revise', 'Planner'), ('sol', 'Validator')]:
            with self.subTest(stage=stage):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    progress = StageProgress(stage)
                    progress.started()
                    progress.finished(-15, timed_out=True)
                    progress.finished(-15, interrupted=True)
                self.assertIn(f'INFO: {label} started', output.getvalue())
                self.assertIn('execution deadline', output.getvalue())
                self.assertIn('was interrupted', output.getvalue())
