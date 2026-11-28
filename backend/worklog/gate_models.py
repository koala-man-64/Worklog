"""Request contracts for pre-completion gates on roadmap steps."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt

from .models import Command, Expected

GateKind = Literal["manual", "approval", "verification", "prerequisite"]
GateTargetType = Literal["step", "deliverable", "ticket", "question"]


class GateTargetRef(Command):
    type: GateTargetType
    id: str = Field(min_length=1, max_length=200)


class GateCreate(Command):
    title: str = Field(min_length=1, max_length=1000)
    criteria: str = Field(default="", max_length=100_000)
    kind: GateKind | None = None
    humanOnly: StrictBool = False
    target: GateTargetRef | None = None
    position: Annotated[StrictInt, Field(ge=0)] | None = None


class GateUpdate(Expected):
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    criteria: str | None = Field(default=None, max_length=100_000)
    kind: GateKind | None = None
    humanOnly: StrictBool | None = None
    target: GateTargetRef | None = None
    position: Annotated[StrictInt, Field(ge=0)] | None = None


class GatePass(Expected):
    note: str = Field(default="", max_length=8000)


class GateWaive(Expected):
    reason: str = Field(min_length=1, max_length=8000)


class GateReopen(Expected):
    reason: str = Field(min_length=1, max_length=8000)
