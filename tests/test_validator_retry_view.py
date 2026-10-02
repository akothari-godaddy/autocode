"""Offer an exact inspected report retry at a stopped Validator repair boundary."""
import copy
import unittest

import autocode_run_view as run_view
import autocode_validator_retry as retry_policy
import autocode_util as util


class ValidatorRetryViewTests(unittest.TestCase):
    def state(self):
        return {'status': 'PAUSED_RESOLVER', 'next_stage': 'sol',
                'settings': {'report_repair': {'max_attempts': 2}},
                'pending_report_repair': {
                    'attempts': 2, 'error': 'Check is not supported by an exact executed Validator event',
                    'original': {'stage': 'sol'},
                    'latest_rejected': {'iteration': 1, 'output': '/run/validator-report-repair-02.json',
                                        'original_stage': 'sol', 'report_only': True, 'rejected': True}}}

    def test_resolver_and_repeated_failure_offer_exact_attempt_without_mutation(self):
        for status in ('PAUSED_RESOLVER', 'PAUSED_REPEATED_FAILURE'):
            state = {**self.state(), 'status': status}
            before = copy.deepcopy(state)
            self.assertEqual('001/validator-report-repair-02', run_view.needs(state)['retry_report_attempt'])
            self.assertEqual(before, state)

    def test_resolver_offers_original_pending_report_while_repair_allowance_remains(self):
        state = self.state()
        pending = state['pending_report_repair']
        pending.update(attempts=0, original={'stage': 'sol', 'iteration': 1,
            'output': '/run/validator-03.json', 'rejected': True, 'exit_code': 0})
        pending.pop('latest_rejected')
        self.assertEqual('001/validator-03', run_view.needs(state).get('retry_report_attempt'))
        for field, value in (('timed_out', True), ('interrupted', True), ('exit_code', 2)):
            invalid = copy.deepcopy(state)
            invalid['pending_report_repair']['original'][field] = value
            self.assertNotIn('retry_report_attempt', run_view.needs(invalid))

    def test_other_pauses_or_unbound_reports_do_not_offer_retry(self):
        pristine = self.state()
        for change in ({'status': 'PAUSED_PROVIDER_UNCERTAIN'}, {'status': 'PAUSED_TIME_LIMIT'},
                       {'next_stage': 'terra'}, {'active_stage': {'stage': 'sol'}}):
            with self.subTest(change=change):
                self.assertNotIn('retry_report_attempt', run_view.needs({**pristine, **change}))
        for key, value in (('attempts', 1), ('error', 'Different error'), ('original', {'stage': 'terra'}),
                           ('latest_rejected', {'iteration': 1, 'output': '/run/other.json'})):
            state = copy.deepcopy(pristine)
            state['pending_report_repair'][key] = value
            with self.subTest(key=key):
                self.assertNotIn('retry_report_attempt', run_view.needs(state))

    def test_only_the_exact_stalled_validator_evidence_failure_is_retryable(self):
        identity = {'stage': 'sol', 'artifact_hash': 'revision', 'error_class': 'ValueError'}
        key = util.digest(identity)
        record = {'stage': 'sol', 'failure_key': key, 'source_revision': 'revision',
                  'rejected': True, 'failure_attempt': 'attempt-3'}
        state = {'status': 'PAUSED_RESOLVER', 'next_stage': 'sol', 'stages': [record],
                 'failure_history': {key: {'identity': identity, 'count': 3, 'streak': 3,
                   'attempts': ['attempt-3'], 'last_error': 'Check is not supported by an exact executed Validator event'}}}
        before = copy.deepcopy(state)
        self.assertEqual(record, retry_policy.inspected_failure(state))
        self.assertEqual(before, state)
        for override in ({'status': 'PAUSED_TIME_LIMIT'}, {'next_stage': 'terra'},
                         {'active_stage': {'stage': 'sol'}}, {'uncertain_artifacts': ['partial']},
                         {'pending_report_repair': {'attempts': 2}}, {'failure_history': {}}):
            with self.subTest(override=override):
                self.assertIsNone(retry_policy.inspected_failure({**state, **override}))
        for field, value in (('last_error', 'Different error'), ('streak', 2),
                             ('identity', {**identity, 'stage': 'terra'})):
            changed = copy.deepcopy(state)
            changed['failure_history'][key][field] = value
            with self.subTest(field=field):
                self.assertIsNone(retry_policy.inspected_failure(changed))
