"""Runner boundary integration, with no live models or autonomous code writes."""
import copy
import unittest
from unittest.mock import patch

from . import test_autocode as base
from . import test_report_repair as repairs
from .goal_fixtures import approve_fixture

runner, support = base.runner, base.s


class ResolverRuntimeTests(unittest.TestCase):
    queue = repairs.RepairTests.queue

    def setUp(self):
        base.RetrofitTest.setUp(self)
        approve_fixture(self.state, runner.goals)

    def boundary(self):
        return runner.resolver_runtime.boundary(runner, self.state, self.run, self.root)

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
