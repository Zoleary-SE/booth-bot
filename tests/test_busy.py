import os, sys, pathlib, tempfile, asyncio
os.environ["CONFIG_PATH"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))
import main
from config import config

config.update({"aiguard": {"fail_closed": True}})
async def timeout_scan(*a, **kw):
    return {"action": "ERROR", "error": "All detectors are either not found or had execution errors", "triggered": [], "detectors": []}
main.scan = timeout_scan
o = asyncio.run(main.chat(main.ChatIn(message="hi", session_id="session-1")))
assert o["stage"] == "guard_busy" and o["reply"] == config.get()["bot"]["guard_busy_message"]
assert main.stats["prompt_blocks"] == 0 and main.stats["errors"] == 1
print("ok")
