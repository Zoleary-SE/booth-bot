import json, os, sys, pathlib, tempfile, time
os.environ["CONFIG_PATH"] = os.path.join(tempfile.mkdtemp(), "config.json")
os.environ["ADMIN_PASSWORD"] = "pw"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))
import auth, main
from fastapi.testclient import TestClient

assert auth.totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", 1) == "287082"   # RFC 6238 test vector (T=59s)
new = lambda: TestClient(main.app, base_url="https://testserver")
c = new()
login = lambda pw="pw", code="", user="admin", cl=None: (cl or c).post("/api/admin/login", json={"username": user, "password": pw, "code": code})
now = lambda: int(time.time() // 30)
saved = lambda: json.loads(auth.ADMINS_PATH.read_text())

assert c.get("/api/admin/config").status_code == 401 and "Sign in" in c.get("/admin").text
assert login("wrong").status_code == 401
# first sign-in: the password alone only hands out the key, no session
r = login(); secret = r.json()["enroll"]["secret"]
assert r.json()["enroll"]["qr"].startswith("data:image/svg+xml") and secret in r.json()["enroll"]["uri"]
assert "set-cookie" not in r.headers and c.get("/api/admin/config").status_code == 401 and not auth.ADMINS_PATH.exists()
assert login(code="000000").status_code == 401 and not auth.ADMINS_PATH.exists()
r = login(code=auth.totp(secret, now()))
assert r.status_code == 200 and "HttpOnly" in r.headers["set-cookie"] and "Secure" in r.headers["set-cookie"]
assert saved()["admin"]["totp"] == secret and oct(auth.ADMINS_PATH.stat().st_mode)[-3:] == "600"
r = c.get("/api/admin/config"); assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
assert "booth admin" in c.get("/admin").text and "Sign in" not in c.get("/admin").text

# ---- more admins
add = lambda u, p: c.post("/api/admin/admins", json={"username": u, "password": p})
assert add("sam", "short").status_code == 400 and add("Bad Name!", "x" * 12).status_code == 400 and add("admin", "x" * 12).status_code == 409
r = add("Sam", "correct horse battery"); assert r.status_code == 200 and r.json()["me"] == "admin"
assert {"username": "sam", "owner": False, "enrolled": False} in r.json()["admins"] and add("sam", "x" * 12).status_code == 409
assert "correct horse" not in auth.ADMINS_PATH.read_text()                 # only a hash is stored
s = new()
assert login("pw", user="sam", cl=s).status_code == 401                      # the owner's password is not sam's
sam_secret = login("correct horse battery", user="sam", cl=s).json()["enroll"]["secret"]
assert sam_secret != secret
assert login("correct horse battery", auth.totp(secret, now() + 1), "sam", s).status_code == 401   # nor is the owner's code
assert login("correct horse battery", auth.totp(sam_secret, now()), "sam", s).status_code == 200
assert s.get("/api/admin/admins").json()["me"] == "sam"
assert s.delete("/api/admin/admins/admin").status_code == 400 and s.delete("/api/admin/admins/sam").status_code == 400
# reset 2FA signs sam out; the old code no longer works and sign-in starts enrolment again
assert c.post("/api/admin/admins/sam/reset-2fa").status_code == 200 and s.get("/api/admin/admins").status_code == 401
assert login("correct horse battery", user="sam", cl=s).json()["enroll"]["secret"] != sam_secret
# ---- change my password
pw = lambda cl, cur, new_: cl.post("/api/admin/password", json={"current": cur, "new": new_})
sam_secret = login("correct horse battery", user="sam", cl=s).json()["enroll"]["secret"]
auth._last_step.clear()
assert login("correct horse battery", auth.totp(sam_secret, now()), "sam", s).status_code == 200
s2 = new(); assert login("correct horse battery", auth.totp(sam_secret, now() + 1), "sam", s2).status_code == 200
assert pw(s, "wrong", "brand new password").status_code == 400 and pw(s, "correct horse battery", "short").status_code == 400
assert pw(s, "correct horse battery", "brand new password").status_code == 200
assert s.get("/api/admin/admins").status_code == 200 and s2.get("/api/admin/admins").status_code == 401   # other sessions end
auth._last_step.clear()
assert login("correct horse battery", auth.totp(sam_secret, now()), "sam", s2).status_code == 401
assert login("brand new password", auth.totp(sam_secret, now()), "sam", s2).status_code == 200
# the owner can too; the saved password then replaces the one from the environment
assert pw(c, "pw", "owner new password").status_code == 200 and "owner new password" not in auth.ADMINS_PATH.read_text()
o = new(); auth._last_step.clear()
assert login("pw", auth.totp(secret, now()), cl=o).status_code == 401
assert login("owner new password", auth.totp(secret, now()), cl=o).status_code == 200
assert pw(c, "owner new password", "pw" * 6).status_code == 200
auth._admins["admin"].pop("hash"); auth._admins["admin"].pop("salt"); auth._save()   # back to the environment password
# removing sam ends it
assert c.delete("/api/admin/admins/sam").status_code == 200 and "sam" not in saved()
assert login("brand new password", user="sam", cl=s).status_code == 401
auth._state["fails"] = 0

c.post("/api/admin/logout"); assert c.get("/api/admin/config").status_code == 401
# enrolled: password alone is refused, and so is a code that was already used
assert login().status_code == 401
assert login(code=auth.totp(secret, now())).status_code == 401
assert login(code=auth.totp(secret, now() + 1)).status_code == 200
# the owner's secret from before admins.json existed is picked up
auth.ADMINS_PATH.unlink(); auth.LEGACY_TOTP_PATH.write_text(secret); auth._admins = None; auth._last_step.clear()
assert login().status_code == 401 and login(code=auth.totp(secret, now())).status_code == 200
# five failures lock the login, even for the right code
auth._state["fails"] = 0
for _ in range(auth.MAX_FAILS): assert login("wrong").status_code == 401
auth._last_step.clear()
assert login(code=auth.totp(secret, now())).status_code == 429
print("ok")
