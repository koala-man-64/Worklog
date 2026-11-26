"""Database-level tests for durable push routing and receipt safety."""

from __future__ import annotations

import json
import uuid

import pytest
from worklog.common import AppError, now
from worklog.coordination import CoordinationService
from worklog.db import Database
from worklog.discussions import DiscussionService, bind_credential, communication_actor
from worklog.items import Items
from worklog.push import DeliveryService
from worklog.push_events import enqueue_event
from worklog.tickets import TicketService


@pytest.fixture
def db(tmp_path):
    value = Database(tmp_path / "worklog.sqlite3")
    value.initialize()
    return value


def chat(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, name))


def add_agent(c, credential_id: str, label: str, chat_id: str) -> tuple[dict, dict]:
    c.execute("INSERT INTO credentials(id,label,token_hash,created_at,revoked) VALUES(?,?,?,?,0)",
              (credential_id, label, uuid.uuid4().hex, now()))
    bind_credential(c, credential_id, label)
    c.execute("UPDATE communication_credentials SET registered_by=?,registered_by_label=?,chat_id=?,directory=? "
              "WHERE credential_id=?", ("sponsor", "Sponsor", chat_id, "C:/registered", credential_id))
    raw = {"id": credential_id, "kind": "agent", "label": label}
    return raw, communication_actor(c, raw)


def test_direct_asks_and_explicit_replies_route_only_to_the_addressed_requester(db):
    delivery, discussions = DeliveryService(), DiscussionService(db)
    with db.transaction() as c:
        alice_raw, alice = add_agent(c, "alice-credential", "Alice", chat("alice"))
        bob_raw, bob = add_agent(c, "bob-credential", "Bob", chat("bob"))
        charlie_raw, charlie = add_agent(c, "charlie-credential", "Charlie", chat("charlie"))
        alice_enrollment = delivery.enroll(c, alice_raw, "claude", chat("alice"))
        bob_enrollment = delivery.enroll(c, bob_raw, "codex", chat("bob"))
        charlie_enrollment = delivery.enroll(c, charlie_raw, "claude", chat("charlie"))
        first = discussions.create(c, alice, {
            "text": "private ask from Alice",
            "acknowledgement": {"mode": "all", "targets": [{"kind": "agent", "id": bob["id"]}]},
        })
        ask = first["message"]
        second = discussions.post(c, charlie, first["discussion"]["id"], {
            "text": "private ask from Charlie",
            "acknowledgement": {"mode": "all", "targets": [{"kind": "agent", "id": bob["id"]}]},
        })
        open_message = discussions.create(c, alice, {
            "text": "open request does not broadcast",
            "acknowledgement": {"mode": "any_agent"},
        })["message"]
        bob_pending = delivery.pending(c, bob_raw, bob_enrollment["id"])
        assert [row["messageId"] for row in bob_pending["items"]] == [ask["id"], second["id"]]
        assert all(row["kind"] == "discussion.ask" for row in bob_pending["items"])
        assert "private ask" not in json.dumps(bob_pending)
        page_one = delivery.status(c, bob_raw, "received", limit=1)
        page_two = delivery.status(c, bob_raw, "received", limit=1, before_seq=page_one["nextSeq"])
        assert page_one["hasMore"] is True and page_two["hasMore"] is False
        page_ids = [row["id"] for page in (page_one, page_two) for row in page["items"]]
        assert len(page_ids) == len(set(page_ids)) == 2
        assert {row["messageId"] for page in (page_one, page_two) for row in page["items"]} == {ask["id"], second["id"]}
        assert delivery.pending(c, alice_raw, alice_enrollment["id"])["items"] == []
        assert delivery.pending(c, charlie_raw, charlie_enrollment["id"])["items"] == []
        event = c.execute("SELECT * FROM events WHERE entity_id=? AND kind='discussion.create'", (ask["id"],)).fetchone()
        before_count = c.execute("SELECT count(*) FROM push_deliveries WHERE source_event_id=?", (event["id"],)).fetchone()[0]
        enqueue_event(c, {"id": event["id"], "entityId": event["entity_id"], "kind": event["kind"],
                          "actorId": event["actor_id"], "actorKind": event["actor_kind"],
                          "createdAt": event["created_at"]}, None, ask)
        after_count = c.execute("SELECT count(*) FROM push_deliveries WHERE source_event_id=?", (event["id"],)).fetchone()[0]
        assert after_count == before_count == 1

        reply = discussions.post(c, bob, first["discussion"]["id"], {
            "text": "answer one",
            "replyToMessageId": ask["id"],
        })
        assert reply["replyToMessageId"] == ask["id"]
        assert delivery.pending(c, alice_raw, alice_enrollment["id"])["items"][0]["messageId"] == reply["id"]
        assert delivery.pending(c, charlie_raw, charlie_enrollment["id"])["items"] == []
        assert delivery.pending(c, bob_raw, bob_enrollment["id"])["items"][-1]["messageId"] == second["id"]

        ordinary = discussions.post(c, alice, first["discussion"]["id"], {"text": "ordinary thread reply"})
        assert ordinary["replyToMessageId"] is None
        assert all(item["messageId"] != ordinary["id"]
                   for item in delivery.pending(c, bob_raw, bob_enrollment["id"])["items"])
        assert all(item["messageId"] != ordinary["id"]
                   for item in delivery.pending(c, charlie_raw, charlie_enrollment["id"])["items"])

        with pytest.raises(AppError) as wrong_target:
            discussions.post(c, charlie, first["discussion"]["id"], {
                "text": "not the addressed agent",
                "replyToMessageId": ask["id"],
            })
        assert wrong_target.value.code == "discussion_reply_target"
        open_reply = discussions.post(c, bob, open_message["discussionId"], {
            "text": "a reply to an open request is correlated but never broadcast",
            "replyToMessageId": open_message["id"],
        })
        # Bob is eligible for the open request, so correlation is valid, but no recipient receives
        # a push because an open audience is not a named direct target.
        assert open_reply["replyToMessageId"] == open_message["id"]
        assert all(item["messageId"] != open_reply["id"]
                   for item in delivery.pending(c, alice_raw, alice_enrollment["id"])["items"])

        to_person = discussions.create(c, alice, {"text": "Please review the result.", "acknowledgement": {
            "mode": "all", "targets": [{"kind": "human", "id": "local-user"}]}})
        human_reply = discussions.post(c, {"id": "local-user", "label": "You", "kind": "human"},
                                      to_person["discussion"]["id"],
                                      {"text": "Reviewed.", "replyToMessageId": to_person["message"]["id"]})
        assert delivery.pending(c, alice_raw, alice_enrollment["id"])["items"][-1]["messageId"] == human_reply["id"]

        group = discussions.create(c, alice, {"text": "Please each confirm.", "acknowledgement": {
            "mode": "all", "targets": [{"kind": "agent", "id": agent["id"]} for agent in (bob, charlie)]}})
        sent = delivery.status(c, alice_raw, "sent")["items"]
        assert {row["recipientAgentId"] for row in sent if row["messageId"] == group["message"]["id"]} == {
            bob["id"], charlie["id"]}


def test_subscriptions_cover_only_explicit_ticket_item_and_run_updates(db):
    delivery = DeliveryService()
    with db.transaction() as c:
        watcher_raw, watcher = add_agent(c, "watcher-credential", "Watcher", chat("watcher"))
        other_raw, _ = add_agent(c, "other-credential", "Other", chat("other"))
        enrollment = delivery.enroll(c, watcher_raw, "codex", chat("watcher"))
        other_enrollment = delivery.enroll(c, other_raw, "claude", chat("other"))
        item = Items(db).apply(c, watcher, "item.create", {"title": "Work item"})
        ticket = TicketService().create(c, watcher, {"title": "Ticket", "kind": "task"})
        run = CoordinationService(db).create_run(c, watcher_raw,
                                                {"clientRunKey": "push-run", "displayName": "Watcher run", "provider": "codex"})
        delivery.subscribe(c, watcher_raw, enrollment["id"], "item", item["id"], True)
        delivery.subscribe(c, watcher_raw, enrollment["id"], "ticket", ticket["key"], True)
        delivery.subscribe(c, watcher_raw, enrollment["id"], "run", run["id"], True)

        ticket = TicketService().update(c, watcher, ticket["key"], {"revision": ticket["revision"], "title": "Retitled"})
        item = Items(db).apply(c, watcher, "item.update", {"id": item["id"], "revision": item["revision"], "title": "Retitled"})
        ticket = TicketService().update(c, watcher, ticket["key"], {"revision": ticket["revision"], "status": "doing"})
        # Unwatch stops later matching events. The already committed event remains durable.
        delivery.subscribe(c, watcher_raw, enrollment["id"], "ticket", ticket["key"], False)
        ticket = TicketService().update(c, watcher, ticket["key"], {"revision": ticket["revision"], "status": "blocked"})
        Items(db).apply(c, watcher, "item.update", {"id": item["id"], "revision": item["revision"], "status": "doing"})
        CoordinationService(db).create_participation(c, watcher_raw, {"runId": run["id"], "itemId": item["id"]})
        CoordinationService(db).end_run(c, watcher_raw, run["id"], {"revision": run["revision"]})

        rows = delivery.pending(c, watcher_raw, enrollment["id"])["items"]
        observed = {(row["kind"], row["subjectType"], row["subjectId"]) for row in rows}
        assert ("ticket.status", "ticket", ticket["id"]) in observed
        assert ("item.status", "item", item["id"]) in observed
        assert ("run.update", "run", run["id"]) in observed
        assert ("run.end", "run", run["id"]) in observed
        assert sum(row["kind"] == "ticket.status" for row in rows) == 1
        assert sum(row["kind"] == "item.status" for row in rows) == 1
        assert delivery.pending(c, other_raw, other_enrollment["id"])["items"] == []


def test_unavailable_retry_requires_a_new_attempt_and_uncertain_stays_non_replayable(db):
    delivery, discussions = DeliveryService(), DiscussionService(db)
    with db.transaction() as c:
        sender_raw, sender = add_agent(c, "sender-credential", "Sender", chat("sender"))
        target_raw, target = add_agent(c, "target-credential", "Target", chat("target"))
        enrollment = delivery.enroll(c, target_raw, "codex", chat("target"))
        discussions.create(c, sender, {
            "text": "answer me",
            "acknowledgement": {"mode": "all", "targets": [{"kind": "agent", "id": target["id"]}]},
        })["message"]
        record = delivery.pending(c, target_raw, enrollment["id"])["items"][0]
        first_attempt = str(uuid.uuid4())
        assert delivery.claim(c, target_raw, enrollment["id"], record["id"], first_attempt)["acquired"]
        assert delivery.receipt(c, target_raw, enrollment["id"], record["id"], first_attempt, "unavailable")["state"] == "unavailable"
        assert not delivery.claim(c, target_raw, enrollment["id"], record["id"], first_attempt)["acquired"]
        second_attempt = str(uuid.uuid4())
        assert delivery.claim(c, target_raw, enrollment["id"], record["id"], second_attempt)["acquired"]
        delivery.disable(c, target_raw, enrollment["id"])
        delivery.enroll(c, target_raw, "codex", chat("target"))
        assert delivery.receipt(c, target_raw, enrollment["id"], record["id"], second_attempt, "unavailable")["state"] == "uncertain"
        assert delivery.pending(c, target_raw, enrollment["id"])["items"] == []
        assert delivery.receipt(c, target_raw, enrollment["id"], record["id"], second_attempt, "submitted")["state"] == "submitted"
        assert delivery.pending(c, target_raw, enrollment["id"])["items"] == []
        assert delivery.status(c, target_raw, "received")["items"][0]["state"] == "submitted"


def test_enrollment_is_bound_to_the_live_registered_credential_and_exact_chat(db):
    delivery = DeliveryService()
    with db.transaction() as c:
        raw, _ = add_agent(c, "registered-credential", "Registered", chat("registered"))
        foreign_raw, _ = add_agent(c, "foreign-credential", "Foreign", chat("foreign"))
        with pytest.raises(AppError) as mismatch:
            delivery.enroll(c, raw, "codex", chat("foreign"))
        assert mismatch.value.code == "delivery_registration"
        enrollment = delivery.enroll(c, raw, "codex", chat("registered"))
        with pytest.raises(AppError) as impersonation:
            delivery.enrollment(c, foreign_raw, enrollment["id"])
        assert impersonation.value.status == 404
        assert delivery.enrollment(c, raw, enrollment["id"])["provider"] == "codex"
        unsupported, _ = add_agent(c, "other-chat-format", "Older registration", "non-provider-session")
        with pytest.raises(AppError) as invalid:
            delivery.enroll(c, unsupported, "codex", "non-provider-session")
        assert invalid.value.status == 422
        desktop = "local_" + chat("desktop")
        local, _ = add_agent(c, "desktop-credential", "Desktop", desktop)
        with pytest.raises(AppError):
            delivery.enroll(c, local, "codex", desktop)
        assert delivery.enroll(c, local, "claude", desktop)["chatId"] == desktop


def test_revoked_credential_cannot_claim_or_receive_new_direct_deliveries(db):
    delivery, discussions = DeliveryService(), DiscussionService(db)
    with db.transaction() as c:
        sender_raw, sender = add_agent(c, "revocation-sender", "Sender", chat("sender"))
        target_raw, target = add_agent(c, "revocation-target", "Target", chat("target"))
        enrollment = delivery.enroll(c, target_raw, "codex", chat("target"))
        request = {"text": "initial ask", "acknowledgement": {
            "mode": "all", "targets": [{"kind": "agent", "id": target["id"]}]}}
        discussions.create(c, sender, request)
        record = delivery.pending(c, target_raw, enrollment["id"])["items"][0]
        c.execute("UPDATE credentials SET revoked=1 WHERE id=?", (target_raw["id"],))
        with pytest.raises(AppError) as revoked:
            delivery.claim(c, target_raw, enrollment["id"], record["id"], str(uuid.uuid4()))
        assert revoked.value.status == 401
        with pytest.raises(AppError) as revoked_read:
            delivery.pending(c, target_raw, enrollment["id"])
        assert revoked_read.value.status == 401
        # The revoked identity remains historically addressable, but it cannot acquire new push work.
        new_request = {"text": "ask after revoke", "acknowledgement": {
            "mode": "all", "targets": [{"kind": "agent", "id": target["id"]}]}}
        discussions.create(c, sender, new_request)
        assert c.execute("SELECT count(*) FROM push_deliveries").fetchone()[0] == 1
