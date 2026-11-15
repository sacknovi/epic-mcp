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
    ]
)

DATA_DIR = Path(os.environ.get("EPIC_MCP_HOME", Path.home() / ".epic-mcp"))
TOKEN_FILE = DATA_DIR / "tokens.json"
ACCESS_LOG = DATA_DIR / "access_log.jsonl"
CERT_DIR = DATA_DIR / "certs"


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DATA_DIR, 0o700)
    return DATA_DIR
