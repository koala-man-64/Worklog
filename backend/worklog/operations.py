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
"""

from __future__ import annotations

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
}

# Operations only the person's own routes run. A proposal can't carry one: they are absent above,
# so creating, updating, submitting or approving a proposal that names one is refused.
SERVER_ONLY = frozenset({
    "reference.create", "reference.archive", "reference.recover",
    "attachment.archive", "attachment.recover",
    "note.archive", "note.recover", "task.archive", "task.recover",
    "roadmap.restore", "roadmap.archive", "roadmap.recover",
})


def validate(action: str, payload: dict) -> None:
    """The checks a proposed operation's values pass before it applies: at submit, when the
    person will review it, and again at approve. Each record's own rules (dates, addresses,
    revisions, a plan's shape) still run only when it applies."""
    spec = OPERATIONS.get(action)
    if spec is None:
        raise AppError(422, "validation", "Unsupported proposal action")
    for name in ("id", "itemId", "date"):
        if name in payload and not isinstance(payload[name], str):
            raise AppError(422, "validation", f"{name} must be a string")
    try:
        spec.model.model_validate({k: v for k, v in payload.items() if k not in spec.path_keys})
    except ValidationError as exc:
        raise AppError(422, "validation", "Invalid proposed operation",
                       exc.errors(include_input=False, include_context=False)) from None
