"""A real large-history fixture verifies bounded reads, not just small mock responses."""

import json
import time
from uuid import UUID

from fastapi.testclient import TestClient
from worklog.app import create_app
from worklog.common import now


def test_ten_thousand_items_and_hundred_thousand_events(tmp_path):
    app = create_app(tmp_path, testing=True)
    db = app.state.db
    stamp = now()
    identities = [str(UUID(int=i + 1)) for i in range(10_000)]
    with db.transaction() as c:
        c.executemany("INSERT INTO work_items(id,title,description,status,ado_url,step_id,backlog_order,archived,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      [(identity, f"Benchmark work {i:05}", "A bounded history fixture", "open", None, None, i, 0, 1, stamp, stamp) for i, identity in enumerate(identities)])
        c.executemany("INSERT INTO search_index(entity_id,type,title,body,owner_id,archived) VALUES(?,?,?,?,?,?)",
                      [(identity, "item", f"Benchmark work {i:05}", "A bounded history fixture", identity, 0) for i, identity in enumerate(identities)])
        c.execute("INSERT INTO days VALUES('2026-10-28','',1)")
        c.executemany("INSERT INTO daily_entries VALUES(?,?,?,?,?,?,?,?)",
                      [(str(UUID(int=20_000 + i)), identity, "2026-10-28", 1, 1, "Recorded outcome", i, 1) for i, identity in enumerate(identities[:20])])
        def events():
            for i in range(100_000):
                identity = identities[i % len(identities)]
                yield (str(UUID(int=100_000 + i)), identity, "item.update", "fixture", "Fixture", "human", stamp, "2026-10-27", "{}", json.dumps({"id": identity, "title": f"Benchmark work {i % 10000:05}", "status": "open", "_statusChanged": False}))
        c.executemany("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?)", events())
    with TestClient(app, base_url="http://127.0.0.1:8877") as client:
        client.headers["Origin"] = "http://127.0.0.1:8877"
        r = client.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        assert r.status_code == 200
        start = time.perf_counter()
        page = client.get("/api/v1/work-items?limit=100&offset=9900").json()
        assert page["total"] == 10000 and len(page["items"]) == 100
        results = client.get("/api/v1/search?q=Benchmark&limit=50").json()
        assert results["total"] == 10000 and len(results["items"]) == 50
        day = client.get("/api/v1/days/2026-10-28").json()
        assert len(day["entries"]) == 20 and len(day["summary"]["worked"]) == 20
        history = client.get(f"/api/v1/history?entityId={identities[0]}&limit=5").json()
        assert len(history["items"]) == 5
        print(f"Large-history page/search/day/history reads: {time.perf_counter() - start:.3f}s; database: {db.path.stat().st_size:,} bytes")
