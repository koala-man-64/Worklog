"""Validated request contracts for repository slot policies, leases, and dispatch queue."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt

from .models import Command, Expected, Revision

LeaseMode = Literal["shared", "exclusive"]
LeasePriority = Literal["normal", "pr_fixup", "wave", "incident"]


class SlotPolicyUpdate(Command):
    """Create or update a repository's checkout slot policy."""

    revision: Revision | None = None
    maxActiveLeases: Annotated[StrictInt, Field(ge=0, le=32)] | None = None
    defaultTtlSeconds: Annotated[StrictInt, Field(ge=300, le=14400)] | None = None
    baseCheckoutPath: str | None = Field(default=None, max_length=2000)
    slotRootPath: str | None = Field(default=None, max_length=2000)
    defaultBranch: str | None = Field(default=None, min_length=1, max_length=200)
    autoRescueDirty: StrictBool | None = None


class BaseLockRequest(Command):
    """Lock or unlock a repository's canonical base checkout read-only."""

    locked: StrictBool


class LeaseAcquire(Command):
    """Request a repository checkout slot lease (grants immediately or enters FIFO queue)."""

    repositoryId: str | None = Field(default=None, min_length=1, max_length=200)
    repositoryUrl: str | None = Field(default=None, min_length=1, max_length=2000)
    branch: str = Field(default="", max_length=200)
    mode: LeaseMode = "shared"
    ttlSeconds: Annotated[StrictInt, Field(ge=300, le=14400)] | None = None
    priority: LeasePriority = "normal"
    reason: str = Field(default="", max_length=1000)
    runId: str | None = Field(default=None, min_length=1, max_length=200)
    provider: str = Field(default="", max_length=100)
    chatId: str = Field(default="", max_length=500)


class LeasePoll(Command):
    """Refresh waiter heartbeat and confirm/provision an offered slot."""

    branch: str | None = Field(default=None, max_length=200)


class LeaseRenew(Expected):
    """Extend an active lease's TTL."""

    ttlSeconds: Annotated[StrictInt, Field(ge=300, le=14400)] | None = None


class LeaseRelease(Expected):
    """Release an active/offered lease or cancel a queued lease."""

    reason: str = Field(default="", max_length=1000)
    rescue: StrictBool = False
    force: StrictBool = False


class LeaseRescue(Expected):
    """Rescue dirty changes in an expired_dirty (or active) slot and recycle it."""

    reason: str = Field(default="", max_length=1000)


class DispatchCancel(Command):
    """Cancel a queued dispatch and its linked lease."""

    reason: str = Field(default="", max_length=1000)
