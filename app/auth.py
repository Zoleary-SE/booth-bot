"""
Admin login: username + password + a 6-digit authenticator code (TOTP, RFC 6238), then a session cookie.

The owner is ADMIN_USER / ADMIN_PASSWORD from the environment. More admins are added on the admin page and kept
in admins.json next to config.json (0600; passwords as scrypt hashes). All admins have the same rights.
Anyone can change their own password; once the owner does, the saved hash replaces ADMIN_PASSWORD (remove the
owner's "salt" and "hash" from admins.json to fall back to the environment).

Each admin's TOTP secret is created on their first successful password login and shown once for enrolment; it is
only saved after a code from the authenticator app confirms it. "reset 2FA" on the admin page (or, for the owner
with no other admin around, deleting their "totp" entry in admins.json) starts enrolment again.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import time
from pathlib import Path

import segno
from fastapi import HTTPException, Request

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")
_DATA = Path(os.getenv("CONFIG_PATH", "/data/config.json")).parent
ADMINS_PATH = _DATA / "admins.json"
LEGACY_TOTP_PATH = _DATA / "admin_totp"  # the owner's secret before there were several admins
COOKIE = "admin_session"
# Browsers only send a Secure cookie over HTTPS (or localhost). Set false for a plain-HTTP homelab.
COOKIE_SECURE = os.getenv("ADMIN_COOKIE_SECURE", "true").strip().lower() in ("1", "true", "yes", "on")
DEFAULT_PASSWORDS = {"", "changeme"}  # what .env.example ships with; never accepted
SESSION_TTL = 12 * 3600
MAX_FAILS, LOCK_SECONDS = 5, 60
MIN_PASSWORD = 12
USERNAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{1,31}")

_sessions: dict[str, tuple[str, float]] = {}  # token -> (username, expiry); in memory, so a restart logs everyone out
_state = {"fails": 0, "locked_until": 0.0}
_last_step: dict[str, int] = {}   # username -> last TOTP step accepted (a code can't be replayed)
_pending: dict[str, str] = {}     # username -> secret shown for enrolment, not yet confirmed
_admins: dict | None = None       # username -> {"salt", "hash", "totp"}; the owner has no hash until they change their password


def _load() -> dict:
    global _admins
    if _admins is None:
        # A file that exists but can't be parsed raises: better no admin login than one with 2FA silently reset.
        data = json.loads(ADMINS_PATH.read_text()) if ADMINS_PATH.exists() else {}
        owner = data.setdefault(ADMIN_USER, {})
        if not owner.get("totp") and LEGACY_TOTP_PATH.exists():
            owner["totp"] = LEGACY_TOTP_PATH.read_text().strip() or None
        _admins = data
    return _admins


def _save():
    try:
        ADMINS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = ADMINS_PATH.with_suffix(".tmp")
        tmp.touch(mode=0o600)
        tmp.write_text(json.dumps(_admins, indent=2))
        os.replace(tmp, ADMINS_PATH)
    except Exception:
        # Read-only filesystem or missing volume: held in memory, so added admins and enrolment are lost on restart.
        pass


def _hash(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1).hex()


def _password_ok(username: str, password: str) -> bool:
    rec = _load().get(username) or {}
    if username == ADMIN_USER and not rec.get("hash"):
        if ADMIN_PASSWORD in DEFAULT_PASSWORDS:
            raise HTTPException(503, "the admin page is disabled until ADMIN_PASSWORD is changed from the default")
        return hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode())
    # Unknown users still cost one hash, so response time doesn't reveal which usernames exist.
    return hmac.compare_digest(_hash(password, rec.get("salt") or "00" * 16), rec.get("hash") or "")


def totp(secret: str, step: int) -> str:
    digest = hmac.new(base64.b32decode(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    o = digest[-1] & 0x0F
    return f"{(struct.unpack('>I', digest[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def _code_ok(username: str, secret: str, code: str) -> bool:
    """Accept the current 30s step or one either side (clock drift). A code can't be used twice."""
    code = "".join(str(code or "").split())
    now_step = int(time.time() // 30)
    for step in (now_step - 1, now_step, now_step + 1):
        if step > _last_step.get(username, 0) and hmac.compare_digest(totp(secret, step), code):
            _last_step[username] = step
            return True
    return False


def login(username: str, password: str, code: str) -> dict:
    """{"token", "user"} on success, {"enroll": {...}} when the authenticator still has to be set up; 401/429 otherwise."""
    now = time.time()
    username = str(username).strip().lower()
    if username == ADMIN_USER.lower():
        username = ADMIN_USER
    # ponytail: one global lockout, not per IP or per user (the client IP isn't trustworthy behind a CDN or proxy).
    # An attacker can keep the login locked; per-IP counters if that ever matters.
    if now < _state["locked_until"]:
        raise HTTPException(429, "too many failed logins, wait a minute and try again")
    ok = _password_ok(username, str(password))
    if ok:
        rec = _load()[username]
        if not rec.get("totp"):
            pending = _pending.setdefault(username, base64.b32encode(secrets.token_bytes(20)).decode())
            if not code:
                uri = f"otpauth://totp/BoothBot:{username}?secret={pending}&issuer=BoothBot"
                # The QR is drawn here, not by a script on the page or a web service: the secret stays between us and the admin.
                return {"enroll": {"secret": pending, "uri": uri, "qr": segno.make(uri, error="m").svg_data_uri(scale=5, border=3, light="#fff")}}
            ok = _code_ok(username, pending, code)
            if ok:
                rec["totp"] = _pending.pop(username)
                _save()
        else:
            ok = _code_ok(username, rec["totp"], code)
    if not ok:
        _state["fails"] += 1
        if _state["fails"] >= MAX_FAILS:
            _state["fails"], _state["locked_until"] = 0, now + LOCK_SECONDS
        raise HTTPException(401, "wrong username, password or code")
    _state["fails"] = 0
    for t in [t for t, (_, exp) in _sessions.items() if exp < now]:
        del _sessions[t]
    token = secrets.token_urlsafe(32)
    _sessions[token] = (username, now + SESSION_TTL)
    return {"token": token, "user": username}


def user_of(request: Request) -> str | None:
    user, exp = _sessions.get(request.cookies.get(COOKIE, ""), (None, 0))
    return user if exp > time.time() else None


def logout(request: Request):
    _sessions.pop(request.cookies.get(COOKIE, ""), None)


def require_admin(request: Request) -> str:
    user = user_of(request)
    if not user:
        raise HTTPException(401, "login required")
    return user


def _end_sessions(username: str, keep: str | None = None):
    for t in [t for t, (u, _) in _sessions.items() if u == username and t != keep]:
        del _sessions[t]


def change_password(request: Request, current: str, new: str):
    """Change the signed-in admin's own password. Their other sessions are signed out."""
    username = require_admin(request)
    # 400, not 401: the admin page treats 401 as "session gone" and reloads to the sign-in form.
    if not _password_ok(username, str(current)):
        raise HTTPException(400, "current password is wrong")
    if len(str(new)) < MIN_PASSWORD:
        raise HTTPException(400, f"new password must be at least {MIN_PASSWORD} characters")
    salt = secrets.token_hex(16)
    _load()[username].update(salt=salt, hash=_hash(str(new), salt))
    _save()
    _end_sessions(username, keep=request.cookies.get(COOKIE))


# ---------- managing admins (any signed-in admin) ----------
def list_admins() -> list[dict]:
    return [{"username": u, "owner": u == ADMIN_USER, "enrolled": bool(r.get("totp"))} for u, r in _load().items()
            if u == ADMIN_USER or r.get("hash")]


def add_admin(username: str, password: str):
    username = str(username).strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise HTTPException(400, "username: 2-32 characters, lowercase letters, digits, dot, dash or underscore")
    if username == ADMIN_USER.lower() or username in _load():
        raise HTTPException(409, "that username is taken")
    if len(str(password)) < MIN_PASSWORD:
        raise HTTPException(400, f"password must be at least {MIN_PASSWORD} characters")
    salt = secrets.token_hex(16)
    _load()[username] = {"salt": salt, "hash": _hash(str(password), salt), "totp": None}
    _save()


def remove_admin(username: str, by: str):
    if username == ADMIN_USER:
        raise HTTPException(400, "the owner account comes from the server's environment and can't be removed here")
    if username == by:
        raise HTTPException(400, "you can't remove yourself; ask another admin")
    if _load().pop(username, None) is None:
        raise HTTPException(404, "no such admin")
    _save()
    _end_sessions(username)
    _pending.pop(username, None)


def reset_2fa(username: str):
    """Forget an admin's authenticator; their next password sign-in sets up a new one."""
    rec = _load().get(username)
    if rec is None:
        raise HTTPException(404, "no such admin")
    rec["totp"] = None
    _save()
    _end_sessions(username)
    _pending.pop(username, None)
