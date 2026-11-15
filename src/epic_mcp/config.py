"""Configuration for Epic MCP. Every value can be overridden with an environment variable."""

from __future__ import annotations

import os
from pathlib import Path

# Public (non-secret) client IDs registered on fhir.epic.com.
SANDBOX_CLIENT_ID = "8a296228-87f1-4f6c-81db-64d69647cd12"
PRODUCTION_CLIENT_ID = "9de34a71-8ccb-477f-a9d8-c8a4892fb820"
SANDBOX_FHIR_BASE = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4"

FHIR_BASE = (os.environ.get("EPIC_FHIR_BASE") or SANDBOX_FHIR_BASE).rstrip("/")
IS_SANDBOX = FHIR_BASE == SANDBOX_FHIR_BASE
CLIENT_ID = os.environ.get("EPIC_CLIENT_ID", SANDBOX_CLIENT_ID if IS_SANDBOX else PRODUCTION_CLIENT_ID)

CALLBACK_HOST = "localhost"
CALLBACK_PORT = int(os.environ.get("EPIC_CALLBACK_PORT", "8765"))
REDIRECT_URI = f"https://{CALLBACK_HOST}:{CALLBACK_PORT}/callback"

# SMART v1 scopes. Epic grants access based on the app registration, but the
# request should still list what the app needs.
SCOPES = " ".join(
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

# Shared app-level files (keys, localhost certificate) live in BASE_DIR.
# Each MyChart account has its own folder (tokens, access log) under BASE_DIR/profiles/<account>,
# so different logins never mix.
BASE_DIR = Path(os.environ.get("EPIC_MCP_BASE", Path.home() / ".epic-mcp"))
PROFILES_DIR = BASE_DIR / "profiles"
CERT_DIR = BASE_DIR / "certs"
DEFAULT_ACCOUNT = os.environ.get("EPIC_MCP_ACCOUNT", "me")


def validate_account(name: str) -> str:
    name = (name or "").strip().lower()
    if not name or len(name) > 40 or not name.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Account names may contain only letters, digits, '-' and '_' (max 40 chars).")
    return name


def account_dir(account: str) -> Path:
    return PROFILES_DIR / validate_account(account)


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

# Confidential-client keys (private_key_jwt): one for Epic's sandbox ("nonprod"),
# one for real health systems ("prod"), chosen from the FHIR base URL. If the key
# file exists, token requests are signed with it; otherwise the app acts as a public client.
KEY_ENV = os.environ.get("EPIC_KEY_ENV", "nonprod" if IS_SANDBOX else "prod")


def private_key_path(env: str) -> Path:
    return BASE_DIR / f"jwt-private-key-{env}.pem"


PRIVATE_KEY = Path(os.environ.get("EPIC_PRIVATE_KEY", private_key_path(KEY_ENV)))
JWT_ALG = "RS384"  # Epic accepts RS384 / ES384 for client assertions


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
