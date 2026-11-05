"""Counter endpoints for customer memory (offline: no Gnani calls)."""
import os

os.environ.setdefault("MEMORY_DIR", "/tmp/bv-test-memory")

from fastapi.testclient import TestClient  # noqa: E402

import app.server as server  # noqa: E402
from app.brain.memory import MemoryStore  # noqa: E402


def test_memory_endpoint_lists_due_and_learned(tmp_path):
    server.MEMORY = MemoryStore(directory=tmp_path)
    m = server.MEMORY.get("CUST-002")
    m.learn([{"sku": "ghadi-1kg", "qty": 2, "unit": "pkt", "said": "kapde dhone aala surf", "via": "asked"}])
    c = TestClient(server.app)
    rows = {r["customer"]: r for r in c.get("/api/memory?horizon_days=30").json()}
    assert rows["CUST-002"]["learned"][0]["said"] == "kapde dhone aala surf"
    assert any(d["sku"] == "amul-taaza" for d in rows["CUST-001"]["due"])  # weekly milk
    assert c.post("/api/memory/CUST-002/forget").json()["ok"]
    assert server.MEMORY.get("CUST-002").phrases == {}
