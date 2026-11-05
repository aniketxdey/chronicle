"""OAuth 2 sign-in for cloud sources (Google, Microsoft, Slack, Notion).

The flow runs in the user's browser. Google and Microsoft redirect straight back to the engine on
127.0.0.1. Slack and Notion only accept HTTPS redirects, so they stay unavailable until
``CHRONICLE_OAUTH_HTTPS_REDIRECT`` names an HTTPS page you control that forwards to ``/oauth/callback``.

Client secrets never ship in the app for confidential clients: when ``CHRONICLE_OAUTH_BROKER`` is set, the code
exchange and refresh go through the Chronicle relay, which holds them. Without a broker, secrets come from the
environment (development).
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
import urllib.parse

from chronicle import db, net, vault
from chronicle.timeutil import utc_iso

PROVIDERS = {
    "google": {
        "auth": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "client_env": "CHRONICLE_GOOGLE_CLIENT_ID", "secret_env": "CHRONICLE_GOOGLE_CLIENT_SECRET",
        "extra": {"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"},
        "pkce": True, "redirect": "loopback",
    },
    "microsoft": {
        "auth": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "client_env": "CHRONICLE_MICROSOFT_CLIENT_ID", "secret_env": None,
        "extra": {"response_mode": "query"}, "pkce": True, "redirect": "localhost",
    },
    "slack": {
        "auth": "https://slack.com/oauth/v2/authorize",
        "token": "https://slack.com/api/oauth.v2.access",
        "client_env": "CHRONICLE_SLACK_CLIENT_ID", "secret_env": "CHRONICLE_SLACK_CLIENT_SECRET",
        "extra": {}, "pkce": False, "redirect": "https", "scope_param": "user_scope", "separator": ",",
    },
    "notion": {
        "auth": "https://api.notion.com/v1/oauth/authorize",
        "token": "https://api.notion.com/v1/oauth/token",
        "client_env": "CHRONICLE_NOTION_CLIENT_ID", "secret_env": "CHRONICLE_NOTION_CLIENT_SECRET",
        "extra": {"owner": "user"}, "pkce": False, "redirect": "https", "basic_auth": True, "json_exchange": True,
    },
}
_pending: dict[str, dict] = {}
PENDING_SECS = 900


def _key(source_id: str) -> str:
    return f"oauth:{source_id}"


def client_id(provider: str) -> str:
    return os.environ.get(PROVIDERS[provider]["client_env"], "")


def configured(provider: str) -> bool:
    needs_page = PROVIDERS[provider]["redirect"] == "https"
    return bool(client_id(provider)) and (not needs_page or bool(os.environ.get("CHRONICLE_OAUTH_HTTPS_REDIRECT")))


def _redirect(provider: str) -> str:
    kind = PROVIDERS[provider]["redirect"]
    port = os.environ.get("CHRONICLE_PORT", "3030")
    if kind == "https":
        return os.environ["CHRONICLE_OAUTH_HTTPS_REDIRECT"]
    host = "localhost" if kind == "localhost" else "127.0.0.1"
    return f"http://{host}:{port}/oauth/callback"


def start(source_id: str, provider: str, scopes: list[str]) -> dict:
    spec = PROVIDERS[provider]
    if not configured(provider):
        raise db.ChronicleError(f"{provider.title()} sign-in isn't set up in this build ({spec['client_env']} is missing)", 409)
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    redirect = _redirect(provider)
    params = {
        "client_id": client_id(provider), "redirect_uri": redirect, "response_type": "code", "state": state,
        spec.get("scope_param", "scope"): spec.get("separator", " ").join(scopes), **spec["extra"],
    }
    if spec["pkce"]:
        params["code_challenge"] = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params["code_challenge_method"] = "S256"
    _pending[state] = {"source": source_id, "provider": provider, "verifier": verifier, "redirect": redirect, "at": time.time()}
    for key in [k for k, v in _pending.items() if time.time() - v["at"] > PENDING_SECS]:
        _pending.pop(key, None)
    return {"auth_url": f"{spec['auth']}?{urllib.parse.urlencode(params)}", "state": state, "redirect_uri": redirect}


def _exchange(provider: str, form: dict) -> dict:
    spec = PROVIDERS[provider]
    broker = os.environ.get("CHRONICLE_OAUTH_BROKER", "").rstrip("/")
    if broker:
        return net.post(f"{broker}/oauth/{provider}/token", json_body=form)
    headers = {}
    secret = os.environ.get(spec["secret_env"] or "", "") if spec["secret_env"] else ""
    form = {**form, "client_id": client_id(provider)}
    if spec.get("basic_auth"):
        raw = f"{client_id(provider)}:{secret}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        form.pop("client_id", None)
    elif secret:
        form["client_secret"] = secret
    if spec.get("json_exchange"):
        return net.post(spec["token"], json_body=form, headers=headers)
    return net.post(spec["token"], form=form, headers=headers)


def _store(source_id: str, provider: str, payload: dict, previous: dict | None = None) -> dict:
    if payload.get("ok") is False:
        raise db.ChronicleError(f"{provider.title()} refused the sign-in: {payload.get('error')}", 400)
    authed = payload.get("authed_user") or {}
    token = {
        "provider": provider,
        "access_token": authed.get("access_token") or payload.get("access_token"),
        "refresh_token": authed.get("refresh_token") or payload.get("refresh_token") or (previous or {}).get("refresh_token"),
        "expires_at": time.time() + float(authed.get("expires_in") or payload.get("expires_in") or 3600) - 60,
        "scope": authed.get("scope") or payload.get("scope"),
        "account": authed.get("id") or (payload.get("owner") or {}).get("user", {}).get("id") or (previous or {}).get("account"),
        "workspace": (payload.get("team") or {}).get("name") or payload.get("workspace_name") or (previous or {}).get("workspace"),
        "updated_at": utc_iso(),
    }
    if not token["access_token"]:
        raise db.ChronicleError(f"{provider.title()} returned no access token", 502)
    if provider in {"slack", "notion"} and not (authed.get("expires_in") or payload.get("expires_in")):
        token["expires_at"] = None
    vault.set_json(_key(source_id), token)
    return token


def callback(code: str | None, state: str | None, error: str | None = None) -> dict:
    if error:
        raise db.ChronicleError(f"sign-in was cancelled: {error}", 400)
    pending = _pending.pop(state or "", None)
    if pending is None or not code:
        raise db.ChronicleError("this sign-in link expired; start again from Chronicle", 400)
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": pending["redirect"]}
    if PROVIDERS[pending["provider"]]["pkce"]:
        form["code_verifier"] = pending["verifier"]
    token = _store(pending["source"], pending["provider"], _exchange(pending["provider"], form))
    from chronicle.sources import base

    base.configure(pending["source"], enabled=True)
    return {"source": pending["source"], "provider": pending["provider"], "workspace": token.get("workspace")}


def token(source_id: str) -> dict | None:
    return vault.get_json(_key(source_id))


def access_token(source_id: str) -> str:
    current = token(source_id)
    if not current:
        raise db.ChronicleError("not signed in", 401)
    expires = current.get("expires_at")
    if expires and time.time() >= expires:
        if not current.get("refresh_token"):
            raise db.ChronicleError("sign-in expired; connect again", 401)
        payload = _exchange(current["provider"], {"grant_type": "refresh_token", "refresh_token": current["refresh_token"]})
        current = _store(source_id, current["provider"], payload, previous=current)
    return current["access_token"]


def forget(source_id: str) -> None:
    vault.delete(_key(source_id))
