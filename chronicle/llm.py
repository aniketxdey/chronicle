"""Optional LLM providers. Off unless ``CHRONICLE_LLM`` (or the ``llm_provider`` setting) names one.

A provider takes a system prompt, a user prompt, and neutral tool specs
(``{"name", "description", "parameters"}``) and returns the tool calls the model
made as ``[{"name": str, "arguments": dict}]``.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Protocol

DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
TIMEOUT_SECS = 60.0
OFF = {"", "0", "off", "none", "false", "no"}


class LLMError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


class Provider(Protocol):
    name: str
    model: str

    def call_tools(self, system: str, user: str, tools: list[dict]) -> list[dict]: ...


_loaded_env = False


def load_env() -> None:
    """Load ``.env`` from CHRONICLE_HOME, then the cwd. Never overrides variables already set."""
    global _loaded_env
    if _loaded_env:
        return
    _loaded_env = True
    from chronicle import db

    for path in (db.home() / ".env", Path.cwd() / ".env"):
        if path.is_file():
            _read_env(path)


def _read_env(path: Path) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None
    if load_dotenv is not None:
        load_dotenv(path, override=False)
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value


def _setting(key: str) -> str:
    from chronicle import db

    try:
        return db.get_setting(key, "") or ""
    except Exception:  # noqa: BLE001
        return ""


def configured_name() -> str:
    load_env()
    name = os.environ.get("CHRONICLE_LLM")
    if name is None:
        name = _setting("llm_provider")
    return name.strip().lower()


def model_name() -> str:
    load_env()
    return os.environ.get("CHRONICLE_LLM_MODEL") or _setting("llm_model") or DEFAULT_MODEL


class OpenAIProvider:
    """OpenAI Responses API with strict function tools. Uses the ``openai`` SDK when installed, else urllib."""

    name = "openai"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, base_url: str | None = None, timeout: float = TIMEOUT_SECS):
        if not api_key:
            raise LLMError("OPENAI_API_KEY is not set")
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def _body(self, system: str, user: str, tools: list[dict]) -> dict:
        return {
            "model": self.model,
            "instructions": system,
            "input": user,
            "tools": [
                {"type": "function", "name": t["name"], "description": t["description"], "parameters": t["parameters"], "strict": True}
                for t in tools
            ],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "store": False,
        }

    def call_tools(self, system: str, user: str, tools: list[dict]) -> list[dict]:
        body = self._body(system, user, tools)
        try:
            import openai
        except ImportError:
            openai = None
        payload = self._sdk(openai, body) if openai is not None else self._http(body)
        calls = []
        for item in payload.get("output") or []:
            if item.get("type") != "function_call":
                continue
            try:
                arguments = json.loads(item.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = None
            calls.append({"name": item.get("name"), "arguments": arguments})
        return calls

    def _sdk(self, openai, body: dict) -> dict:
        client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout, max_retries=3)
        try:
            return client.responses.create(**body).model_dump()
        except openai.RateLimitError as exc:
            raise LLMError(f"rate limited: {exc}", retryable=True) from exc
        except openai.APITimeoutError as exc:
            raise LLMError("request timed out", retryable=True) from exc
        except openai.APIConnectionError as exc:
            raise LLMError(f"connection failed: {exc}", retryable=True) from exc
        except openai.APIStatusError as exc:
            raise LLMError(f"HTTP {exc.status_code}: {exc.message}", retryable=exc.status_code >= 500) from exc
        except openai.OpenAIError as exc:
            raise LLMError(str(exc)) from exc

    def _http(self, body: dict, attempts: int = 3) -> dict:
        data = json.dumps(body).encode()
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        for attempt in range(attempts):
            request = urllib.request.Request(f"{self.base_url}/responses", data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode())
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or exc.code >= 500
                if not retryable or attempt == attempts - 1:
                    detail = exc.read().decode(errors="replace")[:300]
                    raise LLMError(f"HTTP {exc.code}: {detail}", retryable=retryable) from exc
                wait = exc.headers.get("Retry-After") if exc.headers else None
                time.sleep(min(float(wait) if wait and wait.replace(".", "", 1).isdigit() else 2.0 ** attempt, 20))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == attempts - 1:
                    raise LLMError(f"connection failed: {exc}", retryable=True) from exc
                time.sleep(2.0 ** attempt)
        raise LLMError("unreachable")


def _openai() -> Provider:
    return OpenAIProvider(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        model=model_name(),
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
    )


PROVIDERS: dict[str, Callable[[], Provider]] = {"openai": _openai}
_override: Provider | None = None


def register(name: str, factory: Callable[[], Provider]) -> None:
    PROVIDERS[name.lower()] = factory


def use(provider: Provider | None) -> None:
    """Force a provider (tests, embedding). ``None`` returns to configuration."""
    global _override
    _override = provider


def enabled() -> bool:
    return _override is not None or configured_name() not in OFF


def provider() -> Provider | None:
    """The configured provider, or None when LLM features are off. Raises LLMError when misconfigured."""
    if _override is not None:
        return _override
    name = configured_name()
    if name in OFF:
        return None
    factory = PROVIDERS.get(name)
    if factory is None:
        raise LLMError(f"unknown LLM provider {name!r}; known: {', '.join(sorted(PROVIDERS))}")
    return factory()
