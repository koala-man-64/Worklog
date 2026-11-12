"""Reviewed agent changes: typed operations and an atomic, attributable approval."""

from __future__ import annotations

import json
from contextlib import nullcontext

from . import proposal_names, proposal_targets, scratchpad
from .common import (
    NOTE_LIMIT,
    AppError,
    event,
    now,
    read_events,
    require_revision,
    rows_in,
    setting,
    uid,
)
from .discussions import DiscussionService
from .migrations import AUTOMATIC_APPROVAL_ID, stale_signature
from .operations import OPERATIONS, keys, validate
from .plans import Forecast

# Explicit schemas prevent a proposal becoming an arbitrary operation transport. Each operation's
# keys come from its one definition in operations.py: (required, allowed) per action.
FIELDS = {action: tuple(set(names) for names in keys(action)) for action in OPERATIONS}

# Approves under the person's standing choice in Settings rather than a decision on one
# proposal: the person's authority, recorded apart from the approvals they give in Review.
AUTOMATIC = {"id": AUTOMATIC_APPROVAL_ID, "label": "Automatic approval", "kind": "human"}
# Since v12 a proposal's row says when it was last submitted and whether automatic approval
# applied it; before, both were read from its events. Since v18 a proposal also names the directory
# its agent's identity registered from, whichever of the identity's credentials wrote it: rotating a
# credential keeps the identity. Only a credential an agent registered, or continued its identity
# with in another chat, holds a directory: the one that wrote the proposal names its own, and one
# that holds none, as after rotation, names the identity's newest.
SELECT = ("SELECT p.*,(SELECT reg.directory FROM communication_credentials cc JOIN communication_credentials reg"
          " ON reg.agent_id=cc.agent_id JOIN credentials held ON held.id=reg.credential_id"
          " WHERE cc.credential_id=p.actor_id AND reg.directory IS NOT NULL"
          " ORDER BY reg.credential_id=cc.credential_id DESC,held.created_at DESC,held.rowid DESC LIMIT 1)"
          " AS actor_directory FROM proposals p")
# Changes whose record proposal_targets reads from applying them: the work item or roadmap a create
# made, whose ID Worklog chooses, and the ticket a change was made to, which may be named by its key.
APPLIED_TO_RECORD = frozenset({"item.create", "roadmap.create", "ticket.create", "ticket.update"})
# A proposal as a history lists it, without its operations: their number is enough there. A
# history holds the latest first, each numbered by its place in the sequence. An approved
# proposal is final, so it was last updated when it was applied.
HISTORY = ("SELECT p.id,p.title,p.status,p.auto_approved,p.actor_label,cc.agent_id,p.created_at,p.submitted_at,"
           "p.updated_at,CASE WHEN json_valid(p.operations) THEN json_array_length(p.operations) ELSE 0 END"
           " FROM proposals p LEFT JOIN communication_credentials cc ON cc.credential_id=p.actor_id")
# What GET /context lists of proposals (K18): the open ones, a draft or one submitted for review,
# oldest first, each without its changes. A closed one, and any proposal's changes, is one
# GET /proposals or `proposal show` away; the context is read at the start of every agent's chat.
OPEN = ("SELECT p.id,p.title,p.status,p.revision,p.actor_label,cc.agent_id,p.created_at,p.submitted_at,p.updated_at,"
        "CASE WHEN json_valid(p.operations) THEN json_array_length(p.operations) ELSE 0 END,p.actor_id"
        " FROM proposals p LEFT JOIN communication_credentials cc ON cc.credential_id=p.actor_id"
        " WHERE p.status IN ('draft','submitted') ORDER BY p.created_at,p.id")
APPLIED_TO = ("p.status='approved' AND p.id IN "
              "(SELECT proposal_id FROM proposal_targets WHERE target_type=? AND target_id=?)")
MADE_BY = ("p.actor_kind='agent' AND p.actor_id IN "
           "(SELECT credential_id FROM communication_credentials WHERE agent_id=?)")
# K10's quotas (consolidation step 3.4), counted from the live tables. An agent's are its identity's,
# under any of its credentials, as _same_principal reads ownership. The person has none.
DRAFT_LIMIT = 50
AWAITING_LIMIT = 100
EVIDENCE_FILES = 20
EVIDENCE_BYTES = 100_000_000
HELD_BY = ("p.actor_kind='agent' AND (p.actor_id=? OR p.actor_id IN (SELECT theirs.credential_id FROM communication_credentials"
           " theirs JOIN communication_credentials mine ON mine.agent_id=theirs.agent_id WHERE mine.credential_id=?))")


def _entry(row, number):
    status = row[2]
    return {"number": number, "id": row[0], "title": row[1], "status": status,
            "autoApproved": bool(row[3]), "actorLabel": row[4], "agentId": row[5],
            "createdAt": row[6], "submittedAt": None if status == "draft" else row[7],
            "appliedAt": row[8] if status == "approved" else None, "updatedAt": row[8],
            "operationCount": row[9]}


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
    # A name a change uses must name a record an earlier change creates, of the kind it takes.
    problem = proposal_names.first_problem(value)
    if problem is not None:
        number, action, exc = problem
        raise _numbered(exc, number, len(value), action)
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
            "actorLabel": row["actor_label"], "actorDirectory": row["actor_directory"],
            "operations": json.loads(row["operations"]),
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


def can_see(conn, row, actor):
    """Whether the caller may see this proposal: its row needs status, actor_id and submitted_at.

    Agents see other agents' proposals only once submitted; withdrawing an unsubmitted draft keeps it
    private. History asks this too (visible_history), so a tie's old history never shows a draft to
    another agent.
    """
    if actor["kind"] != "agent" or row["status"] in {"submitted", "approved", "rejected"} or _same_principal(conn, row["actor_id"], actor):
        return True
    # submitted_at is set by every submit and never cleared, reopening included.
    return row["status"] == "withdrawn" and row["submitted_at"] is not None


def _visible_events(conn, events: list[dict], actor: dict) -> list[dict]:
    """History as the caller may see it. Until v29 dropped agent ties, a tie's history (agent.link and
    agent.unlink) was written on its agent too, so an agent reading another agent's history would
    otherwise learn of a draft proposal it may not see: such entries are left out for agents, as the
    draft's own history is (can_see)."""
    if actor["kind"] != "agent":
        return events

    def proposal(entry: dict) -> str | None:
        if entry.get("kind") not in {"agent.link", "agent.unlink"}:
            return None
        state = entry.get("after") or entry.get("before")
        if not isinstance(state, dict) or state.get("targetType") != "proposal":
            return None
        return str(state.get("targetId"))

    named = {identity for identity in map(proposal, events) if identity}
    seen = {row["id"] for row in rows_in(conn, "SELECT id,status,actor_id,submitted_at FROM proposals WHERE id IN ({})", named)
            if can_see(conn, row, actor)} if named else set()
    return [entry for entry in events if (identity := proposal(entry)) is None or identity in seen]


def visible_history(conn, entity_id: str, limit: int, actor: dict) -> list[dict]:
    """A record's latest history entries the caller may see, `limit` of them where it has that many: the
    page fills up past entries left out, so its length says nothing about them."""
    window = limit
    while True:
        entries = read_events(conn, entity_id, window)
        shown = _visible_events(conn, entries, actor)
        if len(shown) >= limit or len(entries) < window:
            return shown[:limit]
        window *= 2


def open_proposals(conn, actor, limit, offset=0) -> dict:
    """The open proposals the caller may see, as the context lists them (K18): a page of summaries
    with the number of changes each holds, and how many there are. can_see decides, as for every
    other read, so an agent never finds another agent's draft here."""
    visible = [row for row in conn.execute(OPEN).fetchall()
               if can_see(conn, {"status": row[2], "actor_id": row[10], "submitted_at": row[7]}, actor)]
    return {"items": [{"id": row[0], "title": row[1], "status": row[2], "revision": row[3], "actorLabel": row[4],
                       "agentId": row[5], "createdAt": row[6], "submittedAt": None if row[2] == "draft" else row[7],
                       "updatedAt": row[8], "operationCount": row[9]} for row in visible[offset:offset + limit]],
            "total": len(visible)}


def _visible(conn, row, actor):
    if not can_see(conn, row, actor):
        raise AppError(404, "not_found", "Proposal not found")


def _held(conn, actor, status):
    """How many proposals in this status the calling agent holds, under any of its credentials."""
    return conn.execute("SELECT count(*) FROM proposals p WHERE p.status=? AND " + HELD_BY,
                        (status, actor["id"], actor["id"])).fetchone()[0]


def _room_for_a_draft(conn, actor):
    """A new or reopened draft of an agent's takes a place among its drafts (K10)."""
    if actor["kind"] == "agent" and _held(conn, actor, "draft") >= DRAFT_LIMIT:
        raise AppError(429, "agent_limit", f"An agent keeps at most {DRAFT_LIMIT} drafts; submit or withdraw one first")


def _records(conn, ids):
    """The proposals with these IDs, whole and in the order given."""
    found: dict[str, dict] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        found.update((row["id"], _record(row)) for row in conn.execute(
            SELECT + f" WHERE p.id IN ({','.join('?' * len(chunk))})", chunk))
    return [found[identifier] for identifier in ids]


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

    def list(self, actor, status=None, limit=None, offset=0):
        """The proposals the caller may see, oldest first: a page of `limit` from `offset`, or with no
        limit every one. Visibility is decided on each row's status and owner alone,
        before the page's proposals are read whole. Both reads see one snapshot, so a proposal that
        turns back into a draft between them is never shown whole to another agent."""
        if status is not None and status not in {"draft", "submitted", "approved", "rejected", "withdrawn"}:
            raise AppError(422, "validation", "Invalid proposal status")

        def read(conn):
            rows = conn.execute("SELECT p.id,p.status,p.actor_id,p.submitted_at FROM proposals p"
                                + (" WHERE p.status=?" if status else "") + " ORDER BY p.created_at,p.id",
                                (status,) if status else ()).fetchall()
            visible = [row["id"] for row in rows if can_see(conn, row, actor)]
            page = visible[offset:] if limit is None else visible[offset:offset + limit]
            return {"items": _records(conn, page), "total": len(visible)}
        return self.db.snapshot(read)

    def detail(self, identifier, actor):
        with self.db.read() as conn:
            row = _row(conn, identifier)
            _visible(conn, row, actor)
            return _record(row)

    def applied_to(self, target_type, target_id, limit=50, offset=0):
        """The proposals applied to a work item or roadmap, in the order they were applied, with
        the numbers and actions of the changes made to it. Approved proposals are visible to
        every caller, so the list is the same for the person and for agents."""
        def read(conn):
            table, missing = {"item": ("work_items", "Work item not found"), "roadmap": ("roadmaps", "Roadmap not found")}[target_type]
            if conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (target_id,)).fetchone() is None:
                raise AppError(404, "not_found", missing)
            total = conn.execute("SELECT count(*) FROM proposals p WHERE " + APPLIED_TO, (target_type, target_id)).fetchone()[0]
            rows = conn.execute(HISTORY + " WHERE " + APPLIED_TO + " ORDER BY p.updated_at DESC,p.id DESC LIMIT ? OFFSET ?",
                                (target_type, target_id, limit, offset)).fetchall()
            changes: dict[str, list[dict]] = {row[0]: [] for row in rows}
            if rows:
                for proposal, number, action in conn.execute(
                        "SELECT proposal_id,operation,action FROM proposal_targets WHERE target_type=? AND target_id=? "
                        f"AND proposal_id IN ({','.join('?' * len(rows))}) ORDER BY operation",
                        (target_type, target_id, *changes)):
                    changes[proposal].append({"number": number, "action": action})
            return {"items": [{**_entry(row, total - offset - place), "changes": changes[row[0]]}
                              for place, row in enumerate(rows)], "total": total}
        return self.db.snapshot(read)

    def from_agent(self, agent_id, limit=50, offset=0):
        """An agent identity's proposals, under any of its credentials, in the order it made them."""
        def read(conn):
            if conn.execute("SELECT 1 FROM communication_agents WHERE id=?", (agent_id,)).fetchone() is None:
                raise AppError(404, "not_found", "Agent not found")
            total = conn.execute("SELECT count(*) FROM proposals p WHERE " + MADE_BY, (agent_id,)).fetchone()[0]
            rows = conn.execute(HISTORY + " WHERE " + MADE_BY + " ORDER BY p.created_at DESC,p.id DESC LIMIT ? OFFSET ?",
                                (agent_id, limit, offset)).fetchall()
            return {"items": [_entry(row, total - offset - place) for place, row in enumerate(rows)], "total": total}
        return self.db.snapshot(read)

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
            _room_for_a_draft(connection, actor)
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
            if verb == "reopen":
                _room_for_a_draft(connection, actor)
            # With approval off a submitted proposal is applied at once, and none waits for review.
            if (verb == "submit" and actor["kind"] == "agent" and approval_required(connection)
                    and _held(connection, actor, "submitted") >= AWAITING_LIMIT):
                raise AppError(429, "agent_limit", f"An agent has at most {AWAITING_LIMIT} proposals awaiting review; "
                               "wait until the person reviews some")
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
        # GitHub references may link to a chat created or updated elsewhere in the same proposal, so
        # they apply after the other changes.
        def stage(operation: dict) -> int:
            return 1 if operation["action"].startswith("github.") else 0

        ordered = sorted(enumerate(operations, 1), key=lambda pair: stage(pair[1]))
        previous = self._applying_id
        self._applying_id = row["id"]
        # The records this proposal's changes have made so far, by the names it gave them. A name is
        # used only after the change declaring it, and these stages keep that order: only another
        # GitHub reference's change can name a GitHub reference.
        names: dict[str, str] = {}
        try:
            for number, operation in ordered:
                try:
                    resolved, name = proposal_names.resolve(operation["action"], operation["payload"], names)
                    action, payload = _scratchpad_landing(connection, operation["action"], resolved)
                    if action == "attachment.create":
                        # The file is this proposal's own evidence, which only Worklog names.
                        payload = {**payload, "proposalId": row["id"]}
                    result = self.dispatch(connection, proposer, action, payload)
                except AppError as exc:
                    raise _numbered(exc, number, len(operations), operation["action"]) from exc
                made = result.get("id") if isinstance(result, dict) else None
                if name is not None and isinstance(made, str):
                    names[name] = made
                # Read from the change as proposed, as the v16 upgrade reads it, with its names
                # resolved: a note that landed in a scratchpad counts toward its work item either way.
                record_id = made if operation["action"] in APPLIED_TO_RECORD else None
                proposal_targets.record(connection, row["id"], number, operation["action"], resolved, record_id)
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
        """The proposal's plan changes whose revision no longer matches. Each is checked against the
        revision its record has once the proposal's earlier changes apply (plans.Forecast), so a
        change that expects one more after an earlier change in the same proposal is current (K4).
        currentRevision is that revision."""
        operations = json.loads(row["operations"])
        forecast = Forecast(conn)
        conflicts = []
        for number, op in enumerate(operations, 1):
            action, payload = op["action"], op["payload"]
            if action in {"roadmap.revise", "roadmap.move", "roadmap.archive", "roadmap.recover"}:
                rm = forecast.roadmap(payload.get("id") or payload.get("roadmapId"))
                if rm is None:
                    continue
                if rm["revision"] != payload.get("revision"):
                    if roadmap_id is None or rm["id"] == roadmap_id:
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
                elif action == "roadmap.revise":
                    forecast.revise(rm, payload)
                else:
                    forecast.change_roadmap(rm, action)
            elif action in {"progress.set", "step.update"}:
                st = forecast.step(payload.get("id") or payload.get("stepId"))
                if st is None:
                    continue
                if st["revision"] != payload.get("revision"):
                    if roadmap_id is None or st["roadmap_id"] == roadmap_id:
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
                elif action == "progress.set":
                    forecast.set_progress(st)
                else:
                    forecast.update_step(st, payload)
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

    def check_upload(self, conn, identifier, actor, size=0):
        """Whether the caller may add a file of `size` bytes to this proposal's evidence. An agent
        adds it only to its own draft, within K10's 20 files and 100 MB a proposal, archived files
        counted, since they stay in the file."""
        row = _row(conn, identifier)
        _visible(conn, row, actor)
        if actor["kind"] == "agent":
            _owner(conn, row, actor)
            if row["status"] != "draft":
                raise AppError(409, "conflict", "Agent evidence uploads require an owned draft")
            files, held = conn.execute("SELECT count(*),coalesce(sum(size),0) FROM attachments"
                                       " WHERE owner_type='proposal' AND owner_id=?", (identifier,)).fetchone()
            if files >= EVIDENCE_FILES:
                raise AppError(429, "agent_limit", f"A proposal holds at most {EVIDENCE_FILES} evidence files")
            if held + size > EVIDENCE_BYTES:
                raise AppError(429, "agent_limit", f"A proposal's evidence holds at most {EVIDENCE_BYTES:,} bytes in all")
        return _record(row)
