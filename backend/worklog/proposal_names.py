"""The names a proposal gives the records it creates, so its later changes can use them.

Worklog chooses a new record's ID when it applies the change that creates it, so no change can
give the ID of a record an earlier change of the same proposal creates. A create names its record
instead: its `id` is a name such as `@jane` (operations.NAME), and a later change gives `@jane`
wherever it would give that record's ID. Approval replaces each name with the ID of the record it
names: the one the create made, or for a repository whose remote is already registered, the record
that holds it.

A name stands only in the fields that name one record by ID, for a record of the kind the field
takes, and only after the change that declares it, so each is known by the time its change
applies. Steps keep the IDs agents choose for them, and a GitHub reference names a chat the same
proposal tracks by its provider ID, so neither needs a name. A note on a work item takes none: it
joins the item's one scratchpad (proposals._scratchpad_landing), which other notes change too.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .common import AppError
from .operations import CREATE_ID, NAME_RULE, NAMED, is_name

# The fields that name one record, by the kind of record each takes. ownerId and targetId take the
# kind their payload's ownerType or targetType gives, parentId takes step on step.* actions and
# roadmap otherwise, and an existing record's change takes its own `id`.
KINDS = {"itemId": "item", "roadmapId": "roadmap", "personId": "person", "stepId": "step", "waveId": "wave"}
TYPED = {"ownerId": "ownerType", "targetId": "targetType"}
LISTS = {"repositoryIds": "repository", "stepIds": "step", "upstreamIds": "repository"}
DEPENDENCY_REFS = ("dependent", "prerequisite", "context")
# The kind of record a change names by `id`, where it isn't the one its action starts with.
OWN = {"progress": "step"}
NAMEABLE = frozenset(action.partition(".")[0] for action in NAMED)
LABELS = {"item": "work item", "chat": "tracked chat", "github": "GitHub reference"}


def _label(kind: Any) -> str:
    return LABELS.get(kind, kind) if isinstance(kind, str) else "record of that type"


def _uses(action: str, payload: dict) -> Iterator[tuple[str, Any, Any]]:
    """Each value in a field that names a record, with the field and the kind of record it takes."""
    if action not in NAMED and "id" in payload:
        kind = action.partition(".")[0]
        yield "id", payload["id"], OWN.get(kind, kind)
    if "parentId" in payload:
        yield "parentId", payload["parentId"], "step" if action.startswith("step.") else "roadmap"
    for field, kind in KINDS.items():
        if field in payload:
            yield field, payload[field], kind
    for field, type_field in TYPED.items():
        if field in payload:
            yield field, payload[field], payload.get(type_field)
    for field, kind in LISTS.items():
        if isinstance(payload.get(field), list):
            for value in payload[field]:
                yield field, value, kind
    if action == "dependency.create":
        for field in DEPENDENCY_REFS:
            ref = payload.get(field)
            if isinstance(ref, dict):
                yield field, ref.get("id"), ref.get("type")
    if action == "step.split" and isinstance(payload.get("children"), list):
        for child in payload["children"]:
            if isinstance(child, dict) and isinstance(child.get("repositoryIds"), list):
                for value in child["repositoryIds"]:
                    yield "repositoryIds", value, "repository"
    if action in {"wave.create", "wave.update"} and isinstance(payload.get("repositories"), list):
        for entry in payload["repositories"]:
            if isinstance(entry, dict) and "repositoryId" in entry:
                yield "repositoryId", entry["repositoryId"], "repository"


def first_problem(operations: list[dict]) -> tuple[int, str, AppError] | None:
    """The first change that names a record it can't, with its number and the reason: a name no
    earlier change declares, one of another kind of record, or one declared twice.

    `operations` already has the shape proposals._operations checks. A value that doesn't start
    with "@" is an ID, which applying the change checks.
    """
    declared: dict[str, tuple[int, str]] = {}
    for number, operation in enumerate(operations, 1):
        action, payload = operation["action"], operation["payload"]
        for field, value, kind in _uses(action, payload):
            if not (isinstance(value, str) and value.startswith("@")):
                continue
            if not is_name(value):
                return number, action, AppError(422, "validation", f"{field} {value!r} is not a name: {NAME_RULE}",
                                                {field: "invalid_name"})
            # ownerType and targetType are unchecked here, so they may be any JSON value.
            if not isinstance(kind, str) or kind not in NAMEABLE:
                hint = (": give the step's own ID, which roadmap.create and roadmap.revise take" if kind == "step" else "")
                return number, action, AppError(
                    422, "validation", f"{field} names {value}, but a {_label(kind)} can't be named{hint}",
                    {field: "wrong_kind"})
            if value not in declared:
                return number, action, AppError(
                    422, "validation", f"{field} names {value}, which no earlier change in this proposal creates",
                    {field: "unknown_name"})
            first, made = declared[value]
            if kind != made:
                return number, action, AppError(
                    422, "validation",
                    f"{field} names {value}, which change {first} creates as a {_label(made)}, not a {_label(kind)}",
                    {field: "wrong_kind"})
        if action in NAMED and "id" in payload:
            name = payload["id"]
            if not is_name(name):
                return number, action, AppError(422, "validation", CREATE_ID, {"id": "not_a_name"})
            if action == "note.create" and payload.get("ownerType") == "item":
                return number, action, AppError(
                    422, "validation", "A note on a work item joins its one scratchpad rather than making a record "
                    "of its own, so it can't be named", {"id": "not_nameable"})
            if name in declared:
                return number, action, AppError(
                    422, "validation",
                    f"{name} already names the record change {declared[name][0]} creates; give each new record its own name",
                    {"id": "duplicate_name"})
            declared[name] = (number, action.partition(".")[0])
    return None


def resolve(action: str, payload: dict, names: dict[str, str]) -> tuple[dict, str | None]:
    """The change as it applies: each name it uses replaced by the ID of the record it names, and
    the name it declares taken out, since Worklog chooses that record's ID. Returns the payload and
    that name, or None."""
    resolved = dict(payload)
    declared = resolved.pop("id", None) if action in NAMED else None

    def known(field: str, value: Any) -> Any:
        if not is_name(value):
            return value
        if value not in names:
            raise AppError(422, "validation", f"{field} names {value}, which no earlier change in this proposal creates",
                           {field: "unknown_name"})
        return names[value]

    for field in ("parentId", *KINDS, *TYPED, *(() if action in NAMED else ("id",))):
        if field in resolved:
            resolved[field] = known(field, resolved[field])
    for field in LISTS:
        if isinstance(resolved.get(field), list):
            resolved[field] = [known(field, value) for value in resolved[field]]
    if action == "dependency.create":
        for field in DEPENDENCY_REFS:
            ref = resolved.get(field)
            if isinstance(ref, dict) and "id" in ref:
                resolved[field] = {**ref, "id": known(field, ref["id"])}
    if action == "step.split" and isinstance(resolved.get("children"), list):
        resolved["children"] = [
            {**child, "repositoryIds": [known("repositoryIds", v) for v in child["repositoryIds"]]}
            if isinstance(child, dict) and isinstance(child.get("repositoryIds"), list) else child
            for child in resolved["children"]
        ]
    if action in {"wave.create", "wave.update"} and isinstance(resolved.get("repositories"), list):
        resolved["repositories"] = [
            {**entry, "repositoryId": known("repositoryId", entry["repositoryId"])}
            if isinstance(entry, dict) and "repositoryId" in entry else entry
            for entry in resolved["repositories"]
        ]
    return resolved, declared
