import os, sys, pathlib, tempfile
os.environ["CONFIG_PATH"] = os.path.join(tempfile.mkdtemp(), "config.json")
os.environ.pop("ADMIN_PASSWORD", None)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))
import main
from config import config
from fastapi.testclient import TestClient

c = TestClient(main.app, base_url="https://testserver")
r = c.get("/")
assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"
assert c.get("/health").json() == {"ok": True} and c.get("/static/admin.html").status_code == 404
# the placeholder admin password is never accepted
assert c.post("/api/admin/login", json={"username": "admin", "password": "changeme"}).status_code == 503

# a visitor never receives AI Guard's raw payload or detector details (they can echo the blocked content)
config.update({"llm": {"enabled": False}})
seen = {}
async def fake_scan(text, stage, cfg, conversation_id=None, user_name=None):
    seen.update(conversation_id=conversation_id, user_name=user_name)
    return {"action": "BLOCK", "triggered": ["pii"], "raw": {"maskedContent": "512-44-9087"},
            "detectors": [{"name": "pii", "triggered": True, "action": "BLOCK", "details": {"allMatches": ["512-44-9087"]}}]}
main.scan = fake_scan
r = c.post("/api/chat", json={"message": "ssn please", "session_id": "evil\r\nX-Injected: 1"})
assert r.json()["stage"] == "prompt_blocked" and "512-44-9087" not in r.text and r.json()["prompt_verdict"]["detectors"][0]["name"] == "pii"
# ...and a session id that isn't id-shaped is replaced, so it can't break the headers sent to AI Guard
assert main.SESSION_ID_RE.fullmatch(seen["conversation_id"]) and r.json()["session_id"] == seen["conversation_id"]
good = "0b9f6c1e-8a55-4c38-9d6b-0d2f6d0a1c11"
assert c.post("/api/chat", json={"message": "hi", "session_id": good}).json()["session_id"] == good
print("ok")
