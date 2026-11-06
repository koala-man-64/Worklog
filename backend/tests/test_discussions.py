"""Communication integration tests: real auth, SQLite transactions and restart state."""

import base64
import json
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app
from worklog.common import AppError
from worklog.discussions import (
    DiscussionService,
    bind_credential,
    communication_actor,
)
from worklog.migrations import add_discussions

ORIGIN = "http://127.0.0.1:8877"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        r = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrfToken"]
        yield c


def write(c, path, payload=None, agent=None, key=None):
    headers = {"Idempotency-Key": key or str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    return c.post("/api/v1" + path, headers=headers, json={} if payload is None else payload)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def enroll(c, label):
    return ok(write(c, "/credentials", {"label": label}))


def get(c, path, agent=None):
    return c.get("/api/v1" + path, headers={"Authorization": "Bearer " + agent["token"]} if agent else {})


def item(c):
    return ok(write(c, "/work-items", {"title": "Build discussions"}))


def role(c, work, label="Implementation", agent=None):
    return ok(write(c, "/discussions/roles", {"contextType": "item", "contextId": work["id"], "label": label}, agent))


def change(c, r, action, agent=None, **extra):
    return write(c, f"/discussions/roles/{r['id']}/{action}", {"revision": r["revision"], **extra}, agent)


def post(c, *, agent=None, **payload):
    return ok(write(c, "/discussions", {"text": "Please review this change", **payload}, agent))


def named(*targets, mode="all"):
    return {"mode": mode, "targets": [{"kind": kind, "id": identity} for kind, identity in targets]}


def ack(c, message, agent=None, targets=None):
    payload = {"targetIds": targets} if targets is not None else {}
    return write(c, f"/discussions/messages/{message['id']}/acknowledge", payload, agent)


def slots(message):
    return [target["id"] for target in message["request"]["targets"]]


def test_shared_forum_is_separate_from_provider_chat_and_domain_permissions(client):
    a, b = enroll(client, "Builder"), enroll(client, "Reviewer")
    p = post(client, agent=a, title="Shared question", chatId="missing-provider-session", repos=["worklog"], acknowledgement={"mode": "anyone"})
    message = p["message"]
    assert message["authorId"] == a["agentId"]
    assert message["chatId"] == "missing-provider-session"
    reply = ok(write(client, f"/discussions/{p['discussion']['id']}/messages", {"text": "I can reply"}, b))
    assert reply["authorId"] == b["agentId"] and reply["request"] is None
    assert get(client, "/discussions", b).json()["total"] == 1
    assert get(client, "/discussions?q=reply", a).json()["items"][0]["id"] == p["discussion"]["id"]
    assert get(client, "/chats", a).status_code == 403
    assert write(client, "/work-items", {"title": "Forbidden direct work write"}, a).status_code == 403
    assert ack(client, message, a).status_code == 403
    assert get(client, f"/discussions/messages/{message['id']}", b).json()["request"]["state"] == "pending"
    assert ok(ack(client, message, b))["request"]["state"] == "satisfied"
    assert client.patch(f"/api/v1/discussions/messages/{message['id']}", json={"text": "overwrite"}).status_code in {404, 405}


def test_explicit_named_slots_all_any_and_batch_validation_are_atomic(client):
    a, b, outsider = [enroll(client, label) for label in ["A", "B", "Other"]]
    m = post(client, acknowledgement=named(("agent", a["agentId"]), ("agent", b["agentId"])))["message"]
    target_a = next(t["id"] for t in m["request"]["targets"] if t["recipientId"] == a["agentId"])
    target_b = next(t["id"] for t in m["request"]["targets"] if t["recipientId"] == b["agentId"])
    assert ack(client, m, a).status_code == 422
    assert ack(client, m, outsider, [target_a]).status_code == 403
    assert ack(client, m, a, [target_a, target_b]).status_code == 403
    assert get(client, f"/discussions/messages/{m['id']}").json()["request"]["receipts"] == []
    first = ok(ack(client, m, a, [target_a]))
    assert first["request"]["state"] == "pending"
    assert ok(ack(client, m, b, [target_b]))["request"]["state"] == "satisfied"
    assert len(ok(ack(client, m, b, [target_b]))["request"]["receipts"]) == 2
    any_m = post(client, acknowledgement=named(("agent", a["agentId"]), ("agent", b["agentId"]), mode="any"))["message"]
    selected = next(t["id"] for t in any_m["request"]["targets"] if t["recipientId"] == a["agentId"])
    assert ok(ack(client, any_m, a, [selected]))["request"]["state"] == "satisfied"


def test_human_ack_release_and_archive_remain_distinct_from_replies(client):
    a = enroll(client, "Agent")
    p = post(client, agent=a, acknowledgement=named(("human", "local-user")))
    m, discussion = p["message"], p["discussion"]
    assert write(client, f"/discussions/messages/{m['id']}/release", {"reason": "wrong lane"}, a).status_code == 403
    archived = ok(write(client, f"/discussions/{discussion['id']}/archive", {"revision": discussion["revision"], "archived": True}))
    assert archived["archived"]
    assert get(client, "/discussions?view=awaiting", a).json()["items"][0]["id"] == discussion["id"]
    ok(write(client, f"/discussions/{discussion['id']}/messages", {"text": "A reply is not acknowledgement"}))
    assert get(client, f"/discussions/messages/{m['id']}").json()["request"]["state"] == "pending"
    released = ok(write(client, f"/discussions/messages/{m['id']}/release", {"reason": "Proceed without the acknowledgement"}))
    assert released["request"]["state"] == "released_by_human"
    assert released["request"]["receipts"] == []
    assert released["request"]["terminalActorId"] == "local-user"
    late = ok(ack(client, m, targets=slots(m)))
    assert late["request"]["state"] == "released_by_human"
    assert late["request"]["receipts"][0]["late"]
    assert write(client, f"/discussions/messages/{m['id']}/cancel", agent=a).status_code == 409
    assert write(client, f"/discussions/{discussion['id']}/archive", {"revision": archived["revision"], "archived": False}, a).status_code == 403
    open_m = post(client, agent=a, acknowledgement={"mode": "any_agent"})["message"]
    assert ack(client, open_m).status_code == 403
    human_m = post(client, agent=a, acknowledgement={"mode": "anyone"})["message"]
    assert ok(ack(client, human_m))["request"]["state"] == "satisfied"


def test_rotation_preserves_profile_targets_and_idempotency_not_proposal_namespace(client):
    a, b = enroll(client, "Original"), enroll(client, "Target")
    key = "stable-request-identity"
    payload = {"text": "The persisted original", "acknowledgement": named(("agent", b["agentId"]))}
    original = ok(write(client, "/discussions", payload, a, key))
    rotated = ok(write(client, f"/credentials/{a['id']}/rotate", {"label": "Replacement token"}))
    assert rotated["agentId"] == a["agentId"] and rotated["id"] != a["id"]
    assert get(client, "/discussions", a).status_code == 401
    assert ok(write(client, "/discussions", payload, rotated, key)) == original
    assert write(client, "/discussions", {"text": "Different payload"}, rotated, key).status_code == 409
    target_rotation = ok(write(client, f"/credentials/{b['id']}/rotate", {}))
    acknowledged = ok(ack(client, original["message"], target_rotation, slots(original["message"])))
    assert acknowledged["request"]["receipts"][0]["actorId"] == b["agentId"]
    assert acknowledged["authorLabel"] == "Original"
    assert client.post("/api/v1/discussions", json={"text": "No key"}, headers={"Authorization": "Bearer " + rotated["token"]}).status_code == 428
    with client.app.state.db.read() as c:
        assert c.execute("SELECT count(*) FROM receipts WHERE actor_id=? AND key=?", ("discussion:" + a["agentId"], key)).fetchone()[0] == 1


def test_concurrent_roles_and_accepted_handoff_transfer_incoming_and_outgoing(client):
    a, b, requester = [enroll(client, label) for label in ["Builder", "Successor", "Requester"]]
    work = item(client)
    implementation = ok(change(client, role(client, work), "claim", a))
    review = ok(change(client, role(client, work, "Review"), "claim", b))
    assert implementation["ownerId"] == a["agentId"] and review["ownerId"] == b["agentId"]
    outgoing = post(client, agent=a, onBehalfOfRoleId=implementation["id"], acknowledgement=named(("human", "local-user")))["message"]
    incoming = post(client, agent=requester, acknowledgement=named(("role", implementation["id"])))["message"]
    personal = post(client, agent=a, acknowledgement=named(("human", "local-user")))["message"]
    offered = ok(change(client, implementation, "offer", a, successorId=b["agentId"], note="Continue from the recorded evidence"))
    assert offered["ownerId"] == a["agentId"]
    assert change(client, offered, "accept", requester).status_code == 403
    accepted = ok(change(client, offered, "accept", b))
    assert accepted["ownerId"] == b["agentId"] and accepted["generation"] == 2 and accepted["offer"] is None
    assert change(client, offered, "accept", b).status_code == 409
    assert ack(client, incoming, a, slots(incoming)).status_code == 403
    receipt = ok(ack(client, incoming, b, slots(incoming)))["request"]["receipts"][0]
    assert receipt["actorId"] == b["agentId"] and receipt["roleId"] == implementation["id"] and receipt["ownershipGeneration"] == 2
    assert write(client, f"/discussions/messages/{outgoing['id']}/cancel", agent=a).status_code == 403
    cancelled = ok(write(client, f"/discussions/messages/{outgoing['id']}/cancel", agent=b))
    assert cancelled["request"]["state"] == "cancelled"
    assert cancelled["request"]["authorityRoleId"] == implementation["id"] and cancelled["request"]["authorityGeneration"] == 2
    assert cancelled["authorId"] == a["agentId"] and cancelled["ownershipGeneration"] == 1
    assert write(client, f"/discussions/messages/{personal['id']}/cancel", agent=b).status_code == 403
    successor_post = ok(write(client, f"/discussions/{outgoing['discussionId']}/messages", {"text": "Taking over", "onBehalfOfRoleId": implementation["id"]}, b))
    assert successor_post["authorId"] == b["agentId"] and successor_post["ownershipGeneration"] == 2
    assert write(client, f"/discussions/{outgoing['discussionId']}/messages", {"text": "Old owner", "onBehalfOfRoleId": implementation["id"]}, a).status_code == 403


def test_role_slots_are_explicit_and_completed_receipts_survive_transfer(client):
    a, b = enroll(client, "Two roles"), enroll(client, "Replacement")
    work = item(client)
    r1 = ok(change(client, role(client, work, "Implementation"), "claim", a))
    r2 = ok(change(client, role(client, work, "Review"), "claim", a))
    m = post(client, acknowledgement=named(("role", r1["id"]), ("role", r2["id"])))["message"]
    first_slot = next(t["id"] for t in m["request"]["targets"] if t["recipientId"] == r1["id"])
    one = ok(ack(client, m, a, [first_slot]))
    assert one["request"]["state"] == "pending" and len(one["request"]["receipts"]) == 1
    offered = ok(change(client, r1, "offer", a, successorId=b["agentId"]))
    ok(change(client, offered, "accept", b))
    after = get(client, f"/discussions/messages/{m['id']}", a).json()
    assert next(t for t in after["request"]["targets"] if t["id"] == first_slot)["acknowledged"]
    second_slot = next(t["id"] for t in m["request"]["targets"] if t["recipientId"] == r2["id"])
    assert ok(ack(client, m, a, [second_slot]))["request"]["state"] == "satisfied"
    receipts = ok(ack(client, m, b, [first_slot]))["request"]["receipts"]
    assert len(receipts) == 3 and receipts[-1]["late"]
    assert receipts[0]["ownershipGeneration"] == 1


def test_creator_becoming_owner_cannot_ack_self_and_unassigned_sender_wait_stays(client):
    a, b = enroll(client, "Author"), enroll(client, "Next")
    r = role(client, item(client))
    m = post(client, agent=a, acknowledgement=named(("role", r["id"])))["message"]
    r = ok(change(client, r, "claim", a))
    assert get(client, f"/discussions/messages/{m['id']}", a).json()["request"]["actionableTargetIds"] == []
    assert ack(client, m, a, slots(m)).status_code == 403
    outgoing = post(client, agent=a, onBehalfOfRoleId=r["id"], acknowledgement={"mode": "anyone"})["message"]
    r = ok(change(client, r, "release", a))
    current = get(client, f"/discussions/messages/{outgoing['id']}", a).json()
    assert current["request"]["state"] == "pending" and not current["request"]["canCancel"]
    assert "unassigned" in current["request"]["classifications"]
    r = ok(change(client, r, "claim", b))
    inherited = get(client, f"/discussions/messages/{outgoing['id']}", b).json()
    assert inherited["request"]["canCancel"] and "predecessor" in inherited["request"]["classifications"]
    assert ok(ack(client, outgoing, b))["request"]["state"] == "satisfied"


def test_human_reassignment_invalidates_offers_and_does_not_retarget_named_agents(client):
    a, b, other = [enroll(client, label) for label in ["Original", "Offered", "Recovery owner"]]
    r = ok(change(client, role(client, item(client)), "claim", a))
    named_message = post(client, acknowledgement=named(("agent", a["agentId"])))["message"]
    offered = ok(change(client, r, "offer", a, successorId=b["agentId"]))
    assert change(client, offered, "reassign", b, ownerId=b["agentId"], reason="Not permitted").status_code == 403
    reassigned = ok(change(client, offered, "reassign", ownerId=other["agentId"], reason="Original agent unavailable"))
    assert reassigned["offer"] is None and reassigned["generation"] == 2
    assert change(client, offered, "accept", b).status_code == 409
    assert ack(client, named_message, other, slots(named_message)).status_code == 403
    assert ok(ack(client, named_message, a, slots(named_message)))["request"]["state"] == "satisfied"
    offered_again = ok(change(client, reassigned, "offer", other, successorId=b["agentId"]))
    declined = ok(change(client, offered_again, "decline", b))
    assert declined["ownerId"] == other["agentId"] and declined["generation"] == 2
    next_offer = ok(change(client, declined, "offer", other, successorId=b["agentId"]))
    assert ok(change(client, next_offer, "withdraw", other))["offer"] is None
    with client.app.state.db.read() as c:
        assert c.execute("SELECT state FROM discussion_role_offers WHERE id=?", (offered["offer"]["id"],)).fetchone()[0] == "invalidated"


def test_scope_resume_finds_role_only_requests_and_feed_cursors_bind_dataset(client):
    a, b = enroll(client, "Predecessor"), enroll(client, "Successor")
    work = item(client)
    r = ok(change(client, role(client, work), "claim", a))
    scope = f"contextType=item&contextId={work['id']}"
    snapshot = get(client, "/discussions/resume?" + scope, a).json()
    cursor = snapshot["cursor"]
    personal = post(client, agent=a, onBehalfOfRoleId=r["id"], acknowledgement={"mode": "any_agent"})["message"]
    post(client, acknowledgement=named(("role", r["id"])))
    unrelated = post(client, text="Different work")
    offered = ok(change(client, r, "offer", a, successorId=b["agentId"]))
    ok(change(client, offered, "accept", b))
    resumed = get(client, "/discussions/resume?" + scope, b).json()
    assert resumed["counts"] == {"roles": 1, "discussions": 2, "requests": 2}
    inherited = next(m for m in resumed["requests"] if m["id"] == personal["id"])
    assert {"managed", "predecessor"} <= set(inherited["request"]["classifications"])
    assert {"type": "item", "id": work["id"]} in resumed["discussions"][0]["contexts"]
    feed = get(client, f"/discussions/changes?{scope}&cursor={cursor}&limit=1", b).json()
    assert feed["hasMore"] and len(feed["items"]) == 1
    changes = feed["items"][:]
    while feed["hasMore"]:
        feed = get(client, f"/discussions/changes?{scope}&cursor={feed['cursor']}&limit=1", b).json()
        changes.extend(feed["items"])
    assert [v["seq"] for v in changes] == sorted({v["seq"] for v in changes})
    assert not any(v["discussionId"] == unrelated["discussion"]["id"] for v in changes)
    assert any(v["kind"] == "discussion.role.accept" for v in changes)
    assert get(client, "/discussions/changes?cursor=" + cursor, b).json()["error"]["code"] == "cursor_reset"
    assert get(client, "/discussions/changes?cursor=malformed", b).status_code == 409
    assert get(client, "/discussions/roles?contextType=item", b).status_code == 422
    with client.app.state.db.transaction() as c:
        c.execute("UPDATE settings SET value=? WHERE key='dataId'", (json.dumps(str(uuid.uuid4())),))
    assert get(client, f"/discussions/changes?{scope}&cursor={feed['cursor']}", b).json()["error"]["code"] == "cursor_reset"


def test_pages_bound_all_collections_and_awaiting_matches_current_authority(client):
    a, b = enroll(client, "Owner"), enroll(client, "Recipient")
    work = item(client)
    for index in range(4):
        role(client, work, f"Role {index}")
        post(client, agent=a, acknowledgement=named(("agent", b["agentId"])))
    p = get(client, "/discussions?limit=1&offset=2", b).json()
    assert len(p["items"]) == 1 and p["total"] == 4
    assert get(client, "/discussions?view=awaiting", a).json()["total"] == 4
    assert get(client, "/discussions?view=awaiting", b).json()["total"] == 4
    outsider = enroll(client, "Unrelated reader")
    assert get(client, "/discussions?view=awaiting", outsider).json()["total"] == 0
    roles_page = get(client, "/discussions/roles?limit=1&offset=2").json()
    assert len(roles_page["items"]) == 1 and roles_page["total"] == 4
    resumed = get(client, "/discussions/resume?limit=1&offset=2", a).json()
    assert len(resumed["roles"]) == len(resumed["requests"]) == len(resumed["discussions"]) == 1
    assert resumed["counts"] == {"roles": 4, "requests": 4, "discussions": 4} and resumed["hasMore"]
    assert get(client, "/discussions/resume?limit=1&offset=4", a).json()["requests"] == []


def test_scoped_feed_includes_plain_replies_and_cross_context_role_transfers(client):
    a, b = enroll(client, "Old"), enroll(client, "New")
    work, other_work = item(client), item(client)
    r = ok(change(client, role(client, work), "claim", a))
    p = post(client, contexts=[{"type": "item", "id": other_work["id"]}], acknowledgement=named(("role", r["id"])))
    role_scope = f"contextType=item&contextId={work['id']}"
    other_scope = f"contextType=item&contextId={other_work['id']}"
    role_cursor = get(client, "/discussions/resume?" + role_scope).json()["cursor"]
    other_cursor = get(client, "/discussions/resume?" + other_scope).json()["cursor"]
    reply = ok(write(client, f"/discussions/{p['discussion']['id']}/messages", {"text": "A plain follow-up"}))
    feed = get(client, f"/discussions/changes?{role_scope}&cursor={role_cursor}", a).json()
    assert feed["items"][0]["messageId"] == reply["id"]
    offered = ok(change(client, r, "offer", a, successorId=b["agentId"]))
    ok(change(client, offered, "accept", b))
    changes = get(client, f"/discussions/changes?{other_scope}&cursor={other_cursor}", b).json()["items"]
    assert any(row["kind"] == "discussion.role.accept" for row in changes)


def test_concurrent_claim_and_ack_handoff_have_a_single_transaction_order(client):
    a, b = enroll(client, "A"), enroll(client, "B")
    r = role(client, item(client))
    db = client.app.state.db
    service = DiscussionService(db)
    barrier = threading.Barrier(2)

    def run_claim(credential):
        barrier.wait(timeout=5)
        try:
            with db.transaction() as c:
                actor = communication_actor(c, {"id": credential["id"], "kind": "agent"})
                return service.change_role(c, actor, r["id"], "claim", {"revision": r["revision"]})
        except AppError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run_claim, [a, b]))
    assert sum(isinstance(value, dict) for value in results) == 1
    winner = next(value for value in results if isinstance(value, dict))
    owner = a if winner["ownerId"] == a["agentId"] else b
    successor = b if owner is a else a
    m = post(client, acknowledgement=named(("role", r["id"])))["message"]
    offered = ok(change(client, winner, "offer", owner, successorId=successor["agentId"]))
    barrier = threading.Barrier(2)

    def race(action):
        credential = owner if action == "ack" else successor
        barrier.wait(timeout=5)
        try:
            with db.transaction() as c:
                actor = communication_actor(c, {"id": credential["id"], "kind": "agent"})
                if action == "ack":
                    return service.acknowledge(c, actor, m["id"], {"targetIds": slots(m)})
                return service.change_role(c, actor, r["id"], "accept", {"revision": offered["revision"]})
        except AppError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        acknowledged, transferred = list(pool.map(race, ["ack", "accept"]))
    assert isinstance(transferred, dict) and transferred["ownerId"] == successor["agentId"]
    persisted = get(client, f"/discussions/messages/{m['id']}", successor).json()
    if isinstance(acknowledged, dict):
        assert persisted["request"]["state"] == "satisfied"
        assert persisted["request"]["receipts"][0]["ownershipGeneration"] == 1
    else:
        assert acknowledged == "acknowledgement_authority"
        assert persisted["request"]["state"] == "pending" and not persisted["request"]["receipts"]


def test_audit_failure_rolls_back_message_receipts_state_and_search(client, monkeypatch):
    a = enroll(client, "Acknowledging agent")
    m = post(client, acknowledgement={"mode": "any_agent"})["message"]
    original = DiscussionService._emit

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("injected audit failure")

    monkeypatch.setattr(DiscussionService, "_emit", fail)
    assert ack(client, m, a).status_code == 503
    assert write(client, "/discussions", {"text": "Must roll back search too"}, a).status_code == 503
    monkeypatch.setattr(DiscussionService, "_emit", original)
    persisted = get(client, f"/discussions/messages/{m['id']}").json()
    assert persisted["request"]["state"] == "pending" and not persisted["request"]["receipts"]
    assert get(client, "/discussions?q=roll").json()["total"] == 0
    assert get(client, "/discussions").json()["total"] == 1


def test_restart_keeps_requests_and_no_liveness_is_inferred_from_chat_archive(client, tmp_path):
    a = enroll(client, "Long running")
    work = item(client)
    r = ok(change(client, role(client, work), "claim", a))
    chat = ok(write(client, "/chats", {"ownerType": "item", "ownerId": work["id"], "chatId": "provider-session", "title": "Old chat"}))
    m = post(client, agent=a, chatId=chat["id"], onBehalfOfRoleId=r["id"], acknowledgement={"mode": "anyone"})["message"]
    ok(write(client, f"/chats/{chat['id']}/archive", {"revision": chat["revision"]}))
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as restarted:
        response = get(restarted, f"/discussions/messages/{m['id']}", a)
        persisted = ok(response)
        assert persisted["request"]["state"] == "pending"
        assert persisted["request"]["canCancel"]
        assert get(restarted, "/discussions/roles", a).json()["items"][0]["ownerId"] == a["agentId"]


def test_migration_binding_is_atomic_idempotent_and_partial_schema_fails_closed():
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("CREATE TABLE credentials(id TEXT PRIMARY KEY,label TEXT,revoked INTEGER)")
    c.execute("INSERT INTO credentials VALUES('old','Original',0)")
    c.execute("BEGIN IMMEDIATE")
    add_discussions(c)
    c.commit()
    add_discussions(c)
    assert communication_actor(c, {"id": "old", "kind": "agent"}) == {"id": "old", "label": "Original", "kind": "agent"}
    c.execute("INSERT INTO credentials VALUES('new','Replacement',0)")
    bind_credential(c, "new", "Replacement", "old")
    c.execute("UPDATE credentials SET revoked=1 WHERE id='old'")
    with pytest.raises(AppError, match="invalid or revoked"):
        communication_actor(c, {"id": "old", "kind": "agent"})
    assert communication_actor(c, {"id": "new", "kind": "agent"})["id"] == "old"
    with pytest.raises(AppError, match="cannot change"):
        bind_credential(c, "new", "Other", "wrong-profile")
    c.execute("DROP INDEX discussion_contexts_context")
    with pytest.raises(AppError, match="incomplete or changed"):
        add_discussions(c)
    assert c.execute("SELECT 1 FROM sqlite_master WHERE name='discussion_contexts_context'").fetchone() is None
    c.close()


def test_invalid_contracts_and_allowlist_do_not_expand_agent_authority(client):
    a = enroll(client, "Agent")
    for body in [
        {"text": "  "},
        {"text": "x", "authorId": "spoof"},
        {"text": "x", "acknowledgement": {"mode": "all", "targets": []}},
        {"text": "x", "acknowledgement": {"mode": "anyone", "targets": [{"kind": "human", "id": "local-user"}]}},
        {"text": "x", "contexts": [{"type": "item", "id": "not-a-record"}]},
        {"text": "x", "acknowledgement": named(("human", "someone-else"))},
    ]:
        assert write(client, "/discussions", body, a).status_code == 422
    assert get(client, "/discussions?limit=0", a).status_code == 422
    assert get(client, "/discussions?offset=-1", a).status_code == 422
    # An agent may release its role, but releasing a request, reassigning a role and archiving a
    # thread are the person's, and so is any call no route serves.
    assert write(client, "/discussions/roles/id/release", {"revision": 1}, a).json()["error"]["code"] != "agent_scope"
    for method, path in [("POST", "/discussions/messages/id/release"), ("POST", "/discussions/roles/id/reassign"),
                         ("POST", "/discussions/id/archive"), ("POST", "/discussions/messages/id/cancel/extra"),
                         ("PATCH", "/discussions/id")]:
        refused = client.request(method, "/api/v1" + path, json={"revision": 1},
                                 headers={"Authorization": "Bearer " + a["token"], "Idempotency-Key": str(uuid.uuid4())})
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "agent_scope", (method, path, refused.text)


def test_future_cursor_and_wrong_scope_cannot_skip_or_cross_a_snapshot(client):
    cursor = get(client, "/discussions/resume").json()["cursor"]
    decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    decoded["seq"] = 9999
    forged = base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode().rstrip("=")
    assert get(client, "/discussions/changes?cursor=" + forged).json()["error"]["code"] == "cursor_reset"
