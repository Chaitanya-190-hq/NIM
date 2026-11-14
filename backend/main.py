"""NVIDIA // BRIDGE backend.

A minimal FastAPI service that keeps pasted NVIDIA configurations in process
memory only, then proxies chat requests to the NVIDIA NIM API using the OpenAI
Python SDK. Nothing is written to disk, a database or a log.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import openai
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel, Field

from nvidia_config import DEFAULT_BASE_URL, ConfigError, parse_python_config

MAX_CONFIGS = 10
MAX_CODE_CHARS = 20_000
MAX_MESSAGE_CHARS = 8_000
MAX_HISTORY_MESSAGES = 20
MAX_REPLY_TOKENS = 1024
RATE_LIMIT_REQUESTS = 20
RATE_LIMIT_WINDOW_SECONDS = 60.0

SYSTEM_PROMPT = "You are a helpful assistant."


@dataclass
class StoredConfig:
    """One connected model. Lives in memory only; lost on restart."""

    id: str
    model: str
    base_url: str
    api_key: str
    status: str = "untested"
    error: str | None = None
    messages: list[dict[str, str]] = field(default_factory=list)
    calls: list[float] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


CONFIGS: dict[str, StoredConfig] = {}
LOCK = threading.Lock()

app = FastAPI(title="NVIDIA // BRIDGE", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateConfigRequest(BaseModel):
    python_code: str = Field(min_length=1, max_length=MAX_CODE_CHARS)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


def mask_key(api_key: str) -> str:
    if len(api_key) <= 12:
        return api_key[:3] + "…"
    return f"{api_key[:6]}…{api_key[-4:]}"


def sanitize(text: str, api_key: str) -> str:
    """Never echo a credential back to the client or into an error message."""
    if api_key:
        text = text.replace(api_key, "nvapi-***")
    text = re.sub(r"nvapi-[A-Za-z0-9_\-]{8,}", "nvapi-***", text)
    text = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-***", text)
    return text.strip()


def view(cfg: StoredConfig) -> dict[str, Any]:
    """Public shape of a config. The raw API key is never included."""
    return {
        "id": cfg.id,
        "model": cfg.model,
        "base_url": cfg.base_url,
        "api_key_preview": mask_key(cfg.api_key),
        "status": cfg.status,
        "error": cfg.error,
        "messages": [dict(message) for message in cfg.messages],
        "created_at": cfg.created_at,
    }


def get_config(config_id: str) -> StoredConfig:
    with LOCK:
        cfg = CONFIGS.get(config_id)
    if cfg is None:
        raise HTTPException(
            status_code=404,
            detail="That configuration is not loaded. The backend may have restarted — paste and connect it again.",
        )
    return cfg


def check_rate_limit(cfg: StoredConfig, limit: int = RATE_LIMIT_REQUESTS) -> None:
    now = time.time()
    cfg.calls = [stamp for stamp in cfg.calls if now - stamp < RATE_LIMIT_WINDOW_SECONDS]
    if len(cfg.calls) >= limit:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many requests for {cfg.model}: limit is {limit} per "
                f"{int(RATE_LIMIT_WINDOW_SECONDS)} seconds. Wait a moment and retry."
            ),
        )
    cfg.calls.append(now)


def make_client(cfg: StoredConfig) -> OpenAI:
    return OpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=60.0, max_retries=1)


def upstream_detail(exc: openai.APIStatusError) -> str:
    """Pull the useful sentence out of an NVIDIA error body."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        for key in ("detail", "message", "title", "error"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                inner = value.get("message") or value.get("detail")
                if isinstance(inner, str) and inner.strip():
                    return inner.strip()
    message = getattr(exc, "message", None)
    return message if isinstance(message, str) and message.strip() else str(exc)


def friendly_error(exc: Exception, api_key: str) -> str:
    if isinstance(exc, openai.AuthenticationError):
        return (
            "NVIDIA rejected the API key (HTTP 401). Check that the key is complete, "
            "active, and belongs to a build.nvidia.com account."
        )
    if isinstance(exc, openai.PermissionDeniedError):
        return (
            "NVIDIA denied access to this model (HTTP 403). The key may not be "
            "entitled to it."
        )
    if isinstance(exc, openai.NotFoundError):
        return (
            "NVIDIA could not find that model or route (HTTP 404). Check the model id "
            "and that the base URL ends in /v1."
        )
    if isinstance(exc, openai.RateLimitError):
        return "NVIDIA rate-limited the request (HTTP 429). Wait a moment and retry."
    if isinstance(exc, openai.APITimeoutError):
        return "The request to NVIDIA timed out. Retry, or try a smaller model."
    if isinstance(exc, openai.APIConnectionError):
        return (
            "Could not reach the NVIDIA API. Check this machine's network access and "
            "the base URL."
        )
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code == 410:
            return sanitize(
                "NVIDIA no longer serves this model id (HTTP 410): "
                f"{upstream_detail(exc)} Choose a current model, e.g. "
                '"openai/gpt-oss-20b" or "nvidia/llama-3.1-nemotron-70b-instruct".',
                api_key,
            )
        return sanitize(
            f"NVIDIA returned HTTP {exc.status_code}: {upstream_detail(exc)}", api_key
        )
    if isinstance(exc, openai.OpenAIError):
        return sanitize(f"NVIDIA SDK error: {exc}", api_key)
    return sanitize(f"{type(exc).__name__}: {exc}", api_key)


@app.get("/api/health")
def health() -> dict[str, Any]:
    with LOCK:
        count = len(CONFIGS)
    return {
        "status": "ok",
        "default_base_url": DEFAULT_BASE_URL,
        "configs": count,
        "limits": {
            "max_configs": MAX_CONFIGS,
            "max_code_chars": MAX_CODE_CHARS,
            "max_message_chars": MAX_MESSAGE_CHARS,
            "requests_per_minute": RATE_LIMIT_REQUESTS,
        },
    }


@app.get("/api/configs")
def list_configs() -> dict[str, Any]:
    with LOCK:
        configs = [view(cfg) for cfg in sorted(CONFIGS.values(), key=lambda c: c.created_at)]
    return {"configs": configs}


@app.post("/api/configs")
def create_config(payload: CreateConfigRequest) -> dict[str, Any]:
    try:
        parsed = parse_python_config(payload.python_code)
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with LOCK:
        if len(CONFIGS) >= MAX_CONFIGS:
            raise HTTPException(
                status_code=400,
                detail=f"You can keep at most {MAX_CONFIGS} configurations. Remove one first.",
            )
        cfg = StoredConfig(
            id=uuid.uuid4().hex,
            model=parsed.model,
            base_url=parsed.base_url,
            api_key=parsed.api_key,
        )
        CONFIGS[cfg.id] = cfg

    return {"config": view(cfg), "notes": parsed.notes}


@app.post("/api/configs/{config_id}/test")
def test_config(config_id: str) -> dict[str, Any]:
    cfg = get_config(config_id)
    check_rate_limit(cfg, limit=10)
    try:
        make_client(cfg).chat.completions.create(
            model=cfg.model,
            messages=[{"role": "user", "content": "Reply with the single word: OK"}],
            max_tokens=8,
            temperature=0,
        )
    except Exception as exc:  # reported to the client, never logged with the key
        cfg.status = "failed"
        cfg.error = friendly_error(exc, cfg.api_key)
        return {"config": view(cfg)}

    cfg.status = "connected"
    cfg.error = None
    return {"config": view(cfg)}


@app.post("/api/configs/{config_id}/chat")
def chat(config_id: str, payload: ChatRequest) -> dict[str, Any]:
    cfg = get_config(config_id)
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="The prompt is empty.")
    check_rate_limit(cfg)

    history = cfg.messages[-MAX_HISTORY_MESSAGES:]
    request_messages = (
        [{"role": "system", "content": SYSTEM_PROMPT}]
        + [dict(entry) for entry in history]
        + [{"role": "user", "content": message}]
    )

    try:
        completion = make_client(cfg).chat.completions.create(
            model=cfg.model,
            messages=request_messages,
            temperature=0.2,
            max_tokens=MAX_REPLY_TOKENS,
        )
    except openai.AuthenticationError as exc:
        cfg.status = "failed"
        cfg.error = friendly_error(exc, cfg.api_key)
        raise HTTPException(status_code=401, detail=cfg.error) from exc
    except Exception as exc:
        detail = friendly_error(exc, cfg.api_key)
        raise HTTPException(status_code=502, detail=detail) from exc

    reply = ""
    if completion.choices:
        reply = (completion.choices[0].message.content or "").strip()
    if not reply:
        raise HTTPException(
            status_code=502,
            detail="NVIDIA returned an empty response. Try a different prompt or model.",
        )

    cfg.status = "connected"
    cfg.error = None
    cfg.messages.append({"role": "user", "content": message})
    cfg.messages.append({"role": "assistant", "content": reply})
    return {"config": view(cfg)}


@app.post("/api/configs/{config_id}/clear")
def clear_chat(config_id: str) -> dict[str, Any]:
    cfg = get_config(config_id)
    cfg.messages = []
    return {"config": view(cfg)}


@app.delete("/api/configs/{config_id}")
def delete_config(config_id: str) -> dict[str, Any]:
    with LOCK:
        if CONFIGS.pop(config_id, None) is None:
            raise HTTPException(status_code=404, detail="That configuration is not loaded.")
        configs = [view(cfg) for cfg in sorted(CONFIGS.values(), key=lambda c: c.created_at)]
    return {"configs": configs}
