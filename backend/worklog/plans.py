"""Versioned roadmap trees."""

from __future__ import annotations

import json
import uuid
from bisect import bisect_left

from .common import AppError, event, now, read_events, require_revision, uid
from .repository_links import (
    linked_repositories,
    replace_repository_associations,
    roadmap_step_repositories,
)

STATUSES = {"open", "doing", "blocked", "done"}
WORK_CLASSES = {"", "stabilization", "critical_path", "standard", "refactor", "gate"}
# A step's text: what a save, a step.update or a restore writes, and what counts as changing it.
TEXT = ("title", "description", "outcome")
OUTCOME_LIMIT = 100_000


def _work_class(value, default=""):
    if value is None:
        return default
    if not isinstance(value, str) or value not in WORK_CLASSES:
        raise AppError(422, "validation", "Invalid workClass")
    return value


def _step_repo_ids(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 100 or not all(isinstance(i, str) and i for i in value):
        raise AppError(422, "validation", "Choose up to 100 repositories")
    return sorted(set(value))


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
            "outcome": row["outcome"], "parentId": row["parent_id"], "position": row["position"],
            "status": row["status"], "revision": row["revision"],
            "retired": bool(row["retired"]), "workClass": row["work_class"],
            "repositoryIds": [], "waveIds": []}


def _roadmap_step_waves(conn, roadmap_id):
    """All non-archived wave memberships on a roadmap, plus each step's active/planning wave ID, in 1 query."""
    waves_by_step: dict[str, list[str]] = {}
    active_wave: dict[str, str] = {}
    for step_id, wave_id, status in conn.execute(
        "SELECT ws.step_id,ws.wave_id,w.status FROM wave_steps ws "
        "JOIN waves w ON w.id=ws.wave_id "
        "WHERE w.roadmap_id=? AND w.archived=0 "
        "ORDER BY w.status='active' DESC,w.position,w.created_at,w.id",
        (roadmap_id,),
    ):
        waves_by_step.setdefault(step_id, []).append(wave_id)
        if status in {"active", "planning"} and step_id not in active_wave:
            active_wave[step_id] = wave_id
    return waves_by_step, active_wave


def _hydrate_step(conn, step):
    """Attach repositoryIds and waveIds to one step record."""
    repos = linked_repositories(conn, "step", [step["id"]]).get(step["id"], [])
    waves = [
        r[0]
        for r in conn.execute(
            "SELECT ws.wave_id FROM wave_steps ws JOIN waves w ON w.id=ws.wave_id "
            "WHERE ws.step_id=? AND w.archived=0 ORDER BY w.status='active' DESC,w.position,w.created_at,w.id",
            (step["id"],),
        )
    ]
    return {**step, "repositoryIds": repos, "waveIds": waves}


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


def keep_outcomes(rows, steps):
    """Give each step of a save that names no outcome or workClass the one stored for it, or none for a
    new step, so a save drafted without them leaves each step's stored value as it is. Returns the steps."""
    stored = {row["id"]: row for row in rows}
    for step in steps:
        if step["outcome"] is None:
            step["outcome"] = stored[step["id"]]["outcome"] if step["id"] in stored else ""
        if step.get("workClass") is None:
            step["workClass"] = stored[step["id"]]["work_class"] if step["id"] in stored else ""
    return steps


def changed_by(rows, steps, stored_repos=None):
    """The stored steps a save with these steps changes (K4): each it retires or restores, gives a
    new title, description, outcome, workClass or repositoryIds, or moves (_moved). Every other step
    keeps its revision and gets no event, whatever its position number becomes: the roadmap's revision
    guards the order."""
    wanted = {step["id"]: step for step in steps}
    changed = _moved(rows, steps)
    for row in rows:
        step = wanted.get(row["id"])
        if step is None:
            if not row["retired"]:
                changed.add(row["id"])
        elif (
            row["retired"]
            or any(row[key] != step[key] for key in TEXT)
            or row["work_class"] != step.get("workClass", "")
            or (
                stored_repos is not None
                and step.get("repositoryIds") is not None
                and step["repositoryIds"] != stored_repos.get(row["id"], [])
            )
        ):
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
        self._step_repos: dict[str, dict[str, list[str]]] = {}  # roadmap ID -> step ID -> repo IDs

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

    def _repos(self, roadmap_id):
        if roadmap_id not in self._step_repos:
            self._step_repos[roadmap_id] = roadmap_step_repositories(self.conn, roadmap_id)
        return self._step_repos[roadmap_id]

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
        repos = self._repos(roadmap["id"])
        try:
            title, overview = _title(payload.get("title")), _text(payload.get("overview"), "overview", "")
            steps = keep_outcomes(rows.values(), PlanService.validate_steps(self.conn, roadmap["id"], payload.get("steps")))
        except AppError:
            return
        changed = changed_by(list(rows.values()), steps, repos)
        wanted = {step["id"] for step in steps}
        for sid, row in rows.items():
            if sid not in wanted and not row["retired"]:
                row.update(retired=1, revision=row["revision"] + 1)
        added = False
        for step in steps:
            row = rows.get(step["id"])
            if row is None:
                rows[step["id"]] = {"id": step["id"], "roadmap_id": roadmap["id"], "title": step["title"],
                                    "description": step["description"], "outcome": step["outcome"],
                                    "parent_id": step["parentId"], "work_class": step["workClass"],
                                    "position": step["position"], "status": "open", "revision": 1, "retired": 0}
                if step.get("repositoryIds") is not None:
                    repos[step["id"]] = list(step["repositoryIds"])
                added = True
            elif step["id"] in changed:
                row.update(title=step["title"], description=step["description"], outcome=step["outcome"],
                           parent_id=step["parentId"], work_class=step["workClass"],
                           position=step["position"], retired=0, revision=row["revision"] + 1)
                if step.get("repositoryIds") is not None:
                    repos[step["id"]] = list(step["repositoryIds"])
            else:
                row["position"] = step["position"]
        if changed or added or (title, overview) != (roadmap["title"], roadmap["overview"]):
            roadmap.update(title=title, overview=overview, revision=roadmap["revision"] + 1)

    def update_step(self, step, payload):
        """A step.update whose revision checks: new text, repos or workClass raises the step's and roadmap's revision."""
        roadmap = self.roadmap(step["roadmap_id"])
        if step["retired"] or not self._open(roadmap):
            return
        try:
            text = step_text(step, payload)
            wc = _work_class(payload.get("workClass"), step["work_class"]) if payload.get("workClass") is not None else step["work_class"]
            rids = _step_repo_ids(payload.get("repositoryIds"))
        except AppError:
            return
        repos = self._repos(step["roadmap_id"])
        current_repos = repos.get(step["id"], [])
        if rewrites(step, text) or wc != step["work_class"] or (rids is not None and rids != current_repos):
            step.update(**text, work_class=wc, revision=step["revision"] + 1)
            if rids is not None:
                repos[step["id"]] = list(rids)
            roadmap["revision"] += 1

    def create_step(self, roadmap, payload):
        """A step.create whose revision checks: adds the step and bumps the roadmap's revision."""
        if not self._open(roadmap):
            return
        rows = self._rows(roadmap["id"])
        repos = self._repos(roadmap["id"])
        try:
            title = _title(payload.get("title"))
            description = _text(payload.get("description"), "description", "")
            outcome = _text(payload.get("outcome"), "outcome", "", maximum=OUTCOME_LIMIT)
            wc = _work_class(payload.get("workClass"), "")
            rids = _step_repo_ids(payload.get("repositoryIds")) or []
        except AppError:
            return
        parent_id = payload.get("parentId")
        if parent_id is not None and (parent_id not in rows or rows[parent_id]["retired"]):
            return
        sid = payload.get("id") or uid()
        if sid in rows:
            return
        pos = payload.get("position")
        if pos is None:
            pos = max((r["position"] for r in rows.values() if r["parent_id"] == parent_id and not r["retired"]), default=-1) + 1
        rows[sid] = {"id": sid, "roadmap_id": roadmap["id"], "title": title, "description": description,
                     "outcome": outcome, "parent_id": parent_id, "position": pos, "status": "open",
                     "work_class": wc, "revision": 1, "retired": 0}
        repos[sid] = list(rids)
        roadmap["revision"] += 1

    def split_step(self, step, payload):
        """A step.split whose revision checks on an active leaf: inserts children and bumps parent & roadmap revisions."""
        roadmap = self.roadmap(step["roadmap_id"])
        rows = self._rows(step["roadmap_id"])
        repos = self._repos(step["roadmap_id"])
        leaf = not any(row["parent_id"] == step["id"] and not row["retired"] for row in rows.values())
        children = payload.get("children")
        if step["retired"] or not self._open(roadmap) or not leaf or not isinstance(children, list) or len(children) < 2:
            return
        for idx, child in enumerate(children):
            if not isinstance(child, dict):
                return
            cid = child.get("id") or uid()
            if cid in rows:
                return
            try:
                title = _title(child.get("title"))
                description = _text(child.get("description"), "description", "")
                outcome = _text(child.get("outcome"), "outcome", "", maximum=OUTCOME_LIMIT)
                wc = _work_class(child.get("workClass"), step["work_class"])
                rids = _step_repo_ids(child.get("repositoryIds")) or []
            except AppError:
                return
            rows[cid] = {"id": cid, "roadmap_id": step["roadmap_id"], "title": title, "description": description,
                         "outcome": outcome, "parent_id": step["id"], "position": idx,
                         "status": child.get("status") or "open", "work_class": wc, "revision": 1, "retired": 0}
            repos[cid] = list(rids)
        if payload.get("clearParentRepositories", True):
            repos[step["id"]] = []
        step["revision"] += 1
        roadmap["revision"] += 1

    def move_step(self, roadmap, step, payload):
        """A step.move whose revision checks: updates parent/position and bumps step & roadmap revisions."""
        if step["retired"] or not self._open(roadmap):
            return
        rows = self._rows(roadmap["id"])
        parent_id = payload["parentId"] if "parentId" in payload else step["parent_id"]
        if parent_id is not None:
            if parent_id not in rows or rows[parent_id]["retired"]:
                return
            cur = parent_id
            seen = {step["id"]}
            while cur is not None:
                if cur in seen:
                    return
                seen.add(cur)
                cur = rows[cur]["parent_id"] if cur in rows else None
        pos = payload.get("position")
        if pos is None:
            pos = (max((r["position"] for r in rows.values() if r["parent_id"] == parent_id and not r["retired"]), default=-1) + 1
                   if parent_id != step["parent_id"] else step["position"])
        if (parent_id, pos) != (step["parent_id"], step["position"]):
            step["parent_id"] = parent_id
            step["position"] = pos
            step["revision"] += 1
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


def step_text(row, payload):
    """The text a step.update leaves the step: each of its title, description and outcome the payload
    gives, checked, and the stored one for each it leaves out."""
    return {"title": row["title"] if payload.get("title") is None else _title(payload["title"]),
            "description": row["description"] if payload.get("description") is None
            else _text(payload["description"], "description"),
            "outcome": row["outcome"] if payload.get("outcome") is None
            else _text(payload["outcome"], "outcome", maximum=OUTCOME_LIMIT)}


def rewrites(row, payload):
    """Whether a step.update with this payload changes the stored step's text: a title, description
    or outcome it gives that differs from the step's (a title counts as trimmed). One that changes
    nothing records nothing (K4)."""
    given = {key: payload.get(key) for key in TEXT}
    if isinstance(given["title"], str):
        given["title"] = given["title"].strip()
    return any(value is not None and value != row[key] for key, value in given.items())


def _steps(conn, identifier, retired=False, text_of=None):
    """A roadmap's steps in (position, id) order, each parent's status derived from its leaves. With
    text_of, only that step's description and outcome are read; the others' come back empty, for a
    read that returns one step and only places the rest."""
    if text_of is None:
        rows = conn.execute("SELECT * FROM steps WHERE roadmap_id=? ORDER BY position,id",
                            (identifier,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT id,title,parent_id,position,status,revision,retired,work_class,"
            "CASE WHEN id=? THEN description ELSE '' END AS description,"
            "CASE WHEN id=? THEN outcome ELSE '' END AS outcome "
            "FROM steps WHERE roadmap_id=? ORDER BY position,id", (text_of, text_of, identifier)).fetchall()
    repos = roadmap_step_repositories(conn, identifier)
    waves, _ = _roadmap_step_waves(conn, identifier)
    steps = []
    for row in rows:
        if retired or not row["retired"]:
            st = _step(row)
            st["repositoryIds"] = repos.get(st["id"], [])
            st["waveIds"] = waves.get(st["id"], [])
            steps.append(st)
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


def step_family(conn, roadmap_id, identity):
    """A roadmap's steps, retired ones included, as a read of one step needs them: that step's text in
    full and the others' titles, places and states, each parent's derived from its leaves."""
    return _steps(conn, roadmap_id, retired=True, text_of=identity)


def recently_done(conn, roadmap_id, limit=10):
    """The roadmap's steps in the plan that are done, the latest marked done first, with when: its
    leaves, since a parent's progress is derived from them. A step counts from its latest change to
    done from another status, so setting done again doesn't move it up. A step whose history holds
    no such change is left out, as when it was done before Worklog recorded progress."""
    _row(conn, roadmap_id)
    return [{"id": row[0], "title": row[1], "doneAt": row[2]} for row in conn.execute(
        "SELECT s.id,s.title,max(e.created_at) AS done_at FROM steps s JOIN events e ON e.entity_id=s.id"
        " AND e.kind='progress.set' AND json_extract(e.after_json,'$.status')='done'"
        " AND coalesce(json_extract(e.before_json,'$.status'),'')<>'done'"
        " WHERE s.roadmap_id=? AND s.retired=0 AND s.status='done'"
        " AND NOT EXISTS(SELECT 1 FROM steps c WHERE c.parent_id=s.id AND c.retired=0)"
        " GROUP BY s.id ORDER BY done_at DESC,s.id LIMIT ?", (roadmap_id, limit))]


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

    def list(self, archived=False, *, summary=False):
        with self.db.read() as conn:
            columns = ("r.id,r.title,(SELECT count(*) FROM roadmap_questions q WHERE q.roadmap_id=r.id "
                       "AND q.status='open' AND q.archived=0) AS openQuestions") if summary else "r.*"
            rows = conn.execute(f"SELECT {columns} FROM roadmaps r WHERE r.archived=? ORDER BY r.created_at,r.id",
                                (int(archived),))
            render = dict if summary else _roadmap
            items = [render(row) for row in rows]
            return {"items": items, "total": len(items)}

    def _detail(self, conn, identifier):
        result = _roadmap(_row(conn, identifier))
        result["steps"] = _steps(conn, identifier, retired=True)
        # The three columns the list shows: each version's snapshot holds the whole plan.
        result["versions"] = [{"number": r["number"], "createdAt": r["created_at"],
                               "note": r["note"]} for r in conn.execute(
            "SELECT number,created_at,note FROM roadmap_versions WHERE roadmap_id=? ORDER BY number", (identifier,))]
        result.update(notes=[], attachments=[], links=[], chats=[], github=[], questions=[], decisions=[])
        return result

    def detail(self, identifier):
        with self.db.read() as conn:
            result = self._detail(conn, identifier)
            result.update(self.family(conn, identifier))
            return result

    def outline(self, identifier):
        """A roadmap's plan without its text, small enough for an agent to read whole: each step in
        it, retired ones aside, in outline order, with its title, status (a parent's derived from its
        leaves, as the page shows it), parent, position, workClass, repositoryIds, and active waveId."""
        with self.db.read() as conn:
            roadmap = _roadmap(_row(conn, identifier))
            _, active_wave = _roadmap_step_waves(conn, identifier)
            steps = [{
                "id": step["id"],
                "title": step["title"],
                "status": step["status"],
                "parentId": step["parentId"],
                "position": step["position"],
                "workClass": step["workClass"],
                "repositoryIds": step["repositoryIds"],
                "waveId": active_wave.get(step["id"]),
            } for step in outline_order(_steps(conn, identifier))]
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
            snapshot = json.loads(row["snapshot"])
            for step in snapshot.get("steps", []):
                step.setdefault("outcome", "")  # saved before steps had outcomes (v31)
                step.setdefault("workClass", "")  # saved before steps had work classes (v35)
                step.setdefault("repositoryIds", [])
                step.setdefault("waveIds", [])
            return {"number": row["number"], "createdAt": row["created_at"],
                    "note": row["note"], **snapshot}

    def step_answer(self, conn, identifier, *, with_roadmap=False):
        """A step as progress.set answers it, or step.update with its roadmap's ID: what a replayed
        receipt of either reads back (audit step 2.5)."""
        row = self.get_step(conn, identifier)
        hydrated = _hydrate_step(conn, _step(row))
        return {**hydrated, "roadmapId": row["roadmap_id"]} if with_roadmap else hydrated

    def saved(self, conn, identifier):
        """A roadmap as a save, move, archive or recovery answers it."""
        return self._detail(conn, identifier)

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
        """A step list as a save takes it: each step's ID, title, description, outcome, parent and
        position, checked, with a new step given an ID. A step that names no outcome has None for
        it, which keep_outcomes fills in. A step marked "retired": true, as `roadmap show` marks
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
                               "outcome": None if data.get("outcome") is None
                               else _text(data["outcome"], "outcome", maximum=OUTCOME_LIMIT),
                               "workClass": None if data.get("workClass") is None
                               else _work_class(data["workClass"]),
                               "repositoryIds": _step_repo_ids(data.get("repositoryIds")),
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
        stored_repos = roadmap_step_repositories(conn, identifier)
        keep_outcomes(rows, steps)
        changes = changed_by(rows, steps, stored_repos)
        wanted = {s["id"] for s in steps}
        changed = []
        for old in rows:
            if old["id"] not in wanted and not old["retired"]:
                conn.execute("UPDATE steps SET retired=1,revision=revision+1 WHERE id=?", (old["id"],))
                event(conn, actor, "step.retire", old["id"], _hydrate_step(conn, _step(old)),
                      _hydrate_step(conn, _step(self.get_step(conn, old["id"]))))
                changed.append(old["id"])
        for step in steps:
            old = stored.get(step["id"])
            if old is not None and old["id"] not in changes:
                if old["position"] != step["position"]:
                    conn.execute("UPDATE steps SET position=? WHERE id=?", (step["position"], step["id"]))
                continue
            before = _hydrate_step(conn, _step(old)) if old else None
            if old:
                conn.execute("UPDATE steps SET title=?,description=?,outcome=?,work_class=?,parent_id=?,position=?,retired=0,"
                             "revision=revision+1 WHERE id=?",
                             (step["title"], step["description"], step["outcome"], step["workClass"],
                              step["parentId"], step["position"], step["id"]))
            else:
                conn.execute("INSERT INTO steps(id,roadmap_id,title,description,outcome,parent_id,position,status,revision,retired,work_class) "
                             "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                             (step["id"], identifier, step["title"], step["description"], step["outcome"], step["parentId"],
                              step["position"], "open", 1, 0, step["workClass"]))
            if step.get("repositoryIds") is not None:
                replace_repository_associations(conn, "step", step["id"], step["repositoryIds"])
            event(conn, actor, "step.restore" if old and old["retired"] else "step.revise" if old else "step.create",
                  step["id"], before, _hydrate_step(conn, _step(self.get_step(conn, step["id"]))))
            changed.append(step["id"])
        return changed

    def _update_step(self, conn, actor, payload):
        """One step's own title, description, outcome, repositoryIds, and workClass, checked against
        the step's revision, touching no other step and recording no version. The roadmap's revision
        guards its title, overview and step tree, step text included, so it rises as well. Returns
        the step with its roadmap's ID."""
        identifier = payload.get("id")
        row = self.get_step(conn, identifier)
        require_revision(row, payload.get("revision"))
        if row["retired"] or _row(conn, row["roadmap_id"])["archived"]:
            raise AppError(409, "conflict", "Retired or archived steps cannot change")
        if (all(payload.get(key) is None for key in TEXT)
                and payload.get("repositoryIds") is None and payload.get("workClass") is None):
            raise AppError(422, "validation", "Give the step's title, description, outcome, repositoryIds or workClass")
        text = step_text(row, payload)
        wc = _work_class(payload.get("workClass"), row["work_class"]) if payload.get("workClass") is not None else row["work_class"]
        repo_ids = _step_repo_ids(payload.get("repositoryIds"))
        before = _hydrate_step(conn, _step(row))
        repos_changed = repo_ids is not None and repo_ids != before["repositoryIds"]
        if rewrites(row, text) or wc != row["work_class"] or repos_changed:
            if repo_ids is not None:
                replace_repository_associations(conn, "step", identifier, repo_ids)
            conn.execute("UPDATE steps SET title=?,description=?,outcome=?,work_class=?,revision=revision+1 WHERE id=?",
                         (text["title"], text["description"], text["outcome"], wc, identifier))
            conn.execute("UPDATE roadmaps SET revision=revision+1,updated_at=? WHERE id=?", (now(), row["roadmap_id"]))
            after = _hydrate_step(conn, _step(self.get_step(conn, identifier)))
            event(conn, actor, "step.update", identifier, before, after)
            return {**after, "roadmapId": row["roadmap_id"]}
        return {**before, "roadmapId": row["roadmap_id"]}

    def _create_step(self, conn, actor, payload):
        """Insert a single step without rewriting the full roadmap tree or cutting a snapshot (K15)."""
        roadmap_id = payload.get("roadmapId") or payload.get("id")
        rm = _row(conn, roadmap_id)
        require_revision(rm, payload.get("revision"))
        if rm["archived"]:
            raise AppError(409, "conflict", "Archived roadmaps must be recovered before editing")
        parent_id = payload.get("parentId")
        if parent_id is not None:
            if not isinstance(parent_id, str):
                raise AppError(422, "validation", "Step parentId must be a step id or null")
            parent = conn.execute("SELECT roadmap_id,retired FROM steps WHERE id=?", (parent_id,)).fetchone()
            if parent is None or parent["roadmap_id"] != roadmap_id or parent["retired"]:
                raise AppError(422, "validation", "Parent step does not exist on this roadmap")
        sid = payload.get("id") if payload.get("roadmapId") else None
        if sid is not None:
            try:
                uuid.UUID(sid)
            except (ValueError, TypeError, AttributeError):
                raise AppError(422, "validation", "Invalid step id") from None
            if conn.execute("SELECT 1 FROM steps WHERE id=?", (sid,)).fetchone():
                raise AppError(409, "conflict", "Step id already exists")
        else:
            sid = uid()
        title = _title(payload.get("title"))
        description = _text(payload.get("description"), "description", "")
        outcome = _text(payload.get("outcome"), "outcome", "", maximum=OUTCOME_LIMIT)
        wc = _work_class(payload.get("workClass"), "")
        repo_ids = _step_repo_ids(payload.get("repositoryIds")) or []
        pos = payload.get("position")
        if pos is None:
            if parent_id is None:
                pos = conn.execute(
                    "SELECT COALESCE(MAX(position),-1)+1 FROM steps WHERE roadmap_id=? AND parent_id IS NULL AND retired=0",
                    (roadmap_id,),
                ).fetchone()[0]
            else:
                pos = conn.execute(
                    "SELECT COALESCE(MAX(position),-1)+1 FROM steps WHERE roadmap_id=? AND parent_id=? AND retired=0",
                    (roadmap_id, parent_id),
                ).fetchone()[0]
        else:
            if type(pos) is not int or pos < 0:
                raise AppError(422, "validation", "Invalid step position")
            if parent_id is None:
                conn.execute(
                    "UPDATE steps SET position=position+1 WHERE roadmap_id=? AND parent_id IS NULL AND retired=0 AND position>=?",
                    (roadmap_id, pos),
                )
            else:
                conn.execute(
                    "UPDATE steps SET position=position+1 WHERE roadmap_id=? AND parent_id=? AND retired=0 AND position>=?",
                    (roadmap_id, parent_id, pos),
                )
        conn.execute(
            "INSERT INTO steps(id,roadmap_id,title,description,outcome,parent_id,position,status,revision,retired,work_class) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (sid, roadmap_id, title, description, outcome, parent_id, pos, "open", 1, 0, wc),
        )
        if repo_ids:
            replace_repository_associations(conn, "step", sid, repo_ids)
        conn.execute("UPDATE roadmaps SET revision=revision+1,updated_at=? WHERE id=?", (now(), roadmap_id))
        created = _hydrate_step(conn, _step(self.get_step(conn, sid)))
        event(conn, actor, "step.create", sid, None, created)
        return {**created, "roadmapId": roadmap_id, "roadmapRevision": rm["revision"] + 1}

    def _split_step(self, conn, actor, payload):
        """Split an active leaf step into 2+ single-repo child steps, preserving phase placement and
        inheriting active/planning wave membership cleanly without cutting a roadmap_versions snapshot."""
        identifier = payload.get("id") or payload.get("stepId")
        row = self.get_step(conn, identifier)
        require_revision(row, payload.get("revision"))
        rm = _row(conn, row["roadmap_id"])
        if row["retired"] or rm["archived"]:
            raise AppError(409, "conflict", "Retired or archived steps cannot be split")
        if conn.execute("SELECT 1 FROM steps WHERE parent_id=? AND retired=0", (identifier,)).fetchone():
            raise AppError(422, "validation", "Only a leaf step can be split into child steps")
        children_raw = payload.get("children")
        if not isinstance(children_raw, list) or len(children_raw) < 2 or len(children_raw) > 50:
            raise AppError(422, "validation", "Provide between 2 and 50 child steps")
        rehome_idx = payload.get("rehomeToChildIndex")
        if rehome_idx is not None and (type(rehome_idx) is not int or rehome_idx < 0 or rehome_idx >= len(children_raw)):
            raise AppError(422, "validation", "rehomeToChildIndex must point to one of the new child steps")
        normalized_children = []
        seen_ids: set[str] = {identifier}
        for idx, child in enumerate(children_raw):
            if not isinstance(child, dict):
                raise AppError(422, "validation", "Each child step must be an object")
            cid = child.get("id") or uid()
            try:
                uuid.UUID(cid)
            except (ValueError, TypeError, AttributeError):
                raise AppError(422, "validation", "Invalid child step id") from None
            if cid in seen_ids or conn.execute("SELECT 1 FROM steps WHERE id=?", (cid,)).fetchone():
                raise AppError(409, "conflict", "Duplicate or existing child step id")
            seen_ids.add(cid)
            status = child.get("status") or "open"
            if status not in STATUSES:
                raise AppError(422, "validation", "Invalid child step status")
            normalized_children.append({
                "id": cid,
                "title": _title(child.get("title")),
                "description": _text(child.get("description"), "description", ""),
                "outcome": _text(child.get("outcome"), "outcome", "", maximum=OUTCOME_LIMIT),
                "workClass": _work_class(child.get("workClass"), row["work_class"]),
                "repositoryIds": _step_repo_ids(child.get("repositoryIds")) or [],
                "status": status,
                "position": idx,
            })
        before = _hydrate_step(conn, _step(row))
        created_children = []
        created_ids = []
        for child in normalized_children:
            conn.execute(
                "INSERT INTO steps(id,roadmap_id,title,description,outcome,parent_id,position,status,revision,retired,work_class) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (child["id"], row["roadmap_id"], child["title"], child["description"], child["outcome"],
                 identifier, child["position"], child["status"], 1, 0, child["workClass"]),
            )
            if child["repositoryIds"]:
                replace_repository_associations(conn, "step", child["id"], child["repositoryIds"])
            created_ids.append(child["id"])
        if payload.get("clearParentRepositories", True):
            replace_repository_associations(conn, "step", identifier, [])
        if payload.get("inheritWaves", True):
            stamp = now()
            wave_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT ws.wave_id FROM wave_steps ws JOIN waves w ON w.id=ws.wave_id "
                    "WHERE ws.step_id=? AND w.archived=0 AND w.status IN ('planning','active')",
                    (identifier,),
                )
            ]
            for wid in wave_ids:
                conn.execute("DELETE FROM wave_steps WHERE wave_id=? AND step_id=?", (wid, identifier))
                conn.executemany(
                    "INSERT OR IGNORE INTO wave_steps(wave_id,step_id,position,added_at) VALUES(?,?,?,?)",
                    [(wid, cid, idx, stamp) for idx, cid in enumerate(created_ids)],
                )
                # Ensure every child's active repositories appear in the inherited wave's tier pipeline.
                for child in normalized_children:
                    for rid in child["repositoryIds"]:
                        if not conn.execute(
                            "SELECT 1 FROM wave_repositories WHERE wave_id=? AND repository_id=?", (wid, rid)
                        ).fetchone():
                            repo_tier = conn.execute("SELECT tier FROM repositories WHERE id=?", (rid,)).fetchone()
                            eff_tier = repo_tier["tier"] if (repo_tier and repo_tier["tier"] is not None) else 2
                            conn.execute(
                                "INSERT INTO wave_repositories(wave_id,repository_id,tier,freeze_status,frozen_ref,"
                                "frozen_at,frozen_by_label,notes,revision,updated_at) VALUES(?,?,?,'open','',NULL,'','',1,?)",
                                (wid, rid, eff_tier, stamp),
                            )
        if rehome_idx is not None:
            target_child = created_ids[rehome_idx]
            conn.execute("UPDATE work_items SET step_id=? WHERE step_id=? AND archived=0", (target_child, identifier))
            conn.execute("UPDATE OR IGNORE chat_steps SET step_id=? WHERE step_id=?", (target_child, identifier))
            conn.execute("DELETE FROM chat_steps WHERE step_id=?", (identifier,))
            conn.execute("UPDATE participations SET step_id=? WHERE step_id=? AND released_at IS NULL", (target_child, identifier))
        conn.execute("UPDATE steps SET revision=revision+1 WHERE id=?", (identifier,))
        conn.execute("UPDATE roadmaps SET revision=revision+1,updated_at=? WHERE id=?", (now(), row["roadmap_id"]))
        for cid in created_ids:
            ch = _hydrate_step(conn, _step(self.get_step(conn, cid)))
            created_children.append(ch)
            event(conn, actor, "step.create", cid, None, ch)
        after_parent = _hydrate_step(conn, _step(self.get_step(conn, identifier)))
        states = [ch["status"] for ch in created_children]
        after_parent["status"] = (
            "done" if all(s == "done" for s in states)
            else "blocked" if "blocked" in states
            else "doing" if any(s in {"doing", "done"} for s in states)
            else "open"
        )
        event(conn, actor, "step.split", identifier, before, {**after_parent, "childIds": created_ids})
        return {
            "parent": after_parent,
            "children": created_children,
            "roadmapId": row["roadmap_id"],
            "roadmapRevision": rm["revision"] + 1,
        }

    def _move_step(self, conn, actor, payload):
        """Move a single step to another parent and/or sibling position without rewriting the full tree."""
        identifier = payload.get("id") or payload.get("stepId")
        row = self.get_step(conn, identifier)
        rm = _row(conn, row["roadmap_id"])
        require_revision(rm, payload.get("revision"))
        if row["retired"] or rm["archived"]:
            raise AppError(409, "conflict", "Retired or archived steps cannot move")
        parent_id = payload["parentId"] if "parentId" in payload else row["parent_id"]
        if parent_id is not None:
            if not isinstance(parent_id, str):
                raise AppError(422, "validation", "Step parentId must be a step id or null")
            parent = conn.execute("SELECT id,roadmap_id,parent_id,retired FROM steps WHERE id=?", (parent_id,)).fetchone()
            if parent is None or parent["roadmap_id"] != row["roadmap_id"] or parent["retired"]:
                raise AppError(422, "validation", "Parent step does not exist on this roadmap")
            ancestor: str | None = parent_id
            seen = {identifier}
            while ancestor is not None:
                if ancestor in seen:
                    raise AppError(422, "validation", "Step cannot be moved under itself or its descendants")
                seen.add(ancestor)
                if len(seen) > 100:
                    raise AppError(422, "validation", "Step trees support at most 100 nested levels")
                anc_row = conn.execute("SELECT parent_id FROM steps WHERE id=?", (ancestor,)).fetchone()
                ancestor = anc_row["parent_id"] if anc_row else None
        pos = payload.get("position")
        if pos is None:
            if parent_id != row["parent_id"]:
                if parent_id is None:
                    pos = conn.execute(
                        "SELECT COALESCE(MAX(position),-1)+1 FROM steps WHERE roadmap_id=? AND parent_id IS NULL AND retired=0",
                        (row["roadmap_id"],),
                    ).fetchone()[0]
                else:
                    pos = conn.execute(
                        "SELECT COALESCE(MAX(position),-1)+1 FROM steps WHERE roadmap_id=? AND parent_id=? AND retired=0",
                        (row["roadmap_id"], parent_id),
                    ).fetchone()[0]
            else:
                pos = row["position"]
        elif type(pos) is not int or pos < 0:
            raise AppError(422, "validation", "Invalid step position")
        before = _hydrate_step(conn, _step(row))
        if (parent_id, pos) == (row["parent_id"], row["position"]):
            return {**before, "roadmapId": row["roadmap_id"], "roadmapRevision": rm["revision"]}
        if payload.get("position") is not None:
            if parent_id is None:
                conn.execute(
                    "UPDATE steps SET position=position+1 WHERE roadmap_id=? AND parent_id IS NULL AND retired=0 AND id<>? AND position>=?",
                    (row["roadmap_id"], identifier, pos),
                )
            else:
                conn.execute(
                    "UPDATE steps SET position=position+1 WHERE roadmap_id=? AND parent_id=? AND retired=0 AND id<>? AND position>=?",
                    (row["roadmap_id"], parent_id, identifier, pos),
                )
        conn.execute("UPDATE steps SET parent_id=?,position=?,revision=revision+1 WHERE id=?", (parent_id, pos, identifier))
        conn.execute("UPDATE roadmaps SET revision=revision+1,updated_at=? WHERE id=?", (now(), row["roadmap_id"]))
        after = _hydrate_step(conn, _step(self.get_step(conn, identifier)))
        event(conn, actor, "step.move", identifier, before, after)
        return {**after, "roadmapId": row["roadmap_id"], "roadmapRevision": rm["revision"] + 1}

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
            before = _hydrate_step(conn, _step(row))
            conn.execute("UPDATE steps SET status=?,revision=revision+1 WHERE id=?", (status, identifier))
            # Progress is the step's, guarded by its revision: the roadmap's revision guards its
            # title, overview and step tree, which progress leaves alone (K4).
            conn.execute("UPDATE roadmaps SET updated_at=? WHERE id=?", (now(), row["roadmap_id"]))
            result = _hydrate_step(conn, _step(self.get_step(conn, identifier)))
            event(conn, actor, action, identifier, before, result)
            return result
        if action == "step.update":
            return self._update_step(conn, actor, payload)
        if action == "step.create":
            return self._create_step(conn, actor, payload)
        if action == "step.split":
            return self._split_step(conn, actor, payload)
        if action == "step.move":
            return self._move_step(conn, actor, payload)
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
                before = _roadmap(row)
            # History records the field that changed, with the roadmap's ID, title and revision, not the
            # whole roadmap with every step (audit finding B3).
            changed = "parentId" if action == "roadmap.move" else "archived"

            def fields(state: dict) -> dict:
                return {"id": state["id"], "title": state["title"], changed: state[changed], "revision": state["revision"]}

            if action in {"roadmap.archive", "roadmap.recover"}:
                conn.execute("UPDATE roadmaps SET archived=?,revision=revision+1,updated_at=? WHERE id=?",
                             (int(action == "roadmap.archive"), now(), identifier))
                result = self._detail(conn, identifier)
                event(conn, actor, action, identifier, fields(before), fields(result))
                return result
            if row["archived"]:
                raise AppError(409, "conflict", "Archived roadmaps must be recovered before editing")
            if action == "roadmap.move":
                # Placement is not plan content: no version is recorded and restores keep it.
                parent = _parent(conn, identifier, payload.get("parentId"))
                conn.execute("UPDATE roadmaps SET parent_id=?,revision=revision+1,updated_at=? WHERE id=?",
                             (parent, now(), identifier))
                result = self._detail(conn, identifier)
                event(conn, actor, action, identifier, fields(before), fields(result))
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
