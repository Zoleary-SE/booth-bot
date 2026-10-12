"""
Zscaler AI Guard - DAS/API mode client.

POST {api_base}/v1/detection/execute-policy              (when policy_id is set)
POST {api_base}/v1/detection/resolve-and-execute-policy  (when policy_id is blank)

Headers: Authorization: Bearer <api key>
Body:    {"content": "...", "direction": "IN"|"OUT", "policyId": "...", "transactionId": "..."}

Response (top level): transactionId, statusCode, errorMsg, action, severity,
                      direction, detectorResponses[], throttlingDetails
"""
import time
import uuid

import httpx

DIRECTION_MAP = {
    "IN_OUT": {"prompt": "IN", "response": "OUT"},
    "REQ_RESP": {"prompt": "request", "response": "response"},
}


def _detector_name(d: dict) -> str:
    for k in ("detectorName", "detector", "name", "type", "detectorType", "id"):
        if d.get(k):
            return str(d[k])
    return "detector"


def parse_verdict(raw: dict) -> dict:
    """Normalise whatever AI Guard returns into a stable shape for the UI."""
    action = str(raw.get("action") or "").upper()
    responses = raw.get("detectorResponses") or []
    # The API returns {detector_name: {...}}; older docs show a list of objects.
    items_dict = responses if isinstance(responses, dict) else {}
    items = responses.items() if isinstance(responses, dict) else ((None, d) for d in responses)
    detectors = []
    for key, d in items:
        if not isinstance(d, dict):
            continue
        name = key or _detector_name(d)
        entity_types = (d.get("details") or {}).get("detectedEntityTypes") if isinstance(d.get("details"), dict) else None
        if d.get("triggered") and entity_types:
            name = f"{name} ({', '.join(entity_types)})"
        detectors.append(
            {
                "name": name,
                "triggered": bool(d.get("triggered")),
                "action": str(d.get("action") or "").upper(),
                "severity": d.get("severity"),
                "details": d.get("details"),
                "latency_ms": d.get("latency"),
            }
        )
    detectors.sort(key=lambda d: (not d["triggered"], d["action"] != "BLOCK", d["name"]))
    triggered = [d for d in detectors if d["triggered"]]
    # AI Guard reports detector timeouts as ALLOW + errorMsg; treat as ERROR so fail_closed applies.
    if raw.get("errorMsg") and not triggered and action != "BLOCK":
        action = "ERROR"
    return {
        "action": action or "UNKNOWN",
        "severity": raw.get("severity"),
        "transaction_id": raw.get("transactionId"),
        "policy": raw.get("policyName") or raw.get("policyId"),
        "status_code": raw.get("statusCode"),
        "error": raw.get("errorMsg") or None,
        "detectors": detectors,
        "triggered": [d["name"] for d in triggered],
        "blocking": [d["name"] for d in triggered if d["action"] == "BLOCK"],
        # detectors that answered with a non-200 (504 = that detector timed out)
        "timed_out": [k for k, d in items_dict.items() if isinstance(d, dict) and d.get("statusCode") not in (None, 200)],
        "throttling": raw.get("throttlingDetails"),
    }


async def scan(content: str, stage: str, cfg: dict, conversation_id: str | None = None, user_name: str | None = None) -> dict:
    """Scan, retrying when AI Guard errors/times out (it intermittently 504s every detector)."""
    tries = 1 + max(0, int(cfg["aiguard"].get("retries", 1)))
    for attempt in range(1, tries + 1):
        v = await _scan_once(content, stage, cfg, conversation_id, user_name)
        if v["action"] != "ERROR":
            break
    v["attempts"] = attempt
    return v


async def _scan_once(content: str, stage: str, cfg: dict, conversation_id: str | None = None, user_name: str | None = None) -> dict:
    """
    stage: "prompt" or "response".
    Returns a verdict dict; never raises. On transport/API error returns
    action "ERROR" with the message so the UI can show it.
    """
    ag = cfg["aiguard"]
    style = DIRECTION_MAP.get(ag.get("direction_style", "IN_OUT"), DIRECTION_MAP["IN_OUT"])
    direction = style[stage]
    policy_id = (ag.get("policy_id") or "").strip()
    path = "/v1/detection/execute-policy" if policy_id else "/v1/detection/resolve-and-execute-policy"
    url = ag["api_base"].rstrip("/") + path

    body = {"content": content or "", "direction": direction, "transactionId": str(uuid.uuid4())}
    if policy_id:
        body["policyId"] = policy_id
    headers = {
        "Authorization": f"Bearer {ag.get('api_key', '')}",
        "Content-Type": "application/json",
    }
    if conversation_id:
        headers["X-Conversation-Id"] = conversation_id
    if user_name:
        headers["X-User-Name"] = user_name

    t0 = time.perf_counter()

    def error(message: str, raw=None) -> dict:
        return {"action": "ERROR", "error": message, "latency_ms": int((time.perf_counter() - t0) * 1000), "direction": direction,
                "endpoint": path, "raw": raw, "detectors": [], "triggered": [], "blocking": []}

    try:
        async with httpx.AsyncClient(timeout=ag.get("timeout", 15)) as client:
            r = await client.post(url, json=body, headers=headers)
        elapsed = int((time.perf_counter() - t0) * 1000)
        try:
            raw = r.json()
        except Exception:
            raw = {"errorMsg": r.text[:300]}
        if r.status_code >= 400:
            return error(f"HTTP {r.status_code}: {raw.get('errorMsg') or raw.get('message') or raw}", raw)
        v = parse_verdict(raw)
        v["policy"] = v["policy"] or policy_id or None
        v.update({"latency_ms": elapsed, "direction": direction, "endpoint": path, "raw": raw, "user": user_name})
        return v
    except Exception as e:
        return error(f"{type(e).__name__}: {e}")


def is_blocked(verdict: dict, cfg: dict) -> bool:
    ag = cfg["aiguard"]
    a = verdict.get("action")
    if a == "BLOCK":
        return True
    if a == "DETECT" and ag.get("block_on_detect"):
        return True
    if a in ("ERROR", "UNKNOWN") and ag.get("fail_closed"):
        return True
    return False
