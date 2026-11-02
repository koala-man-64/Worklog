"""Pydantic contracts for the quota burndown API surface."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)


class QuotaPlanSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: StrictStr | StrictInt | StrictFloat
    starts_at: StrictStr
    ends_at: StrictStr
    model: StrictStr
    speed: Literal["standard", "fast", "ultrafast"] = "standard"


class QuotaPlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["claude", "codex"]
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    plan: QuotaPlanSpec | None = None


class QuotaPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reserve_pct: Literal[5, 10, 20]


class QuotaCollectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    since_days: Annotated[StrictFloat | StrictInt, Field(ge=0.1, le=3650)] = 14
    full: StrictBool = False
    usage: StrictBool = True
