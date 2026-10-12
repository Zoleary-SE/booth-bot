import os, sys, pathlib, tempfile, asyncio
os.environ["CONFIG_PATH"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))
import main

gate = asyncio.Event()
async def slow_chat(body):
    await gate.wait()
    return {"stage": "allowed"}
main._chat = slow_chat

async def run():
    first = asyncio.create_task(main.chat(main.ChatIn(message="a", session_id="session-1")))
    await asyncio.sleep(0)
    second = await main.chat(main.ChatIn(message="b", session_id="session-1"))   # same visitor, still waiting
    other = asyncio.create_task(main.chat(main.ChatIn(message="c", session_id="session-2")))  # different visitor
    await asyncio.sleep(0)
    gate.set()
    assert second.status_code == 429
    assert (await first)["stage"] == "allowed" and (await other)["stage"] == "allowed"
    assert not main.inflight
    assert (await main.chat(main.ChatIn(message="d", session_id="session-1")))["stage"] == "allowed"  # free again
asyncio.run(run()); print("ok")
