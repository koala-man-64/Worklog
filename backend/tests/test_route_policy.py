"""The route policy (consolidation step 3.1): the middleware lets an agent's call through to a route's
handler exactly when policy.ROUTES gives the call an agent scope, and the agent CLI sends nothing the
table refuses.

Each call here goes through the real middleware with an agent's bearer token, the router replaced by a
stub, so a call the middleware lets through reaches no handler and changes nothing; policy.decide
answers the same call from the table. Both decide from a call's method, path and query alone, so these
calls stand for every call an agent can make. Before the table decided, the same test held today's
checks to it, which found the four calls pinned below; test_agent_route_matrix.py keeps pinning what
agents get from each route.
"""

import asyncio
import json
import uuid

import pytest
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
# Calls the checks before the table refused an agent and the table does not, with what an agent gets
# now. The checks matched words in the path, so a chat or roadmap whose ID were one of these words read
# as the person's pages of that name. No record has such an ID, since Worklog makes each one, so the
# handler finds nothing; a revision numbered with a word fails validation before it looks.
WORDS_TAKEN_FOR_PAGES = {
    ("GET", "/api/v1/chats/messages"): (404, "not_found"), ("GET", "/api/v1/chats/transcript"): (404, "not_found"),
    ("GET", "/api/v1/roadmaps/agents"): (404, "not_found"),
    ("GET", f"/api/v1/roadmaps/{PLACEHOLDER}/revisions/agents"): (422, "validation"),
}
# Calls no route serves, which reach no handler: the router answers them with 404, 405 or a redirect.
# The middleware lets an agent's read go on to the router, unless the router would redirect it to the
# person's page or to a path that serves no read, and refuses anything else (policy.passes_unserved).
# Before the table decided, a roadmap ID named agents with a slash was refused too, and writes under
# /proposals/ went on to a 404, 405 or redirect.
UNSERVED = {
    ("GET", "/api/v1/nonexistent"): True, ("POST", "/api/v1/nonexistent"): False,
    ("DELETE", "/api/v1/context"): False, ("HEAD", "/api/v1/context"): False, ("GET", "/api/v1/context/"): True,
    ("GET", "/api/v1/tickets/"): True, ("GET", "/api/v1/roadmaps/agents/"): True,
    ("GET", "/api/v1/settings/"): False, ("GET", "/api/v1/agents/"): False,
    ("GET", "/api/v1/backups/"): False, ("GET", "/api/v1/agent-registrations/"): False,
    ("POST", "/api/v1/proposals/submit"): False, ("POST", f"/api/v1/proposals/{PLACEHOLDER}/x/submit"): False,
    ("PATCH", "/api/v1/proposals/"): False,
}
# Paths a router can treat in its own way: a newline the route pattern's $ lets through, doubled or
# trailing slashes, a dot segment. served() must find whatever the router dispatches them to.
ODD = [("POST", f"/api/v1/proposals/{PLACEHOLDER}/submit\n", ""), ("GET", "/api/v1//context", ""),
       ("GET", "/api/v1/context//", ""), ("GET", "/api/v1/context/.", ""), ("GET", "/api/v1/settings/", ""),
       ("PATCH", "/api/v1/proposals//approve", ""), ("GET", "/api/v1/backups/", "")]
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


async def passes(app, token, method, path, query=""):
    """through(), as one direct ASGI call: thousands of these run in one event loop without a client."""
    body = b"" if method in {"GET", "HEAD"} else b"{}"
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "root_path": "", "query_string": query.removeprefix("?").encode(),
             "headers": [(b"host", b"127.0.0.1:8877"), (b"authorization", b"Bearer " + token.encode()),
                         (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
             "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8877)}
    pending = [{"type": "http.request", "body": body, "more_body": False}]
    answer: dict = {"body": b""}

    async def receive():
        if pending:
            return pending.pop()
        await asyncio.Event().wait()  # the caller stays connected until the call ends

    async def send(message):
        if message["type"] == "http.response.start":
            answer["status"] = message["status"]
        else:
            answer["body"] += message.get("body", b"")

    await app(scope, receive, send)
    if answer["status"] == REACHED:
        return True
    assert answer["status"] == 403 and json.loads(answer["body"])["error"]["code"] == "agent_scope", (method, path + query, answer)
    return False


def test_every_route_has_an_entry_for_each_method_it_serves_and_no_entry_names_another(tmp_path):
    app = create_app(tmp_path, testing=True)
    served = {(method, route.path) for route in api_routes(app) for method in route.methods - {"HEAD"}}
    declared = {(method, policy.PREFIX + template) for template, entry in policy.ROUTES.items() for method in entry}
    assert sorted(served - declared) == [], "Give each method of each new route a scope in policy.ROUTES"
    assert sorted(declared - served) == [], "These policy.ROUTES entries name no route"


def test_the_middleware_gives_the_tables_decision_for_every_call_a_route_serves(tmp_path):
    app, agent = agent_through_stub(tmp_path)
    token = agent.headers["Authorization"].removeprefix("Bearer ")
    # A call no route serves under its method reaches no handler; the router answers it.
    served = [(call, policy.decide(app.router.routes, request_scope(*call))) for call in calls(app)]
    served = [(call, decided) for call, decided in served if decided is not None]

    async def run():
        return [await passes(app, token, *call) for call, _ in served]

    differ = {(method, path + query) for ((method, path, query), decided), through_ in zip(served, asyncio.run(run()), strict=True)
              if through_ != policy.agent_may(decided)}
    compared = set()
    for call, _ in served:
        found = policy.served(app.router.routes, request_scope(*call))
        assert found is not None
        compared.add((call[0], found[0]))
    assert differ == set()
    assert {(method, path) for method, path, _ in calls(app)} >= set(WORDS_TAKEN_FOR_PAGES)
    # Every method each route serves was compared: HEAD only where a route serves it, since FastAPI's
    # routes answer HEAD with 405, while the API document's plain Starlette route serves it.
    assert compared == {(method, route.path) for route in api_routes(app) for method in route.methods}


def test_served_finds_the_route_the_router_dispatches_each_call_to(tmp_path):
    # The table decides by the route served() finds, so served() must find the very route whose handler
    # the router runs: here each route's handle is replaced by a recorder, and the router itself matches.
    app = create_app(tmp_path, testing=True)
    dispatched = []

    def recorder(route):
        async def handle(scope, receive, send):
            # A route that matches the path but not the method answers 405 and runs no handler.
            dispatched.append(route if scope["method"] in getattr(route, "methods", None) or () else None)
            await send({"type": "http.response.start", "status": REACHED, "headers": []})
            await send({"type": "http.response.body", "body": b""})
        return handle

    for route in app.router.routes:
        route.handle = recorder(route)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    async def compare():
        for method, path, query in calls(app) + ODD:
            scope = {**request_scope(method, path, query), "scheme": "http", "server": ("127.0.0.1", 8877),
                     "headers": [(b"host", b"127.0.0.1:8877")]}
            dispatched.clear()
            await app.router.app(dict(scope), receive, send)
            route = dispatched[0] if dispatched else None
            found = policy.served(app.router.routes, request_scope(method, path, query))
            assert (found[0] if found else None) == getattr(route, "path", None), (method, path, query)

    asyncio.run(compare())


def test_an_agents_read_no_route_serves_goes_on_to_the_router_unless_it_leads_to_the_persons_page(tmp_path):
    app, agent = agent_through_stub(tmp_path)
    for (method, path), passes in UNSERVED.items():
        assert policy.decide(app.router.routes, request_scope(method, path)) is None, (method, path)
        assert through(agent, method, path) is passes, (method, path)


def test_an_id_named_like_the_persons_pages_reaches_its_handler_and_a_redirect_is_decided_again(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as person:
        person.headers["Origin"] = ORIGIN
        boot = person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        person.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        token = person.post("/api/v1/credentials", json={"label": "Route policy"}, headers={"Idempotency-Key": str(uuid.uuid4())}).json()["token"]
    agent = TestClient(app, base_url=ORIGIN)
    agent.headers["Authorization"] = "Bearer " + token
    for (method, path), (status, code) in WORDS_TAKEN_FOR_PAGES.items():
        found = agent.request(method, path)
        assert found.status_code == status and found.json()["error"]["code"] == code, (path, found.text)
    # A read with a trailing slash is decided as the read the router would redirect it to: the person's
    # page refuses it on the spot, and an agent's own read follows the redirect.
    refused = agent.get("/api/v1/settings/", follow_redirects=False)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "agent_scope"
    followed = agent.get("/api/v1/context/")
    assert followed.status_code == 200 and followed.url.path == "/api/v1/context"


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


@pytest.mark.parametrize("path", ["/agent-runs", "/inbox/receive"])
def test_a_retry_in_coordination_or_the_inbox_refuses_a_credential_revoked_after_the_middleware_let_it_in(tmp_path, monkeypatch, path):
    # The middleware authenticates before the write lock, so the person's revoke can land in between.
    # A write in these scopes checks the credential again before it replays a receipt, which would
    # otherwise hand a revoked credential the response it was given before, messages included.
    import worklog.app as app_module
    from test_chat_messages import agent, confirm, ok, request, send, tracked_chat

    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        c.headers["X-CSRF-Token"] = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={}).json()["csrfToken"]
        chat = tracked_chat(c)
        credential = agent(c)
        payloads = {"/agent-runs": {"clientRunKey": "raced", "displayName": "Raced run", "externalChatId": "session-1"}, "/inbox/receive": {}}
        ok(request(c, "POST", "/agent-runs", payloads["/agent-runs"], credential, "run-key-1"))
        confirm(c, chat, credential)
        send(c, chat)
        first = ok(request(c, "POST", "/inbox/receive", {}, credential, "receive-key-1"))
        assert first["total"] == 1  # so the receive kept a receipt
        keys = {"/agent-runs": "run-key-1", "/inbox/receive": "receive-key-1"}
        read_body = app_module.bounded_body

        async def revoke_after_authentication(incoming, maximum):
            with app.state.db.transaction() as conn:
                conn.execute("UPDATE credentials SET revoked=1 WHERE id=?", (credential["id"],))
            return await read_body(incoming, maximum)

        monkeypatch.setattr(app_module, "bounded_body", revoke_after_authentication)
        replay = request(c, "POST", path, payloads[path], credential, keys[path])
        assert replay.status_code == 401 and replay.json()["error"]["code"] == "authentication", replay.text


def test_a_scope_can_hang_on_a_verb_or_a_query_value_and_an_unlisted_method_is_the_persons():
    proposal = "/api/v1/proposals/{identity}/{verb}"
    assert policy.scope_for("POST", proposal, {"verb": "submit"}) == policy.PROPOSE
    assert policy.scope_for("POST", proposal, {"verb": "approve"}) == policy.HUMAN
    # The route pattern's $ lets a trailing newline into the verb, which then names no agent verb.
    assert policy.scope_for("POST", proposal, {"verb": "submit\n"}) == policy.HUMAN
    upload = "/api/v1/attachments"
    assert policy.scope_for("POST", upload, {}, b"ownerType=proposal") == policy.PROPOSE
    assert policy.scope_for("POST", upload, {}, b"ownerType=proposal&ownerType=item") == policy.HUMAN
    assert policy.scope_for("POST", upload, {}, b"ownerType=item&ownerType=proposal") == policy.PROPOSE
    assert policy.scope_for("HEAD", "/api/v1/context", {}) == policy.HUMAN
    assert policy.scope_for("GET", "/api/v1/unknown", {}) == policy.HUMAN
