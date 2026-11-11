"""Strict contracts for the opt-in communication lane."""

from typing import Literal

from pydantic import Field, StrictBool, model_validator

from .models import Command, Revision

# What a discussion can be tagged with (v22), as discussions.TAG_KINDS lists them; roles sit only on
# the first three, the work contexts.
ContextType = Literal["item", "roadmap", "step", "repository", "ticket", "person", "resource", "question",
                      "decision"]
RoleContextType = Literal["item", "roadmap", "step"]


class Context(Command):
    """A tag: a record of one kind by its id, or a ticket by its key or number."""
    type: ContextType
    id: str = Field(min_length=1, max_length=500)


class Target(Command):
    kind: Literal["agent", "human", "role"]
    id: str = Field(min_length=1, max_length=500)


class Acknowledgement(Command):
    mode: Literal["all", "any", "any_agent", "anyone"] = "all"
    targets: list[Target] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def target_shape(self) -> "Acknowledgement":
        if self.mode in {"all", "any"} and not self.targets:
            raise ValueError("Named acknowledgement requests require targets")
        if self.mode in {"any_agent", "anyone"} and self.targets:
            raise ValueError("Open acknowledgement requests do not take named targets")
        keys = [(target.kind, target.id) for target in self.targets]
        if len(set(keys)) != len(keys):
            raise ValueError("Choose each target only once")
        return self


class MessageCreate(Command):
    text: str = Field(min_length=1, max_length=1_000_000)
    chatId: str | None = Field(default=None, max_length=500)
    onBehalfOfRoleId: str | None = Field(default=None, min_length=1, max_length=500)
    acknowledgement: Acknowledgement | None = None


class DiscussionCreate(MessageCreate):
    title: str | None = Field(default=None, max_length=1000)
    contexts: list[Context] = Field(default_factory=list, max_length=30)
    # Free-text repository labels, which a new discussion no longer takes (K8): any given are refused.
    repos: list[str] = Field(default_factory=list, max_length=20)


class Acknowledge(Command):
    targetIds: list[str] | None = Field(default=None, min_length=1, max_length=100)


class Empty(Command):
    pass


class AvatarChoice(Command):
    avatar: str = Field(min_length=1, max_length=100)


class ReleaseRequest(Command):
    reason: str = Field(min_length=1, max_length=2000)


class Archive(Command):
    revision: Revision
    archived: StrictBool


class Pin(Command):
    revision: Revision
    pinned: StrictBool


class RoleCreate(Command):
    contextType: RoleContextType
    contextId: str = Field(min_length=1, max_length=500)
    label: str = Field(min_length=1, max_length=200)


class RoleRevision(Command):
    revision: Revision


class RoleOffer(RoleRevision):
    successorId: str = Field(min_length=1, max_length=500)
    note: str = Field(default="", max_length=2000)


class RoleReassign(RoleRevision):
    ownerId: str | None = Field(default=None, min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=2000)
