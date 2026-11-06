"""The route policy (consolidation step 3.1) in observe mode: for every call that reaches a route's
handler, policy.ROUTES gives the decision today's middleware makes, and the agent CLI allows nothing
the table refuses.

The middleware still decides. Each call here goes through the real middleware with an agent's bearer
token, the router replaced by a stub, so a call the middleware lets through reaches no handler and
changes nothing; policy.decide answers the same call from the table. The next PR of step 3.1 makes the
table decide. test_agent_route_matrix.py keeps pinning what agents get from each route.
"""

import uuid

from fastapi.testclient import TestClient
from starlette.routing import Route
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
# What the stub router answers a call the middleware let through.
REACHED = 299


def api_routes(app):
    return [route for route in app.router.routes if isinstance(route, Route) and route.path.startswith(policy.PREFIX + "/")]


def concrete(template, verb):
    return (template.replace("{identity}", PLACEHOLDER).replace("{item_id}", PLACEHOLDER).replace("{ref}", "T-1")
            .replace("{verb}", verb).replace("{date}", "2026-01-05").replace("{number}", "1"))


def calls(app):
    """Every route's path, with each verb and query string its decision can read, under every method."""
    found = set()
    for route in api_routes(app):
        for verb in VERBS if "{verb}" in route.path else ("",):
            for query in QUERIES.get(route.path, ("",)):
                found.update((method, concrete(route.path, verb), query) for method in METHODS)
    return sorted(found)


def request_scope(method, path, query):
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


def test_every_route_has_an_entry_for_each_method_it_serves_and_no_entry_names_another(tmp_path):
    app = create_app(tmp_path, testing=True)
    served = {(method, route.path) for route in api_routes(app) for method in route.methods - {"HEAD"}}
    declared = {(method, policy.PREFIX + template) for template, entry in policy.ROUTES.items() for method in entry}
    assert sorted(served - declared) == [], "Give each method of each new route a scope in policy.ROUTES"
    assert sorted(declared - served) == [], "These policy.ROUTES entries name no route"


def test_the_table_gives_the_middlewares_decision_for_every_call_a_route_serves(tmp_path):
    app, agent = agent_through_stub(tmp_path)
    compared, disagreements = set(), []
    for method, path, query in calls(app):
        decided = policy.decide(app.router.routes, request_scope(method, path, query))
        if decided is None:
            continue  # no route serves this path and method: the router answers, and no handler runs
        response = agent.request(method, path + query, json=None if method in {"GET", "HEAD"} else {})
        if response.status_code == REACHED:
            through = True
        else:
            # The middleware's refusal; a response to HEAD has no body to read its code from.
            assert response.status_code == 403 and (method == "HEAD" or response.json()["error"]["code"] == "agent_scope"), \
                (method, path + query, response.status_code, response.text[:200])
            through = False
        if through != policy.agent_may(decided):
            disagreements.append(f"{method} {path}{query}: the middleware {'lets it through' if through else 'refuses it'}, "
                                 f"the table says {decided}")
        found = policy.served(app.router.routes, request_scope(method, path, query))
        assert found is not None
        compared.add((method, found[0]))
    assert disagreements == []
    # Every method each route serves was compared: HEAD only where a route serves it, since FastAPI's
    # routes answer HEAD with 405, while the API document's plain Starlette route serves it.
    assert compared =={(method, route.path) for route in api_routes(app) for method in route.methods}


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
            writes.add((method, found[0]))
    listed = {(method, policy.PREFIX + template) for template, entry in policy.ROUTES.items() for method, rule in entry.items()
              if method != "GET" and (rule.scope if isinstance(rule, policy.Only) else rule) not in {policy.HUMAN, policy.PUBLIC}}
    assert writes == listed


def test_a_scope_can_hang_on_a_verb_or_a_query_value_and_an_unlisted_method_is_the_persons(tmp_path):
    proposal = "/api/v1/proposals/{identity}/{verb}"
    assert policy.scope_for("POST", proposal, {"verb": "submit"}) == policy.PROPOSE
    assert policy.scope_for("POST", proposal, {"verb": "approve"}) == policy.HUMAN
    upload = "/api/v1/attachments"
    assert policy.scope_for("POST", upload, {}, b"ownerType=proposal") == policy.PROPOSE
    assert policy.scope_for("POST", upload, {}, b"ownerType=proposal&ownerType=item") == policy.HUMAN
    assert policy.scope_for("POST", upload, {}, b"ownerType=item&ownerType=proposal") == policy.PROPOSE
    assert policy.scope_for("HEAD", "/api/v1/context", {}) == policy.HUMAN
    assert policy.scope_for("GET", "/api/v1/unknown", {}) == policy.HUMAN
    # A path no route serves under its method reaches no handler: the router answers 404, 405 or a redirect.
    app = create_app(tmp_path, testing=True)
    for method, path in (("GET", "/api/v1/context/"), ("GET", "/api/v1/settings/x"), ("DELETE", "/api/v1/context"),
                         ("GET", "/api/v1/session/bootstrap")):
        assert policy.decide(app.router.routes, request_scope(method, path, "")) is None, (method, path)
