"""Extract NVIDIA NIM connection settings from pasted Python source.

The pasted snippet is NEVER executed, imported or evaluated. This module only
reads string literals out of the Python syntax tree (with a regular-expression
fallback for snippets that do not parse as Python) to find three values:

    * the NVIDIA API key          (api_key="nvapi-...")
    * the OpenAI-compatible URL   (base_url="https://integrate.api.nvidia.com/v1")
    * the model id                (model="meta/llama-3.1-8b-instruct")

Everything else in the snippet is ignored.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"

KEY_NAMES = {
    "api_key",
    "apikey",
    "nvidia_api_key",
    "nim_api_key",
    "nv_api_key",
    "openai_api_key",
    "token",
    "key",
}
BASE_NAMES = {
    "base_url",
    "baseurl",
    "api_base",
    "api_url",
    "endpoint",
    "url",
    "nvidia_base_url",
    "openai_base_url",
}
MODEL_NAMES = {"model", "model_id", "modelid", "model_name", "modelname"}

# Values that contain these are treated as documentation, not real settings.
PLACEHOLDER_MARKERS = (
    "your",
    "paste",
    "replace",
    "xxxx",
    "example",
    "placeholder",
    "<",
    "insert",
    "here",
)

NVIDIA_KEY_RE = re.compile(r"nvapi-[A-Za-z0-9_\-]{8,}")
BEARER_RE = re.compile(r"Bearer\s+([A-Za-z0-9_\-\.]{8,})")
KEY_ASSIGN_RE = re.compile(
    r"api[_-]?key['\"]?\s*[:=]\s*['\"]([^'\"]{8,})['\"]", re.IGNORECASE
)
BASE_ASSIGN_RE = re.compile(
    r"base[_-]?url['\"]?\s*[:=]\s*['\"](https?://[^'\"]+)['\"]", re.IGNORECASE
)
MODEL_ASSIGN_RE = re.compile(
    r"(?<![\w.])model(?:_id|_name)?['\"]?\s*[:=]\s*['\"]([^'\"\s]{2,})['\"]",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s'\"`]+")


class ConfigError(ValueError):
    """The pasted snippet did not contain usable connection settings."""


@dataclass
class ParsedConfig:
    api_key: str
    base_url: str
    model: str
    notes: list[str] = field(default_factory=list)


def _field_for_name(name: str) -> str | None:
    normalized = name.strip().strip("\"'").lower().replace("-", "_")
    if normalized in KEY_NAMES:
        return "api_key"
    if normalized in BASE_NAMES:
        return "base_url"
    if normalized in MODEL_NAMES:
        return "model"
    return None


def _const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _target_name(target: ast.AST | None) -> str | None:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Subscript):
        return _const_str(target.slice)
    return None


def _collect_from_tree(tree: ast.AST, add) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg:
            field_name = _field_for_name(node.arg)
            if field_name:
                add(field_name, _const_str(node.value), 1)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                name = _target_name(target)
                field_name = _field_for_name(name) if name else None
                if field_name:
                    add(field_name, _const_str(node.value), 2)
        elif isinstance(node, ast.AnnAssign):
            name = _target_name(node.target)
            field_name = _field_for_name(name) if name else None
            if field_name:
                add(field_name, _const_str(node.value), 2)
        elif isinstance(node, ast.Dict):
            for key_node, value_node in zip(node.keys, node.values):
                key = _const_str(key_node)
                if not key:
                    continue
                field_name = _field_for_name(key)
                if field_name:
                    add(field_name, _const_str(value_node), 1)
                elif key.lower() == "authorization":
                    text = _const_str(value_node)
                    if text and text.lower().startswith("bearer "):
                        add("api_key", text[7:].strip(), 0)


def _collect_from_text(code: str, add) -> None:
    for match in NVIDIA_KEY_RE.finditer(code):
        add("api_key", match.group(0), 4)
    for match in KEY_ASSIGN_RE.finditer(code):
        add("api_key", match.group(1), 5)
    for match in BEARER_RE.finditer(code):
        add("api_key", match.group(1), 5)
    for match in BASE_ASSIGN_RE.finditer(code):
        add("base_url", match.group(1), 5)
    for match in MODEL_ASSIGN_RE.finditer(code):
        add("model", match.group(1), 5)
    # Last resort for the endpoint: any NVIDIA-looking URL in the snippet.
    for match in URL_RE.finditer(code):
        url = match.group(0).rstrip(".,;)]}")
        if "integrate.api.nvidia.com" in url or url.rstrip("/").endswith("/v1"):
            add("base_url", url, 8)


def _is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def parse_python_config(code: str) -> ParsedConfig:
    """Return the NVIDIA settings found in ``code`` without running it."""
    if not code or not code.strip():
        raise ConfigError("Paste a Python configuration first.")

    found: dict[str, list[tuple[int, str]]] = {
        "api_key": [],
        "base_url": [],
        "model": [],
    }
    notes: list[str] = []

    def add(field_name: str, value: str | None, priority: int) -> None:
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned and len(cleaned) < 2000:
                found[field_name].append((priority, cleaned))

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        tree = None
        notes.append(
            f"The pasted code is not valid Python (line {exc.lineno or '?'}); "
            "fell back to pattern matching."
        )
    if tree is not None:
        _collect_from_tree(tree, add)
    _collect_from_text(code, add)

    def pick(field_name: str) -> str | None:
        for _, value in sorted(found[field_name], key=lambda pair: pair[0]):
            if not _is_placeholder(value):
                return value
        return None

    api_key = pick("api_key")
    if not api_key:
        if found["api_key"]:
            raise ConfigError(
                "Found a placeholder API key instead of a real one. Replace it with "
                'your NVIDIA key (it starts with "nvapi-") and connect again.'
            )
        raise ConfigError(
            "No NVIDIA API key found in the pasted code. Include it literally, e.g. "
            'api_key="nvapi-..." (keys read from environment variables cannot be parsed).'
        )
    if len(api_key) < 16:
        raise ConfigError(
            "The extracted API key looks too short to be an NVIDIA key. "
            "Paste the full value that starts with \"nvapi-\"."
        )
    if not api_key.lower().startswith("nvapi-"):
        notes.append(
            "The extracted key does not start with \"nvapi-\"; make sure it is an "
            "NVIDIA API key."
        )

    extracted_base = pick("base_url")
    base_url = (extracted_base or DEFAULT_BASE_URL).rstrip("/")
    if base_url.endswith("/chat/completions"):
        base_url = base_url[: -len("/chat/completions")].rstrip("/")
        notes.append(
            "The base URL pointed at the chat completions route; trimmed it back to "
            f"{base_url}."
        )
    if not base_url.startswith(("http://", "https://")):
        raise ConfigError(
            f'The extracted base URL "{base_url}" is not a valid http(s) URL.'
        )
    if extracted_base is None:
        notes.append(f"No base URL in the pasted code; using NVIDIA's default {DEFAULT_BASE_URL}.")
    elif "nvidia" not in base_url:
        notes.append("The base URL is not an NVIDIA host; make sure it is OpenAI-compatible.")

    model = pick("model")
    if not model:
        raise ConfigError(
            "No model id found in the pasted code. Include it literally, e.g. "
            'model="meta/llama-3.1-8b-instruct".'
        )
    if " " in model or "\n" in model:
        raise ConfigError(
            f'The extracted model id "{model}" contains whitespace and is not a valid model name.'
        )

    return ParsedConfig(api_key=api_key, base_url=base_url, model=model, notes=notes)
