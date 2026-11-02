"""Agents read roadmaps and steps in full, not only context summaries and search snippets."""

import io
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from worklog import cli
from worklog.app import create_app

ID = "11111111-1111-4111-8111-111111111111"
PARENT = "22222222-2222-4222-8222-222222222222"
CHILD = "33333333-3333-4333-8333-333333333333"
# Longer than a search snippet (220 characters), with characters a cp1252 console cannot encode.
DESCRIPTION = "Move the parser → streaming reads ✓ 🚀\n\n" + "Keep every detail of the step. " * 12
CHILD_DESCRIPTION = "Measure the reads — before and after.\n\n" + "Record each measurement. " * 12


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", "private-agent-token")
    captured = []

    def respond(request, **kwargs):
        captured.append(request)
        return io.BytesIO(json.dumps({"id": ID, "description": DESCRIPTION}).encode())

    monkeypatch.setattr(cli, "_open", respond)
    return captured


@pytest.mark.parametrize("arguments,route", [
    (["roadmap", "show", ID], f"/roadmaps/{ID}"),
    (["step", "show", ID], f"/steps/{ID}"),
])
def test_show_reads_one_record_by_id(transport, arguments, route):
    assert cli.main(["agent", *arguments]) == 0
    request = transport[-1]
    assert (request.method, urlsplit(request.full_url).path, request.data) == ("GET", "/api/v1" + route, None)
    assert request.get_header("Idempotency-key") is None


@pytest.mark.parametrize("method,path", [
    ("POST", "/roadmaps"), ("POST", f"/roadmaps/{ID}/revisions"), ("POST", f"/roadmaps/{ID}/restore"),
    ("POST", f"/roadmaps/{ID}/move"), ("POST", f"/roadmaps/{ID}/archive"), ("PATCH", f"/roadmaps/{ID}"),
    ("PATCH", f"/steps/{ID}/progress"), ("PATCH", f"/steps/{ID}"),
    ("GET", "/roadmaps/%2e%2e"), ("GET", "/steps/not-a-step"),
])
def test_plan_changes_stay_proposals(transport, method, path):
    with pytest.raises(ValueError, match="outside"):
        cli.AgentClient().request(method, path, {})
    assert not transport


def test_text_survives_a_console_that_cannot_encode_it(transport, monkeypatch, capsys):
    # A piped stdout on Windows encodes cp1252 unless PYTHONIOENCODING says otherwise.
    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(cli.sys, "stdout", console)
    assert cli.main(["agent", "step", "show", ID]) == 0, capsys.readouterr().err
    console.flush()
    assert json.loads(console.buffer.getvalue())["description"] == DESCRIPTION


def test_an_agent_reads_every_step_in_full_from_the_application(tmp_path, monkeypatch, capsys):
    app = create_app(tmp_path / "server", testing=True)
    with TestClient(app, base_url="http://127.0.0.1:8877") as person, TestClient(app, base_url="http://127.0.0.1:8877") as wire:
        person.headers["Origin"] = "http://127.0.0.1:8877"
        csrf = person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={}).json()["csrfToken"]

        def write(path, body, key):
            response = person.post("/api/v1" + path, json=body, headers={"X-CSRF-Token": csrf, "Idempotency-Key": key})
            assert response.status_code == 200, response.text
            return response.json()

        token = write("/credentials", {"label": "Planner"}, "issue-planner-credential")["token"]
        steps = [{"id": PARENT, "title": "Stream the reads", "description": DESCRIPTION, "parentId": None, "position": 0},
                 {"id": CHILD, "title": "Measure", "description": "Draft", "parentId": PARENT, "position": 0}]
        roadmap = write("/roadmaps", {"title": "Parser", "steps": steps}, "create-parser-roadmap")
        steps[1]["description"] = CHILD_DESCRIPTION
        # A revision adds whole copies of the plan to the roadmap's history.
        revised = write(f"/roadmaps/{roadmap['id']}/revisions",
                        {"revision": roadmap["revision"], "title": "Parser", "steps": steps}, "revise-parser-roadmap")
        assert len(wire.get(f"/api/v1/roadmaps/{roadmap['id']}", headers={"Authorization": "Bearer " + token}).json()["history"]) == 2
        monkeypatch.setenv("WORKLOG_AGENT_TOKEN", token)

        def bridge(request, **kwargs):
            response = wire.request(request.get_method(), request.full_url, content=request.data, headers=dict(request.header_items()))
            if response.status_code >= 400:
                raise HTTPError(request.full_url, response.status_code, "rejected", {}, io.BytesIO(response.content))
            return io.BytesIO(response.content)

        monkeypatch.setattr(cli, "_open", bridge)

        def run(*args):
            code = cli.main(["agent", *args])
            out, err = capsys.readouterr()
            assert code == 0, err
            assert out.isascii() and token not in out
            return json.loads(out)

        # Search finds the step but shows only the start of its text.
        hit = next(h for h in run("search", "streaming")["items"] if h["id"] == PARENT)
        assert hit["snippet"] == DESCRIPTION[:220] != DESCRIPTION

        step = run("step", "show", PARENT)
        assert (step["description"], step["roadmapId"], step["status"]) == (DESCRIPTION, roadmap["id"], "open")

        shown = run("roadmap", "show", roadmap["id"])
        assert "history" not in shown
        assert shown["revision"] == revised["revision"]
        assert [(s["id"], s["parentId"], s["description"]) for s in shown["steps"]] == [
            (PARENT, None, DESCRIPTION), (CHILD, PARENT, CHILD_DESCRIPTION)]
        assert all(isinstance(s["revision"], int) for s in shown["steps"])

        code = cli.main(["agent", "step", "show", ID])
        assert code == 1 and "HTTP 404" in capsys.readouterr().err
