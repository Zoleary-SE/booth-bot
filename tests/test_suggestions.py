import os, sys, pathlib, tempfile
os.environ["CONFIG_PATH"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))
import asyncio, main
from config import config

on = [x["text"] for x in config.get()["bot"]["suggestions"] if x["enabled"]]
b = asyncio.run(main.bootstrap())
assert b["suggestions"] == on and len(on) >= 15
# admin switches the first one off -> it drops off the chat page
s = config.get()["bot"]["suggestions"]; s[0]["enabled"] = False
config.update({"bot": {"suggestions": s}})
assert s[0]["text"] not in asyncio.run(main.bootstrap())["suggestions"]
print("ok")
