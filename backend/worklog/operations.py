"""The one registry of the operations a proposal can carry (consolidation step 2.3).

Each operation was defined in five places: the key sets in proposals.FIELDS, the request model
dispatch validated it with, the Literal in models.Operation, the person's own route, and the
prefix WorkService.apply routes it by. Here each one is defined once, by the request model the
person's route also takes, so its keys and its values are checked by the same rules:

- `path_keys` are the keys the person's route takes from its path, here part of the payload;
- `also_required` are keys a proposal must give although the model has a default for them;
- `server_injected` are keys only Worklog sets while applying, never the proposal.

proposals.FIELDS is the key sets derived from this, in this order, and the agent guide's table
must match it. The key sets must not narrow: approve checks stored proposals against them again.

A create may also give `id`: not an ID, which Worklog chooses, but a name such as `@jane` for the
record it makes, so later changes in the same proposal can use it (proposal_names).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from . import coordination_models as cm
from . import models as m
from .common import AppError


@dataclass(frozen=True)
class Spec:
    model: type[m.Command]
    path_keys: frozenset[str] = field(default_factory=frozenset)
    also_required: frozenset[str] = field(default_factory=frozenset)
    server_injected: frozenset[str] = field(default_factory=frozenset)

    @property
    def allowed(self) -> frozenset[str]:
        return (frozenset(self.model.model_fields) - self.server_injected) | self.path_keys

    @property
    def required(self) -> frozenset[str]:
        model_required = {name for name, info in self.model.model_fields.items() if info.is_required()}
        return (frozenset(model_required) - self.server_injected) | self.path_keys | self.also_required


def _new(model: type[m.Command], **extra: frozenset[str]) -> Spec:
    """An operation that creates a record."""
    return Spec(model, **extra)


def _existing(model: type[m.Command], **extra: frozenset[str]) -> Spec:
    """An operation on a record its payload names by id."""
    return Spec(model, frozenset({"id"}), **extra)


OPERATIONS: dict[str, Spec] = {
    "repository.create": _new(cm.RepositoryCreate),
    "repository.update": _existing(cm.RepositoryUpdate),
    "repository.archive": _existing(m.Expected),
    "repository.recover": _existing(m.Expected),
    "item.create": _new(m.ItemCreate),
    "item.update": _existing(m.ItemUpdate),
    "item.archive": _existing(m.Expected),
    "item.recover": _existing(m.Expected),
    "entry.upsert": Spec(m.EntryUpdate, frozenset({"date", "itemId"})),
    # A proposed note is its text; the person's own route may start an empty one.
    "note.create": _new(m.NoteCreate, also_required=frozenset({"text"})),
    "note.update": _existing(m.NoteUpdate),
    "task.create": _new(m.TaskCreate),
    "task.update": _existing(m.TaskUpdate),
    "roadmap.create": _new(m.RoadmapCreate),
    # A proposed revision states the whole plan, so it can't leave the steps out by accident.
    "roadmap.revise": _existing(m.RoadmapRevision, also_required=frozenset({"steps"})),
    "roadmap.move": _existing(m.RoadmapMove),
    "progress.set": _existing(m.Progress),
    "person.create": _new(m.PersonCreate),
    "person.update": _existing(m.PersonUpdate),
    "person.archive": _existing(m.Expected),
    "person.recover": _existing(m.Expected),
    "person.link": _new(m.PersonLinkCreate),
    "person.unlink": _new(m.PersonLinkRemove),
    "link.create": _new(m.LinkCreate),
    "link.update": _existing(m.LinkUpdate),
    "link.archive": _existing(m.Expected),
    "link.recover": _existing(m.Expected),
    # The file is the proposal's own evidence, which only Worklog names.
    "attachment.create": _new(m.AttachmentCreate, server_injected=frozenset({"proposalId"})),
    "chat.create": _new(m.ChatCreate),
    "chat.update": _existing(m.ChatUpdate),
    "chat.archive": _existing(m.Expected),
    "chat.recover": _existing(m.Expected),
    "github.create": _new(m.GithubRefCreate),
    "github.update": _existing(m.GithubRefUpdate),
    "github.archive": _existing(m.Expected),
    "github.recover": _existing(m.Expected),
    "question.create": _new(m.QuestionCreate),
    "question.update": _existing(m.QuestionUpdate),
    "question.archive": _existing(m.Expected),
    "question.recover": _existing(m.Expected),
    "decision.create": _new(m.DecisionCreate),
    "decision.update": _existing(m.DecisionUpdate),
    "decision.archive": _existing(m.Expected),
    "decision.recover": _existing(m.Expected),
    "resource.create": _new(m.ResourceCreate),
    "resource.update": _existing(m.ResourceUpdate),
    "resource.archive": _existing(m.Expected),
    "resource.recover": _existing(m.Expected),
    # A ticket's id is its UUID, its key (T-12) or its number, as the person's route takes it.
    "ticket.create": _new(m.TicketCreate),
    "ticket.update": _existing(m.TicketUpdate),
}

# Operations only the person's own routes run. A proposal can't carry one: they are absent above,
# so creating, updating, submitting or approving a proposal that names one is refused.
SERVER_ONLY = frozenset({
    "reference.create", "reference.archive", "reference.recover",
    "attachment.archive", "attachment.recover",
    "note.archive", "note.recover", "task.archive", "task.recover",
    "roadmap.restore", "roadmap.archive", "roadmap.recover",
    "ticket.archive", "ticket.recover",
})

# The creates that may name the record they make: every one but a file's, a copy of the proposal's
# own evidence that no later change can name. A person link is a tie, not a record a change names.
NAMED = frozenset(action for action in OPERATIONS if action.endswith(".create") and action != "attachment.create")
# A name: "@" and up to 100 letters, digits, '.', '_' or '-', the first a letter or digit. No ID
# starts with "@", so a name can't be mistaken for one.
NAME = re.compile(r"@[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
NAME_RULE = "use @ and up to 100 letters, digits, '.', '_' or '-', starting with a letter or digit"
CREATE_ID = ("On a create, id names the new record for later changes in this proposal, such as @jane: "
             f"{NAME_RULE}. Worklog chooses its ID")


def is_name(value: object) -> bool:
    return isinstance(value, str) and NAME.fullmatch(value) is not None


def keys(action: str) -> tuple[frozenset[str], frozenset[str]]:
    """The keys a proposed change must give, and the keys it may give: a create's name among them."""
    spec = OPERATIONS[action]
    return spec.required, spec.allowed | (frozenset({"id"}) if action in NAMED else frozenset())


def validate(action: str, payload: dict) -> None:
    """The checks a proposed operation's values pass before it applies: at submit, when the
    person will review it, and again at approve. Each record's own rules (dates, addresses,
    revisions, a plan's shape) still run only when it applies."""
    spec = OPERATIONS.get(action)
    if spec is None:
        raise AppError(422, "validation", "Unsupported proposal action")
    # The model checks every other key: these come from the person's route path, not its body. A
    # ticket's itemId is in its body, where null unlinks the work item.
    for name in sorted(spec.path_keys):
        if name in payload and not isinstance(payload[name], str):
            raise AppError(422, "validation", f"{name} must be a string")
    named = action in NAMED and "id" in payload
    if named and not is_name(payload["id"]):
        raise AppError(422, "validation", CREATE_ID, {"id": "not_a_name"})
    try:
        spec.model.model_validate({k: v for k, v in payload.items()
                                   if k not in spec.path_keys and not (named and k == "id")})
    except ValidationError as exc:
        raise AppError(422, "validation", "Invalid proposed operation",
                       exc.errors(include_input=False, include_context=False)) from None
