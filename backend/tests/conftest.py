"""Every test starts from the same environment, whatever the shell running pytest carries, and
every answer a typed route gives in a test is the answer the route built.

A shell set up beside a running Worklog can hold WORKLOG_PORT, WORKLOG_DEV, an agent
token, or a WORKLOG_DATA_DIR that names a real data directory. Each test starts with
none of them, and with WORKLOG_DATA_DIR pointing at a temporary folder of its own, so
code that falls back to the default data directory never reaches real data. A test that
needs one of these variables sets it itself with monkeypatch.

A response model (responses.py, consolidation step 5.3) validates what a route returns and
serializes it again. Pydantic accepts some values it then converts, such as 0 and 1 for a
boolean, so a model could change an answer without failing it. Each test checks every typed
answer against what the route returned, so the whole suite, not only the golden files, holds
typing to today's answers.
"""

from __future__ import annotations

import json
import os
from typing import Any

import fastapi.routing
import pytest
from fastapi.encoders import jsonable_encoder


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    for name in [key for key in os.environ if key.upper().startswith("WORKLOG_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("WORKLOG_DATA_DIR", str(tmp_path_factory.mktemp("default-data-dir")))


class TypedAnswerChanged(AssertionError):
    """A response model gave a route's answer differently from how the route built it."""


def _text(answer: Any) -> str:
    return json.dumps(answer, sort_keys=True, ensure_ascii=False)


@pytest.fixture(autouse=True)
def _typed_answers_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    original = fastapi.routing.serialize_response

    async def checked(*, field: Any = None, response_content: Any = None, **options: Any) -> Any:
        typed = await original(field=field, response_content=response_content, **options)
        if field is not None:
            given = json.loads(typed) if isinstance(typed, (bytes, bytearray)) else typed
            # Compared as JSON text, keys sorted: Python holds True equal to 1, and JSON doesn't.
            if _text(given) != _text(jsonable_encoder(response_content)):
                route = (options.get("endpoint_ctx") or {}).get("path", "a route")
                raise TypedAnswerChanged(f"The response model of {route} changed its answer")
        return typed

    # FastAPI's request handler looks serialize_response up in its module on each request.
    monkeypatch.setattr(fastapi.routing, "serialize_response", checked)
