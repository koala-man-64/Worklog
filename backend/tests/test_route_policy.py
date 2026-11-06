"""The route policy (consolidation step 3.1) in observe mode: for every call that reaches a route's
handler, policy.ROUTES gives the decision today's middleware makes, apart from four pinned below, and
the agent CLI allows nothing the table refuses.

The middleware still decides. Each call here goes through the real middleware with an agent's bearer
token, the router replaced by a stub, so a call the middleware lets through reaches no handler and
changes nothing; policy.decide answers the same call from the table. Both decide from a call's method,
path and query alone, so these calls stand for every call an agent can make. The next PR of step 3.1
makes the table decide; test_agent_route_matrix.py keeps pinning what agents get from each route.
"""

import json
import uuid

from fastapi.testclient import TestClient
from starlette.datastructures import QueryParams
from starlette.routing import Route
from test_agent_route_matrix import SNAPSHOT
from test_agent_route_matrix import concrete as matrix_path
from worklog import cli, policy
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
PLACEHOLDER = "00000000-0000-4000-8000-000000000000"
METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
# Each value a {verb} segment takes here: the agents', the person's, and one no route knows.
VERBS = ("submit", "withdraw", "reopen", "approve", "reject", "archive", "recover", "confirm", "merge", "comments", "unknown")
# The query strings a decision reads. A repeated parameter counts by its last value, as the handler reads it.
QUERIES = {
    "/api/v1/attachments": ("", "?ownerType=proposal", f"?ownerType=proposal&ownerId={PLACEHOLDER}", "?ownerType=item",
                            "?ownerType=item&ownerType=proposal", "?ownerType=proposal&ownerType=item",
                            "?ownerType=Proposal", "?ownertype=proposal"),
}
# Calls a route serves where the middleware refuses an agent and the table does not. Its checks match
# words in the path: a chat or roadmap whose ID were one of these words would read as the person's
# pages of that name. No record has such an ID, since Worklog makes each one, so the handler answers
# 404; once the table decides, an agent gets that 404 rather than 403.
WORDS_TAKEN_FOR_PAGES = {
    ("GET", "/api/v1/chats/messages"), ("GET", "/api/v1/chats/transcript"), ("GET", "/api/v1/roadmaps/agents"),
    ("GET", f"/api/v1/roadmaps/{PLACEHOLDER}/revisions/agents"),
}
# Today's middleware on calls no route serves, which reach no handler: the router answers them with
# 404, 405 or a redirect. True lets the call through to the router; False refuses it with 403.
UNSERVED = {
    ("GET", "/api/v1/nonexistent"): True, ("POST", "/api/v1/nonexistent"): False,
    ("DELETE", "/api/v1/context"): False, ("HEAD", "/api/v1/context"): False, ("GET", "/api/v1/context/"): True,
    ("GET", "/api/v1/settings/"): False, ("GET", "/api/v1/agents/"): False, ("GET", "/api/v1/roadmaps/agents/"): False,
    ("POST", "/api/v1/proposals/submit"): True, ("POST", f"/api/v1/proposals/{PLACEHOLDER}/x/submit"): True,
    ("GET", "/api/v1/session/bootstrap"): True,
}
# Calls the route matrix records as refused that the middleware lets through: their handlers refuse an
# agent with the same code. Retiring is for a credential an agent registered, not one issued in Settings.
REFUSED_BY_HANDLER = {"POST /api/v1/agent-registrations/retire"}
# What the stub router answers a call the middleware let through.
REACHED = 299


def api_routes(app):
    return [route for route in app.router.routes if isinstance(route, Route) and route.path.startswith(policy.PREFIX + "/")]


def concrete(template, verb):
    return (template.replace("{identity}", PLACEHOLDER).replace("{item_id}", PLACEHOLDER).replace("{ref}", "T-1")
            .replace("{verb}", verb).replace("{date}", "2026-01-05").replace("{number}", "1"))


def calls(app):
    """Every route's path under every method: with placeholder values, with each {verb} and query string
    a decision reads, and, where a path ends in another parameter, with each word a route's path ends in
    as its value. Today's checks read a path's first and last segments by name, and no route's path
    starts with a parameter."""
    routes = api_routes(app)
    words = {route.path.rsplit("/", 1)[-1] for route in routes if not route.path.endswith("}")} | set(VERBS)
    found = set()
    for route in routes:
        last = route.path.rsplit("/", 1)[-1]
        endings = [""] + (sorted(words) if last.startswith("{") and last != "{verb}" else [])
        for verb in VERBS if "{verb}" in route.path else ("",):
            for ending in endings:
                path = concrete(route.path, verb)
                path = path.rsplit("/", 1)[0] + "/" + ending if ending else path
                for query in QUERIES.get(route.path, ("",)):
                    found.update((method, path, query) for method in METHODS)
    return sorted(found)


def request_scope(method, path, query=""):
    return {"type": "http", "method": method, "path": path, "root_path": "", "query_string": query.removeprefix("?").encode()}


def agent_through_stub(tmp_path):
    """An agent's client of an app whose router answers every call the middleware lets through."""
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as person:
        person.headers["Origin"] = ORIGIN
        boot = person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        person.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        issued = person.post("/api/v1/credentials", json={"label": "Route policy"}, headers={"Idempotency-Key": str(uuid.uuid4())})
        assert issued.status_code == 200, issued.text

    async def reached(scope, receive, send):
        await send({"type": "http.response.start", "status": REACHED, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b"{}"})

    app.router.middleware_stack = reached
    agent = TestClient(app, base_url=ORIGIN)
    # Like the agent CLI: a bearer token and no browser Origin.
    agent.headers["Authorization"] = "Bearer " + issued.json()["token"]
    return app, agent


def through(agent, method, path, query=""):
    """Whether today's middleware lets an agent's call through to the router."""
    response = agent.request(method, path + query, json=None if method in {"GET", "HEAD"} else {})
    if response.status_code == REACHED:
        return True
    # The middleware's refusal; a response to HEAD has no body to read its code from.
    assert response.status_code == 403 and (method == "HEAD" or response.json()["error"]["code"] == "agent_scope"), \
        (method, path + query, response.status_code, response.text[:200])
    return False


def test_every_route_has_an_entry_for_each_method_it_serves_and_no_entry_names_another(tmp_path):
    app = create_app(tmp_path, testing=True)
    served = {(method, route.path) for route in api_routes(app) for method in route.methods - {"HEAD"}}
    declared = {(method, policy.PREFIX + template) for template, entry in policy.ROUTES.items() for method in entry}
    assert sorted(served - declared) == [], "Give each method of each new route a scope in policy.ROUTES"
    assert sorted(declared - served) == [], "These policy.ROUTES entries name no route"


def test_the_table_gives_the_middlewares_decision_for_every_call_a_route_serves(tmp_path):
    app, agent = agent_through_stub(tmp_path)
    compared, differ = set(), set()
    for method, path, query in calls(app):
        decided = policy.decide(app.router.routes, request_scope(method, path, query))
        if decided is None:
            continue  # no route serves this path and method: the router answers, and no handler runs
        if through(agent, method, path, query) != policy.agent_may(decided):
            differ.add((method, path + query))
        found = policy.served(app.router.routes, request_scope(method, path, query))
        assert found is not None
        compared.add((method, found[0]))
    assert differ == WORDS_TAKEN_FOR_PAGES
    for method, path in WORDS_TAKEN_FOR_PAGES:
        assert policy.decide(app.router.routes, request_scope(method, path)) == policy.READ
    # Every method each route serves was compared: HEAD only where a route serves it, since FastAPI's
    # routes answer HEAD with 405, while the API document's plain Starlette route serves it.
    assert compared == {(method, route.path) for route in api_routes(app) for method in route.methods}


def test_todays_middleware_on_calls_no_route_serves_is_pinned(tmp_path):
    app, agent = agent_through_stub(tmp_path)
    for (method, path), passes in UNSERVED.items():
        assert policy.decide(app.router.routes, request_scope(method, path)) is None, (method, path)
        assert through(agent, method, path) is passes, (method, path)


def test_the_table_agrees_with_the_route_matrix(tmp_path):
    app = create_app(tmp_path, testing=True)
    for key, answer in json.loads(SNAPSHOT.read_text(encoding="utf-8")).items():
        method, target = key.split(" ", 1)
        template, _, query = target.partition("?")
        decided = policy.decide(app.router.routes, request_scope(method, matrix_path(template), query))
        assert decided is not None and policy.agent_may(decided) == (answer != "refused" or key in REFUSED_BY_HANDLER), (key, answer, decided)


def test_the_cli_sends_no_call_the_table_refuses_and_can_make_every_agent_write(tmp_path, monkeypatch):
    app = create_app(tmp_path, testing=True)

    class Sent(Exception):
        pass

    def send(*args, **kwargs):
        raise Sent

    monkeypatch.setattr(cli, "_open", send)
    client = cli.AgentClient(ORIGIN, "t" * 43)
    writes = set()
    for method, path, query in calls(app):
        try:
            client.request(method, path.removeprefix(policy.PREFIX) + query, None if method in {"GET", "HEAD"} else {})
        except Sent:
            pass
        except ValueError as exc:
            assert "outside proposal/read scope" in str(exc), exc
            continue
        decided = policy.decide(app.router.routes, request_scope(method, path, query))
        assert decided is not None and policy.agent_may(decided), f"The CLI sends {method} {path}{query}, which the table gives {decided}"
        if method not in {"GET", "HEAD"}:
            found = policy.served(app.router.routes, request_scope(method, path, query))
            assert found is not None
            template, parameters = found
            rule = policy.ROUTES[template.removeprefix(policy.PREFIX)][method]
            # A write whose scope hangs on a value counts once for each value: each verb, each owner type.
            value = None if not isinstance(rule, policy.Only) else (
                QueryParams(query.removeprefix("?")).get(rule.parameter) if rule.query else parameters.get(rule.parameter))
            writes.add((method, template, value))
    listed = set()
    for template, entry in policy.ROUTES.items():
        for method, rule in entry.items():
            scope = rule.scope if isinstance(rule, policy.Only) else rule
            if method != "GET" and scope not in {policy.HUMAN, policy.PUBLIC}:
                listed.update((method, policy.PREFIX + template, value)
                              for value in (sorted(rule.values) if isinstance(rule, policy.Only) else [None]))
    assert writes == listed


def test_a_scope_can_hang_on_a_verb_or_a_query_value_and_an_unlisted_method_is_the_persons():
    proposal = "/api/v1/proposals/{identity}/{verb}"
    assert policy.scope_for("POST", proposal, {"verb": "submit"}) == policy.PROPOSE
    assert policy.scope_for("POST", proposal, {"verb": "approve"}) == policy.HUMAN
    upload = "/api/v1/attachments"
    assert policy.scope_for("POST", upload, {}, b"ownerType=proposal") == policy.PROPOSE
    assert policy.scope_for("POST", upload, {}, b"ownerType=proposal&ownerType=item") == policy.HUMAN
    assert policy.scope_for("POST", upload, {}, b"ownerType=item&ownerType=proposal") == policy.PROPOSE
    assert policy.scope_for("HEAD", "/api/v1/context", {}) == policy.HUMAN
    assert policy.scope_for("GET", "/api/v1/unknown", {}) == policy.HUMAN
