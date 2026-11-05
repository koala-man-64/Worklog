"""Reviewed agent changes: typed operations and an atomic, attributable approval."""

from __future__ import annotations

import json
from contextlib import nullcontext

from . import scratchpad
from .common import NOTE_LIMIT, AppError, event, now, require_revision, setting, uid
from .migrations import AUTOMATIC_APPROVAL_ID, stale_signature
from .operations import OPERATIONS, validate

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, rationale TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft', revision INTEGER NOT NULL,
 actor_id TEXT NOT NULL, actor_label TEXT NOT NULL, actor_kind TEXT NOT NULL,
 operations TEXT NOT NULL, supersedes_id TEXT REFERENCES proposals(id),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS proposals_status ON proposals(status,created_at);
"""

# Explicit schemas prevent a proposal becoming an arbitrary operation transport. Each operation's
# keys come from its one definition in operations.py: (required, allowed) per action.
FIELDS = {action: (set(spec.required), set(spec.allowed)) for action, spec in OPERATIONS.items()}

# Approves under the person's standing choice in Settings rather than a decision on one
# proposal: the person's authority, recorded apart from the approvals they give in Review.
AUTOMATIC = {"id": AUTOMATIC_APPROVAL_ID, "label": "Automatic approval", "kind": "human"}
# Since v12 a proposal's row says when it was last submitted and whether automatic approval
# applied it; before, both were read from its events.
SELECT = "SELECT p.* FROM proposals p"


def approval_required(conn):
    # Only an explicit false turns review off; a missing or unexpected value keeps it.
    return setting(conn, "requireProposalApproval", True) is not False


def _string(value, name, max_bytes=1_000_000, required=False):
    if not isinstance(value, str) or len(value.encode("utf-8")) > max_bytes or (required and not value.strip()):
        raise AppError(422, "validation", f"Invalid {name}")
    return value.strip() if required else value


def _operations(value):
    if not isinstance(value, list) or len(value) > 500:
        raise AppError(422, "validation", "Operations must be a list of at most 500 operations")
    for operation in value:
        if not isinstance(operation, dict) or set(operation) != {"action", "payload"}:
            raise AppError(422, "validation", "Each operation requires action and payload")
        action, payload = operation["action"], operation["payload"]
        if not isinstance(action, str) or action not in FIELDS or not isinstance(payload, dict):
            raise AppError(422, "validation", "Unsupported proposal action")
        required, allowed = FIELDS[action]
        if not required <= payload.keys() or not payload.keys() <= allowed:
            # Name what is wrong, so an agent working from older instructions can correct it.
            missing, unknown = sorted(required - payload.keys()), sorted(payload.keys() - allowed)
            parts = []
            if missing:
                parts.append("missing " + ", ".join(missing))
            if unknown:
                parts.append("not accepted " + ", ".join(unknown))
            detail = "; ".join(parts)
            if action.startswith("person.") and "role" in unknown:
                detail += " (a person's role is now their team)"
            raise AppError(422, "validation", f"Invalid fields for {action}: {detail}",
                           {"missing": missing, "unknown": unknown})
        if "revision" in required and (type(payload["revision"]) is not int or payload["revision"] < 0):
            raise AppError(422, "validation", "An existing target requires an integer revision")
    try:
        encoded = json.dumps(value, allow_nan=False)
    except (ValueError, TypeError):
        raise AppError(422, "validation", "Operations must contain JSON values") from None
    if len(encoded.encode("utf-8")) > 5_000_000:
        raise AppError(413, "too_large", "Proposal operations exceed 5,000,000 bytes")
    # A file attaches to a record once: a repeat would only add an identical copy beside it.
    attached: dict[str, int] = {}
    for number, operation in enumerate(value, 1):
        if operation["action"] == "attachment.create":
            payload = operation["payload"]
            key = json.dumps([payload["attachmentId"], payload["ownerType"], payload["ownerId"]])
            if key in attached:
                raise _numbered(AppError(422, "validation", f"Change {attached[key]} already attaches this file to this record",
                                         {"attachmentId": "duplicate"}), number, len(value), "attachment.create")
            attached[key] = number
    return encoded


def evidence_file(conn, proposal_id, attachment_id):
    """The file, uploaded to this proposal as evidence, that an attachment.create names.

    Only the proposal's own files qualify, so an agent can never copy another proposal's
    evidence or a file already on the person's records. The refusal is the same whether the
    file is missing or belongs elsewhere, so it reveals nothing about other drafts.
    """
    # Not the content: a copy is made inside SQLite, so the bytes never need to be read here.
    row = conn.execute("SELECT id,archived FROM attachments WHERE id=? AND owner_type='proposal' AND owner_id=?",
                       (attachment_id, proposal_id)).fetchone() if isinstance(attachment_id, str) else None
    if row is None:
        raise AppError(422, "validation", "attachmentId must name a file uploaded to this proposal as evidence",
                       {"attachmentId": "not_evidence"})
    if row["archived"]:
        raise AppError(409, "archived", "That file was archived from this proposal, so it cannot be attached",
                       {"attachmentId": "archived"})
    return row


def _numbered(exc, number, total, action):
    """Name the change as Review numbers it, so the person or agent knows which one to fix."""
    return AppError(exc.status, exc.code, f"Change {number} of {total}, {action}: {exc.message}",
                    {"operation": number, "action": action, "fields": exc.fields})


def _record(row):
    return {"id": row["id"], "title": row["title"], "rationale": row["rationale"],
            "status": row["status"], "autoApproved": bool(row["auto_approved"]),
            "revision": row["revision"], "actorId": row["actor_id"],
            "actorLabel": row["actor_label"], "operations": json.loads(row["operations"]),
            "supersedesId": row["supersedes_id"], "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "submittedAt": None if row["status"] == "draft" else row["submitted_at"],
            "attachments": []}


def _row(conn, identifier):
    row = conn.execute(SELECT + " WHERE p.id=?", (identifier,)).fetchone()
    if row is None:
        raise AppError(404, "not_found", "Proposal not found")
    return row


def _same_principal(conn, actor_id, actor):
    """Whether actor_id, as a proposal records it, names the caller.

    A proposal records the credential that wrote it. Rotation binds a new credential to the
    same agent identity and revokes the old one (discussions.bind_credential), so for an
    agent any credential of its identity counts as its own.
    """
    if actor_id == actor["id"]:
        return True
    return actor["kind"] == "agent" and conn.execute(
        "SELECT 1 FROM communication_credentials theirs JOIN communication_credentials mine ON mine.agent_id=theirs.agent_id"
        " WHERE theirs.credential_id=? AND mine.credential_id=?", (actor_id, actor["id"])).fetchone() is not None


def _can_see(conn, row, actor):
    # Agents see other agents' proposals only once submitted; withdrawing an unsubmitted draft keeps it private.
    if actor["kind"] != "agent" or row["status"] in {"submitted", "approved", "rejected"} or _same_principal(conn, row["actor_id"], actor):
        return True
    # submitted_at is set by every submit and never cleared, reopening included.
    return row["status"] == "withdrawn" and row["submitted_at"] is not None


def _visible(conn, row, actor):
    if not _can_see(conn, row, actor):
        raise AppError(404, "not_found", "Proposal not found")


def _owner(conn, row, actor):
    if not _same_principal(conn, row["actor_id"], actor):
        raise AppError(403, "forbidden", "Only the originating actor may edit or submit this proposal")


def _scratchpad_landing(conn, action, payload):
    """An approved note for a work item lands in its scratchpad, appended as a section or as the first.

    A work item keeps one scratchpad (v8), so a second note there could only be refused. The
    note's title becomes its section's heading. Anything malformed passes through unchanged,
    for dispatch to refuse as it would have.
    """
    if action != "note.create" or payload.get("ownerType") != "item":
        return action, payload
    owner, title, text = payload.get("ownerId"), payload.get("title", ""), payload.get("text")
    if not (isinstance(owner, str) and isinstance(title, str) and isinstance(text, str)):
        return action, payload
    addition = scratchpad.section(title, text)
    pad = scratchpad.find(conn, owner)
    if pad is None:
        if scratchpad.size(addition) > NOTE_LIMIT:
            return action, payload  # as proposed, its title kept, for dispatch to take or refuse by size
        return action, {"ownerType": "item", "ownerId": owner, "title": scratchpad.TITLE, "text": addition}
    merged = scratchpad.joined([pad[2], addition])
    if scratchpad.size(merged) > NOTE_LIMIT:
        raise AppError(422, "validation", f"This work item's scratchpad is full: adding this note would take it past {NOTE_LIMIT:,} bytes")
    return "note.update", {"id": pad[0], "revision": pad[3], "text": merged}


class ProposalService:
    def __init__(self, db, dispatch):
        self.db = db
        self.dispatch = dispatch
        self._applying_id: str | None = None

    def list(self, actor, status=None):
        if status is not None and status not in {"draft", "submitted", "approved", "rejected", "withdrawn"}:
            raise AppError(422, "validation", "Invalid proposal status")
        with self.db.read() as conn:
            rows = conn.execute(SELECT + " ORDER BY p.created_at,p.id").fetchall()
            items = [_record(r) for r in rows if (not status or r["status"] == status)
                     and _can_see(conn, r, actor)]
            return {"items": items, "total": len(items)}

    def detail(self, identifier, actor):
        with self.db.read() as conn:
            row = _row(conn, identifier)
            _visible(conn, row, actor)
            return _record(row)

    def create(self, payload, actor, conn=None):
        if actor.get("kind") not in {"human", "agent"}:
            raise AppError(403, "forbidden", "Invalid actor")
        title = _string(payload.get("title"), "title", 1000, required=True)
        rationale = _string(payload.get("rationale", ""), "rationale")
        operations = _operations(payload.get("operations", []))
        with self.db.transaction() if conn is None else nullcontext(conn) as connection:
            supersedes = payload.get("supersedesId")
            if supersedes:
                prior = _row(connection, supersedes)
                _visible(connection, prior, actor)
                _owner(connection, prior, actor)
                if prior["status"] not in {"rejected", "withdrawn"}:
                    raise AppError(409, "conflict", "Only rejected or withdrawn proposals may be superseded")
            identifier, stamp = uid(), now()
            connection.execute("INSERT INTO proposals(id,title,rationale,status,revision,actor_id,actor_label,actor_kind,"
                               "operations,supersedes_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                               (identifier, title, rationale, "draft", 1, actor["id"], actor["label"],
                                actor["kind"], operations, supersedes, stamp, stamp))
            result = _record(_row(connection, identifier))
            event(connection, actor, "proposal.create", identifier, None, result)
            return result

    def update(self, identifier, payload, actor):
        if not set(payload) <= {"revision", "title", "rationale", "operations"}:
            raise AppError(422, "validation", "Unknown proposal fields")
        with self.db.transaction() as conn:
            row = _row(conn, identifier)
            _visible(conn, row, actor)
            _owner(conn, row, actor)
            require_revision(row, payload.get("revision"))
            if row["status"] != "draft":
                raise AppError(409, "conflict", "Only drafts can be edited")
            before = _record(row)
            title = _string(payload.get("title", row["title"]), "title", 1000, required=True)
            rationale = _string(payload.get("rationale", row["rationale"]), "rationale")
            operations = _operations(payload.get("operations", json.loads(row["operations"])))
            conn.execute("UPDATE proposals SET title=?,rationale=?,operations=?,revision=revision+1,updated_at=? WHERE id=?",
                         (title, rationale, operations, now(), identifier))
            result = _record(_row(conn, identifier))
            event(conn, actor, "proposal.update", identifier, before, result)
            return result

    def transition(self, identifier, verb, payload, actor, conn=None):
        if verb not in {"submit", "withdraw", "reopen", "approve", "reject"}:
            raise AppError(422, "validation", "Unsupported proposal transition")
        with self.db.transaction() if conn is None else nullcontext(conn) as connection:
            row = _row(connection, identifier)
            _visible(connection, row, actor)
            require_revision(row, payload.get("revision"))
            if verb in {"approve", "reject"}:
                if actor["kind"] != "human":
                    raise AppError(403, "forbidden", "Only a human may approve or reject proposals")
                allowed = {"submitted"}
            elif verb == "reopen":
                if actor["kind"] != "human":
                    _owner(connection, row, actor)
                allowed = {"submitted"}
            else:
                _owner(connection, row, actor)
                allowed = {"draft"} if verb == "submit" else {"draft", "submitted"}
            if row["status"] not in allowed:
                raise AppError(409, "conflict", f"Cannot {verb} a {row['status']} proposal")
            before = _record(row)
            if verb == "submit" and not before["operations"]:
                raise AppError(422, "validation", "A submitted proposal needs at least one operation")
            if verb == "submit" and approval_required(connection):
                # A value its model refuses, or a file it cannot attach, would only wait for the
                # person to reject it. With approval off, applying it below makes the same checks.
                self._check_values(row)
                self._check_files(connection, row)
            if verb == "approve":
                self._apply(connection, row)
            result = self._advance(connection, identifier, verb, actor, before)
            if verb == "submit" and not approval_required(connection):
                # The person turned approval off, so submitting applies the proposal. A failure
                # rolls the submission back with it and leaves the draft for its agent to fix.
                try:
                    self._apply(connection, row)
                except AppError as exc:
                    raise AppError(exc.status, exc.code, "Not submitted: with approval off, submitting "
                                   f"applies the proposal, and that failed. {exc.message}", exc.fields) from exc
                result = self._advance(connection, identifier, "approve", AUTOMATIC, result)
            if verb == "reopen" and actor["kind"] == "human":
                self._notify_proposer(connection, row, conflicts=self._stale_operations(connection, row), reason="reopened")
            elif result["status"] == "approved":
                self.notify_stale_proposals(connection)
            return result

    def _apply(self, connection, row):
        operations = json.loads(row["operations"])
        _operations(operations)
        proposer = {"id": row["actor_id"], "label": row["actor_label"], "kind": row["actor_kind"]}
        # Dispatch uses this same transaction. Any stale revision, validation
        # error, or injected failure rolls back all operations and approval.
        # GitHub references may link to a chat created or updated elsewhere in the same proposal.
        ordered = sorted(enumerate(operations, 1), key=lambda pair: pair[1]["action"].startswith("github."))
        previous = self._applying_id
        self._applying_id = row["id"]
        try:
            for number, operation in ordered:
                try:
                    action, payload = _scratchpad_landing(connection, operation["action"], operation["payload"])
                    if action == "attachment.create":
                        # The file is this proposal's own evidence, which only Worklog names.
                        payload = {**payload, "proposalId": row["id"]}
                    self.dispatch(connection, proposer, action, payload)
                except AppError as exc:
                    raise _numbered(exc, number, len(operations), operation["action"]) from exc
        finally:
            self._applying_id = previous

    @staticmethod
    def _check_values(row):
        """Each operation's values pass the checks approve makes before applying it, so a proposal
        the person reviews can fail only on what applying it finds: a stale revision, or a rule
        of the record itself."""
        operations = json.loads(row["operations"])
        # A stored row changed behind Worklog's back is refused for its shape, as approve refuses it.
        _operations(operations)
        for number, operation in enumerate(operations, 1):
            payload = operation["payload"]
            if operation["action"] == "attachment.create":
                payload = {**payload, "proposalId": row["id"]}
            try:
                validate(operation["action"], payload)
            except AppError as exc:
                raise _numbered(exc, number, len(operations), operation["action"]) from exc

    def _check_files(self, connection, row):
        operations = json.loads(row["operations"])
        for number, operation in enumerate(operations, 1):
            if operation["action"] == "attachment.create":
                try:
                    evidence_file(connection, row["id"], operation["payload"].get("attachmentId"))
                except AppError as exc:
                    raise _numbered(exc, number, len(operations), operation["action"]) from exc

    def _advance(self, connection, identifier, verb, actor, before):
        state = {
            "submit": "submitted",
            "withdraw": "withdrawn",
            "reopen": "draft",
            "approve": "approved",
            "reject": "rejected",
        }[verb]
        stamp = now()
        connection.execute("UPDATE proposals SET status=?,revision=revision+1,updated_at=?"
                           ",submitted_at=CASE WHEN ? THEN ? ELSE submitted_at END"
                           ",auto_approved=CASE WHEN ? THEN 1 ELSE auto_approved END WHERE id=?",
                           (state, stamp, verb == "submit", stamp,
                            verb == "approve" and actor.get("id") == AUTOMATIC_APPROVAL_ID, identifier))
        result = _record(_row(connection, identifier))
        event(connection, actor, f"proposal.{verb}", identifier, before, result, at=stamp)
        return result

    def _stale_operations(self, conn, row, roadmap_id=None):
        operations = json.loads(row["operations"])
        revised_steps: dict[str, int] = {}
        conflicts = []
        for number, op in enumerate(operations, 1):
            action, payload = op["action"], op["payload"]
            if action in {"roadmap.revise", "roadmap.move", "roadmap.archive", "roadmap.recover"}:
                target = payload.get("id") or payload.get("roadmapId")
                rm = conn.execute("SELECT id,title,revision FROM roadmaps WHERE id=?", (target,)).fetchone() if isinstance(target, str) else None
                if rm is not None:
                    if action == "roadmap.revise" and rm["revision"] == payload.get("revision") and isinstance(payload.get("steps"), list):
                        for step in payload["steps"]:
                            sid = step.get("id") if isinstance(step, dict) else None
                            if isinstance(sid, str):
                                revised_steps[sid] = revised_steps.get(sid, 0) + 1
                    if (roadmap_id is None or rm["id"] == roadmap_id) and rm["revision"] != payload.get("revision"):
                        conflicts.append({
                            "operation": number,
                            "action": action,
                            "targetType": "roadmap",
                            "targetId": rm["id"],
                            "targetTitle": rm["title"],
                            "roadmapId": rm["id"],
                            "expectedRevision": payload.get("revision"),
                            "currentRevision": rm["revision"],
                        })
            elif action == "progress.set":
                target = payload.get("id") or payload.get("stepId")
                st = conn.execute(
                    "SELECT s.id,s.title,s.revision,s.roadmap_id,r.title AS roadmap_title FROM steps s JOIN roadmaps r ON r.id=s.roadmap_id WHERE s.id=?",
                    (target,),
                ).fetchone() if isinstance(target, str) else None
                if st is not None:
                    expected_after_earlier = st["revision"] + revised_steps.get(st["id"], 0)
                    if (roadmap_id is None or st["roadmap_id"] == roadmap_id) and expected_after_earlier != payload.get("revision"):
                        conflicts.append({
                            "operation": number,
                            "action": action,
                            "targetType": "step",
                            "targetId": st["id"],
                            "targetTitle": st["title"],
                            "roadmapId": st["roadmap_id"],
                            "expectedRevision": payload.get("revision"),
                            "currentRevision": st["revision"],
                        })
        return conflicts

    def notify_stale_proposals(self, conn, roadmap_id=None):
        if self._applying_id is not None:
            return []
        notified = []
        rows = conn.execute(SELECT + " WHERE p.status='submitted' ORDER BY p.created_at,p.id").fetchall()
        for row in rows:
            conflicts = self._stale_operations(conn, row, roadmap_id=roadmap_id)
            if not conflicts:
                continue
            # One notice per proposal revision and set of conflicts, however often the plan changes.
            if conn.execute("SELECT 1 FROM proposal_stale_notices WHERE proposal_id=? AND proposal_revision=? AND conflicts=?",
                            (row["id"], row["revision"], stale_signature(conflicts))).fetchone():
                continue
            discussion = self._notify_proposer(conn, row, conflicts=conflicts, reason="plan_changed")
            if discussion is not None:
                notified.append({"proposalId": row["id"], "discussionId": discussion["discussion"]["id"],
                                 "messageId": discussion["message"]["id"], "conflicts": conflicts})
        return notified

    def _notify_proposer(self, conn, row, *, conflicts, reason):
        agent = conn.execute(
            """SELECT a.id, a.label FROM communication_agents a
               LEFT JOIN communication_credentials cc ON cc.agent_id = a.id
               WHERE cc.credential_id = ? OR a.id = ?
               LIMIT 1""",
            (row["actor_id"], row["actor_id"]),
        ).fetchone()
        if agent is None:
            return None
        contexts: list[dict[str, str]] = []
        seen_ctx: set[tuple[str, str]] = set()
        for conflict in conflicts:
            rid = conflict.get("roadmapId")
            if isinstance(rid, str) and ("roadmap", rid) not in seen_ctx and conn.execute("SELECT 1 FROM roadmaps WHERE id=?", (rid,)).fetchone():
                seen_ctx.add(("roadmap", rid))
                contexts.append({"type": "roadmap", "id": rid})
        if not contexts:
            for op in json.loads(row["operations"]):
                act, p = op["action"], op["payload"]
                for ctype, table, key in (
                    ("roadmap", "roadmaps", "roadmapId" if "roadmapId" in p else "id" if act.startswith("roadmap.") else "ownerId" if p.get("ownerType") == "roadmap" else ""),
                    ("item", "work_items", "itemId" if "itemId" in p else "id" if act.startswith("item.") else "ownerId" if p.get("ownerType") == "item" else ""),
                    ("step", "steps", "stepId" if "stepId" in p else "id" if act == "progress.set" else "ownerId" if p.get("ownerType") == "step" else ""),
                ):
                    val = p.get(key) if key else None
                    if isinstance(val, str) and (ctype, val) not in seen_ctx and conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (val,)).fetchone():
                        seen_ctx.add((ctype, val))
                        contexts.append({"type": ctype, "id": val})
        lines = [
            f"Change {c['operation']} ({c['action']} on {c['targetTitle']!r} `{c['targetId']}`): expected revision {c['expectedRevision']}, current revision {c['currentRevision']}"
            for c in conflicts
        ]
        summary = "; ".join(lines)
        if reason == "reopened":
            title = f"Proposal returned to draft: {row['title']}"[:1000]
            body = (
                f"Proposal **{row['title']}** (`{row['id']}`) was returned to draft for re-review."
                + (f" Stale target(s): {summary}." if summary else "")
                + " Reload the current plan, update the draft, and resubmit."
            )
        else:
            title = f"Plan changed — re-review proposal: {row['title']}"[:1000]
            body = (
                f"Proposal **{row['title']}** (`{row['id']}`) has stale target revision(s) after a plan update: {summary}. "
                "Reload the plan, reopen or supersede the proposal, and resubmit."
            )
        from .chat_messages import BOUND, RECIPIENTS, ChatMessageService
        from .discussions import DiscussionService

        notifier = {"id": "local-user", "label": "You", "kind": "human"}
        discussion = DiscussionService(self.db).create(
            conn,
            notifier,
            {
                "title": title,
                "text": body,
                "contexts": contexts,
                "acknowledgement": {
                    "mode": "all",
                    "targets": [{"kind": "agent", "id": agent["id"]}],
                },
            },
        )
        chat_service = ChatMessageService()
        # Only chats the agent's identity is bound to: a chat ID its run merely claims is not its chat.
        for chat_id, ext_id in conn.execute(
            """SELECT DISTINCT ch.id, ch.chat_id FROM agent_chats ch
               JOIN agent_runs ar ON ar.external_chat_id = ch.chat_id
               JOIN credentials cr ON cr.id = ar.credential_id
               WHERE ch.archived = 0 AND ar.ended_at IS NULL AND cr.revoked = 0
                 AND (ar.agent_id = ? OR ar.credential_id = ?) AND """ + BOUND,
            (agent["id"], row["actor_id"]),
        ).fetchall():
            if conn.execute(RECIPIENTS, (ext_id,)).fetchone():
                chat_service.post(conn, notifier, chat_id, {"text": body})
        if reason == "plan_changed":
            stamp = now()
            conn.execute("INSERT OR IGNORE INTO proposal_stale_notices(proposal_id,proposal_revision,conflicts,created_at) VALUES(?,?,?,?)",
                         (row["id"], row["revision"], stale_signature(conflicts), stamp))
            event(conn, notifier, "proposal.stale", row["id"], None, {
                "proposalRevision": row["revision"],
                "discussionId": discussion["discussion"]["id"],
                "messageId": discussion["message"]["id"],
                "conflicts": conflicts,
            }, at=stamp)
        return discussion

    def check_upload(self, conn, identifier, actor):
        row = _row(conn, identifier)
        _visible(conn, row, actor)
        if actor["kind"] == "agent":
            _owner(conn, row, actor)
            if row["status"] != "draft":
                raise AppError(409, "conflict", "Agent evidence uploads require an owned draft")
        return _record(row)
