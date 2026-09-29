"""Runner-owned retry decisions. Models diagnose; configuration selects routes."""
import copy
try:
    from . import autocode_support as s
except ImportError:
    import autocode_support as s

# The stronger attempt is GPT-6 Sol at xhigh: Astra is too expensive and only for the
# Resolver (user 2026-09-28). The default checkers also run GPT-6 Sol, so for the rest of
# that milestone a checker on the strong model moves to checker_model and a different model
# checks the escalated work (user 2026-09-29).
DEFAULTS = {'enabled': True, 'ordinary_retries': 1, 'strong_model': 'openai/gpt-6-sol',
            'strong_reasoning_effort': 'xhigh', 'checker_model': 'zai-coding-plan/glm-5.3'}
# The roles that check the Builder's work (autocode_dispatch._VERIFIER_PAIRS).
CHECKERS = ('sol', 'completion')
# Efforts a replacement checker keeps; a climbed xhigh/max rung belongs to the GPT ladder.
CHECKER_EFFORTS = ('low', 'medium', 'high')


def _bare(model):
    return model.split('/', 1)[1] if isinstance(model, str) and model.startswith('openai/') else model


def enabled(state):
    return (state.get('settings', {}).get('builder_retry', {}).get('enabled') is True
            and not state.get('settings', {}).get('workflow'))


def key(state):
    task = state.get('current_task') or {}
    members = task.get('milestone_ids') or [task.get('milestone_id') or task.get('id')]
    return s.digest([state.get('goal_contract', {}).get('hash'), sorted(members)])


def lane(state):
    ident = key(state)
    lanes = state.setdefault('builder_retries', {})
    previous = lanes.get(state.get('builder_retry_key'))
    if previous and state.get('builder_retry_key') != ident:
        # An operator may pin a new route while a revised contract is reviewed.
        # A prior milestone's retry baseline must never undo that instruction.
        roles = state['settings']['roles']
        for role, route in {'terra': previous['initial_route'], **previous.get('checker_routes', {})}.items():
            if not roles.get(role, {}).get('model_pinned'):
                roles[role] = copy.deepcopy(route)
            state.setdefault('sessions', {}).pop(role, None)
    state['builder_retry_key'] = ident
    return lanes.setdefault(ident, {'initial_route': copy.deepcopy(state['settings']['roles']['terra']),
                                   'failures': [], 'action': None})


def guard(state):
    if not enabled(state):
        return
    current = lane(state)
    if current['action'] == 'pause':
        raise s.Paused('PAUSED_BUILDER_RETRY_LIMIT',
                       'Builder retry/escalation exhausted for this approved milestone; replan or change policy explicitly')


def colliding_checkers(state, model):
    """Checker roles whose route is the given Builder model."""
    roles = state['settings']['roles']
    return [role for role in CHECKERS
            if isinstance(roles.get(role), dict) and _bare(roles[role].get('model')) == _bare(model)]


def swap_checkers(state, current, config, model):
    """Move checkers off the escalated Builder's model so it never checks its own work.

    The replaced routes are kept on the lane and restored at the next milestone. Pinned,
    custom-provider and bare-name (Codex engine) checkers are left alone; the dispatch
    cross-model guard still pauses if one of them collides.
    """
    replacement = config.get('checker_model')
    swapped = {}
    for role in colliding_checkers(state, model) if replacement else ():
        route = state['settings']['roles'][role]
        if (route.get('model_pinned') or route.get('provider') not in (None, 'openai')
                or '/' not in str(route.get('model'))):
            continue
        current.setdefault('checker_routes', {}).setdefault(role, copy.deepcopy(route))
        effort = route.get('reasoning_effort')
        route.update(model=replacement, reasoning_effort=effort if effort in CHECKER_EFFORTS else 'high')
        state.setdefault('sessions', {}).pop(role, None)
        swapped[role] = replacement
    return swapped


def failure(state, evidence, reason):
    """One persisted resolver decision per failure; restart cannot add authority."""
    if not enabled(state):
        state.update(status='PAUSED_NO_PROGRESS', phase='PAUSED_OR_BLOCKED', stop_reason=reason)
        return 'pause'
    config = state['settings']['builder_retry']
    count = config['ordinary_retries']
    if type(count) is not int or not 0 <= count <= 3:
        raise ValueError('builder_retry.ordinary_retries must be between 0 and 3')
    current = lane(state)
    if evidence in current['failures']:
        return current['action']
    current['failures'].append(evidence)
    route = state['settings']['roles']['terra']
    n = len(current['failures'])
    action = 'retry' if n <= count else 'escalate' if n == count + 1 else 'pause'
    checkers = {}
    stop_reason = 'Implementation remains blocked after configured retry/escalation; human decision or replanning required'
    if action == 'escalate':
        model = config['strong_model']
        if route.get('engine', state['settings'].get('engine')) == 'opencode' and '/' not in model:
            model = 'openai/' + model
        # Never undo explicit pins or silently change provider/transport.
        if route.get('model_pinned') or route.get('provider') not in (None, 'openai'):
            action = 'pause'
        elif state.get('parent_run') and colliding_checkers(state, model):
            # A parallel Builder's work is checked by the parent run with its own checkers,
            # which this worker cannot move; the strong model would check its own work.
            action = 'pause'
            stop_reason = (f'Parallel Builder needs the stronger model {model}, which also checks this batch; '
                           'replan the milestone or change the checker route')
        else:
            route.update(model=model, reasoning_effort=config['strong_reasoning_effort'])
            checkers = swap_checkers(state, current, config, model)
    current['action'] = action
    state.setdefault('sessions', {}).pop('terra', None)
    state.setdefault('builder_retry_decisions', []).append({
        'at': s.now(), 'owner': 'autoresolver', 'action': action, 'failure': evidence,
        'reason': reason, 'milestone_key': key(state), 'attempt': n,
        'selected_model': route['model'], 'selected_effort': route.get('reasoning_effort'),
        **({'checker_models': checkers} if checkers else {})})
    if action == 'pause':
        state.update(status='PAUSED_BUILDER_RETRY_LIMIT', phase='PAUSED_OR_BLOCKED', stop_reason=stop_reason)
    return action
