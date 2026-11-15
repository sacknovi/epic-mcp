"""Epic MCP server: read your own MyChart medication list."""

from __future__ import annotations

import time
import webbrowser
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import auth, config, fhir

mcp = MCPServer(
    name="epic-mcp",
    instructions=(
        "Reads the signed-in patient's own medication list from MyChart (Epic) via FHIR. "
        "Read-only. If a tool says the user is not signed in, call `login`, show the user the "
        "sign-in link, and after they finish call `login_status`. Medication info is not medical "
        "advice; suggest confirming changes with their care team or pharmacist."
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


def _add_prescriber_details(meds: list[dict], simplified: list[dict]) -> None:
    """Look up each distinct prescriber once. Silently skip if the app lacks Practitioner access."""
    cache: dict[str, dict | None] = {}
    for raw, simple in zip(meds, simplified):
        ref = raw.get("requester", {}).get("reference", "")
        if not ref.startswith("Practitioner/"):
            continue
        pid = ref.split("/", 1)[1]
        if pid not in cache:
            try:
                cache[pid] = _practitioner_summary(fhir.read("Practitioner", pid, tool="get_medications"))
            except Exception:  # noqa: BLE001  (403 if Practitioner.Read not granted)
                cache[pid] = None
        if cache[pid]:
            simple["prescriber_details"] = cache[pid]
            simple["prescriber"] = cache[pid].get("name") or simple.get("prescriber")


# ---------- auth tools ----------

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True))
def login() -> dict:
    """Start MyChart sign-in. Returns a link the user must open to sign in and approve access."""
    url = auth.start_login()
    opened = False
    try:
        opened = webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    return {
        "sign_in_url": url,
        "browser_opened": opened,
        "health_system": config.FHIR_BASE,
        "next_steps": (
            "Open the link, sign in to MyChart and approve access. The browser will be sent to "
            f"{config.REDIRECT_URI}; if it warns about the certificate, continue (it's this computer). "
            "Then call `login_status`. If the final page doesn't load, copy the full address from the "
            "browser bar and pass it to `complete_login`."
        ),
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True))
def complete_login(redirect_url: str) -> dict:
    """Finish sign-in by passing the full URL the browser ended on (https://localhost:.../callback?code=...)."""
    tok = auth.complete_login_from_url(redirect_url)
    return {"signed_in": True, "patient_id": tok.get("patient")}


@mcp.tool(annotations=RO)
def login_status() -> dict:
    """Report whether the user is signed in to MyChart and when the session expires."""
    pending = auth.pending_result()
    tok = auth.load_tokens()
    if not tok or tok.get("fhir_base") != config.FHIR_BASE:
        return {"signed_in": False, "profile": config.PROFILE or "default", "last_attempt": pending[1] if pending else None}
    remaining = int(tok.get("expires_at", 0) - time.time())
    return {
        "signed_in": remaining > 0 or bool(tok.get("refresh_token")),
        "profile": config.PROFILE or "default",
        "patient_id": tok.get("patient"),
        "access_expires_in_seconds": max(remaining, 0),
        "can_refresh": bool(tok.get("refresh_token")),
        "health_system": config.FHIR_BASE,
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def logout() -> dict:
    """Delete the locally stored MyChart tokens."""
    return {"tokens_deleted": auth.clear_tokens()}


# ---------- data tools ----------

@mcp.tool(annotations=RO)
def get_medications(
    status: Literal["active", "all", "completed", "stopped", "on-hold", "cancelled"] = "active",
) -> dict:
    """Get the signed-in patient's medication list (prescriptions and outside records).

    status: "active" (default) for current meds, "all" for full history, or a specific FHIR status.
    """
    params: dict = {"patient": fhir.patient_id()}
    if status != "all":
        params["status"] = status
    meds = fhir.search("MedicationRequest", params, tool="get_medications")
    simplified = [_simplify_med(m) for m in meds]
    _add_prescriber_details(meds, simplified)
    simplified.sort(key=lambda m: m.get("prescribed_on") or "", reverse=True)
    return {
        "count": len(simplified),
        "status_filter": status,
        "medications": simplified,
        "note": "Not medical advice. Confirm any changes with your care team or pharmacist.",
    }


@mcp.tool(annotations=RO)
def get_patient() -> dict:
    """Get the signed-in patient's basic demographics (name, birth date) to confirm whose record this is."""
    p = fhir.read("Patient", fhir.patient_id(), tool="get_patient")
    name = next(iter(p.get("name", [])), {})
    return {
        "name": name.get("text") or " ".join(name.get("given", []) + [name.get("family", "")]).strip(),
        "birth_date": p.get("birthDate"),
        "gender": p.get("gender"),
        "id": p.get("id"),
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def get_access_log(limit: int = 50) -> dict:
    """Show the local record of every time Epic MCP fetched health data (most recent last)."""
    entries = fhir.read_access_log(limit)
    return {"log_file": str(config.ACCESS_LOG), "entries": entries}
