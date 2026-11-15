"""Configuration for Epic MCP. Every value can be overridden with an environment variable."""

from __future__ import annotations

import os
from pathlib import Path

# Public (non-secret) client IDs registered on fhir.epic.com.
SANDBOX_CLIENT_ID = "8a296228-87f1-4f6c-81db-64d69647cd12"
SANDBOX_FHIR_BASE = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4"

CLIENT_ID = os.environ.get("EPIC_CLIENT_ID", SANDBOX_CLIENT_ID)
FHIR_BASE = os.environ.get("EPIC_FHIR_BASE", SANDBOX_FHIR_BASE).rstrip("/")

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
# Per-login files (tokens, access log) live in DATA_DIR, which is a separate
# folder per profile so different MyChart logins never mix.
BASE_DIR = Path(os.environ.get("EPIC_MCP_BASE", Path.home() / ".epic-mcp"))
PROFILE = os.environ.get("EPIC_MCP_PROFILE", "").strip()
if PROFILE and not PROFILE.replace("-", "").replace("_", "").isalnum():
    raise ValueError("EPIC_MCP_PROFILE may contain only letters, digits, '-' and '_'")
DATA_DIR = Path(
    os.environ.get("EPIC_MCP_HOME", BASE_DIR / "profiles" / PROFILE if PROFILE else BASE_DIR)
)
TOKEN_FILE = DATA_DIR / "tokens.json"
TOKEN_LOCK = DATA_DIR / "tokens.lock"
ACCESS_LOG = DATA_DIR / "access_log.jsonl"
CERT_DIR = BASE_DIR / "certs"

# Confidential-client keys (private_key_jwt): one for Epic's sandbox ("nonprod"),
# one for real health systems ("prod"), chosen from the FHIR base URL. If the key
# file exists, token requests are signed with it; otherwise the app acts as a public client.
KEY_ENV = os.environ.get("EPIC_KEY_ENV", "nonprod" if FHIR_BASE == SANDBOX_FHIR_BASE else "prod")


def private_key_path(env: str) -> Path:
    return BASE_DIR / f"jwt-private-key-{env}.pem"


PRIVATE_KEY = Path(os.environ.get("EPIC_PRIVATE_KEY", private_key_path(KEY_ENV)))
JWT_ALG = "RS384"  # Epic accepts RS384 / ES384 for client assertions


def ensure_data_dir() -> Path:
    for d in (BASE_DIR, DATA_DIR):
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    return DATA_DIR
