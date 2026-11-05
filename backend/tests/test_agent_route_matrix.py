"""What an agent credential may call, pinned so that any change shows up in review.

Every API operation is called once with an agent's bearer token and placeholder path
values. The middleware refuses an out-of-scope call with 403 agent_scope before any
handler runs. Any other answer, such as a 2xx or the 404, 409, 422 or 428 a placeholder
earns, means the call reached its handler, where the service may still check the actor;
a 403 from a handler is recorded with its code. agent_route_matrix.json holds today's
answers. Changing what agents may call changes them: regenerate the file, in the same
PR as docs/AGENT-GUIDE.md, with

    ./.venv/Scripts/python.exe backend/tests/test_agent_route_matrix.py
"""

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

if __name__ == "__main__":
    # Run as a script: import this checkout's backend, from a clean environment.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    for name in [key for key in os.environ if key.upper().startswith("WORKLOG_")]:
        del os.environ[name]

from fastapi.testclient import TestClient  # noqa: E402
from worklog.app import create_app  # noqa: E402

ORIGIN = "http://127.0.0.1:8877"
SNAPSHOT = Path(__file__).with_name("agent_route_matrix.json")
PLACEHOLDER = "00000000-0000-4000-8000-000000000000"
# Operations whose scope depends on the query string, with one call per case.
VARIANTS = {("post", "/api/v1/attachments"): ["?ownerType=proposal", "?ownerType=item"]}
# Codes that mean the harness itself is broken, not that an agent was refused.
HARNESS_FAILURES = {"host", "origin", "authentication", "session", "csrf"}


def concrete(template: str) -> str:
    """Placeholder values that pass every path check the middleware makes."""
    verb = "submit" if template.startswith("/api/v1/proposals") else "archive"
    return (template.replace("{identity}", PLACEHOLDER).replace("{item_id}", PLACEHOLDER)
            .replace("{ref}", "T-1").replace("{verb}", verb).replace("{date}", "2026-01-05")
            .replace("{number}", "1"))


def matrix(data_dir: Path) -> dict[str, str]:
    app = create_app(data_dir, testing=True)
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as person, \
            TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as agent:
        person.headers["Origin"] = ORIGIN
        boot = person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        assert boot.status_code == 200, boot.text
        person.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        issued = person.post("/api/v1/credentials", json={"label": "Route matrix"},
                             headers={"Idempotency-Key": str(uuid.uuid4())})
        assert issued.status_code == 200, issued.text
        # Like the agent CLI: a bearer token and no browser Origin.
        agent.headers["Authorization"] = "Bearer " + issued.json()["token"]
        answers: dict[str, str] = {}
        for template, operations in sorted(app.openapi()["paths"].items()):
            if not template.startswith("/api/v1/"):
                continue
            for method in sorted(operations):
                for query in VARIANTS.get((method, template), [""]):
                    response = agent.request(method.upper(), concrete(template) + query,
                                             json=None if method == "get" else {},
                                             headers={"Idempotency-Key": str(uuid.uuid4())})
                    code = response.json().get("error", {}).get("code") if response.status_code >= 400 else None
                    key = f"{method.upper()} {template}{query}"
                    assert code not in HARNESS_FAILURES, (key, response.status_code, response.text[:300])
                    if response.status_code == 403:
                        answers[key] = "refused" if code == "agent_scope" else f"refused by handler: {code}"
                    else:
                        answers[key] = "reaches handler"
        return answers


def test_agent_route_matrix_matches_the_snapshot(tmp_path):
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    actual = matrix(tmp_path)
    added = {key: actual[key] for key in sorted(set(actual) - set(expected))}
    removed = sorted(set(expected) - set(actual))
    changed = {key: [expected[key], actual[key]] for key in sorted(set(actual) & set(expected))
               if actual[key] != expected[key]}
    assert not (added or removed or changed), (
        "What an agent credential may call has changed. If that is intended, update docs/AGENT-GUIDE.md "
        "and regenerate backend/tests/agent_route_matrix.json (see this module's docstring).\n"
        f"added: {added}\nremoved: {removed}\nchanged (was, now): {changed}")


def test_the_snapshot_keeps_refusals_and_open_routes(tmp_path):
    answers = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert answers["GET /api/v1/settings"] == "refused"
    assert answers["POST /api/v1/work-items"] == "refused"
    assert answers["GET /api/v1/chats/{identity}/transcript"] == "refused"
    assert answers["POST /api/v1/attachments?ownerType=item"] == "refused"
    assert answers["POST /api/v1/attachments?ownerType=proposal"] == "reaches handler"
    assert answers["POST /api/v1/proposals"] == "reaches handler"
    assert answers["GET /api/v1/context"] == "reaches handler"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        SNAPSHOT.write_text(json.dumps(matrix(Path(directory)), indent=2, sort_keys=True) + "\n",
                            encoding="utf-8", newline="\n")
    print(f"Wrote {SNAPSHOT}")
