"""Contracts for declared coordination, separate from reviewed canonical work."""

from typing import Literal

from pydantic import Field

from .models import Command, Expected, Revision


class RepositoryCreate(Command):
    name: str
    url: str = ""
    description: str = ""


class RepositoryUpdate(Expected):
    name: str | None = None
    url: str | None = None
    description: str | None = None


class RepositoryEnsure(Command):
    """Register a remote, or find the record that already holds it."""
    url: str
    name: str | None = None
    description: str = ""


class RepositoryMerge(Expected):
    """Merge the addressed repository into `intoId`; both revisions must be current."""
    intoId: str
    intoRevision: Revision


class RunCreate(Command):
    clientRunKey: str
    displayName: str
    provider: str = ""
    externalChatId: str | None = None


class ParticipationContext(Command):
    # One of the two: a catalog ID, or a remote address that is registered or found on the way.
    repositoryId: str | None = None
    repositoryUrl: str | None = None
    checkoutPath: str = ""
    worktreePath: str = ""
    branch: str = ""


class ParticipationCreate(Command):
    runId: str
    description: str = ""
    itemId: str | None = None
    contexts: list[ParticipationContext] = Field(default_factory=list, max_length=40)


class ParticipationUpdate(Expected):
    description: str | None = None
    itemId: str | None = None
    contexts: list[ParticipationContext] | None = Field(default=None, max_length=40)


class CoordinationUpdateCreate(Command):
    kind: Literal["update", "blocker", "handoff"] = "update"
    text: str
