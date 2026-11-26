"""Request and response contracts for Worklog dependencies."""

from typing import Literal

from pydantic import Field

from .models import Command, Expected

DependencyType = Literal["repository", "step", "item", "ticket", "agent"]
WaitContextType = Literal["repository", "step", "item", "ticket"]


class DependencyRef(Command):
    type: DependencyType
    id: str = Field(min_length=1, max_length=200)


class WaitContextRef(Command):
    type: WaitContextType
    id: str = Field(min_length=1, max_length=200)


class DependencyCreate(Command):
    dependent: DependencyRef
    prerequisite: DependencyRef
    context: WaitContextRef | None = None
    condition: str = Field(default="", max_length=2000)


class WaitReport(Command):
    prerequisite: DependencyRef
    context: WaitContextRef
    condition: str = Field(min_length=1, max_length=2000)


class DependencyConfirm(Expected):
    note: str = Field(default="", max_length=2000)
