import copy
import json
from pathlib import Path
import unittest
import autocode_builder_policy as policy
import autocode_dispatch as dispatch
from . import test_build_blackbox as bb


class PolicyTests(unittest.TestCase):
    def state(self):
        return {'settings': {'engine': 'codex', 'builder_retry': dict(policy.DEFAULTS),
                'roles': {'terra': {'model': 'gpt-6-luna', 'reasoning_effort': 'medium'}}},
                'goal_contract': {'hash': 'approved'},
                'current_task': {'id': 'task1', 'milestone_id': 'M1'}, 'status': 'RUNNING'}

    def test_retry_escalate_pause_is_durable_idempotent_and_bounded(self):
        state = self.state()
        self.assertEqual('retry', policy.failure(state, 'e1', 'failure'))
        state = json.loads(json.dumps(state))
        self.assertEqual('retry', policy.failure(state, 'e1', 'failure'))
        self.assertEqual('escalate', policy.failure(state, 'e2', 'failure'))
        self.assertEqual(policy.DEFAULTS['strong_model'], state['settings']['roles']['terra']['model'])
        self.assertEqual('pause', policy.failure(state, 'e3', 'failure'))
        with self.assertRaises(policy.s.Paused): policy.guard(state)
        self.assertEqual(3, len(state['builder_retry_decisions']))

    def test_pins_and_custom_providers_are_not_overridden(self):
        for override in ({'model_pinned': True}, {'provider': 'custom'}):
            state = self.state(); state['settings']['roles']['terra'].update(override)
            policy.failure(state,'e1','failure')
            self.assertEqual('pause',policy.failure(state,'e2','failure'))
            self.assertEqual('gpt-6-luna',state['settings']['roles']['terra']['model'])

    def test_new_milestone_restores_normal_route_and_new_budget(self):
        state = self.state(); policy.failure(state,'e1','f'); policy.failure(state,'e2','f')
        state['current_task'] = {'id':'task2','milestone_id':'M2'}
        policy.guard(state)
        self.assertEqual('gpt-6-luna',state['settings']['roles']['terra']['model'])
        self.assertEqual('retry',policy.failure(state,'e3','f'))

    def test_new_contract_preserves_operator_pinned_route(self):
        state = self.state()
        policy.guard(state)
        selected = {'model': 'openai/gpt-6-sol', 'reasoning_effort': 'max',
                    'engine': 'opencode', 'provider': None, 'model_pinned': True}
        state['settings']['roles']['terra'] = copy.deepcopy(selected)
        state['goal_contract']['hash'] = 'reapproved'
        state = json.loads(json.dumps(state))
        policy.guard(state)
        self.assertEqual(selected, state['settings']['roles']['terra'])
        self.assertEqual(selected, policy.lane(state)['initial_route'])

    def default_routes(self):
        state = self.state()
        state['settings']['engine'] = 'opencode'
        state['settings']['roles'] = {
            'terra': {'model': 'zai-coding-plan/glm-5.3', 'reasoning_effort': 'medium'},
            'sol': {'model': 'openai/gpt-6-sol', 'reasoning_effort': 'high'},
            'completion': {'model': 'openai/gpt-6-sol', 'reasoning_effort': 'medium'}}
        return state

    def test_escalated_builder_is_checked_by_glm_until_the_next_milestone(self):
        state = self.default_routes()
        dispatch.enforce_cross_model_verification(state)
        policy.failure(state, 'e1', 'f')
        self.assertEqual('escalate', policy.failure(state, 'e2', 'f'))
        roles = state['settings']['roles']
        self.assertEqual(('openai/gpt-6-sol', 'xhigh'), (roles['terra']['model'], roles['terra']['reasoning_effort']))
        self.assertEqual(('zai-coding-plan/glm-5.3', 'high'), (roles['sol']['model'], roles['sol']['reasoning_effort']))
        self.assertEqual(('zai-coding-plan/glm-5.3', 'medium'),
                         (roles['completion']['model'], roles['completion']['reasoning_effort']))
        self.assertEqual({'sol': 'zai-coding-plan/glm-5.3', 'completion': 'zai-coding-plan/glm-5.3'},
                         state['builder_retry_decisions'][-1]['checker_models'])
        dispatch.enforce_cross_model_verification(state)
        state = json.loads(json.dumps(state))
        state['current_task'] = {'id': 'task2', 'milestone_id': 'M2'}
        policy.guard(state)
        self.assertEqual(self.default_routes()['settings']['roles'], state['settings']['roles'])
        dispatch.enforce_cross_model_verification(state)

    def test_a_climbed_checker_moves_to_a_glm_effort(self):
        state = self.default_routes()
        state['settings']['roles']['sol']['reasoning_effort'] = 'max'
        policy.failure(state, 'e1', 'f'); policy.failure(state, 'e2', 'f')
        self.assertEqual('high', state['settings']['roles']['sol']['reasoning_effort'])
        state['current_task'] = {'id': 'task2', 'milestone_id': 'M2'}
        policy.guard(state)
        self.assertEqual('max', state['settings']['roles']['sol']['reasoning_effort'])

    def test_a_pinned_checker_is_not_moved_and_the_cross_model_guard_still_pauses(self):
        state = self.default_routes()
        state['settings']['roles']['sol']['model_pinned'] = True
        policy.failure(state, 'e1', 'f')
        self.assertEqual('escalate', policy.failure(state, 'e2', 'f'))
        self.assertEqual('openai/gpt-6-sol', state['settings']['roles']['sol']['model'])
        self.assertEqual('zai-coding-plan/glm-5.3', state['settings']['roles']['completion']['model'])
        with self.assertRaises(policy.s.Paused) as raised:
            dispatch.enforce_cross_model_verification(state)
        self.assertEqual('PAUSED_CROSS_MODEL', raised.exception.status)

    def test_a_parallel_builder_pauses_rather_than_be_checked_by_its_strong_model(self):
        state = self.default_routes()
        state['parent_run'] = '/runs/parent'
        policy.failure(state, 'e1', 'f')
        self.assertEqual('pause', policy.failure(state, 'e2', 'f'))
        self.assertEqual('zai-coding-plan/glm-5.3', state['settings']['roles']['terra']['model'])
        self.assertEqual('openai/gpt-6-sol', state['settings']['roles']['sol']['model'])
        self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT', state['status'])
        self.assertIn('also checks this batch', state['stop_reason'])

    def test_configured_strong_model_retains_opencode_transport(self):
        state=self.state(); state['settings']['engine']='opencode'
        state['settings']['builder_retry']['strong_model']='gpt-6-sol'
        policy.failure(state,'e1','f'); policy.failure(state,'e2','f')
        self.assertEqual('openai/gpt-6-sol',state['settings']['roles']['terra']['model'])
        self.assertEqual('xhigh',state['settings']['roles']['terra']['reasoning_effort'])


class PolicyBlackbox(unittest.TestCase):
    setUp = bb.BuildBlackbox.setUp
    command = bb.BuildBlackbox.command
    invoke = bb.BuildBlackbox.invoke
    seed = bb.BuildBlackbox.seed
    state = bb.BuildBlackbox.state
    events = bb.BuildBlackbox.events
    build = bb.BuildBlackbox.build
    candidate = bb.BuildBlackbox.candidate

    def test_ordinary_retry_keeps_luna_and_successful_siblings(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT']='retry_success'; self.build(); self.candidate()
        self.assertEqual(['gpt-6-luna']*2,[r['model'] for r in self.events() if r['milestone']=='M1'])
        self.assertEqual(1,len([r for r in self.events() if r['milestone']=='M2']))

    def test_strong_retry_uses_sol_high_and_integration_still_requires_review(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT']='escalate_success'; self.build(); self.candidate()
        strong = policy.DEFAULTS['strong_model']
        self.assertEqual(['gpt-6-luna','gpt-6-luna',strong],[r['model'] for r in self.events() if r['milestone']=='M1'])
        self.assertNotEqual('TASK_COMPLETE',self.state()['status'])

    def test_exhaustion_resume_cannot_reset_budget_or_claim_built(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT']='retry_exhausted'; self.build(2)
        before = self.events()
        worker=next(w for w in self.state()['orchestration_batch']['workers'] if w['milestone_id']=='M1')
        child=json.loads((Path(worker['run_dir'])/'state.json').read_text())
        self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT',child['status'])
        self.assertEqual(['retry','escalate','pause'],[r['action'] for r in child['builder_retry_decisions']])
        self.assertNotIn('implementation',child)
        self.build(2,extra=['--resume-paused','--retry-builder','M1'])
        self.assertEqual(before,self.events())
        self.assertNotIn('autocode',self.state().get('unit_handoffs',{}))

    def test_independent_failure_resolver_retries_escalates_then_pauses(self):
        spec=bb.plan([([], 'MAX_RETRIES must be 3', ['retry.py'])],
            {'M1': {'retry.py':'MAX_RETRIES = 5\n'}},
            {'M1':'from retry import MAX_RETRIES; assert MAX_RETRIES == 3'},
            'Preserve exactly three retries')
        self.seed(spec); self.env['BUILD_AUDIT_FAULT']='validation_fails'
        for attempt in range(3):
            self.build(); self.candidate()
            self.invoke('autoreview',['--run-dir',str(self.run),'--no-chat'])
            self.assertEqual('FAIL',self.state()['validation']['verdict'])
            self.invoke('autoresolver',['--run-dir',str(self.run),'--no-chat'],2 if attempt==2 else 0)
        self.assertEqual(['gpt-6-luna','gpt-6-luna',policy.DEFAULTS['strong_model']],[r['model'] for r in self.events()])
        self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT',self.state()['status'])
        self.assertEqual(['retry','escalate','pause'],[r['action'] for r in self.state()['builder_retry_decisions']])

    def test_serial_noop_uses_same_bounded_policy(self):
        self.seed(bb.plan([([], 'Version prints 1', ['version.py'])],
            {'M1':{'version.py':'print(1)\n'}}, {'M1':'import version'}, 'Print version'))
        self.env['BUILD_AUDIT_FAULT']='retry_exhausted'
        self.build(2)
        self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT',self.state()['status'])
        self.assertEqual(['gpt-6-luna','gpt-6-luna',policy.DEFAULTS['strong_model']],[r['model'] for r in self.events()])
        self.assertNotIn('implementation',self.state())
        self.assertNotIn('autocode',self.state().get('unit_handoffs',{}))
