"""Versioned roadmap trees."""

from __future__ import annotations

import json
import uuid
from bisect import bisect_left

from .common import AppError, event, now, read_events, require_revision, uid

STATUSES = {"open", "doing", "blocked", "done"}


def _text(value, field, default=None, maximum=1_000_000):
    if value is None and default is not None:
        value = default
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum:
        raise AppError(422, "validation", f"Invalid {field}")
    return value


def _title(value):
    value = _text(value, "title", maximum=1000).strip()
    if not value:
        raise AppError(422, "validation", "Title is required")
    return value


def _row(conn, identifier):
    row = conn.execute("SELECT * FROM roadmaps WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise AppError(404, "not_found", "Roadmap not found")
    return row


def _roadmap(row):
    return {"id": row["id"], "title": row["title"], "overview": row["overview"],
            "parentId": row["parent_id"], "revision": row["revision"],
            "archived": bool(row["archived"]),
            "createdAt": row["created_at"], "updatedAt": row["updated_at"]}


def _parent(conn, identifier, parent):
    """Validate a new parent: an active roadmap that is neither this one nor below it."""
    if parent is None:
        return None
    if not isinstance(parent, str):
        raise AppError(422, "validation", "parentId must be a roadmap id or null")
    row = conn.execute("SELECT archived FROM roadmaps WHERE id=?", (parent,)).fetchone()
    if row is None:
        raise AppError(422, "validation", "Parent roadmap does not exist")
    if row["archived"]:
        raise AppError(409, "conflict", "Recover the parent roadmap before nesting under it")
    ancestor, seen = parent, set()
    while ancestor is not None and ancestor not in seen:
        if ancestor == identifier:
            raise AppError(422, "validation", "A roadmap cannot be nested under itself or its sub-roadmaps")
        seen.add(ancestor)
        ancestor = conn.execute("SELECT parent_id FROM roadmaps WHERE id=?", (ancestor,)).fetchone()[0]
    return parent


def _step(row):
    return {"id": row["id"], "title": row["title"], "description": row["description"],
            "parentId": row["parent_id"], "position": row["position"],
            "status": row["status"], "revision": row["revision"],
            "retired": bool(row["retired"])}


def _kept_in_order(values):
    """The values of one longest increasing run through `values`, distinct numbers: the most of them
    that keep their order among themselves. Patience sorting, so O(n log n); of two runs as long,
    the one ending lower."""
    tails: list[int] = []  # the lowest last value of a run of each length so far
    ends: list[int] = []  # where in `values` that run ends
    before = [-1] * len(values)
    for index, value in enumerate(values):
        length = bisect_left(tails, value)
        if length:
            before[index] = ends[length - 1]
        if length == len(tails):
            tails.append(value)
            ends.append(index)
        else:
            tails[length], ends[length] = value, index
    kept, index = set(), ends[-1] if ends else -1
    while index >= 0:
        kept.add(values[index])
        index = before[index]
    return kept


def _moved(rows, steps):
    """The stored steps a save moves: each it gives another parent, and of the steps that stay under
    the same parent, the fewest whose new places account for their new order. Numbering positions
    afresh, or adding or retiring a step, moves no other step (K4)."""
    active = {row["id"]: row for row in rows if not row["retired"]}
    moved: set[str] = set()
    staying: dict[str | None, list[dict]] = {}
    for step in steps:
        old = active.get(step["id"])
        if old is None:
            continue  # a new or a restored step, which the save writes anyway
        if old["parent_id"] != step["parentId"]:
            moved.add(step["id"])
        else:
            staying.setdefault(step["parentId"], []).append(step)
    for siblings in staying.values():
        ids = [step["id"] for step in siblings]
        # Steps show in (position, id) order, before the save and after it.
        rank = {sid: n for n, sid in enumerate(sorted(ids, key=lambda sid: (active[sid]["position"], sid)))}
        after = [step["id"] for step in sorted(siblings, key=lambda step: (step["position"], step["id"]))]
        kept = _kept_in_order([rank[sid] for sid in after])
        moved.update(sid for sid in after if rank[sid] not in kept)
    return moved


def changed_by(rows, steps):
    """The stored steps a save with these steps changes (K4): each it retires or restores, gives a
    new title or description, or moves (_moved). Every other step keeps its revision and gets no
    event, whatever its position number becomes: the roadmap's revision guards the order."""
    wanted = {step["id"]: step for step in steps}
    changed = _moved(rows, steps)
    for row in rows:
        step = wanted.get(row["id"])
        if step is None:
            if not row["retired"]:
                changed.add(row["id"])
        elif row["retired"] or (row["title"], row["description"]) != (step["title"], step["description"]):
            changed.add(row["id"])
    return changed


class Forecast:
    """What a proposal's plan changes leave, worked out in order without writing anything: each
    roadmap's revision, title and overview, and each step's revision, text, parent, place and
    retired flag. The stale check reads it, so a change expects the revisions the proposal's earlier
    changes leave, as approving it applies them (K4). A change approving would refuse, such as a
    step list that is not one tree, leaves everything as it was."""

    def __init__(self, conn):
        self.conn = conn
        self._roadmaps: dict[str, dict | None] = {}
        self._steps: dict[str, dict[str, dict]] = {}  # roadmap ID -> step ID -> row

    def roadmap(self, identifier):
        """The roadmap as the earlier changes leave it, or None when there is none."""
        if not isinstance(identifier, str):
            return None
        if identifier not in self._roadmaps:
            row = self.conn.execute("SELECT id,title,overview,revision,archived FROM roadmaps WHERE id=?",
                                    (identifier,)).fetchone()
            self._roadmaps[identifier] = dict(row) if row else None
        return self._roadmaps[identifier]

    def _rows(self, roadmap_id):
        if roadmap_id not in self._steps:
            self._steps[roadmap_id] = {row["id"]: dict(row) for row in self.conn.execute(
                "SELECT * FROM steps WHERE roadmap_id=?", (roadmap_id,))}
        return self._steps[roadmap_id]

    def step(self, identifier):
        """The step as the earlier changes leave it, a new one among them, or None."""
        if not isinstance(identifier, str):
            return None
        for rows in self._steps.values():
            if identifier in rows:
                return rows[identifier]
        row = self.conn.execute("SELECT roadmap_id FROM steps WHERE id=?", (identifier,)).fetchone()
        return self._rows(row["roadmap_id"]).get(identifier) if row else None

    def _open(self, roadmap):
        return roadmap is not None and not roadmap["archived"]

    def revise(self, roadmap, payload):
        """A revise whose revision checks: what it changes (changed_by) takes one more revision."""
        if not self._open(roadmap):
            return
        rows = self._rows(roadmap["id"])
        try:
            title, overview = _title(payload.get("title")), _text(payload.get("overview"), "overview", "")
            steps = PlanService.validate_steps(self.conn, roadmap["id"], payload.get("steps"))
        except AppError:
            return
        changed = changed_by(list(rows.values()), steps)
        wanted = {step["id"] for step in steps}
        for sid, row in rows.items():
            if sid not in wanted and not row["retired"]:
                row.update(retired=1, revision=row["revision"] + 1)
        added = False
        for step in steps:
            row = rows.get(step["id"])
            if row is None:
                rows[step["id"]] = {"id": step["id"], "roadmap_id": roadmap["id"], "title": step["title"],
                                    "description": step["description"], "parent_id": step["parentId"],
                                    "position": step["position"], "status": "open", "revision": 1, "retired": 0}
                added = True
            elif step["id"] in changed:
                row.update(title=step["title"], description=step["description"], parent_id=step["parentId"],
                           position=step["position"], retired=0, revision=row["revision"] + 1)
            else:
                row["position"] = step["position"]
        if changed or added or (title, overview) != (roadmap["title"], roadmap["overview"]):
            roadmap.update(title=title, overview=overview, revision=roadmap["revision"] + 1)

    def update_step(self, step, payload):
        """A step.update whose revision checks: new text raises the step's revision and its roadmap's."""
        roadmap = self.roadmap(step["roadmap_id"])
        if step["retired"] or not self._open(roadmap):
            return
        try:
            title = step["title"] if payload.get("title") is None else _title(payload["title"])
            description = step["description"] if payload.get("description") is None else _text(payload["description"], "description")
        except AppError:
            return
        if rewrites(step, {"title": title, "description": description}):
            step.update(title=title, description=description, revision=step["revision"] + 1)
            roadmap["revision"] += 1

    def set_progress(self, step):
        """A progress.set whose revision checks, on a leaf: the step's revision rises."""
        roadmap = self.roadmap(step["roadmap_id"])
        leaf = not any(row["parent_id"] == step["id"] and not row["retired"]
                       for row in self._rows(step["roadmap_id"]).values())
        if not step["retired"] and self._open(roadmap) and leaf:
            step["revision"] += 1

    def change_roadmap(self, roadmap, action):
        """A move, archive or recover whose revision checks: the roadmap's revision rises."""
        if action == "roadmap.move" and not self._open(roadmap):
            return
        roadmap["revision"] += 1
        if action in {"roadmap.archive", "roadmap.recover"}:
            roadmap["archived"] = int(action == "roadmap.archive")


def rewrites(row, payload):
    """Whether a step.update with this payload changes the stored step's text: a title or description
    it gives that differs from the step's (a title counts as trimmed). One that changes nothing
    records nothing (K4)."""
    title, description = payload.get("title"), payload.get("description")
    if isinstance(title, str):
        title = title.strip()
    return ((row["title"] if title is None else title, row["description"] if description is None else description)
            != (row["title"], row["description"]))


def _steps(conn, identifier, retired=False):
    rows = conn.execute("SELECT * FROM steps WHERE roadmap_id=? ORDER BY position,id",
                        (identifier,)).fetchall()
    steps = [_step(row) for row in rows if retired or not row["retired"]]
    active = {s["id"]: s for s in steps if not s["retired"]}
    children: dict[str | None, list[dict]] = {}
    for step in active.values():
        children.setdefault(step["parentId"], []).append(step)

    def derive(step):
        descendants = children.get(step["id"], [])
        if descendants:
            states = [derive(child) for child in descendants]
            step["status"] = ("done" if all(s == "done" for s in states) else
                              "blocked" if "blocked" in states else
                              "doing" if any(s in {"doing", "done"} for s in states) else "open")
        return step["status"]

    for root in children.get(None, []):
        derive(root)
    return steps


def step_tree(conn, roadmap_id):
    """A roadmap's steps, retired ones included, with each parent's progress derived from its leaves
    as the roadmap's page shows it."""
    return _steps(conn, roadmap_id, retired=True)


def outline_order(steps):
    """Steps depth-first, each before its children and siblings in (position, id) order, as the
    outline shows them. A step whose parent is not among them starts a branch of its own, and so does
    any step a broken parent chain leaves unreached, so each comes exactly once."""
    ids = {step["id"] for step in steps}
    by_place = sorted(steps, key=lambda step: (step["position"], step["id"]))
    children: dict[str | None, list[dict]] = {}
    for step in by_place:
        children.setdefault(step["parentId"] if step["parentId"] in ids else None, []).append(step)
    ordered: list[dict] = []
    seen: set[str] = set()
    for root in [*children.get(None, []), *by_place]:
        stack = [root]
        while stack:
            step = stack.pop()
            if step["id"] in seen:
                continue
            seen.add(step["id"])
            ordered.append(step)
            stack.extend(reversed(children.get(step["id"], [])))
    return ordered


def step_place(steps, identity):
    """Where a step sits among its roadmap's steps: those above it, from the top down, and the others
    under its parent in outline order, retired ones aside. Each is named by its title and status."""
    by_id = {step["id"]: step for step in steps}
    current = by_id[identity]
    ancestors: list[dict] = []
    seen, parent = {identity}, current["parentId"]
    while parent in by_id and parent not in seen:
        seen.add(parent)
        step = by_id[parent]
        ancestors.insert(0, {"id": step["id"], "title": step["title"], "status": step["status"]})
        parent = step["parentId"]
    siblings = [{"id": step["id"], "title": step["title"], "status": step["status"], "position": step["position"]}
                for step in sorted(steps, key=lambda step: (step["position"], step["id"]))
                if step["parentId"] == current["parentId"] and step["id"] != identity and not step["retired"]]
    return {"ancestors": ancestors, "siblings": siblings}


class PlanService:
    def __init__(self, db):
        self.db = db

    def list(self, archived=False):
        with self.db.read() as conn:
            items = [_roadmap(row) for row in conn.execute(
                "SELECT * FROM roadmaps WHERE archived=? ORDER BY created_at,id", (int(archived),))]
            return {"items": items, "total": len(items)}

    def _detail(self, conn, identifier):
        result = _roadmap(_row(conn, identifier))
        result["steps"] = _steps(conn, identifier, retired=True)
        result["versions"] = [{"number": r["number"], "createdAt": r["created_at"],
                               "note": r["note"]} for r in conn.execute(
            "SELECT * FROM roadmap_versions WHERE roadmap_id=? ORDER BY number", (identifier,))]
        result.update(notes=[], attachments=[], links=[], chats=[], github=[], questions=[], decisions=[], history=[])
        return result

    def detail(self, identifier):
        with self.db.read() as conn:
            result = self._detail(conn, identifier)
            result.update(self.family(conn, identifier))
            return result

    def outline(self, identifier):
        """A roadmap's plan without its text, small enough for an agent to read whole: each step in
        it, retired ones aside, in outline order, with its title, status (a parent's derived from its
        leaves, as the page shows it), parent and position."""
        with self.db.read() as conn:
            roadmap = _roadmap(_row(conn, identifier))
            steps = [{key: step[key] for key in ("id", "title", "status", "parentId", "position")}
                     for step in outline_order(_steps(conn, identifier))]
            return {"id": roadmap["id"], "title": roadmap["title"], "archived": roadmap["archived"],
                    "revision": roadmap["revision"], "steps": steps}

    @staticmethod
    def family(conn, identifier):
        """Ancestors from the top level down, and direct sub-roadmaps in list order."""
        ancestors: list[dict] = []
        seen = {identifier}
        parent = _row(conn, identifier)["parent_id"]
        while parent is not None and parent not in seen:
            seen.add(parent)
            row = _row(conn, parent)
            ancestors.insert(0, {"id": row["id"], "title": row["title"], "archived": bool(row["archived"])})
            parent = row["parent_id"]
        children = [{"id": r["id"], "title": r["title"], "archived": bool(r["archived"])} for r in conn.execute(
            "SELECT id,title,archived FROM roadmaps WHERE parent_id=? ORDER BY created_at,id", (identifier,))]
        return {"ancestors": ancestors, "children": children}

    def version(self, identifier, number):
        with self.db.read() as conn:
            _row(conn, identifier)
            row = conn.execute("SELECT * FROM roadmap_versions WHERE roadmap_id=? AND number=?",
                               (identifier, number)).fetchone()
            if row is None:
                raise AppError(404, "not_found", "Roadmap revision not found")
            return {"number": row["number"], "createdAt": row["created_at"],
                    "note": row["note"], **json.loads(row["snapshot"])}

    @staticmethod
    def get_step(conn, identifier):
        row = conn.execute("SELECT * FROM steps WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise AppError(404, "not_found", "Step not found")
        return row

    def history(self, step_id):
        with self.db.read() as conn:
            self.get_step(conn, step_id)
            items = read_events(conn, step_id)
            return {"items": items, "total": len(items)}

    @staticmethod
    def validate_steps(conn, identifier, steps):
        """A step list as a save takes it: each step's ID, title, description, parent and position,
        checked, with a new step given an ID. A step marked "retired": true, as `roadmap show` marks
        one, stays out of the plan as a step left out does. Refuses a list that is not one tree of
        this roadmap's."""
        if not isinstance(steps, list) or len(steps) > 5000:
            raise AppError(422, "validation", "Steps must be a list of at most 5000 steps")
        normalized = []
        seen = set()
        for index, data in enumerate(steps):
            if not isinstance(data, dict):
                raise AppError(422, "validation", "Each step must be an object")
            retired = data.get("retired", False)
            if not isinstance(retired, bool):
                raise AppError(422, "validation", "A step's retired must be true or false")
            sid = data.get("id") or uid()
            try:
                uuid.UUID(sid)
            except (ValueError, TypeError, AttributeError):
                raise AppError(422, "validation", "Invalid step id") from None
            if sid in seen:
                raise AppError(422, "validation", "Duplicate step id")
            seen.add(sid)
            previous = conn.execute("SELECT roadmap_id FROM steps WHERE id=?", (sid,)).fetchone()
            if previous and previous["roadmap_id"] != identifier:
                raise AppError(409, "conflict", "Step belongs to another roadmap")
            if retired:
                # Sent back as read, a retired step stays retired rather than being restored.
                continue
            position = data.get("position", index)
            if type(position) is not int or position < 0:
                raise AppError(422, "validation", "Invalid step position")
            parent = data.get("parentId")
            if parent is not None and not isinstance(parent, str):
                raise AppError(422, "validation", "Step parentId must be a step id or null")
            normalized.append({"id": sid, "title": _title(data.get("title")),
                               "description": _text(data.get("description"), "description", ""),
                               "parentId": parent, "position": position})
        parents = {s["id"]: s["parentId"] for s in normalized}
        for sid, parent in parents.items():
            path = {sid}
            while parent is not None:
                if parent not in parents or parent in path:
                    raise AppError(422, "validation", "Step tree contains a missing parent or cycle")
                path.add(parent)
                if len(path) > 100:
                    raise AppError(422, "validation", "Step trees support at most 100 nested levels")
                parent = parents[parent]
        return normalized

    def _snapshot(self, conn, identifier, note):
        """Record the roadmap as it now stands as its next version, and return the version's number."""
        row = _row(conn, identifier)
        number = conn.execute("SELECT COALESCE(MAX(number),0)+1 FROM roadmap_versions WHERE roadmap_id=?",
                              (identifier,)).fetchone()[0]
        snapshot = {"title": row["title"], "overview": row["overview"],
                    "steps": _steps(conn, identifier)}
        conn.execute("INSERT INTO roadmap_versions(roadmap_id,number,created_at,note,snapshot) VALUES(?,?,?,?,?)",
                     (identifier, number, now(), note, json.dumps(snapshot)))
        return number

    def _save_steps(self, conn, actor, identifier, steps):
        """Make the roadmap's steps these, touching only the ones that change (K4, changed_by): a step
        left out is retired, and a new, changed or restored one is written. A step that only takes
        another position number, as when a step is added or retired before it, keeps its revision and
        gets no event. Returns the IDs of the steps changed."""
        rows = conn.execute("SELECT * FROM steps WHERE roadmap_id=?", (identifier,)).fetchall()
        stored = {row["id"]: row for row in rows}
        changes = changed_by(rows, steps)
        wanted = {s["id"] for s in steps}
        changed = []
        for old in rows:
            if old["id"] not in wanted and not old["retired"]:
                conn.execute("UPDATE steps SET retired=1,revision=revision+1 WHERE id=?", (old["id"],))
                event(conn, actor, "step.retire", old["id"], _step(old), _step(self.get_step(conn, old["id"])))
                changed.append(old["id"])
        for step in steps:
            old = stored.get(step["id"])
            if old is not None and old["id"] not in changes:
                if old["position"] != step["position"]:
                    conn.execute("UPDATE steps SET position=? WHERE id=?", (step["position"], step["id"]))
                continue
            if old:
                conn.execute("UPDATE steps SET title=?,description=?,parent_id=?,position=?,retired=0,revision=revision+1 WHERE id=?",
                             (step["title"], step["description"], step["parentId"], step["position"], step["id"]))
            else:
                conn.execute("INSERT INTO steps(id,roadmap_id,title,description,parent_id,position,status,revision,retired) "
                             "VALUES(?,?,?,?,?,?,?,?,?)",
                             (step["id"], identifier, step["title"], step["description"], step["parentId"], step["position"], "open", 1, 0))
            event(conn, actor, "step.restore" if old and old["retired"] else "step.revise" if old else "step.create",
                  step["id"], _step(old) if old else None, _step(self.get_step(conn, step["id"])))
            changed.append(step["id"])
        return changed

    def _update_step(self, conn, actor, payload):
        """One step's own title and description, checked against the step's revision, touching no
        other step and recording no version. The roadmap's revision guards its title, overview and
        step tree, step text included, so it rises as well: a structure save drafted before this
        change would otherwise write the old text back. Returns the step with its roadmap's ID."""
        identifier = payload.get("id")
        row = self.get_step(conn, identifier)
        require_revision(row, payload.get("revision"))
        if row["retired"] or _row(conn, row["roadmap_id"])["archived"]:
            raise AppError(409, "conflict", "Retired or archived steps cannot change")
        if payload.get("title") is None and payload.get("description") is None:
            raise AppError(422, "validation", "Give the step's title, its description or both")
        title = row["title"] if payload.get("title") is None else _title(payload["title"])
        description = row["description"] if payload.get("description") is None else _text(payload["description"], "description")
        if rewrites(row, {"title": title, "description": description}):
            conn.execute("UPDATE steps SET title=?,description=?,revision=revision+1 WHERE id=?",
                         (title, description, identifier))
            conn.execute("UPDATE roadmaps SET revision=revision+1,updated_at=? WHERE id=?", (now(), row["roadmap_id"]))
            event(conn, actor, "step.update", identifier, _step(row), _step(self.get_step(conn, identifier)))
        return {**_step(self.get_step(conn, identifier)), "roadmapId": row["roadmap_id"]}

    def apply(self, conn, actor, action, payload):
        if actor.get("kind") not in {"human", "agent"}:
            raise AppError(403, "forbidden", "Invalid actor")
        if action == "progress.set":
            identifier = payload.get("id") or payload.get("stepId")
            row = self.get_step(conn, identifier)
            require_revision(row, payload.get("revision"))
            roadmap = _row(conn, row["roadmap_id"])
            if row["retired"] or roadmap["archived"]:
                raise AppError(409, "conflict", "Retired or archived steps cannot change progress")
            if conn.execute("SELECT 1 FROM steps WHERE parent_id=? AND retired=0", (identifier,)).fetchone():
                raise AppError(422, "validation", "Parent progress is derived from its leaves")
            status = payload.get("status")
            if status not in STATUSES:
                raise AppError(422, "validation", "Invalid progress status")
            before = _step(row)
            conn.execute("UPDATE steps SET status=?,revision=revision+1 WHERE id=?", (status, identifier))
            # Progress is the step's, guarded by its revision: the roadmap's revision guards its
            # title, overview and step tree, which progress leaves alone (K4).
            conn.execute("UPDATE roadmaps SET updated_at=? WHERE id=?", (now(), row["roadmap_id"]))
            result = _step(self.get_step(conn, identifier))
            event(conn, actor, action, identifier, before, result)
            return result
        if action == "step.update":
            return self._update_step(conn, actor, payload)
        identifier = payload.get("id") or payload.get("roadmapId")
        if action == "roadmap.create":
            identifier = uid()
            title = _title(payload.get("title"))
            overview = _text(payload.get("overview"), "overview", "")
            steps = self.validate_steps(conn, identifier, payload.get("steps", []))
            parent = _parent(conn, identifier, payload.get("parentId"))
            stamp = now()
            conn.execute("INSERT INTO roadmaps(id,title,overview,revision,archived,created_at,updated_at,parent_id) VALUES(?,?,?,?,?,?,?,?)",
                         (identifier, title, overview, 1, 0, stamp, stamp, parent))
            note = "Created"
        else:
            row = _row(conn, identifier)
            require_revision(row, payload.get("revision"))
            if action in {"roadmap.archive", "roadmap.recover", "roadmap.move"}:
                before = self._detail(conn, identifier)
            if action in {"roadmap.archive", "roadmap.recover"}:
                conn.execute("UPDATE roadmaps SET archived=?,revision=revision+1,updated_at=? WHERE id=?",
                             (int(action == "roadmap.archive"), now(), identifier))
                result = self._detail(conn, identifier)
                event(conn, actor, action, identifier, before, result)
                return result
            if row["archived"]:
                raise AppError(409, "conflict", "Archived roadmaps must be recovered before editing")
            if action == "roadmap.move":
                # Placement is not plan content: no version is recorded and restores keep it.
                parent = _parent(conn, identifier, payload.get("parentId"))
                conn.execute("UPDATE roadmaps SET parent_id=?,revision=revision+1,updated_at=? WHERE id=?",
                             (parent, now(), identifier))
                result = self._detail(conn, identifier)
                event(conn, actor, action, identifier, before, result)
                return result
            if action == "roadmap.restore":
                version = conn.execute("SELECT snapshot FROM roadmap_versions WHERE roadmap_id=? AND number=?",
                                       (identifier, payload.get("number"))).fetchone()
                if version is None:
                    raise AppError(404, "not_found", "Roadmap revision not found")
                content = json.loads(version["snapshot"])
                note = f"Restored revision {payload.get('number')}"
            elif action == "roadmap.revise":
                content = payload
                note = _text(payload.get("note"), "note", "")
            else:
                raise AppError(422, "validation", "Unsupported roadmap action")
            title = _title(content.get("title"))
            overview = _text(content.get("overview"), "overview", "")
            steps = self.validate_steps(conn, identifier, content.get("steps", []))
        changed = self._save_steps(conn, actor, identifier, steps)
        if action != "roadmap.create":
            if not changed and (title, overview) == (row["title"], row["overview"]):
                # A save that changes nothing records nothing: no revision, version or event (K4).
                # Position numbers it renumbers without moving a step are stored all the same, with
                # no record of their own: the order they give is the one shown before.
                return self._detail(conn, identifier)
            conn.execute("UPDATE roadmaps SET title=?,overview=?,revision=revision+1,updated_at=? WHERE id=?",
                         (title, overview, now(), identifier))
        number = self._snapshot(conn, identifier, note)
        # The version holds the whole plan, and each changed step's own event its before and after,
        # so the roadmap's event names them rather than holding two copies of the tree (B-02).
        event(conn, actor, action, identifier, None, {"versionNumber": number, "changedStepIds": changed})
        return self._detail(conn, identifier)
