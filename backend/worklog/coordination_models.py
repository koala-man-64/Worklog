"""Contracts for declared coordination, separate from reviewed canonical work."""

from typing import Annotated, Literal

from pydantic import Field, StrictInt

from .models import Command, Expected, Revision


class RepositoryCreate(Command):
    name: str
    url: str = ""
    description: str = ""
    wipCap: Annotated[StrictInt, Field(ge=1, le=100)] | None = None
    tier: Annotated[StrictInt, Field(ge=1, le=9)] | None = None
    upstreamIds: list[str] = Field(default_factory=list, max_length=100)


class RepositoryUpdate(Expected):
    name: str | None = None
    url: str | None = None
    description: str | None = None
    wipCap: Annotated[StrictInt, Field(ge=1, le=100)] | None = None
    tier: Annotated[StrictInt, Field(ge=1, le=9)] | None = None
    upstreamIds: list[str] | None = Field(default=None, max_length=100)


class RepositoryQueueReorder(Command):
    stepIds: list[str] = Field(default_factory=list, max_length=2000)


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
    stepId: str | None = None
    contexts: list[ParticipationContext] = Field(default_factory=list, max_length=40)


class ParticipationUpdate(Expected):
    description: str | None = None
    itemId: str | None = None
    stepId: str | None = None
    contexts: list[ParticipationContext] | None = Field(default=None, max_length=40)


class ParticipationOutputs(Command):
    """What a participation produced: pull requests by address or number, and commits by SHA, in a
    repository it reports, by ID or remote; one it reports alone may be left out."""
    repositoryId: str | None = None
    repositoryUrl: str | None = None
    pullRequests: list[str] = Field(default_factory=list, max_length=50)
    commits: list[str] = Field(default_factory=list, max_length=50)


class CoordinationUpdateCreate(Command):
    kind: Literal["update", "blocker", "handoff"] = "update"
    text: str


class ChatTrack(Command):
    """Track the chat the calling agent registered with on a work item or roadmap, and on steps
    of that roadmap. Worklog takes the chat's ID from the identity, never from the request."""
    ownerType: Literal["item", "roadmap"]
    ownerId: str
    stepIds: list[str] = Field(default_factory=list, max_length=500)
    # A new tracked chat's title: the identity's label unless given.
    title: str | None = None
    provider: str = ""
    model: str = ""
