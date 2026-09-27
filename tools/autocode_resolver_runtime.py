"""Stage-boundary adapter for the existing bounded resolver policy.

Only runner-observed conditions get automatic proposals. Agent prose cannot
authorize a retry, mutate the contract, grant permission, or declare success.
"""
from dataclasses import fields, is_dataclass
from collections.abc import Mapping
from pathlib import Path
import time

try:
    from . import autocode_resolver as policy, autocode_support as support
    from . import autocode_goals as goals, autocode_failures as failures
except ImportError:
    import autocode_resolver as policy
    import autocode_support as support
    import autocode_goals as goals
    import autocode_failures as failures


def plain(value):
    if is_dataclass(value):
        return {field.name: plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    return value


_DECISION_FIELDS = frozenset(f.name for f in fields(policy.Decision))
_RECEIPT_FIELDS = frozenset(f.name for f in fields(policy.Receipt)) - {'version'}
_OPTIONAL_STR = lambda value: value is None or isinstance(value, str)
_OPTIONAL_MAPPING = lambda value: value is None or isinstance(value, Mapping)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _validate_decision_dict(raw):
    """Reject a malformed or reinterpreted Decision before construction.

    A dataclass constructor does not check runtime types: ``Decision(**raw)``
    would happily accept a non-string action or a list-valued payload. Every
    field here is authority-bearing (it drives dispatch), so each one is
    checked explicitly rather than trusted to the constructor.
    """
    _require(isinstance(raw, Mapping) and set(raw) == _DECISION_FIELDS, 'malformed decision fields')
    _require(isinstance(raw['action'], str) and raw['action'] in policy.ACTIONS, 'invalid decision action')
    _require(isinstance(raw['payload'], Mapping), 'invalid decision payload')
    _require(isinstance(raw['rationale'], str) and isinstance(raw['in_scope_reason'], str), 'invalid decision rationale')
    return policy.Decision(**raw)


def _validate_receipt_dict(raw):
    """Reject a malformed, retyped or unsupported-version Receipt before construction."""
    _require(isinstance(raw, Mapping), 'malformed receipt')
    version = raw.get('version', 1)
    _require(type(version) is int and version in policy.SUPPORTED_RECEIPT_VERSIONS, 'unsupported receipt version')
    present = set(raw) - {'version'}
    _require(present == _RECEIPT_FIELDS, 'malformed receipt fields')
    _require(isinstance(raw['blocker_id'], str) and isinstance(raw['blocker_digest'], str), 'invalid receipt identity')
    _require(isinstance(raw['classifier_outcome'], str) and isinstance(raw['rationale'], str)
              and isinstance(raw['in_scope_reason'], str), 'invalid receipt narrative fields')
    _require(_OPTIONAL_STR(raw['budget_key']) and _OPTIONAL_STR(raw['idempotency_key']), 'invalid receipt budget/idempotency key')
    _require(raw['attempt'] is None or (type(raw['attempt']) is int and raw['attempt'] >= 0), 'invalid receipt attempt')
    _require(isinstance(raw['action'], str) and raw['action'] in policy.ACTIONS, 'invalid receipt action')
    _require(_OPTIONAL_STR(raw['proposal_rationale']) and _OPTIONAL_STR(raw['reviewer_rationale']), 'invalid receipt review narrative')
    _require(raw['review_verdict'] in (None, 'approved', 'vetoed'), 'invalid receipt review verdict')
    # The type must be exactly bool: a list-valued callbacks_used would pass a
    # bare isinstance/truthiness check but is not the boolean the policy emits.
    _require(type(raw['callbacks_used']) is bool, 'invalid receipt callbacks_used type')
    _require(_OPTIONAL_MAPPING(raw['prior_lineage']) and _OPTIONAL_MAPPING(raw['new_lineage']), 'invalid receipt lineage')
    return policy.Receipt(**{**raw, 'version': version})


def load_ledger(saved):
    return policy.Ledger(
        attempts=dict(saved.get('attempts', {})), outcomes=dict(saved.get('outcomes', {})),
        cache={key: (_validate_decision_dict(pair[0]), _validate_receipt_dict(pair[1]))
               for key, pair in saved.get('cache', {}).items()})


def boundary(runner, state, run_dir, workspace):
    """Record one deterministic decision at a stopped, approved stage boundary."""
    if (state.get('active_stage') or state.get('version', 2) < 3
            or state.get('status') not in ('RUNNING', 'WAITING_FOR_USER')
            or not goals.approved(state)):
        return False
    pending = state.get('pending_report_repair')
    request = state.get('user_request') or (state.get('agent_request') or {}).get('request')
    validation = state.get('validation') or {}
    failed = next((row for row in reversed(state.get('stages', [])) if not row.get('runner_owned')), None)
    if request and request.get('kind') != 'none':
        # The legacy 'blocker' category has no typed risk/permission boundary.
        # Never treat an agent's "no scope change" prose as authorization.
        kind = request.get('kind', 'unknown')
        description = request.get('decision_needed') or 'Unclassified user decision'
        evidence = []
        selected = {'stage': state.get('next_stage'), 'request': request,
                    'contract_hash': state['goal_contract']['hash']}
        proposal = policy.Proposal('escalate', {'reason': description}, 'User decision boundary')
    elif pending:
        failed = pending['original']
        if (support.snapshot(workspace)['revision'] != failed.get('source_revision')
                or pending.get('contract_hash') != state['goal_contract']['hash']
                or any(not Path(path).is_file() or support.file_hash(path) != digest
                       for path, digest in pending.get('pins', {}).items())):
            raise support.Paused('PAUSED_STALE_VALIDATION', 'Saved report-repair inputs changed; do not resolve or retry')
        kind, description = 'model_output', pending.get('error') or failed.get('rejection_reason', 'Invalid stage report')
        evidence = [failed[key] for key in ('output', 'events') if failed.get(key)]
        selected = {'stage': failed['stage'], 'artifact_hash': failed.get('source_revision'),
                    'failure_key': failed.get('failure_key')}
        proposal = policy.Proposal('retry', {'guidance': 'Use only the existing bounded report-repair path; preserve original execution evidence.'},
                                   'Terminal report failure eligible for report-only repair')
    elif (validation.get('verdict') == 'BLOCKED' and failed
          and validation.get('output') == failed.get('output')):
        if (not Path(validation['output']).is_file()
                or support.snapshot(workspace)['revision'] != failed.get('source_revision')):
            raise support.Paused('PAUSED_STALE_VALIDATION', 'Failed validation artifact changed before resolution')
        kind, description = 'validation', 'Independent validation could not complete'
        evidence = [validation['output']]
        failures.record(state, failed, support.Paused('VALIDATION_BLOCKED', description), support.now())
        selected = {'stage': failed['stage'], 'artifact_hash': failed.get('source_revision'),
                    'failure_key': failed.get('failure_key')}
        proposal = policy.Proposal('continue', {'guidance': 'Preserve failed evidence and continue the existing approved repair or review route.'},
                                   'Existing workflow routing handles failed validation')
    else:
        return False

    repeated = failures.repeated(state, failed) if failed and not request else None
    exhausted = bool(pending and pending['attempts'] >= runner.repair_limit(state))
    if repeated or exhausted:
        proposal = policy.Proposal('escalate', {'reason': 'Persisted failure identity or report-repair budget exhausted'},
                                   'Existing failure and repair limits take precedence')
    contract = state['goal_contract']
    snapshot = policy.ContractSnapshot(**{key: contract[key] for key in
        ('task_id', 'revision', 'hash', 'body', 'approval_status', 'approval_event')})
    saved = state.setdefault('resolver', {})
    try:
        ledger = load_ledger(saved)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
        raise support.Paused('PAUSED_RESOLVER_STATE', 'Saved resolver ledger is malformed; reconcile before retry') from error
    # Stable identity controls the policy's budget; individual attempts control
    # idempotency. Restarting alone changes neither.
    blocker = policy.Blocker(support.digest(selected), kind, description, tuple(evidence))
    context = {'next_stage': state.get('next_stage'),
               'report_attempts': pending['attempts'] if pending else None,
               'evidence_attempt': failed.get('output') if failed else None,
               'failure_count': repeated['count'] if repeated else None}
    started = time.monotonic()
    decision, receipt = policy.resolve(policy.ResolverRequest(
        blocker, snapshot, context, evidence,
        policy.Boundaries(frozenset({'continue', 'retry', 'escalate'}), frozenset()),
        ledger, proposed_resolution=proposal, clock=support.now))
    saved.update(attempts=ledger.attempts, outcomes=ledger.outcomes,
                 cache={key: plain(pair) for key, pair in ledger.cache.items()})
    # Fallbacks have no policy cache key; make their runner records idempotent too.
    outcome_key = receipt.idempotency_key or support.digest({'blocker': plain(blocker), 'context': context,
                                                           'contract_hash': contract['hash']})
    recorded = saved.setdefault('recorded', [])
    if outcome_key not in recorded:
        path = Path(run_dir) / 'resolver' / (outcome_key + '.json')
        record = {'stage': 'resolver', 'role': 'resolver', 'engine': 'runner', 'runner_owned': True,
                  'iteration': state['iteration'], 'finished_at': support.now(), 'exit_code': 0,
                  'duration_seconds': time.monotonic() - started, 'runner_calls': 0,
                  'metrics': {'provider_tokens': {'input_tokens': 0, 'output_tokens': 0}},
                  'output': str(path), 'summary': decision.rationale,
                  'decision': plain(decision), 'receipt': plain(receipt)}
        support.atomic_json(path, record)
        state.setdefault('stages', []).append(record)
        state.setdefault('history', []).append(record)
        recorded.append(outcome_key)
    # A malformed proposal can yield the policy's diagnostic "retry" outcome;
    # only a validated, runner-proposed repair may authorize dispatch.
    accepted = (receipt.in_scope_reason == 'proposal within boundaries'
                and decision.action == proposal.action)
    if decision.action == 'escalate' or not accepted:
        if request:
            # Preserve the actual pending question and exact approval boundary.
            pass
        else:
            status = 'PAUSED_REPEATED_FAILURE' if repeated else 'PAUSED_RESOLVER'
            state.update(status=status, phase='PAUSED_OR_BLOCKED', stop_reason=decision.rationale)
            runner.write_json(Path(run_dir) / 'state.json', state)
            raise support.Paused(status, decision.rationale)
    runner.write_json(Path(run_dir) / 'state.json', state)
    return True


def reset_for_resume(state):
    """Clear the resolver's per-incident attempt budget for an explicit resume.

    The per-blocker attempt count is a current-cycle allowance, not a lifetime
    cap: --resume-paused clears it so an operator can retry after fixing the
    underlying cause. That clearing must not be silent. Each reset records how
    many attempts it erased and folds them into a lifetime total that this
    function itself never resets, so a future run-level diagnostic cap has a
    real number to check instead of restarting at zero on every resume.
    """
    saved = state.get('resolver')
    if not isinstance(saved, dict):
        return
    prior = {key: value for key, value in saved.get('attempts', {}).items() if isinstance(value, int)}
    saved['attempts'] = {}
    if not prior:
        return
    total = sum(prior.values())
    saved['lifetime_attempts'] = saved.get('lifetime_attempts', 0) + total
    state.setdefault('user_events', []).append({
        'kind': 'resolver_resume_epoch', 'actor': 'user_cli', 'at': support.now(),
        'cleared_attempts': prior, 'cleared_total': total, 'lifetime_attempts': saved['lifetime_attempts']})
