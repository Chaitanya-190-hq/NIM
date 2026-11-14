# NVIDIA // BRIDGE — MVP prototype

Paste an existing NVIDIA Python API configuration, have the app extract the
key / base URL / model id, test the connection, and chat with the model through
NVIDIA's OpenAI-compatible Chat Completions API.

- **Frontend:** React + Vite (port `3000`)
- **Backend:** Python + FastAPI (port `8000`), OpenAI Python SDK
- **Default endpoint:** `https://integrate.api.nvidia.com/v1`
- **No database, no auth, no Firebase, no mock replies.** Credentials live in
  backend process memory only and disappear when the server restarts.

## Layout

```
backend/
  main.py            FastAPI app: config store, connection test, chat proxy
  nvidia_config.py   AST/regex parser — parses pasted code, never runs it
  requirements.txt
frontend/
  src/App.jsx        paste box, model list, chat
  src/styles.css
  vite.config.js     proxies /api -> http://127.0.0.1:8000
examples/
  sample_nvidia_config.py
```

## Setup

Requires Python 3.10+ and Node 18+.

```bash
# 1. backend
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. frontend (in a second terminal)
cd frontend
npm install
```

Nothing needs to be configured in `.env`; you paste the key in the browser and
it is sent only to the local backend. No key is committed to the repo.

## Start both services

```bash
# terminal 1 — FastAPI on http://127.0.0.1:8000
cd backend
source .venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

```bash
# terminal 2 — Vite on http://localhost:3000
cd frontend
npm run dev
```

Open <http://localhost:3000>.

## Sample Python configuration

Paste this into the editor and replace the key with your own NVIDIA key from
<https://build.nvidia.com> (it starts with `nvapi-`). The same content is in
[examples/sample_nvidia_config.py](examples/sample_nvidia_config.py).

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key="nvapi-YOUR_KEY_HERE",
)

completion = client.chat.completions.create(
    model="openai/gpt-oss-20b",
    messages=[{"role": "user", "content": "Hello"}],
    temperature=0.2,
    top_p=0.7,
    max_tokens=512,
)
```

Model ids are retired over time. To list the models your key can use:

```bash
curl -s https://integrate.api.nvidia.com/v1/models | python3 -c 'import sys,json;print("\n".join(m["id"] for m in json.load(sys.stdin)["data"]))'
```

These shapes are also understood:

- `NVIDIA_API_KEY = "nvapi-..."` / `BASE_URL = "..."` / `MODEL = "..."`
- `{"api_key": "...", "base_url": "...", "model": "..."}`
- `headers = {"Authorization": "Bearer nvapi-..."}`

A key read from an environment variable (`os.environ["NVIDIA_API_KEY"]`) cannot
be extracted — paste the key value itself, or a snippet that contains it.

## Connection test

Press **Connect**. The extracted settings are shown on the model card (the key
is masked, e.g. `nvapi-…9f31`), and the backend sends a tiny 8-token request to
the model. The card shows `connected` or `failed` with the reason; `retry test`
repeats it.

The same thing from the command line:

```bash
curl -s http://127.0.0.1:8000/api/configs \
  -H 'Content-Type: application/json' \
  -d '{"python_code":"from openai import OpenAI\nclient = OpenAI(base_url=\"https://integrate.api.nvidia.com/v1\", api_key=\"nvapi-REAL_KEY\", model=\"openai/gpt-oss-20b\")"}'

# use the returned id
curl -s -X POST http://127.0.0.1:8000/api/configs/<id>/test
```

Expected on success: `"status":"connected"`. A bad key returns
`status: failed` with *"NVIDIA rejected the API key (HTTP 401)…"*; a bad model
id returns the 404 explanation.

## Test chat

```bash
curl -s http://127.0.0.1:8000/api/configs/<id>/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Say hello in exactly five words."}'
```

Expected: `config.messages` contains your prompt and a real model reply. The
inputs in the browser do the same, and history is kept for follow-up messages.
**Clear Chat** empties the thread; **remove** drops the model and its key.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | liveness + limits |
| GET | `/api/configs` | stored configs (keys masked) |
| POST | `/api/configs` | parse `{"python_code": "..."}` and store |
| POST | `/api/configs/{id}/test` | small live NVIDIA request |
| POST | `/api/configs/{id}/chat` | `{"message": "..."}` → reply + history |
| POST | `/api/configs/{id}/clear` | clear chat history |
| DELETE | `/api/configs/{id}` | forget the model and its key |

Interactive docs: <http://127.0.0.1:8000/docs>.

## Security notes

- The pasted Python is parsed with `ast`/regex only — it is never executed.
- The key is held in the FastAPI process (`CONFIGS` dict), never in the browser,
  `localStorage`, `sessionStorage`, a cookie, a file or the repo.
- `GET /api/configs` and every response use a masked preview
  (`nvapi-…9f31`); the raw key is not returned anywhere.
- Errors are scrubbed of `nvapi-…`/`sk-…` values before being returned, and the
  key is never logged.
- Restarting the backend wipes all configs, keys and chat history.
- Limits: 10 configs, 20 000 chars of pasted code, 8 000 chars per message,
  1 024 reply tokens, 20 requests/minute per model (10/minute for connection
  tests), 60 s upstream timeout.

For real deployments, run this behind HTTPS, add authentication, and keep keys
in a secret manager rather than process memory.
