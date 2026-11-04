"""LLM providers for graph tuning, context writing, and Ask.

Providers are ``openai``, ``anthropic``, and ``local`` (any OpenAI-compatible server). ``CHRONICLE_LLM`` or the
``llm_provider`` setting picks one and ``off`` disables them. Unset means on when a cloud key is available, from
Settings > Model or from ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``.

``call_tools`` takes a system prompt, a user prompt, and neutral tool specs
(``{"name", "description", "parameters"}``) and returns the tool calls the model
made as ``[{"name": str, "arguments": dict}]``. ``complete`` returns plain text.
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
CATALOG = [
    {"id": "openai", "label": "OpenAI", "env": "OPENAI_API_KEY", "default_model": DEFAULT_MODEL},
    {"id": "anthropic", "label": "Anthropic", "env": "ANTHROPIC_API_KEY", "default_model": "claude-sonnet-5-5"},
    {"id": "gemini", "label": "Google Gemini", "env": "GEMINI_API_KEY", "default_model": "gemini-2.5-flash", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai"},
    {"id": "openrouter", "label": "OpenRouter", "env": "OPENROUTER_API_KEY", "default_model": "openai/gpt-4.1-mini", "base_url": "https://openrouter.ai/api/v1"},
    {"id": "local", "label": "Local model", "env": None, "default_model": ""},
]
_BY_ID = {item["id"]: item for item in CATALOG}
DEFAULT_MODELS = {item["id"]: item["default_model"] for item in CATALOG}
DEFAULT_BASE_URL = "https://api.openai.com/v1"
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
LOCAL_BASE_URL = "http://localhost:11434/v1"
TIMEOUT_SECS = 60.0
LOCAL_TIMEOUT_SECS = 300.0
TOOL_MAX_TOKENS = 8000
OFF = {"", "0", "off", "none", "false", "no"}
ATTEMPTS = 3
# 429s that retrying can't fix.
BILLING_CODES = ("insufficient_quota", "credit_balance_exhausted", "billing_hard_limit_reached", "billing_not_active")
NO_CREDITS = "the OpenAI account has no credits left; add them at https://platform.openai.com/settings/organization/billing"


def _billing(*parts) -> bool:
    text = " ".join(str(part) for part in parts if part)
    return any(code in text for code in BILLING_CODES)


class LLMError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


class Provider(Protocol):
    name: str
    model: str

    def call_tools(self, system: str, user: str, tools: list[dict]) -> list[dict]: ...

    def complete(self, system: str, user: str, max_tokens: int = 4000) -> str: ...


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


def _saved_openai() -> tuple[str, str]:
    """The Settings > Model key and endpoint, when they point at OpenAI (tuning uses the Responses API)."""
    base, key = _setting("ai_base_url").strip(), _setting("ai_api_key").strip()
    if key and (not base or "api.openai.com" in base):
        return key, base
    return "", ""


def key_for(name: str) -> str:
    """A provider's key: saved in Settings first, then the environment."""
    load_env()
    saved = _setting(f"llm_api_key_{name}").strip()
    if saved:
        return saved
    env = _BY_ID.get(name, {}).get("env")
    return (os.environ.get(env, "") if env else "") or (_saved_openai()[0] if name == "openai" else "")


def api_key() -> str:
    return key_for("openai")


def anthropic_key() -> str:
    return key_for("anthropic")


def local_base_url() -> str:
    load_env()
    return _setting("llm_base_url").strip() or os.environ.get("CHRONICLE_LLM_BASE_URL") or LOCAL_BASE_URL


def configured_name() -> str:
    """The provider in use: what Settings chose, else CHRONICLE_LLM, else the first with a key, else ``off``.

    CHRONICLE_LLM=off always wins, so a machine can be kept offline.
    """
    load_env()
    env = (os.environ.get("CHRONICLE_LLM") or "").strip().lower()
    if os.environ.get("CHRONICLE_LLM") is not None and env in OFF:
        return "off"
    name = _setting("llm_provider").strip().lower()
    if name in {"", "auto"}:
        name = env
    if name in {"", "auto"}:
        return next((item["id"] for item in CATALOG if item["env"] and key_for(item["id"])), "off")
    return name


def model_name(name: str | None = None) -> str:
    """A provider's model: saved for it in Settings, then CHRONICLE_LLM_MODEL if that's meant for it, then its default."""
    load_env()
    name = name or configured_name()
    saved = _setting(f"llm_model_{name}").strip()
    if saved:
        return saved
    env_provider = (os.environ.get("CHRONICLE_LLM") or "").strip().lower()
    if os.environ.get("CHRONICLE_LLM_MODEL") and env_provider in {"", "auto", name}:
        return os.environ["CHRONICLE_LLM_MODEL"]
    return _setting("llm_model") or DEFAULT_MODELS.get(name, DEFAULT_MODEL)


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

    def _send(self, body: dict) -> dict:
        try:
            import openai
        except ImportError:
            openai = None
        return self._sdk(openai, body) if openai is not None else self._http(body)

    def complete(self, system: str, user: str, max_tokens: int = 4000) -> str:
        payload = self._send({"model": self.model, "instructions": system, "input": user,
                              "max_output_tokens": max_tokens, "store": False})
        text = "".join(
            part.get("text") or ""
            for item in payload.get("output") or [] if item.get("type") == "message"
            for part in item.get("content") or [] if part.get("type") == "output_text"
        ).strip()
        if not text:
            raise LLMError("model returned no text")
        return text

    def call_tools(self, system: str, user: str, tools: list[dict]) -> list[dict]:
        payload = self._send(self._body(system, user, tools))
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

    def _sdk(self, openai, body: dict, attempts: int = ATTEMPTS) -> dict:
        # The SDK's own retries would also retry billing 429s, so retries happen here.
        client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout, max_retries=0)
        for attempt in range(attempts):
            try:
                return client.responses.create(**body).model_dump()
            except openai.RateLimitError as exc:
                if _billing(getattr(exc, "code", None), exc):
                    raise LLMError(NO_CREDITS) from exc
                error = LLMError(f"rate limited: {exc}", retryable=True)
            except openai.APITimeoutError:
                error = LLMError("request timed out", retryable=True)
            except openai.APIConnectionError as exc:
                error = LLMError(f"connection failed: {exc}", retryable=True)
            except openai.APIStatusError as exc:
                if exc.status_code < 500:
                    raise LLMError(f"HTTP {exc.status_code}: {exc.message}") from exc
                error = LLMError(f"HTTP {exc.status_code}: {exc.message}", retryable=True)
            except openai.OpenAIError as exc:
                raise LLMError(str(exc)) from exc
            if attempt == attempts - 1:
                raise error
            time.sleep(min(2.0 ** attempt, 20))
        raise LLMError("unreachable")

    def _http(self, body: dict, attempts: int = ATTEMPTS) -> dict:
        return _post_json(f"{self.base_url}/responses", body, {"Authorization": f"Bearer {self.api_key}"}, self.timeout, attempts)


def _post_json(url: str, body: dict, headers: dict, timeout: float, attempts: int = ATTEMPTS) -> dict:
    """POST JSON with retries on rate limits, server errors, and dropped connections."""
    data = json.dumps(body).encode()
    for attempt in range(attempts):
        request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            if exc.code == 429 and _billing(detail):
                raise LLMError(NO_CREDITS) from exc
            retryable = exc.code == 429 or exc.code >= 500
            if not retryable or attempt == attempts - 1:
                raise LLMError(f"HTTP {exc.code}: {detail}", retryable=retryable) from exc
            wait = exc.headers.get("Retry-After") if exc.headers else None
            time.sleep(min(float(wait) if wait and wait.replace(".", "", 1).isdigit() else 2.0 ** attempt, 20))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == attempts - 1:
                raise LLMError(f"connection failed: {exc}", retryable=True) from exc
            time.sleep(2.0 ** attempt)
    raise LLMError("unreachable")


def _tools_from_text(text: str, tools: list[dict] | None = None) -> list[dict]:
    """Tool calls a model wrote as JSON, for local models without native tool calling.

    Small models add prose, wrap the array in other text, or write one bare object, so this
    takes the first JSON array or object that reads as calls. With a single tool, an object
    with no name is taken as that tool's arguments.
    """
    decoder = json.JSONDecoder()
    only = tools[0]["name"] if tools and len(tools) == 1 else None
    for start, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            found = decoder.raw_decode(text[start:])[0]
        except json.JSONDecodeError:
            continue
        items = found if isinstance(found, list) else [found]
        calls = []
        for item in items:
            if isinstance(item, dict) and item.get("name") and (item.get("arguments") is not None or item.get("parameters") is not None):
                calls.append({"name": item["name"], "arguments": item.get("arguments", item.get("parameters"))})
            elif isinstance(item, dict) and only and item:
                calls.append({"name": only, "arguments": item})
        if calls:
            return calls
    return []


class ChatProvider:
    """Any OpenAI-compatible /chat/completions server: Ollama, LM Studio, llama.cpp, vLLM, OpenRouter."""

    name = "local"

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = LOCAL_TIMEOUT_SECS):
        if not model:
            raise LLMError("choose a model for the local endpoint")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def _send(self, body: dict) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        return _post_json(f"{self.base_url}/chat/completions", {"model": self.model, "temperature": 0.2, **body}, headers, self.timeout)

    @staticmethod
    def _message(payload: dict) -> dict:
        try:
            return payload["choices"][0]["message"] or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("model returned no message") from exc

    def complete(self, system: str, user: str, max_tokens: int = 4000) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        text = (self._message(self._send({"messages": messages, "max_tokens": max_tokens})).get("content") or "").strip()
        if not text:
            raise LLMError("model returned no text")
        return text

    def call_tools(self, system: str, user: str, tools: list[dict]) -> list[dict]:
        fallback = f'{system}\n\nIf you cannot call tools, reply with only a JSON array of {{"name": ..., "arguments": {{...}}}} objects.'
        try:
            message = self._message(self._send({
                "messages": [{"role": "system", "content": fallback}, {"role": "user", "content": user}],
                "tools": [{"type": "function", "function": tool} for tool in tools],
                "tool_choice": "auto",
            }))
        except LLMError as exc:
            # Many local models don't take a tools field, so they get the schemas in the prompt instead.
            if not ("HTTP 400" in exc.message and "tool" in exc.message.lower()):
                raise
            schemas = json.dumps([{key: tool[key] for key in ("name", "description", "parameters")} for tool in tools])
            prompt = f'{system}\n\nThese are your tools: {schemas}\n\nCall them by replying with only a JSON array of {{"name": ..., "arguments": {{...}}}} objects, and nothing else.'
            message = self._message(self._send({"messages": [{"role": "system", "content": prompt}, {"role": "user", "content": user}]}))
        calls = []
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments or "{}")
                except json.JSONDecodeError:
                    arguments = None
            calls.append({"name": function.get("name"), "arguments": arguments})
        return calls or _tools_from_text(message.get("content") or "", tools)


class AnthropicProvider:
    """Anthropic Messages API over urllib."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str = DEFAULT_MODELS["anthropic"], base_url: str | None = None, timeout: float = TIMEOUT_SECS):
        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or ANTHROPIC_BASE_URL).rstrip("/")
        self.timeout = timeout

    def _send(self, body: dict) -> dict:
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        return _post_json(f"{self.base_url}/messages", {"model": self.model, **body}, headers, self.timeout)

    def complete(self, system: str, user: str, max_tokens: int = 4000) -> str:
        payload = self._send({"system": system, "messages": [{"role": "user", "content": user}], "max_tokens": max_tokens})
        text = "".join(block.get("text") or "" for block in payload.get("content") or [] if block.get("type") == "text").strip()
        if not text:
            raise LLMError("model returned no text")
        return text

    def call_tools(self, system: str, user: str, tools: list[dict]) -> list[dict]:
        payload = self._send({
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": TOOL_MAX_TOKENS,
            "tools": [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools],
        })
        return [
            {"name": block.get("name"), "arguments": block.get("input")}
            for block in payload.get("content") or [] if block.get("type") == "tool_use"
        ]


def _openai() -> Provider:
    return OpenAIProvider(
        api_key=api_key(),
        model=model_name("openai"),
        base_url=os.environ.get("OPENAI_BASE_URL") or _saved_openai()[1] or None,
    )


def _anthropic() -> Provider:
    return AnthropicProvider(api_key=anthropic_key(), model=model_name("anthropic"))


def _local() -> Provider:
    return ChatProvider(base_url=local_base_url(), model=model_name("local"), api_key=_setting("llm_api_key_local").strip())


def _hosted(name: str) -> Callable[[], Provider]:
    """A cloud provider with an OpenAI-compatible chat endpoint."""

    def build() -> Provider:
        item = _BY_ID[name]
        key = key_for(name)
        if not key:
            raise LLMError(f"add an API key for {item['label']} in Settings")
        return ChatProvider(base_url=item["base_url"], model=model_name(name), api_key=key, timeout=TIMEOUT_SECS)

    return build


PROVIDERS: dict[str, Callable[[], Provider]] = {"openai": _openai, "anthropic": _anthropic, "local": _local, "gemini": _hosted("gemini"), "openrouter": _hosted("openrouter")}
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


def _catalog_entry(item: dict, active: str) -> dict:
    name = item["id"]
    saved = bool(_setting(f"llm_api_key_{name}").strip())
    has_key = bool(key_for(name)) if item["env"] else saved
    return {
        "id": name, "label": item["label"], "needs_key": item["env"] is not None, "has_key": has_key,
        "key_source": "saved" if saved else "environment" if has_key else None, "env": item["env"],
        "model": model_name(name), "default_model": item["default_model"], "active": name == active,
    }


def status() -> dict:
    """What the Settings page shows: every provider, which one is in use, and the last failure."""
    try:
        name = configured_name()
    except Exception:  # noqa: BLE001
        name = "off"
    on = enabled()
    env = os.environ.get("CHRONICLE_LLM")
    return {
        "enabled": on,
        "provider": (_override.name if _override is not None else name) if on else None,
        "model": (_override.model if _override is not None else model_name()) if on else None,
        "choice": name,
        "locked_by_env": env is not None and env.strip().lower() in OFF,
        "key_set": bool(api_key()),
        "keys": {"openai": bool(api_key()), "anthropic": bool(anthropic_key()), "local": bool(_setting("llm_api_key_local"))},
        "providers": [_catalog_entry(item, name) for item in CATALOG],
        "base_url": local_base_url(),
        "default_models": DEFAULT_MODELS,
        "last_error": _setting("last_llm_error") or None,
    }


def test_connection(name: str | None = None) -> dict:
    """Send one small request to a provider, or the one in use. Never raises."""
    try:
        if name:
            if name not in PROVIDERS:
                return {"ok": False, "error": f"unknown provider {name!r}"}
            chosen = PROVIDERS[name]()
        else:
            chosen = provider()
        if chosen is None:
            return {"ok": False, "error": "no model is set up"}
        chosen.complete("Reply with the single word OK.", "Connection test.", max_tokens=64)
        return {"ok": True, "provider": chosen.name, "model": chosen.model}
    except LLMError as exc:
        return {"ok": False, "error": exc.message}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def list_models(name: str = "local") -> dict:
    """Models a provider offers, for the Settings picker. Never raises."""
    item = _BY_ID.get(name)
    if item is None:
        return {"models": [], "error": f"unknown provider {name!r}"}
    key = key_for(name)
    if item["env"] and not key:
        return {"models": [], "error": None}
    url = local_base_url() if name == "local" else item.get("base_url") or (ANTHROPIC_BASE_URL if name == "anthropic" else DEFAULT_BASE_URL)
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"} if name == "anthropic" else {"Authorization": f"Bearer {key}"} if key else {}
    request = urllib.request.Request(f"{url.rstrip('/')}/models", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode())
        found = [str(entry["id"]).removeprefix("models/") for entry in payload.get("data") or [] if entry.get("id")]
        return {"models": sorted(found), "error": None}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {"models": [], "error": str(exc)}
