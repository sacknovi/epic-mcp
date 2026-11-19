"""Configuration for Epic MCP. Every value can be overridden with an environment variable."""

from __future__ import annotations

import json
import os
from pathlib import Path

# Public (non-secret) client IDs registered on fhir.epic.com.
SANDBOX_CLIENT_ID = "8a296228-87f1-4f6c-81db-64d69647cd12"
PRODUCTION_CLIENT_ID = "9de34a71-8ccb-477f-a9d8-c8a4892fb820"
SANDBOX_FHIR_BASE = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4"
SANDBOX_NAME = "Epic Sandbox (test patients)"

# Epic's public directories: patient-access FHIR R4 endpoints, and the larger "brands"
# bundle that maps patient-facing organization names (e.g. "Northwell Health") to endpoints.
ENDPOINT_DIRECTORY_URL = "https://open.epic.com/Endpoints/R4"
BRANDS_DIRECTORY_URL = "https://open.epic.com/Endpoints/Brands"

CALLBACK_HOST = "localhost"
CALLBACK_PORT = int(os.environ.get("EPIC_CALLBACK_PORT", "8765"))
LOCAL_REDIRECT_URI = f"https://{CALLBACK_HOST}:{CALLBACK_PORT}/callback"
# Some health systems' firewalls reject any request mentioning localhost, so by default MyChart
# redirects to a static relay page (docs/callback.html on GitHub Pages) that forwards the browser
# to the local callback above, or lets the user copy the link on a phone. Set EPIC_REDIRECT=local
# to have MyChart redirect straight to localhost instead. Both URIs are registered with Epic.
RELAY_REDIRECT_URI = os.environ.get("EPIC_RELAY_URI", "https://sacknovi.github.io/epic-mcp/callback")
REDIRECT_MODE = os.environ.get("EPIC_REDIRECT", "relay").strip().lower()
REDIRECT_URI = LOCAL_REDIRECT_URI if REDIRECT_MODE == "local" else RELAY_REDIRECT_URI

# SMART v1 scopes. Epic grants access based on the app registration, but the
# request should still list what the app needs.
_DEFAULT_SCOPES = " ".join(
    [
        "openid",
        "fhirUser",
        "launch/patient",
        "patient/Patient.read",
        "patient/MedicationRequest.read",
        "patient/Practitioner.read",
        "offline_access",
    ]
)
# EPIC_SCOPES overrides the requested scopes (for troubleshooting a health system's sign-in).
SCOPES = os.environ.get("EPIC_SCOPES") or _DEFAULT_SCOPES

JWT_ALG = "RS384"  # Epic accepts RS384 / ES384 for client assertions

# Shared app-level files (keys, localhost certificate, endpoint cache) live in BASE_DIR.
# Each MyChart account has its own folder under BASE_DIR/profiles/<account> holding its
# health system (account.json), tokens and access log, so different logins never mix.
BASE_DIR = Path(os.environ.get("EPIC_MCP_BASE", Path.home() / ".epic-mcp"))
PROFILES_DIR = BASE_DIR / "profiles"
CERT_DIR = BASE_DIR / "certs"
ENDPOINT_CACHE = BASE_DIR / "endpoints-r4.json"
DEFAULT_ACCOUNT = os.environ.get("EPIC_MCP_ACCOUNT", "me")


# ---------- health-system-specific settings ----------

def normalize_base(url: str) -> str:
    return url.strip().rstrip("/")


def is_sandbox(fhir_base: str) -> bool:
    return normalize_base(fhir_base) == SANDBOX_FHIR_BASE


def client_id_for(fhir_base: str) -> str:
    return SANDBOX_CLIENT_ID if is_sandbox(fhir_base) else PRODUCTION_CLIENT_ID


def private_key_path(env: str) -> Path:
    return BASE_DIR / f"jwt-private-key-{env}.pem"


def private_key_for(fhir_base: str) -> Path:
    """Sandbox uses the nonprod key; every real health system uses the prod key."""
    return private_key_path("nonprod" if is_sandbox(fhir_base) else "prod")


# ---------- accounts ----------

def validate_account(name: str) -> str:
    name = (name or "").strip().lower()
    if not name or len(name) > 40 or not name.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Account names may contain only letters, digits, '-' and '_' (max 40 chars).")
    return name


def account_dir(account: str) -> Path:
    return PROFILES_DIR / validate_account(account)


def account_file(account: str) -> Path:
    return account_dir(account) / "account.json"


def token_file(account: str) -> Path:
    return account_dir(account) / "tokens.json"


def token_lock(account: str) -> Path:
    return account_dir(account) / "tokens.lock"


def access_log(account: str) -> Path:
    return account_dir(account) / "access_log.jsonl"


def list_account_names() -> list[str]:
    if not PROFILES_DIR.exists():
        return []
    return sorted(d.name for d in PROFILES_DIR.iterdir() if d.is_dir())


def load_account(account: str) -> dict | None:
    """Return {"fhir_base", "health_system"} for an account, or None if it isn't set up."""
    try:
        return json.loads(account_file(account).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # Accounts created before account.json existed: fall back to the base stored with the tokens.
    try:
        tok = json.loads(token_file(account).read_text())
        if tok.get("fhir_base"):
            base = tok["fhir_base"]
            return {"fhir_base": base, "health_system": SANDBOX_NAME if is_sandbox(base) else base}
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return None


def save_account(account: str, fhir_base: str, health_system: str) -> None:
    ensure_account_dir(account)
    account_file(account).write_text(
        json.dumps({"fhir_base": normalize_base(fhir_base), "health_system": health_system}, indent=2)
    )


def ensure_base_dir() -> Path:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(BASE_DIR, 0o700)
    return BASE_DIR


def ensure_account_dir(account: str) -> Path:
    ensure_base_dir()
    d = account_dir(account)
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(PROFILES_DIR, 0o700)
    os.chmod(d, 0o700)
    return d
