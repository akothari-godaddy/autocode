"""Approved continuous-conversation model profile.

This policy applies to conversations using this profile. It does not replace
AutoCode's global defaults or modify routes saved by unrelated task runs.
The module is pure domain logic and never imports the dashboard or controller.
"""
from __future__ import annotations
from copy import deepcopy

SOL_PLANNER_MODEL = 'openai/gpt-6-sol'
GLM_REVIEW_MODEL = 'zai-coding-plan/glm-5.3'
ASTRA_VISUAL_MODEL = 'openai/gpt-6-astra'

class PlannerDispatchError(ValueError):
    """A Planner dispatch or structured result failed; safe to display, evidence retained."""

    def __init__(self, message, *, stage='planner', evidence=None):
        super().__init__(message)
        self.stage = stage
        self.evidence = evidence


class PlannerRouteError(PlannerDispatchError):
    """The configured routes violate the mandated model policy."""

    def __init__(self, message):
        super().__init__(message, stage='route_policy')


def _route(model, effort):
    return {'engine': 'opencode', 'provider': 'opencode', 'model': model,
            'reasoning_effort': effort}


# Mandated per-role model policy (saved user corrections, 2026-09-27/29):
# openai/gpt-6-sol Low Gatherer, High Planner/Builder, Max Resolver;
# zai-coding-plan/glm-5.3 High for independent architecture, validation,
# review and completion. Saved Q4-VISUAL-ROUTE-SCOPE: future visual review uses
# openai/gpt-6-astra at high reasoning, visual review only.  No other model
# and no silent fallback.
MANDATED_ROUTES = {
    'requirements_gatherer': _route(SOL_PLANNER_MODEL, 'low'),
    'planner': _route(SOL_PLANNER_MODEL, 'high'),
    'builder': _route(SOL_PLANNER_MODEL, 'high'),
    'resolver': _route(SOL_PLANNER_MODEL, 'max'),
    'architect': _route(GLM_REVIEW_MODEL, 'high'),
    'plan_reviewer': _route(GLM_REVIEW_MODEL, 'high'),
    'validation': _route(GLM_REVIEW_MODEL, 'high'),
    'completion': _route(GLM_REVIEW_MODEL, 'high'),
    'visual_review': _route(ASTRA_VISUAL_MODEL, 'high'),
}
POLICY_ROLES = ('planner', 'builder', 'resolver', 'architect', 'plan_reviewer',
                'validation', 'completion', 'visual_review')
VERIFIER_ROLES = ('architect', 'plan_reviewer', 'validation', 'completion')
NONVISUAL_ROLES = ('requirements_gatherer', 'planner', 'builder', 'resolver',
                   'architect', 'plan_reviewer', 'validation', 'completion')

# Usage/time/idle/tool/iteration caps are disabled (None/0 = unlimited, matching
# the runner's execution-limits convention) while semantic safety checks are
# retained: the deny-all tool-free Planner session, the mandated route policy
# and cross-model verifier separation below.
DISPATCH_LIMITS = {
    'usage_cap': None,
    'time_cap_seconds': 0,
    'idle_timeout_seconds': 0,
    'iteration_ceiling': None,
    'tool_policy': 'deny-all',
    'semantic_safety': ('deny_all_tools', 'route_policy', 'cross_model_verification'),
}


def caps_disabled():
    """The Planner dispatch cap configuration: everything disabled, safety kept."""
    return dict(DISPATCH_LIMITS)


def conversation_planner_routes():
    """The routes this pipeline adds to a conversation's configured_routes."""
    return {'planner': deepcopy(MANDATED_ROUTES['planner'])}


def visual_review_route():
    """The mandated future visual-review route (saved Q4 answer): Astra High.

    Shared backend policy only: this selection is valid for visual review
    exclusively, every unsupported or misspelled visual selection fails
    loudly with no fallback, and the visual-only model is rejected for all
    nonvisual roles by :func:`enforce_route_policy`.  Selecting the route is
    not visual evidence: source-matched visual PASS stays with the tasks
    that capture real images, and historical Flash receipts stay historical.
    """
    return deepcopy(MANDATED_ROUTES['visual_review'])


def select_visual_review_route(route=None):
    """Select and validate the standalone future visual-review route (Q4).

    Independent of any conversation Planner route: a standalone visual-only
    selection is valid on its own — enforcing it must not demand an unrelated
    Planner route — and a correct selection is exactly openai/gpt-6-astra at
    high reasoning, visual review only.  Unsupported models, efforts and
    misspelled roles fail loudly with no fallback.  This is shared backend
    policy for the separately owned image-review consumers (Q5); it is not
    visual evidence and makes no visual PASS claim.
    """
    candidate = visual_review_route() if route is None else route
    return enforce_route_policy({'visual_review': candidate})['visual_review']


# Fresh runner-configuration boundary: runner role names mapped to their
# shared-policy roles.  Saved runs keep their persisted pins as immutable
# history; only newly constructed role settings (a new run or the fresh half
# of a joint configuration) pass through the fresh-configuration check, and
# custom providers keep their own catalogues.
RUNNER_NONVISUAL_ROLES = ('requirements', 'glm', 'plan_reviewer', 'astra',
                          'terra', 'sol', 'completion', 'resolver')
RUNNER_POLICY_ROLES = {
    'requirements': 'requirements_gatherer',
    'glm': 'planner',
    'plan_reviewer': 'plan_reviewer',
    'astra': 'architect',
    'terra': 'builder',
    'sol': 'validation',
    'completion': 'completion',
    'resolver': 'resolver',
}


def enforce_fresh_runner_role_models(models, efforts=None, *, complete=False):
    """Validate exact fresh built-in OpenCode role routes, never saved pins.

    ``models`` may contain explicit model strings or constructed role settings;
    ``efforts`` supplies explicit efforts before construction. Bare GPT aliases
    are compared by their OpenCode identifiers. The final constructed settings
    must contain every runner role with its mandated model AND effort. No
    configured value is replaced with a default to make a route pass.
    """
    models = models or {}
    efforts = efforts or {}
    unknown = set(models) | set(efforts)
    unknown -= set(RUNNER_POLICY_ROLES) | {'visual_review'}
    if unknown:
        raise PlannerRouteError('Unknown fresh runner route(s): ' + ', '.join(sorted(unknown)))
    if complete and set(RUNNER_POLICY_ROLES) - set(models):
        raise PlannerRouteError('Missing fresh runner route(s): '
                                + ', '.join(sorted(set(RUNNER_POLICY_ROLES) - set(models))))
    for role in set(models) | set(efforts):
        route = models.get(role)
        model = route.get('model') if isinstance(route, dict) else route
        effort = route.get('reasoning_effort') if isinstance(route, dict) else efforts.get(role)
        if isinstance(model, str) and '/' not in model and model.startswith('gpt-'):
            model = 'openai/' + model
        policy_role = RUNNER_POLICY_ROLES.get(role, role)
        mandated = MANDATED_ROUTES[policy_role]
        if model is not None and model != mandated['model']:
            if model == ASTRA_VISUAL_MODEL and role != 'visual_review':
                raise PlannerRouteError(
                    f'The {role} ({policy_role}) route is nonvisual; {ASTRA_VISUAL_MODEL} '
                    'is the visual-review-only model. No fallback is permitted.')
            raise PlannerRouteError(
                f'The {role} ({policy_role}) route must use {mandated["model"]} '
                f'at {mandated["reasoning_effort"]} effort; got {model!r}. No fallback is permitted.')
        if (effort is not None and effort != mandated['reasoning_effort']) or (
                complete and effort is None):
            raise PlannerRouteError(
                f'The {role} ({policy_role}) route must use {mandated["model"]} '
                f'at {mandated["reasoning_effort"]} effort; got {effort!r}. No fallback is permitted.')
        if complete and model is None:
            raise PlannerRouteError(f'The {role} ({policy_role}) route needs its mandated model.')
    return True


def _model_family(model):
    """Producer family for cross-verification, mirroring autocode_dispatch.

    Kept local so this backend module stays import-light and offline-testable
    (autocode_dispatch pulls runner process dependencies).
    """
    if not isinstance(model, str) or not model:
        return ''
    if model.startswith('openai/'):
        return 'openai'
    if model.startswith('zai-coding-plan/'):
        return 'glm'
    if model.startswith('xiaomi-token-plan-sgp/') or model.startswith('mimo-'):
        return 'mimo'
    return model


def enforce_route_policy(routes):
    """Validate persisted/represented routes; fail loudly, never fall back.

    Policy roles must match the mandated model/effort exactly.  The independent
    Planner route is required. Unknown/misspelled route names are rejected (a
    silent fallback to a default route is never attempted). Verifier roles
    must never share the Planner's model family so no model grades its own work.
    The saved Q4 visual route is visual-review-only: ``visual_review`` must
    match openai/gpt-6-astra at high reasoning exactly, and the visual-only
    model is rejected for every nonvisual role (including persisted Gatherer
    representations) so it can never substitute for a nonvisual route.  A
    standalone visual-only selection is valid without an unrelated Planner
    route (Q5: separately owned image-review consumers select it through
    :func:`select_visual_review_route`); the independent Planner route stays
    required for every route set that carries a nonvisual role.
    This validator keeps historical Gatherer selections representable so
    already-persisted documents and dispatch records stay readable; it does not
    authorize any route for newly configured dispatch — see
    :func:`enforce_conversation_routes`.
    """
    if not isinstance(routes, dict) or not routes:
        raise PlannerRouteError('Planner pipeline routes must be a non-empty object.')
    unknown = sorted(set(routes) - set(MANDATED_ROUTES))
    if unknown:
        raise PlannerRouteError(
            'Unknown Planner pipeline route(s): ' + ', '.join(unknown)
            + '. Allowed routes: ' + ', '.join(sorted(MANDATED_ROUTES)) + '.')
    for name in POLICY_ROLES:
        mandated = MANDATED_ROUTES[name]
        if name in routes and routes[name] != mandated:
            raise PlannerRouteError(
                f'The {name} route must match the mandated model policy exactly '
                f'({mandated["model"]} at {mandated["reasoning_effort"]} effort); '
                'silent fallback to another model is not allowed.')
    for name in NONVISUAL_ROLES:
        configured = routes.get(name)
        if isinstance(configured, dict) and configured.get('model') == ASTRA_VISUAL_MODEL:
            raise PlannerRouteError(
                f'The {name} route is nonvisual; {ASTRA_VISUAL_MODEL} is the '
                'visual-review-only model (saved Q4 route: high reasoning, visual '
                'review only). It cannot substitute for a nonvisual role and no '
                'fallback is permitted.')
    if any(role in routes for role in NONVISUAL_ROLES) and 'planner' not in routes:
        raise PlannerRouteError('The independent Planner route is required and must not be dropped.')
    gatherer = routes.get('requirements_gatherer')
    if gatherer is not None:
        if not isinstance(gatherer, dict) or not isinstance(gatherer.get('model'), str) or not gatherer['model']:
            raise PlannerRouteError('The Requirements Gatherer route needs a model.')
        effort = gatherer.get('reasoning_effort') or 'low'
        if effort != 'low':
            raise PlannerRouteError('The Requirements Gatherer must run at the mandated low reasoning effort.')
    if 'planner' in routes:
        planner_family = _model_family(routes['planner']['model'])
        for verifier in VERIFIER_ROLES:
            if verifier not in routes:
                continue
            verifier_family = _model_family(routes[verifier]['model'])
            if verifier_family == planner_family:
                raise PlannerRouteError(
                f'{verifier} must not grade Planner work: both use the {planner_family} family '
                f'({routes["planner"]["model"]} / {routes[verifier]["model"]}). Cross-model verifier separation is required.')
    return deepcopy(routes)


def enforce_conversation_routes(routes):
    """Validate the route set for NEW dispatch (create/model update/send/retry).

    Everything :func:`enforce_route_policy` checks, plus the mandated Gatherer
    route: an explicit override to any non-mandated model or effort — and a
    persisted historical route that would dispatch a continued turn — is
    rejected here, before any provider is invoked, with no fallback.  Reading
    historical documents (load/normalize/handoff) never routes through this
    check, so persisted legacy selections stay readable while every new
    dispatch follows the saved openai-instead-of-glm correction exactly.  The
    next turn is permitted again only after an explicit approved model update,
    which rebuilds the routes under this same policy.
    """
    enforced = enforce_route_policy(routes)
    gatherer = enforced.get('requirements_gatherer')
    mandated = MANDATED_ROUTES['requirements_gatherer']
    if gatherer is None:
        raise PlannerRouteError(
            'The Requirements Gatherer route is required for new dispatch; update the '
            'conversation models to rebuild it. No fallback to a saved model is permitted.')
    if gatherer != mandated:
        raise PlannerRouteError(
            f'The Requirements Gatherer route must match the mandated model policy exactly '
            f'({mandated["model"]} at {mandated["reasoning_effort"]} effort); '
            f'{gatherer.get("model")!r} is not allowed for new dispatch (new conversations, '
            'model updates or continued turns). Update the conversation models to the '
            'mandated route to continue; no fallback is permitted.')
    return enforced


def configure_runner_profile(settings, args):
    """Select the continuous profile for a new conversation handoff only.

    Explicit routes are checked before mutation. Existing runs never call this
    helper. Normal CLI tasks retain the provider defaults selected elsewhere.
    Explicit user budgets remain authoritative; this profile disables defaults.
    """
    if settings.get('engine') != 'opencode' or settings.get('provider') != 'opencode':
        raise PlannerRouteError('Continuous conversation handoff requires the built-in OpenCode provider.')
    models = {role: getattr(args, role + '_model', None) for role in RUNNER_POLICY_ROLES
              if getattr(args, role + '_model', None) is not None}
    efforts = {role: getattr(args, role + '_reasoning_effort', None)
               if getattr(args, role + '_reasoning_effort', None) is not None
               else getattr(args, 'reasoning_effort', None)
               for role in RUNNER_POLICY_ROLES
               if getattr(args, role + '_reasoning_effort', None) is not None
               or getattr(args, 'reasoning_effort', None) is not None}
    enforce_fresh_runner_role_models(models, efforts)
    strong = getattr(args, 'builder_strong_model', None)
    if strong is not None:
        enforce_fresh_runner_role_models({'terra': strong})
    configured = deepcopy(settings)
    for role, policy_role in RUNNER_POLICY_ROLES.items():
        prior = configured.setdefault('roles', {}).get(role, {})
        configured['roles'][role] = {**prior, **deepcopy(MANDATED_ROUTES[policy_role]), 'provider': None}
    configured['roles']['plan_reviewer']['model_pinned'] = True
    configured['conversation_profile'] = 'continuous-v1'
    configured.setdefault('builder_retry', {}).update(
        strong_model=SOL_PLANNER_MODEL, strong_reasoning_effort='high')
    limits = configured.setdefault('limits', {})
    for argument, field, value in (
            ('max_seconds', 'max_seconds', 0),
            ('max_stage_seconds', 'stage_timeout_seconds', 0),
            ('max_idle_seconds', 'idle_timeout_seconds', 0),
            ('max_tool_seconds', 'tool_timeout_seconds', 0),
            ('no_progress_limit', 'no_progress_batches', 0)):
        if getattr(args, argument, None) is None:
            limits[field] = value
    if (getattr(args, 'max_iterations', None) is None
            and getattr(args, 'legacy_iteration_ceiling', None) is None):
        limits['iteration_ceiling'] = None
    checkpoints = configured.setdefault('milestone_checkpoints', {})
    if getattr(args, 'max_milestone_seconds', None) is None:
        checkpoints['max_seconds'] = 0
    if getattr(args, 'max_milestone_replans', None) is None:
        checkpoints['max_replans'] = None
    enforce_fresh_runner_role_models(configured['roles'], complete=True)
    settings.clear()
    settings.update(configured)
    return settings
