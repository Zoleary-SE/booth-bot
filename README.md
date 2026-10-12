# Booth Bot — Zscaler AI Guard booth demo

A deliberately leaky "intern-built" chatbot for a fictional company, with seasonal themes (see "Themes").
Visitors scan a QR code, try to make the bot spill fake confidential
data, and **Zscaler AI Guard (DAS/API mode)** blocks the prompt and/or the response.
A live verdict panel shows what fired.

All confidential data is fictional. Card numbers are Luhn-valid test patterns; keys are fake.

## What's here
- `app/main.py` — FastAPI: chat pipeline (scan prompt → LLM → scan response), admin API, stats
- `app/aiguard.py` — AI Guard DAS/API client (`execute-policy` / `resolve-and-execute-policy`)
- `app/llm.py` — LiteLLM wrapper (Bedrock / OpenAI / Anthropic / Azure / Ollama) with fallbacks
- `app/persona.py` + `app/data/globex_data.json` — the bot's system prompt and the fake data
- `app/themes.py` — seasonal themes: personality, greeting and default name/motto
- `app/auth.py` — admin sign-in: password + authenticator code, several admins
- `app/static/index.html` — booth chat + verdict panel
- `app/static/admin.html` — `/admin` page to set LLM + AI Guard, flip the guard, run tests, see stats

## Deploy
The app is one container listening on port 8080. Whatever you run it on, it needs:
- **HTTPS in front of it.** The admin session cookie is HTTPS-only (see "Admin sign-in").
- **Exactly one instance and one worker.** Settings, sessions, rate limits and stats live in process memory.
- **A persistent `/data` volume**, writable by UID 10001. It holds `config.json` (admin-page settings) and
  `admins.json` (admin accounts and authenticator secrets). Without it, both reset on every restart.
- **No caching** of responses by any CDN or proxy, and cookies passed through to the app.
- **Outbound HTTPS** to your LLM provider and to the AI Guard API host.
- **A strong `ADMIN_PASSWORD`.** The admin page stays disabled while it is still the `changeme` placeholder.

All settings can be seeded from environment variables (`.env.example` lists them) and changed later on `/admin`.

### Homelab (Docker Compose)
```bash
git clone <this repo> && cd booth-bot
cp .env.example .env      # set ADMIN_PASSWORD, AIGUARD_API_KEY and one LLM provider's credentials
docker compose up -d --build
# chat:  http://<host>:8080/      admin: http://<host>:8080/admin
```
To put it on the internet, front it with HTTPS: a reverse proxy you already run (Caddy, Traefik, nginx) or a
tunnel service, forwarding to port 8080.

LAN only, over plain HTTP: set `ADMIN_COOKIE_SECURE=false`, or the browser drops the admin session cookie
(`http://localhost` is fine without it).

### AWS (EC2 + CloudFront)
This is the layout the demo has been run on at a show.
1. **Instance:** a small Linux instance (for example `t4g.small` on Amazon Linux 2023) with Docker installed. Build
   the image for the instance's CPU (`linux/arm64` on Graviton, `linux/amd64` otherwise) and push it to a registry
   the instance can pull from (ECR or Docker Hub).
2. **Instance role:** attach `AmazonSSMManagedInstanceCore` so you can use Session Manager instead of SSH. If the LLM
   is on Bedrock, also allow `bedrock:InvokeModel` on the model you use and leave the AWS key variables blank; the
   app then uses the role.
3. **Security group:** allow inbound TCP 8080 only from the CloudFront origin-facing managed prefix list
   (`com.amazonaws.global.cloudfront.origin-facing`). Nothing else inbound.
4. **Run it** with the settings in a root-only env file and a named volume for `/data`:
   ```bash
   docker run -d --name booth-bot --restart unless-stopped -p 8080:8080 \
     --env-file booth.env -v booth-config:/data <registry>/booth-bot:<tag>
   ```
   If the volume was created by something else, hand it to the image's `app` user first:
   `docker run --rm --user 0 -v booth-config:/data --entrypoint chown <image> -R app:app /data`.
5. **CloudFront:** one distribution with the instance's public DNS name as a custom origin (HTTP, port 8080).
   Viewer protocol policy "Redirect HTTP to HTTPS", all HTTP methods allowed, the managed `CachingDisabled` cache
   policy and the managed `AllViewerExceptHostHeader` origin request policy (so cookies and request bodies reach
   the app). Add the managed security-headers response policy if you like.
6. **Optional, AWS WAF** on the distribution: the Amazon IP reputation and known-bad-inputs managed rule groups, plus
   rate-based rules on `/api/chat` and `/api/admin/*`.

Things to know: the instance's public DNS name changes when it is stopped and started, so update the origin (or use
an Elastic IP). The hop from CloudFront to the instance is plain HTTP; put an ALB with a certificate in between, or
TLS on the instance, if that matters to you. On ECS Fargate instead of EC2, keep the desired count at 1 and mount
EFS at `/data`.

### Azure (App Service for Containers)
These steps follow the Azure CLI documentation but have not been run end to end with this app.
```bash
az group create -n booth-bot -l <region>
az acr create -g booth-bot -n <registry> --sku Basic --admin-enabled true
az acr build -r <registry> -t booth-bot:latest .          # builds from this repo in Azure
az appservice plan create -g booth-bot -n booth-plan --is-linux --sku B1
az webapp create -g booth-bot -p booth-plan -n <app-name> \
  --container-image-name <registry>.azurecr.io/booth-bot:latest \
  --container-registry-url https://<registry>.azurecr.io \
  --container-registry-user <registry> \
  --container-registry-password "$(az acr credential show -n <registry> --query 'passwords[0].value' -o tsv)"
az webapp config appsettings set -g booth-bot -n <app-name> --settings \
  WEBSITES_PORT=8080 WEBSITES_ENABLE_APP_SERVICE_STORAGE=true CONFIG_PATH=/home/data/config.json \
  ADMIN_USER=<user> ADMIN_PASSWORD=<long password> AIGUARD_API_KEY=<key> \
  LLM_MODEL=azure/<deployment-name> AZURE_API_KEY=<key> \
  AZURE_API_BASE=https://<resource>.openai.azure.com AZURE_API_VERSION=<api version>
az webapp update -g booth-bot -n <app-name> --https-only true
az webapp config set -g booth-bot -n <app-name> --always-on true
```
- App Service gives you HTTPS at `https://<app-name>.azurewebsites.net` and keeps `/home` across restarts, which
  is why `CONFIG_PATH` points there instead of `/data`.
- Keep the plan at one instance (do not scale out).
- For the LLM, any provider works; the example uses Azure OpenAI through LiteLLM's `azure/<deployment-name>`.
- Prefer a VM? An Azure VM with Docker works exactly like the homelab steps, with a reverse proxy or tunnel for HTTPS.

## Admin sign-in (password + authenticator code)
`/admin` asks for the username, password and a 6-digit code from an authenticator app (TOTP). On the first sign-in,
leave the code blank: the page shows a QR code (and the key as text) to add to the app, then asks for a code to confirm. Five failed sign-ins
lock the login for a minute. Sessions last 12 hours and end when the container restarts.
- More admins: add them under "Admins" on the admin page (username + a password of 12+ characters). Each sets up
  their own authenticator at first sign-in. All admins have the same rights. They are stored in `/data/admins.json`
  (passwords as scrypt hashes); the owner stays `ADMIN_USER` / `ADMIN_PASSWORD` from the environment.
- Change my password: under "Admins"; it needs the current password and signs out that admin's other sessions.
  Once the owner changes theirs, the saved password replaces `ADMIN_PASSWORD`. Forgotten: another admin removes and
  re-adds the account; for the owner, delete `"salt"` and `"hash"` from their entry in `/data/admins.json` and
  restart, which falls back to `ADMIN_PASSWORD`.
- Lost the phone: another admin clicks "reset 2FA". With no other admin, set the owner's `"totp"` to `null` in
  `/data/admins.json` and restart the container.
- Plain HTTP on a LAN (not localhost): set `ADMIN_COOKIE_SECURE=false`, or the browser drops the session cookie.

## Config precedence
`/data/config.json` (edited on the admin page, persisted on a volume) → `.env` → defaults.
You can ship with almost-empty `.env` and fill everything in from `/admin` at the booth.

## Themes
Pick one under "Bot copy" on the admin page (or seed it with `BOT_THEME`): `general` (default), `halloween`,
`thanksgiving`, `winter`, `spring`, `summer`, `july4`. A theme changes the chat page colours, the greeting and the bot's personality, and resets the
bot name, tagline and canned replies to its defaults. Themes live in `app/themes.py`; colours are in
`app/static/index.html` under `[data-theme="<key>"]`.

## Guard ON/OFF
Toggle on the admin page (or `AIGUARD_ENABLED`). OFF = the bot leaks freely (the "shadow AI"
story). ON = same prompt gets blocked. That contrast is the demo.

## Capacity
~30 concurrent booth users is trivial: 1 uvicorn worker, async I/O. Keep it at 1 — config,
guard toggle, sessions and stats are held in process memory and would split across workers. Per-session rate
limit (default 10/min) and a 1000-char prompt cap keep one person from hogging it.

## Tests and security checks
- Each file in `tests/` is a standalone script: `python tests/test_auth.py` (needs the packages in `requirements.txt`).
- `pip-audit` on the built image should report no known vulnerabilities; re-run before each show.

## AI Guard notes (from the DAS/API docs)
- Endpoint: `execute-policy` when a policy ID is set, else `resolve-and-execute-policy`
  (policy bound to the key). Leave `AIGUARD_POLICY_ID` blank unless you have a reason —
  a stale ID fails closed and looks like an outage.
- Direction literals: SDK/most integrations use `IN`/`OUT`; the DAS Help page examples use
  `request`/`response`. Toggle with `AIGUARD_DIRECTION_STYLE` (default `IN_OUT`).
- Scan both prompt and response — prompt-only won't enforce the output side.
- Host: `api.us1.zseclipse.net` (regional) or `api.zseclipse.net` (global) per your tenant.
