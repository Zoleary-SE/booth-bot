import os, sys, pathlib, tempfile, asyncio
os.environ["CONFIG_PATH"] = os.path.join(tempfile.mkdtemp(), "config.json")
root = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "app"))
import main, themes
from config import config
from persona import build_system_prompt

index, admin = (root / "app/static/index.html").read_text(), (root / "app/static/admin.html").read_text()
for key, t in themes.THEMES.items():
    assert f'<option value="{key}">' in admin                      # every theme is pickable on the admin page
    assert key == "general" or f'[data-theme="{key}"]' in index   # ...and has colours on the chat page
    config.update({"bot": {"name": "Custom", "theme": key}})        # explicit fields still win over the theme's
    assert config.get()["bot"]["name"] == "Custom"
    config.update({"bot": {"theme": key}})
    bot = config.get()["bot"]
    assert bot["theme"] == key and all(bot[k] == t[k] for k in themes.COPY_KEYS)
    b = asyncio.run(main.bootstrap())
    assert b["theme"] == key and t["name"] in b["greeting"] and "{" not in b["greeting"]
    page = asyncio.run(main.index()).body.decode()
    assert f'<html lang="en" data-theme="{key}">' in page and t["name"] in page
    prompt = build_system_prompt(config.get())
    assert t["personality"] in prompt and t["ex_close"] in prompt
# an unknown theme is ignored
config.update({"bot": {"theme": "nope"}})
assert config.get()["bot"]["theme"] == "general"
print("ok")
