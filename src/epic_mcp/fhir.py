"""Read-only FHIR client that records every request in a local access log."""

from __future__ import annotations

import datetime as dt
import json

import httpx

from . import auth, config


def log_access(account: str, tool: str, resource: str, url: str, status: int | str, count: int | None = None) -> None:
    config.ensure_account_dir(account)
    entry = {
        "time": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "account": account,
        "accessed_by": "Epic MCP",
        "tool": tool,
        "resource": resource,
        "url": url,
        "status": status,
        "records": count,
    }
    with open(config.access_log(account), "a") as f:
        f.write(json.dumps(entry) + "\n")


def read_access_log(account: str, limit: int = 50) -> list[dict]:
    try:
        lines = config.access_log(account).read_text().splitlines()
    except FileNotFoundError:
        return []
    return [json.loads(l) for l in lines[-limit:] if l.strip()]


def _get(account: str, url: str, params: dict | None, tool: str, resource: str) -> dict:
    tok = auth.get_valid_token(account)
    r = httpx.get(
        url,
        params=params,
        headers={"Authorization": f"Bearer {tok['access_token']}", "Accept": "application/fhir+json"},
        timeout=30,
    )
    full_url = str(r.request.url)
    if r.status_code != 200:
        log_access(account, tool, resource, full_url, r.status_code)
        raise RuntimeError(f"FHIR {resource} request failed ({r.status_code}): {r.text[:500]}")
    data = r.json()
    count = len(data.get("entry", [])) if data.get("resourceType") == "Bundle" else 1
    log_access(account, tool, resource, full_url, 200, count)
    return data


def patient_display_name(p: dict) -> str | None:
    name = next(iter(p.get("name", [])), {})
    return name.get("text") or " ".join(name.get("given", []) + [name.get("family", "")]).strip() or None


def patient_id(account: str) -> str:
    pid = auth.get_valid_token(account).get("patient")
    if not pid:
        raise auth.AuthError("The sign-in did not return a patient. Sign in again and pick a patient.")
    return pid


def search(account: str, resource: str, params: dict, tool: str, max_pages: int = 10) -> list[dict]:
    """Search and follow `next` links. Returns resources of the requested type only."""
    out: list[dict] = []
    url, p = f"{auth.account_base(account)}/{resource}", params
    for _ in range(max_pages):
        bundle = _get(account, url, p, tool, resource)
        for e in bundle.get("entry", []):
            res = e.get("resource", {})
            if res.get("resourceType") == resource:
                out.append(res)
        nxt = next((l["url"] for l in bundle.get("link", []) if l.get("relation") == "next"), None)
        if not nxt:
            break
        url, p = nxt, None
    return out


def read(account: str, resource: str, rid: str, tool: str) -> dict:
    return _get(account, f"{auth.account_base(account)}/{resource}/{rid}", None, tool, resource)
