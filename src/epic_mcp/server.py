"""Epic MCP server: read your own MyChart medication list."""

from __future__ import annotations

import time
import webbrowser
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import auth, config, directory, fhir

mcp = MCPServer(
    name="epic-mcp",
    instructions=(
        "Reads medication lists from one or more MyChart (Epic) accounts via FHIR. Read-only. "
        "Each account (e.g. 'me', 'mom') is a separate sign-in, possibly at a different health "
        "system, with its own data. To add an account, find its health system with "
        "`find_health_system`, then call `login` with the account name and that health system. "
        "Every tool takes "
        "an `account` argument and every result names the account and patient. Call "
        "`list_accounts` to see them. Never mix up accounts: when the user asks about a person, use "
        "that person's account, and state whose data you are showing. If an account is not signed "
        "in, call `login` for it, show the user the link, then call `login_status`. Medication info "
        "is not medical advice; suggest confirming changes with their care team or pharmacist."
    ),
)

RO = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


# ---------- helpers ----------

def _codeable_text(cc: dict | None) -> str | None:
    if not cc:
        return None
    if cc.get("text"):
        return cc["text"]
    for c in cc.get("coding", []):
        if c.get("display"):
            return c["display"]
    return None


def _simplify_med(mr: dict) -> dict:
    name = _codeable_text(mr.get("medicationCodeableConcept")) or (
        mr.get("medicationReference", {}).get("display")
    )
    dosages = []
    for d in mr.get("dosageInstruction", []):
        txt = d.get("text") or d.get("patientInstruction")
        if not txt:
            parts = []
            for dr in d.get("doseAndRate", []):
                q = dr.get("doseQuantity")
                if q:
                    parts.append(f"{q.get('value')} {q.get('unit', '')}".strip())
            if d.get("route"):
                parts.append(_codeable_text(d["route"]) or "")
            t = d.get("timing", {})
            if _codeable_text(t.get("code")):
                parts.append(_codeable_text(t.get("code")))
            txt = " ".join(p for p in parts if p) or None
        if txt:
            dosages.append(txt)
    disp = mr.get("dispenseRequest", {})
    qty = disp.get("quantity")
    out = {
        "medication": name,
        "status": mr.get("status"),
        "intent": mr.get("intent"),
        "dosage": "; ".join(dosages) or None,
        "prescribed_on": mr.get("authoredOn"),
        "prescriber": mr.get("requester", {}).get("display"),
        "reason": ", ".join(filter(None, (_codeable_text(r) for r in mr.get("reasonCode", [])))) or None,
        "category": ", ".join(filter(None, (_codeable_text(c) for c in mr.get("category", [])))) or None,
        "refills_allowed": disp.get("numberOfRepeatsAllowed"),
        "quantity": f"{qty.get('value')} {qty.get('unit', '')}".strip() if qty else None,
        "patient_reported": bool(mr.get("reportedBoolean") or mr.get("reportedReference")),
        "id": mr.get("id"),
    }
    return {k: v for k, v in out.items() if v not in (None, "", False)} | {"status": out["status"]}


def _practitioner_summary(p: dict) -> dict:
    name = next(iter(p.get("name", [])), {})
    full = name.get("text") or " ".join(
        name.get("prefix", []) + name.get("given", []) + [name.get("family", "")] + name.get("suffix", [])
    ).strip()
    quals = [q for q in (_codeable_text(q.get("code")) for q in p.get("qualification", [])) if q]
    phones = [t.get("value") for t in p.get("telecom", []) if t.get("system") == "phone" and t.get("value")]
    out = {"name": full or None, "credentials": ", ".join(quals) or None, "phone": phones[0] if phones else None}
    return {k: v for k, v in out.items() if v}


def _add_prescriber_details(account: str, meds: list[dict], simplified: list[dict]) -> None:
    """Look up each distinct prescriber once. Silently skip if the app lacks Practitioner access."""
    cache: dict[str, dict | None] = {}
    for raw, simple in zip(meds, simplified):
        ref = raw.get("requester", {}).get("reference", "")
        if not ref.startswith("Practitioner/"):
            continue
        pid = ref.split("/", 1)[1]
        if pid not in cache:
            try:
                cache[pid] = _practitioner_summary(fhir.read(account, "Practitioner", pid, tool="get_medications"))
            except Exception:  # noqa: BLE001  (403 if Practitioner.Read not granted)
                cache[pid] = None
        if cache[pid]:
            simple["prescriber_details"] = cache[pid]
            simple["prescriber"] = cache[pid].get("name") or simple.get("prescriber")


# ---------- accounts ----------

AccountArg = str
ACCOUNT_DOC = "Account name, e.g. 'me' or 'mom' (letters, digits, '-', '_')."


def _label(account: str) -> dict:
    tok = auth.load_tokens(account) or {}
    acct = config.load_account(account) or {}
    return {"account": account, "patient_name": tok.get("patient_name"), "health_system": acct.get("health_system")}


def _account_status(account: str) -> dict:
    acct = config.load_account(account)
    out: dict = {"account": account, "health_system": acct.get("health_system") if acct else None}
    tok = auth.load_tokens(account)
    if not acct or not tok or config.normalize_base(tok.get("fhir_base", "")) != acct["fhir_base"]:
        return out | {"signed_in": False}
    remaining = int(tok.get("expires_at", 0) - time.time())
    return out | {
        "patient_name": tok.get("patient_name"),
        "signed_in": remaining > 0 or bool(tok.get("refresh_token")),
        "access_expires_in_seconds": max(remaining, 0),
        "can_refresh": bool(tok.get("refresh_token")),
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def list_accounts() -> dict:
    """List the MyChart accounts set up in Epic MCP: health system, whose record, and sign-in status."""
    return {
        "default_account": config.DEFAULT_ACCOUNT,
        "accounts": [_account_status(a) for a in config.list_account_names()],
        "hint": "Add an account by calling `login` with a new account name and its health_system.",
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
def find_health_system(query: str) -> dict:
    """Search Epic's public directory for a health system's MyChart/FHIR endpoint by name (e.g. 'Optum')."""
    hits = directory.search(query)
    return {"query": query, "matches": hits, "count": len(hits)}


# ---------- auth tools ----------

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True))
def login(account: AccountArg = config.DEFAULT_ACCOUNT, health_system: str | None = None) -> dict:
    """Start MyChart sign-in for an account. Returns a link the user must open.

    account: e.g. 'me' or 'mom'. A new name creates a new account.
    health_system: the health system's exact name from `find_health_system` (or its FHIR base URL,
    or 'sandbox' for Epic's test system). Required for a new account; optional to re-sign-in an
    existing one (its saved health system is used).

    With MyChart proxy access, the user can sign in as themselves and pick the other person at the
    patient-selection step.
    """
    account = config.validate_account(account)
    if health_system:
        base, hs_name = directory.resolve(health_system)
    else:
        acct = config.load_account(account)
        if not acct:
            raise ValueError(
                f"'{account}' is a new account: pass health_system (use `find_health_system` to look it up)."
            )
        base, hs_name = acct["fhir_base"], acct["health_system"]
    url = auth.start_login(account, base, hs_name)
    opened = False
    try:
        opened = webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    return {
        "account": account,
        "health_system": hs_name,
        "sign_in_url": url,
        "browser_opened": opened,
        "next_steps": (
            f"Open the link and sign in to {hs_name} MyChart for account '{account}' (or pick that "
            f"person if using proxy access). Afterwards the browser returns to this computer "
            f"({config.LOCAL_REDIRECT_URI}); if it warns about the certificate, continue (it's this "
            "computer). Then call `login_status`. On a phone or other device, the final page offers a "
            "'Copy sign-in link' button: pass that link to `complete_login`. Only one sign-in can be "
            "in progress at a time."
        ),
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True))
def complete_login(redirect_url: str) -> dict:
    """Finish the in-progress sign-in by passing the sign-in link from the final page (https://localhost:.../callback?code=... or the relay page URL)."""
    account = auth.pending_account()
    auth.complete_login_from_url(redirect_url)
    return {"signed_in": True, **_label(account)}  # type: ignore[arg-type]


@mcp.tool(annotations=RO)
def login_status(account: AccountArg = config.DEFAULT_ACCOUNT) -> dict:
    """Report whether an account is signed in, its health system, whose record it is, and session expiry."""
    account = config.validate_account(account)
    out = _account_status(account)
    pending = auth.pending_result()
    if not out["signed_in"] and auth.pending_account() == account and pending:
        out["last_attempt"] = pending[1]
    return out


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def logout(account: AccountArg = config.DEFAULT_ACCOUNT) -> dict:
    """Delete an account's locally stored MyChart tokens (its health system and access log are kept)."""
    account = config.validate_account(account)
    return {"account": account, "tokens_deleted": auth.clear_tokens(account)}


# ---------- data tools ----------

@mcp.tool(annotations=RO)
def get_medications(
    account: AccountArg = config.DEFAULT_ACCOUNT,
    status: Literal["active", "all", "completed", "stopped", "on-hold", "cancelled"] = "active",
) -> dict:
    """Get one account's medication list (prescriptions and outside records).

    account: whose medications (see `list_accounts`).
    status: "active" (default) for current meds, "all" for full history, or a specific FHIR status.
    """
    account = config.validate_account(account)
    params: dict = {"patient": fhir.patient_id(account)}
    if status != "all":
        params["status"] = status
    meds = fhir.search(account, "MedicationRequest", params, tool="get_medications")
    simplified = [_simplify_med(m) for m in meds]
    _add_prescriber_details(account, meds, simplified)
    simplified.sort(key=lambda m: m.get("prescribed_on") or "", reverse=True)
    return {
        **_label(account),
        "count": len(simplified),
        "status_filter": status,
        "medications": simplified,
        "note": "Not medical advice. Confirm any changes with your care team or pharmacist.",
    }


@mcp.tool(annotations=RO)
def get_patient(account: AccountArg = config.DEFAULT_ACCOUNT) -> dict:
    """Get an account's basic demographics (name, birth date) to confirm whose record it is."""
    account = config.validate_account(account)
    p = fhir.read(account, "Patient", fhir.patient_id(account), tool="get_patient")
    name = fhir.patient_display_name(p)
    if name:
        auth.update_token_fields(account, patient_name=name)
    return {
        "account": account,
        "name": name,
        "birth_date": p.get("birthDate"),
        "gender": p.get("gender"),
        "id": p.get("id"),
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def get_access_log(account: AccountArg = config.DEFAULT_ACCOUNT, limit: int = 50) -> dict:
    """Show the local record of every time Epic MCP fetched an account's health data (most recent last)."""
    account = config.validate_account(account)
    return {
        **_label(account),
        "log_file": str(config.access_log(account)),
        "entries": fhir.read_access_log(account, limit),
    }
