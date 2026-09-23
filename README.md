# BizApp247 AI Avatar — "Bailey"

A live, talking video avatar that greets visitors on the BizApp247 website, has a real
voice conversation with them, and guides them through a six-field demo request form.
On submit the lead is pushed into GoHighLevel, which triggers a real outbound AI phone
call a few minutes later — so the visitor experiences the product by being a customer of it.

Built for BizApp247 (powered by Baytech Companies) on [LiveKit Agents](https://docs.livekit.io/agents/).

---

## How it works

```
  Visitor's browser                    Your infra                   LiveKit Cloud
 ┌──────────────────┐         ┌──────────────────────┐        ┌────────────────────┐
 │  widget/         │  POST   │  server/ (FastAPI)   │        │                    │
 │  index.html      │ ──────► │  /api/token          │        │   Room             │
 │  widget.js       │ ◄────── │  mints JWT +         │───────►│   + agent dispatch │
 │                  │  token  │  agent dispatch      │        │     "aria"         │
 │                  │         │                      │        │        │           │
 │   WebRTC ────────┼─────────────────────────────────────────┼────────┤           │
 │   • avatar video │         │                      │        │        ▼           │
 │   • avatar audio │         │                      │        │  livekit_agent/    │
 │   • visitor mic  │         │                      │        │  app.py (worker)   │
 │   • data channel │         │                      │        │   STT→LLM→TTS      │
 │     topic:"form" │         │                      │        │   → LiveAvatar     │
 │                  │  POST   │  /api/lead           │        └────────────────────┘
 │  submit ─────────┼───────► │  ──► GoHighLevel     │
 │                  │ ◄────── │      webhook         │
 └──────────────────┘redirect └──────────────────────┘
```

The widget and the agent stay in sync over a **LiveKit data channel** on topic `form`.
The browser is the source of truth for form state; it emits three events:

| Event | Sent when | Agent reaction |
|---|---|---|
| `session_started` | Room connected, tracks up | Delivers the spoken welcome, asks what business they run |
| `field_completed` | A field blurs/changes with a value | Acknowledges it and guides to the next field |
| `form_submitted` | `/api/lead` returned OK | Says goodbye; widget waits 6s, then redirects |

Field-level validity travels with the event, so a mistyped email makes Bailey *verbally*
nudge the visitor rather than just flashing a red border. Fields can be completed in any
order, and the agent serializes its replies through a queue — if a visitor tabs through
the form faster than the avatar can speak, stale acknowledgements are dropped and only
the newest one is spoken.

If the LiveKit session fails to start, the widget degrades gracefully: the avatar stage
shows an error and **the form still works**.

---

## Repository layout

| Path | What it is |
|---|---|
| `livekit_agent/` | The LiveKit agent worker — voice pipeline, avatar, form-flow logic |
| `livekit_agent/app.py` | Session setup, data-channel handler, instruction queue |
| `livekit_agent/prompts.py` | Bailey's persona, welcome script, per-field copy, validation nudges |
| `livekit_agent/Dockerfile` | Production image for the worker (Python 3.13 slim, non-root) |
| `livekit_agent/livekit.toml` | LiveKit Cloud project + agent binding |
| `server/` | FastAPI token + lead service |
| `server/main.py` | `/api/token` and `/api/lead` |
| `server/test_token.py` | Prints a decoded JWT to verify agent dispatch is encoded correctly |
| `widget/` | The embeddable front end — vanilla ES modules, no build step |
| `wordpress/embed-snippet.html` | Iframe snippet to paste into the WordPress hero |

---

## The AI pipeline

Speech-to-text, the LLM, and text-to-speech all run through **LiveKit's inference
gateway**, so they are billed and authenticated through your LiveKit Cloud project —
you do *not* need separate provider API keys for these three.

| Stage | Model |
|---|---|
| STT | `deepgram/flux-general-multi` |
| LLM | `google/gemini-2.5-flash` |
| TTS | `cartesia/sonic-3` (voice `a33f7a4c…`, emotion `happy`) |
| VAD | Silero |
| Turn detection | LiveKit turn detector |
| Noise cancellation | ai-coustics `QUAIL_VF_S` on the visitor's mic |
| Video avatar | **LiveAvatar** (`avatar_id` set in `app.py`) |

A Tavus avatar session is left commented out in `app.py` as a drop-in alternative —
swap the two blocks and set `TAVUS_API_KEY` / `TAVUS_REPLICA_ID` / `TAVUS_PERSONA_ID`.

---

## Environment variables

There is one `.env` per service. Copy `.env.example` into **both** `server/` and
`livekit_agent/` and fill in the values.

**`server/.env`**

| Variable | Required | Notes |
|---|---|---|
| `LIVEKIT_URL` | yes | `wss://<subdomain>.livekit.cloud` |
| `LIVEKIT_API_KEY` | yes | |
| `LIVEKIT_API_SECRET` | yes | |
| `GHL_INBOUND_WEBHOOK_URL` | yes | GoHighLevel inbound webhook trigger |
| `DEMO_PAGE_URL` | no | Defaults to `https://bizapp247.com/demo` |
| `ALLOWED_ORIGINS` | no | Comma-separated. Defaults to `*` — **lock this down in production** |

**`livekit_agent/.env`**

| Variable | Required | Notes |
|---|---|---|
| `LIVEKIT_URL` | yes | |
| `LIVEKIT_API_KEY` | yes | |
| `LIVEKIT_API_SECRET` | yes | |
| `LIVEAVATAR_API_KEY` | yes | For the video avatar |

> `.env` files are gitignored. Never commit real credentials — `.env.example` is a
> template and must stay empty.

---

## Running locally

### 1. Token + lead server

```bash
cd server
python -m venv .venv && .venv\Scripts\activate    # Windows
# source .venv/bin/activate                       # macOS / Linux
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

Sanity-check that the minted token carries the agent dispatch:

```bash
python test_token.py    # roomConfig.agents[0].agentName should be "aria"
```

### 2. Agent worker

```bash
cd livekit_agent
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python app.py download-files    # pre-fetch Silero / turn-detector weights
python app.py dev
```

The worker connects to LiveKit Cloud and idles until a room requests agent `aria`.

### 3. Widget

The widget is plain ES modules — no bundler. Serve the folder statically:

```bash
cd widget
python -m http.server 5173
```

Point it at your API by setting `window.BIZAPP_API_BASE` in `index.html` before
`widget.js` loads:

```html
<script>window.BIZAPP_API_BASE = "http://localhost:8080";</script>
```

Because the browser needs microphone access, the page must be served over **HTTPS or
`localhost`**. To test from a phone or from inside WordPress, expose the API with a
tunnel (ngrok, Cloudflare Tunnel) and set `BIZAPP_API_BASE` to that URL.

---

## Deploying

**Agent** — deploy the worker to LiveKit Cloud:

```bash
cd livekit_agent
lk agent deploy
```

`livekit.toml` already points at the project subdomain and agent ID. The Dockerfile runs
`download-files` at build time so cold starts don't stall on model downloads.

**Server** — any container or VM host (Railway, Fly, Render, a VPS). It's a standard ASGI
app: `uvicorn main:app --host 0.0.0.0 --port $PORT`. Set `ALLOWED_ORIGINS` to the
WordPress domain(s).

**Widget** — static hosting (Vercel, Cloudflare Pages, S3). A Vercel project
(`avatar_widget`) is already linked in `widget/.vercel`.

**WordPress** — paste `wordpress/embed-snippet.html` into a Custom HTML block in the hero
and point the iframe `src` at your widget host. The `allow="microphone"` attribute is
mandatory; without it the mic prompt silently fails inside the iframe.

---

## Operational notes

- **Token TTL is 20 minutes**, which caps how long one visitor can burn avatar minutes.
- **Rate limiting** is a naive in-process dict with a 60-second window per `client_id`.
  It resets on restart and does not work across replicas — move it to Redis before
  running more than one server instance.
- `client_id` is a `crypto.randomUUID()` stored in the visitor's `localStorage`, so the
  rate limit is trivially bypassable by clearing storage. It's a bot speed bump, not a
  security control.
- **Bailey never reads emails or phone numbers aloud.** This is enforced in `prompts.py`
  (a hard rule) and by the `SPEAKABLE` allowlist in `app.py`, since the widget may sit on
  a page the visitor is screen-sharing.
- Rooms are named `bizapp-demo-<uuid12>` and are single-use.
- Leads are tagged `avatar-demo-lead` plus `industry-<slug>`; the GoHighLevel workflow
  keys off those tags to schedule the outbound call.

---

## License

Proprietary — © Baytech Companies. All rights reserved.
