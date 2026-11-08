"""The suite's check that typing changes no answer (conftest.py) catches a model that converts.

Pydantic takes 1 for a boolean and gives true, so a route that built its answer with a 0/1 column
left as a number would answer differently once typed. The check turns that into a failed request.
"""

from __future__ import annotations

import pytest
from conftest import TypedAnswerChanged
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel


class Flag(BaseModel):
    archived: bool


def app() -> FastAPI:
    served = FastAPI()

    @served.get("/converted", response_model=Flag)
    def converted() -> dict:
        return {"archived": 1}

    @served.get("/kept", response_model=Flag)
    def kept() -> dict:
        return {"archived": True}

    return served


def test_an_answer_a_model_converts_fails_its_request():
    with TestClient(app()) as client, pytest.raises(TypedAnswerChanged, match="/converted"):
        client.get("/converted")


def test_an_answer_a_model_keeps_passes():
    with TestClient(app()) as client:
        assert client.get("/kept").json() == {"archived": True}
