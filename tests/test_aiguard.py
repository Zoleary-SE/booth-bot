import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))
from aiguard import parse_verdict, is_blocked

ERR = "All detectors are either not found or had execution errors"
cfg = {"aiguard": {"fail_closed": True}}

# detector timeout: ALLOW + errorMsg -> ERROR -> blocked when fail_closed
v = parse_verdict({"action": "ALLOW", "errorMsg": ERR, "detectorResponses": {}})
assert v["action"] == "ERROR" and is_blocked(v, cfg)
assert not is_blocked(v, {"aiguard": {"fail_closed": False}})
# clean allow untouched
assert parse_verdict({"action": "ALLOW", "detectorResponses": {"pii": {"triggered": False}}})["action"] == "ALLOW"
# real block with an errorMsg stays BLOCK
assert parse_verdict({"action": "BLOCK", "errorMsg": ERR, "detectorResponses": {"pii": {"triggered": True, "action": "BLOCK"}}})["action"] == "BLOCK"
print("ok")

# X-User-Name / X-Conversation-Id reach AI Guard
import asyncio, httpx, aiguard
seen = {}
def handler(req):
    seen.update(req.headers)
    return httpx.Response(200, json={"action": "ALLOW", "detectorResponses": {}})
real = httpx.AsyncClient
aiguard.httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
scfg = {"aiguard": {"api_base": "https://x", "api_key": "k"}}
v = asyncio.run(aiguard.scan("hi", "prompt", scfg, conversation_id="c1", user_name="booth-visitor-abc"))
assert seen["x-user-name"] == "booth-visitor-abc" and seen["x-conversation-id"] == "c1" and v["user"] == "booth-visitor-abc"
print("headers ok")

# retry: first call times out (500 + all-detector errors), second succeeds
calls = []
def flaky(req):
    calls.append(1)
    if len(calls) == 1:
        return httpx.Response(200, json={"statusCode": 500, "action": "ALLOW", "errorMsg": ERR, "detectorResponses": {"pii": {"statusCode": 504}}})
    return httpx.Response(200, json={"action": "BLOCK", "detectorResponses": {"pii": {"triggered": True, "action": "BLOCK"}}})
aiguard.httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(flaky), **kw)
v = asyncio.run(aiguard.scan("x", "prompt", {"aiguard": {**scfg["aiguard"], "retries": 1}}))
assert v["action"] == "BLOCK" and v["attempts"] == 2 and len(calls) == 2
calls.clear()
v = asyncio.run(aiguard.scan("x", "prompt", {"aiguard": {**scfg["aiguard"], "retries": 0}}))
assert v["action"] == "ERROR" and v["attempts"] == 1 and len(calls) == 1
print("retry ok")

# the detectors that timed out are named
v = parse_verdict({"action": "ALLOW", "errorMsg": ERR, "detectorResponses": {"pii": {"statusCode": 504}, "code": {"statusCode": 504}, "gibberish": {"statusCode": 200, "triggered": False}}})
assert v["timed_out"] == ["pii", "code"], v["timed_out"]
print("timed_out ok")
