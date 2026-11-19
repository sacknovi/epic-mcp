"""Look up health systems in Epic's public directories (R4 endpoints + brands)."""

from __future__ import annotations

import json
import time

import httpx

from . import config

_CACHE_MAX_AGE = 24 * 3600


def _load() -> list[dict]:
    """Return [{"name", "fhir_base"}], cached on disk for a day."""
    path = config.ENDPOINT_CACHE
    try:
        if time.time() - path.stat().st_mtime < _CACHE_MAX_AGE:
            return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    try:
        entries = _download_endpoints() + _download_brands()
        seen, unique = set(), []
        for e in entries:
            key = (e["name"].lower(), e["fhir_base"])
            if key not in seen:
                seen.add(key)
                unique.append(e)
        entries = unique
        config.ensure_base_dir()
        path.write_text(json.dumps(entries))
        return entries
    except Exception:  # noqa: BLE001  (offline: use a stale cache if there is one)
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            raise RuntimeError("Could not download Epic's endpoint directory.") from None


def _download_endpoints() -> list[dict]:
    r = httpx.get(config.ENDPOINT_DIRECTORY_URL, headers={"Accept": "application/json"}, timeout=60)
    r.raise_for_status()
    out = []
    for e in r.json().get("entry", []):
        res = e.get("resource", {})
        if res.get("name") and res.get("address"):
            out.append({"name": res["name"], "fhir_base": config.normalize_base(res["address"])})
    return out


def _download_brands() -> list[dict]:
    """Brand organizations (with an endpoint reference) -> their endpoint's FHIR base.

    The brands bundle is large (~100 MB), so failures here are tolerated: the R4 list still works.
    """
    try:
        r = httpx.get(config.BRANDS_DIRECTORY_URL, headers={"Accept": "application/json"}, timeout=300)
        r.raise_for_status()
        entries = r.json().get("entry", [])
    except Exception:  # noqa: BLE001
        return []
    endpoints = {}
    for e in entries:
        res = e.get("resource", {})
        if res.get("resourceType") == "Endpoint" and res.get("address") and e.get("fullUrl"):
            endpoints[e["fullUrl"]] = config.normalize_base(res["address"])
    out = []
    for e in entries:
        res = e.get("resource", {})
        if res.get("resourceType") != "Organization" or not res.get("name"):
            continue
        for ref in res.get("endpoint") or []:
            base = endpoints.get(ref.get("reference", ""))
            if base:
                out.append({"name": res["name"], "fhir_base": base})
    return out


def search(query: str, limit: int = 15) -> list[dict]:
    q = query.strip().lower()
    entries = [{"name": config.SANDBOX_NAME, "fhir_base": config.SANDBOX_FHIR_BASE}] + _load()
    words = q.replace("-", " ").split()
    exact = [e for e in entries if e["name"].lower() == q]
    if exact:
        return exact
    hits = [e for e in entries if all(w in e["name"].lower() for w in words)]
    return hits[:limit]


def resolve(health_system: str) -> tuple[str, str]:
    """Turn a health-system name or FHIR URL into (fhir_base, display name). Raises ValueError if unclear."""
    hs = health_system.strip()
    if hs.lower().startswith("https://"):
        base = config.normalize_base(hs)
        name = next((e["name"] for e in search_by_base(base)), base)
        return base, name
    if hs.lower() in ("sandbox", "epic sandbox", "test"):
        return config.SANDBOX_FHIR_BASE, config.SANDBOX_NAME
    hits = search(hs)
    if len(hits) == 1:
        return hits[0]["fhir_base"], hits[0]["name"]
    if not hits:
        raise ValueError(f"No health system matching '{hs}' in Epic's directory. Try `find_health_system`.")
    names = "; ".join(h["name"] for h in hits[:10])
    raise ValueError(f"'{hs}' matches several health systems: {names}. Use the exact name.")


def search_by_base(base: str) -> list[dict]:
    if config.is_sandbox(base):
        return [{"name": config.SANDBOX_NAME, "fhir_base": config.SANDBOX_FHIR_BASE}]
    return [e for e in _load() if e["fhir_base"] == config.normalize_base(base)]
