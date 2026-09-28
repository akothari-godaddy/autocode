"""Runner boundary integration, with no live models or autonomous code writes."""
import copy
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from . import test_autocode as base
from . import test_report_repair as repairs
from goal_fixtures import approve_fixture

runner, support = base.runner, base.s
# The exact module instance runner.autopilot itself dispatches through: importing
# tools.units.autoresolver directly here would load a second, package-relative
# copy whose autocode_support.Paused is a different class than the one raised
# through runner's own bare (sys.path) import chain.
diagnosis_unit = runner.autopilot.unit_module('astra_diagnose')


class ResolverRuntimeTests(unittest.TestCase):
    queue = repairs.RepairTests.queue

    def setUp(self):
        base.RetrofitTest.setUp(self)
        approve_fixture(self.state, runner.goals)

    def boundary(self):
        return runner.resolver_runtime.boundary(runner, self.state, self.run, self.root)

    def repeated_terra_failure(self):
        """A repeated, report-repair-exhausted Builder failure, paused as such."""
        pending = self.queue()
        entry = self.state['failure_history'][pending['original']['failure_key']]
        entry['count'] = 3
        entry['last_error'] = 'Missing summary'
        self.state.update(status='PAUSED_REPEATED_FAILURE')
        return pending['original']

    def test_report_repair_gate_is_durable_idempotent_and_runner_owned(self):
        self.queue()
        original_goal = copy.deepcopy(self.state['goal_contract'])
        self.assertTrue(self.boundary())
        saved = support.read(self.run / 'state.json')
        outcome = saved['stages'][-1]
        self.assertTrue(outcome['runner_owned'])
        self.assertEqual('runner', outcome['engine'])
        self.assertEqual(0, outcome['runner_calls'])
        self.assertNotIn('command', outcome)
        self.assertEqual('retry', outcome['decision']['action'])
        self.assertEqual(1, outcome['receipt']['attempt'])
        self.state = saved
        self.boundary()
        self.assertEqual(len(saved['stages']), len(support.read(self.run / 'state.json')['stages']))
        self.assertEqual([1], list(self.state['resolver']['attempts'].values()))
        self.assertEqual(original_goal, self.state['goal_contract'])

    def test_execute_repair_reaches_resolver_before_only_report_launch(self):
        self.queue()
        with patch.object(runner, 'run_role', side_effect=RuntimeError('offline stop')) as launch:
            with self.assertRaisesRegex(RuntimeError, 'offline stop'):
                runner.execute_report_repair(self.state, self.run, self.root)
        self.assertTrue(self.state['stages'][-1]['runner_owned'])
        self.assertTrue(launch.call_args.kwargs['report_only'])
        self.assertFalse(launch.call_args.kwargs['allow_write'])

    def test_repeated_failure_caps_resolver_retry_after_restart(self):
        self.queue()
        self.boundary()
        entry = next(iter(self.state['failure_history'].values()))
        entry['count'] = 3
        support.atomic_json(self.run / 'state.json', self.state)
        self.state = support.read(self.run / 'state.json')
        with patch.object(runner, 'run_role') as launch, self.assertRaises(support.Paused) as caught:
            runner.execute_report_repair(self.state, self.run, self.root)
        self.assertEqual('PAUSED_REPEATED_FAILURE', caught.exception.status)
        self.assertEqual('escalate', self.state['stages'][-1]['decision']['action'])
        self.assertEqual(0, self.state['pending_report_repair']['attempts'])
        launch.assert_not_called()

    def test_resolver_attempt_budget_survives_reload(self):
        self.queue()
        self.boundary()
        self.state['pending_report_repair']['attempts'] = 1
        self.boundary()
        self.state = support.read(self.run / 'state.json')
        # Even if another path resets the local repair counter, a third distinct
        # request for the same blocker cannot reset the resolver's durable budget.
        self.state['pending_report_repair']['attempts'] = 0
        self.state['pending_report_repair']['error'] = 'different error wording'
        with self.assertRaises(support.Paused):
            self.boundary()
        self.assertEqual([2], list(self.state['resolver']['attempts'].values()))

    def test_permissions_goal_changes_and_untyped_blockers_remain_user_owned(self):
        for kind in ('permission', 'goal_change', 'clarification', 'blocker'):
            request = {'kind': kind, 'decision_needed': 'Make a material decision', 'impact': 'Changes work',
                       'discovered': 'Needs decision', 'options': ['yes', 'no'], 'proposed_delta': ''}
            runner.goals.wait_for_user(self.state, request)
            before = copy.deepcopy(self.state)
            self.boundary()
            self.assertEqual('WAITING_FOR_USER', self.state['status'])
            self.assertEqual(before['pending_questions'], self.state['pending_questions'])
            self.assertEqual(before['goal_contract'], self.state['goal_contract'])
            self.assertEqual('escalate', self.state['stages'][-1]['decision']['action'])
            self.assertFalse(self.state['stages'][-1]['receipt']['callbacks_used'])

    def test_no_resolver_during_active_operation_or_unapproved_goal(self):
        self.queue()
        self.state['active_stage'] = {'stage': 'terra'}
        before = copy.deepcopy(self.state)
        self.assertFalse(self.boundary())
        self.assertEqual(before, self.state)
        self.state.pop('active_stage')
        self.state['goal_contract']['approval_status'] = 'draft'
        self.assertFalse(self.boundary())
        self.assertNotIn('resolver', self.state)

    def test_corrupt_saved_ledger_pauses_before_launch(self):
        self.queue()
        self.state['resolver'] = {'cache': {'bad': []}}
        with patch.object(runner, 'run_role') as launch, self.assertRaises(support.Paused):
            runner.execute_report_repair(self.state, self.run, self.root)
        launch.assert_not_called()
        self.assertEqual(0, self.state['pending_report_repair']['attempts'])

    def test_blocked_validation_keeps_original_review_route_and_evidence(self):
        record = {'stage': 'sol', 'role': 'sol', 'iteration': 5, 'output': str(self.run / 'sol.json'),
                  'source_revision': support.snapshot(self.root)['revision']}
        support.atomic_json(self.run / 'sol.json', {'verdict': 'BLOCKED'})
        self.state['stages'].append(record)
        self.state.update(validation={'verdict': 'BLOCKED', 'output': record['output']}, next_stage='astra_review')
        self.boundary()
        self.assertEqual('astra_review', self.state['next_stage'])
        self.assertEqual('BLOCKED', self.state['validation']['verdict'])
        self.assertEqual('continue', self.state['stages'][-1]['decision']['action'])

    def test_plain_fail_does_not_record_resolver_or_consume_failure_budget(self):
        self.state.update(validation={'verdict': 'FAIL', 'output': str(self.run / 'sol.json')},
                          next_stage='astra_review')
        self.state['stages'].append({'stage': 'sol', 'output': str(self.run / 'sol.json')})
        before = copy.deepcopy(self.state)
        for _ in range(4):
            self.assertFalse(self.boundary())
        self.assertEqual(before, self.state)

    def test_receipt_loader_accepts_a_saved_receipt_missing_the_version_field(self):
        self.queue()
        self.boundary()
        saved = self.state['resolver']
        key = next(iter(saved['cache']))
        del saved['cache'][key][1]['version']
        ledger = runner.resolver_runtime.load_ledger(saved)
        _, receipt = next(iter(ledger.cache.values()))
        self.assertEqual(1, receipt.version)

    def test_receipt_loader_rejects_a_retyped_callbacks_used(self):
        # A dataclass constructor accepts Receipt(callbacks_used=[]) without
        # complaint; only the explicit type check catches this reinterpretation.
        self.queue()
        self.boundary()
        saved = self.state['resolver']
        key = next(iter(saved['cache']))
        saved['cache'][key][1]['callbacks_used'] = []
        with self.assertRaisesRegex(ValueError, 'callbacks_used'):
            runner.resolver_runtime.load_ledger(saved)

    def test_receipt_loader_rejects_an_unsupported_version(self):
        self.queue()
        self.boundary()
        saved = self.state['resolver']
        key = next(iter(saved['cache']))
        saved['cache'][key][1]['version'] = 99
        with self.assertRaisesRegex(ValueError, 'version'):
            runner.resolver_runtime.load_ledger(saved)

    def test_corrupt_receipt_still_pauses_before_launch(self):
        self.queue()
        self.boundary()
        saved = self.state['resolver']
        key = next(iter(saved['cache']))
        saved['cache'][key][1]['callbacks_used'] = []
        support.atomic_json(self.run / 'state.json', self.state)
        self.state = support.read(self.run / 'state.json')
        with patch.object(runner, 'run_role') as launch, self.assertRaises(support.Paused) as caught:
            runner.execute_report_repair(self.state, self.run, self.root)
        self.assertEqual('PAUSED_RESOLVER_STATE', caught.exception.status)
        launch.assert_not_called()

    def test_explicit_resume_records_epoch_and_accumulates_a_lifetime_total_it_never_resets(self):
        self.queue()
        self.boundary()
        self.state['pending_report_repair']['attempts'] = 1
        self.boundary()
        self.assertEqual([2], list(self.state['resolver']['attempts'].values()))
        events_before = len(self.state.get('user_events', []))
        runner.resolver_runtime.reset_for_resume(self.state)
        self.assertEqual({}, self.state['resolver']['attempts'])
        self.assertEqual(2, self.state['resolver']['lifetime_attempts'])
        self.assertEqual(events_before + 1, len(self.state['user_events']))
        epoch = self.state['user_events'][-1]
        self.assertEqual('resolver_resume_epoch', epoch['kind'])
        self.assertEqual(2, epoch['cleared_total'])
        self.assertEqual(2, epoch['lifetime_attempts'])
        # A resume with nothing left to clear is a true no-op: no duplicate
        # event, and the lifetime total is never itself reset by this function.
        runner.resolver_runtime.reset_for_resume(self.state)
        self.assertEqual(2, self.state['resolver']['lifetime_attempts'])
        self.assertEqual(events_before + 1, len(self.state['user_events']))

    def test_reset_for_resume_is_a_no_op_when_no_resolver_state_exists(self):
        before = copy.deepcopy(self.state)
        runner.resolver_runtime.reset_for_resume(self.state)
        self.assertEqual(before, self.state)

    def test_explicit_resume_records_report_repair_epoch_and_accumulates_lifetime_total(self):
        self.queue()
        self.boundary()
        self.state['pending_report_repair']['attempts'] = 3
        events_before = len(self.state.get('user_events', []))
        runner.reset_report_repair_for_resume(self.state)
        self.assertEqual(0, self.state['pending_report_repair']['attempts'])
        self.assertEqual(3, self.state['report_repair_lifetime_attempts'])
        self.assertEqual(events_before + 1, len(self.state['user_events']))
        epoch = self.state['user_events'][-1]
        self.assertEqual('report_repair_resume_epoch', epoch['kind'])
        self.assertEqual(3, epoch['cleared_attempts'])
        runner.reset_report_repair_for_resume(self.state)
        self.assertEqual(3, self.state['report_repair_lifetime_attempts'])
        self.assertEqual(events_before + 1, len(self.state['user_events']))


class OperationalDiagnosisTests(unittest.TestCase):
    """The astra_diagnose route: admission, model completion, and their shared budget."""
    queue = repairs.RepairTests.queue
    repeated_terra_failure = ResolverRuntimeTests.repeated_terra_failure

    def setUp(self):
        base.RetrofitTest.setUp(self)
        approve_fixture(self.state, runner.goals)

    def admit(self):
        return runner.resolver_runtime.admit_operational_diagnosis(runner, self.state, self.run, self.root)

    def test_admission_requires_paused_repeated_failure_status(self):
        self.queue()
        with self.assertRaisesRegex(ValueError, 'paused for repeated failure'):
            self.admit()

    def test_admission_requires_an_exhausted_report_repair_identity(self):
        self.state.update(status='PAUSED_REPEATED_FAILURE')
        with self.assertRaisesRegex(ValueError, 'retry-failed-stage'):
            self.admit()

    def test_admission_is_scoped_to_a_terra_failure(self):
        self.queue(role='sol', stage='sol')
        pending = self.state['pending_report_repair']
        entry = self.state['failure_history'][pending['original']['failure_key']]
        entry['count'] = 3
        self.state.update(status='PAUSED_REPEATED_FAILURE')
        with self.assertRaisesRegex(ValueError, 'scoped to a repeated Builder'):
            self.admit()

    def test_admission_requires_actual_repetition(self):
        self.queue()
        self.state.update(status='PAUSED_REPEATED_FAILURE')
        with self.assertRaisesRegex(ValueError, 'No unchanged repeated failure'):
            self.admit()

    def test_admission_dispatches_astra_diagnose_without_yet_charging_the_run_level_cap(self):
        # Admission alone does not guarantee astra_diagnose ever launches, so
        # it must not charge the run-level cap; charge_diagnostic_dispatch does
        # that at actual dispatch (see the ChargeDiagnosticDispatch tests).
        record = self.repeated_terra_failure()
        self.admit()
        self.assertEqual('astra_diagnose', self.state['next_stage'])
        self.assertEqual('RUNNING', self.state['status'])
        self.assertNotIn('diagnostic_calls', self.state['resolver'])
        request = self.state['diagnosis_request']
        self.assertEqual('terra', request['original_stage'])
        self.assertEqual(record['failure_key'], request['failure_key'])
        self.assertEqual(3, request['repeated_count'])
        outcome = self.state['stages'][-1]
        self.assertTrue(outcome['runner_owned'])
        self.assertEqual('retry', outcome['decision']['action'])
        self.assertEqual(1, outcome['receipt']['attempt'])
        saved = support.read(self.run / 'state.json')
        self.assertEqual('astra_diagnose', saved['next_stage'])
        # The superseded report-repair pointer is archived, not left dangling
        # against a next_stage its own stale-route check would now reject.
        self.assertNotIn('pending_report_repair', self.state)
        self.assertEqual('Superseded by an admitted operational diagnosis',
                         self.state['report_repair_archive'][-1]['reason'])

    def test_admission_exhausted_run_level_cap_escalates_without_dispatch(self):
        self.repeated_terra_failure()
        self.state.setdefault('resolver', {})['diagnostic_calls'] = runner.resolver_runtime.diagnostic_call_limit(self.state)
        with self.assertRaises(support.Paused) as caught:
            self.admit()
        self.assertEqual('PAUSED_REPEATED_FAILURE', caught.exception.status)
        self.assertNotIn('diagnosis_request', self.state)
        self.assertEqual('escalate', self.state['stages'][-1]['decision']['action'])

    def test_admission_is_not_reentrant_once_status_leaves_the_pause(self):
        self.repeated_terra_failure()
        self.admit()
        # A second admission attempt is refused by the status check alone.
        with self.assertRaisesRegex(ValueError, 'paused for repeated failure'):
            self.admit()

    def test_finish_accepts_retry_and_clears_the_failure_identity(self):
        record = self.repeated_terra_failure()
        self.admit()
        result = runner.resolver_runtime.finish_operational_diagnosis(
            self.state, self.run, {'action': 'retry', 'rationale': 'Missing summary field; add it explicitly.',
                                    'guidance': 'Include a nonempty summary before resubmitting.'})
        self.assertTrue(result)
        self.assertEqual('terra', self.state['next_stage'])
        self.assertEqual('RUNNING', self.state['status'])
        self.assertNotIn('diagnosis_request', self.state)
        self.assertNotIn(record['failure_key'], self.state['failure_history'])
        self.assertEqual([2], list(self.state['resolver']['attempts'].values()))

    def test_finish_rejects_escalate_and_preserves_the_failure_identity(self):
        # Must not raise: this function's own _evaluate call already mutated
        # the (candidate) state, and autopilot.apply_result's deep-copy-then-
        # commit pattern discards every candidate mutation the moment
        # anything raises out of it, silently losing the spent evaluation.
        record = self.repeated_terra_failure()
        self.admit()
        result = runner.resolver_runtime.finish_operational_diagnosis(
            self.state, self.run, {'action': 'escalate', 'rationale': 'Cause is unclear; needs a human decision.'})
        self.assertFalse(result)
        self.assertEqual('PAUSED_REPEATED_FAILURE', self.state['status'])
        self.assertIn('diagnosis_request', self.state)
        self.assertIn(record['failure_key'], self.state['failure_history'])
        self.assertEqual([2], list(self.state['resolver']['attempts'].values()))

    def test_finish_shares_the_two_attempt_budget_with_admission(self):
        self.repeated_terra_failure()
        self.admit()
        result = runner.resolver_runtime.finish_operational_diagnosis(
            self.state, self.run, {'action': 'retry', 'rationale': ''})  # malformed: empty rationale rejected below
        self.assertFalse(result)
        self.assertEqual('PAUSED_REPEATED_FAILURE', self.state['status'])
        # The malformed proposal still consumed the second and final evaluation.
        self.assertEqual([2], list(self.state['resolver']['attempts'].values()))

    def test_finish_on_the_real_apply_result_path_commits_an_escalate_durably(self):
        """The production exception path, not a direct mutable-state call:
        proves the escalate outcome survives autopilot.apply_result's
        deep-copy-then-commit boundary rather than being discarded."""
        record = self.repeated_terra_failure()
        self.admit()
        diag_record = {'role': 'astra', 'stage': 'astra_diagnose', 'iteration': self.state['iteration'],
                       'output': str(self.run / 'astra_diagnose.json'), 'source_revision':
                       self.state['diagnosis_request']['source_revision'], 'changed_files': [], 'duration_seconds': 0.1}
        value = {'diagnosis': 'Unclear cause after inspection.',
                 'recommendation': {'action': 'escalate', 'rationale': 'Cause is unclear; needs a human decision.'}}
        runner.autopilot.apply_result(runner, self.state, 'astra_diagnose', value, diag_record, self.root, self.run)
        self.assertEqual('PAUSED_REPEATED_FAILURE', self.state['status'])
        self.assertIn('diagnosis_request', self.state)
        self.assertIn(record['failure_key'], self.state['failure_history'])
        # The second evaluation is durably spent: a plain resume must not
        # let a later real dispatch spend a third, uncounted evaluation.
        self.assertEqual([2], list(self.state['resolver']['attempts'].values()))
        # The model's own completion record was saved too, not just the ledger.
        self.assertEqual('astra_diagnose', self.state['stages'][-1]['stage'])

    def test_diagnostic_call_limit_rejects_an_out_of_range_setting(self):
        self.state['settings']['operational_diagnosis'] = {'max_calls_per_run': 9}
        with self.assertRaises(ValueError):
            runner.resolver_runtime.diagnostic_call_limit(self.state)

    def charge(self):
        return runner.resolver_runtime.charge_diagnostic_dispatch(runner, self.state, self.run, self.root)

    def archive_timed_out_astra_diagnose_attempt(self):
        """Simulate what automatically_recover_timed_out_stage actually does:
        archive the timed-out attempt into state['stages'] at the same
        iteration, then route back to astra_diagnose for a fresh launch."""
        self.state.setdefault('stages', []).append({
            'stage': 'astra_diagnose', 'role': 'astra', 'iteration': self.state.get('iteration'),
            'automatic_recovery': True, 'timed_out': True})
        self.state['next_stage'] = 'astra_diagnose'

    def test_charge_dispatch_is_a_no_op_without_an_admitted_diagnosis_request(self):
        before = copy.deepcopy(self.state)
        self.charge()
        self.assertEqual(before, self.state)

    def test_charge_dispatch_charges_exactly_once_at_launch(self):
        self.repeated_terra_failure()
        self.admit()
        self.assertNotIn('diagnostic_calls', self.state['resolver'])
        self.charge()
        self.assertEqual(1, self.state['resolver']['diagnostic_calls'])
        saved = support.read(self.run / 'state.json')
        self.assertEqual(1, saved['resolver']['diagnostic_calls'])

    def test_charge_dispatch_rechecking_the_same_unlaunched_attempt_is_idempotent(self):
        # No new stages row appeared between these calls (no relaunch
        # actually happened), so this must not spend the budget three times.
        self.repeated_terra_failure()
        self.admit()
        self.charge()
        self.charge()
        self.charge()
        self.assertEqual(1, self.state['resolver']['diagnostic_calls'])

    def test_charge_dispatch_charges_a_genuine_timeout_relaunch_again(self):
        # The exact case the review found uncounted: a real timeout archives
        # the attempt and routes back to astra_diagnose for a real relaunch,
        # which is a second genuine provider call and must be charged again.
        self.repeated_terra_failure()
        self.admit()
        self.charge()
        self.assertEqual(1, self.state['resolver']['diagnostic_calls'])
        self.archive_timed_out_astra_diagnose_attempt()
        self.charge()
        self.assertEqual(2, self.state['resolver']['diagnostic_calls'])
        # And a third relaunch (e.g. the cap is 2) is refused before launch.
        self.archive_timed_out_astra_diagnose_attempt()
        with self.assertRaises(support.Paused) as caught:
            self.charge()
        self.assertEqual('PAUSED_REPEATED_FAILURE', caught.exception.status)
        self.assertEqual(2, self.state['resolver']['diagnostic_calls'])

    def test_charge_dispatch_pauses_at_the_cap_before_any_launch(self):
        self.repeated_terra_failure()
        self.admit()
        self.state['resolver']['diagnostic_calls'] = runner.resolver_runtime.diagnostic_call_limit(self.state)
        with self.assertRaises(support.Paused) as caught:
            self.charge()
        self.assertEqual('PAUSED_REPEATED_FAILURE', caught.exception.status)
        # The exhausted charge is not itself recorded as spent again.
        self.assertEqual(runner.resolver_runtime.diagnostic_call_limit(self.state),
                         self.state['resolver']['diagnostic_calls'])

    def test_charge_dispatch_survives_a_reload_between_admission_and_launch(self):
        self.repeated_terra_failure()
        self.admit()
        self.state = support.read(self.run / 'state.json')
        self.charge()
        self.state = support.read(self.run / 'state.json')
        self.assertEqual(1, self.state['resolver']['diagnostic_calls'])
        self.charge()
        self.assertEqual(1, self.state['resolver']['diagnostic_calls'])

    def test_charge_dispatch_refuses_a_stale_source_before_charging(self):
        self.repeated_terra_failure()
        self.admit()
        self.state['diagnosis_request']['source_revision'] = 'a-different-revision'
        with self.assertRaises(support.Paused) as caught:
            self.charge()
        self.assertEqual('PAUSED_STALE_HANDOFF', caught.exception.status)
        self.assertNotIn('diagnostic_calls', self.state['resolver'])

    def test_cli_diagnose_failed_stage_reaches_astra_diagnose_and_retries_terra(self):
        """The real production dispatch path (autocode.main), not a test helper."""
        local = {'auth_mode': 'fixture'}
        self.state['settings'].update(transport_identity=local,
            limits={'iteration_ceiling': 18, 'max_seconds': None, 'max_reported_tokens': None,
                    'no_progress_batches': 3, 'automatic_retries': 0})
        self.state['workspace'] = str(self.root.resolve())
        self.repeated_terra_failure()
        support.atomic_json(self.run / 'state.json', self.state)
        called = []

        def fake_role(**kwargs):
            stage = kwargs['state']['next_stage']
            called.append(stage)
            out = self.run / f'{stage}-cli.json'
            ev = self.run / f'{stage}-cli.jsonl'
            ev.write_text('{"type":"turn.completed"}\n')
            if stage == 'astra_diagnose':
                value = {'diagnosis': 'The summary field was omitted from the report.',
                         'recommendation': {'action': 'retry', 'rationale': 'Include a nonempty summary field.',
                                            'guidance': 'Add commands_run, results and a nonempty summary.'}}
                support.atomic_json(out, value)
                record = {'role': kwargs['role'], 'stage': stage, 'iteration': kwargs['state']['iteration'],
                          'output': str(out), 'events': str(ev), 'changed_files': [],
                          'source_revision': kwargs['state']['diagnosis_request']['source_revision'], 'duration_seconds': 0.01}
                return value, record
            raise support.Paused('PAUSED_REQUESTED', 'stop after the diagnosed retry for this test')

        argv = ['autocode.py', '--workspace', str(self.root.resolve()), '--run-dir', str(self.run.resolve()),
                '--resume-paused', '--diagnose-failed-stage']
        # A registry home nested inside the git workspace (the base fixture's own
        # convenience) is untracked and would make the run's own bookkeeping
        # writes look like uncommitted source drift; keep it genuinely external
        # here, matching how AUTOCODE_HOME works outside this test suite.
        registry_home = tempfile.TemporaryDirectory()
        self.addCleanup(registry_home.cleanup)
        with patch.dict(os.environ, {'AUTOCODE_HOME': registry_home.name}), \
             patch.object(sys, 'argv', argv), patch.object(support, 'assert_no_legacy_process'), \
             patch.object(support, 'local_settings', return_value=local), patch.object(runner, 'run_role', side_effect=fake_role):
            code = runner.main()
        self.assertEqual(2, code)
        self.assertEqual(['astra_diagnose', 'terra'], called)
        saved = support.read(self.run / 'state.json')
        self.assertEqual('PAUSED_REQUESTED', saved['status'])
        self.assertEqual('terra', saved['next_stage'])
        self.assertNotIn('diagnosis_request', saved)
        self.assertEqual(1, saved['resolver']['diagnostic_calls'])
        self.assertEqual([2], list(saved['resolver']['attempts'].values()))

    def test_cli_rejects_combining_diagnose_and_retry_failed_stage(self):
        argv = ['autocode.py', '--workspace', str(self.root.resolve()), '--run-dir', str(self.run.resolve()),
                '--resume-paused', '--diagnose-failed-stage', '--retry-failed-stage']
        with patch.object(sys, 'argv', argv):
            with self.assertRaises(SystemExit) as caught:
                runner.main()
        self.assertEqual(2, caught.exception.code)


class OperationalDiagnosisUnitTests(unittest.TestCase):
    """tools/units/autoresolver.py's astra_diagnose prepare/validate, standalone."""

    def setUp(self):
        base.RetrofitTest.setUp(self)
        approve_fixture(self.state, runner.goals)
        self.state['workspace'] = str(self.root)
        pending = repairs.RepairTests.queue(self)
        record = pending['original']
        self.state.update(status='PAUSED_REPEATED_FAILURE')
        self.state['diagnosis_request'] = {
            'contract_hash': self.state['goal_contract']['hash'],
            'source_revision': support.snapshot(self.root)['revision'],
            'original_stage': 'terra', 'failure_key': record.get('failure_key'), 'blocker_id': 'b1',
            'description': 'Repeated Builder report rejection', 'repeated_count': 3,
            'evidence': [record['output'], record['events']], 'evidence_hashes': {}}
        self.state['next_stage'] = 'astra_diagnose'

    def test_prepare_diagnosis_is_read_only_with_a_bounded_schema(self):
        request = diagnosis_unit.prepare_diagnosis(self.state, 'astra_diagnose', self.run / 'state.json', runner.SCHEMA_DIR)
        self.assertFalse(request.allow_write)
        self.assertEqual({'diagnosis', 'recommendation'}, set(request.schema['required']))
        self.assertEqual(['retry', 'escalate'], request.schema['properties']['recommendation']['properties']['action']['enum'])
        self.assertIn('diagnosis_request', request.prompt.split('CURRENT HANDOFF DATA\n', 1)[1])

    def test_prepare_diagnosis_rejects_the_wrong_stage(self):
        with self.assertRaises(ValueError):
            diagnosis_unit.prepare_diagnosis(self.state, 'astra_resolve', self.run / 'state.json', runner.SCHEMA_DIR)

    def test_validate_diagnosis_rejects_a_missing_diagnosis(self):
        record = {'source_revision': self.state['diagnosis_request']['source_revision'], 'changed_files': []}
        with self.assertRaises(ValueError):
            diagnosis_unit.validate_diagnosis(self.state, {'diagnosis': '  ', 'recommendation': {
                'action': 'retry', 'rationale': 'x'}}, record, self.root)

    def test_validate_diagnosis_rejects_an_invalid_action(self):
        record = {'source_revision': self.state['diagnosis_request']['source_revision'], 'changed_files': []}
        with self.assertRaises(ValueError):
            diagnosis_unit.validate_diagnosis(self.state, {'diagnosis': 'Looked at the logs',
                'recommendation': {'action': 'implement', 'rationale': 'x'}}, record, self.root)

    def test_validate_diagnosis_rejects_source_drift(self):
        record = {'source_revision': 'a-different-revision', 'changed_files': []}
        with self.assertRaises(support.Paused):
            diagnosis_unit.validate_diagnosis(self.state, {'diagnosis': 'Looked at the logs',
                'recommendation': {'action': 'escalate', 'rationale': 'x'}}, record, self.root)

    def test_validate_diagnosis_accepts_a_well_formed_recommendation(self):
        record = {'source_revision': self.state['diagnosis_request']['source_revision'], 'changed_files': []}
        diagnosis_unit.validate_diagnosis(self.state, {'diagnosis': 'Looked at the logs',
            'recommendation': {'action': 'retry', 'rationale': 'Add the missing summary field'}}, record, self.root)
