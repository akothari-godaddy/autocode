"""Offer an exact inspected report retry at a stopped Validator repair boundary."""
import copy
import unittest

import autocode_run_view as run_view


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
