"""SMART on FHIR standalone patient login (PKCE + private_key_jwt) with per-account token storage.

Each account remembers its own health system (FHIR base URL); the client ID and signing key
are chosen from that (sandbox -> nonprod, real health systems -> prod).
"""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import secrets
import ssl
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

from . import config, keys

_smart_config_cache: dict[str, dict] = {}
_pending: dict | None = None  # account, fhir_base, health_system, state, verifier, server, result
_lock = threading.Lock()


class AuthError(Exception):
    pass


# ---------- SMART discovery ----------

def smart_configuration(fhir_base: str) -> dict:
    base = config.normalize_base(fhir_base)
    if base not in _smart_config_cache:
        r = httpx.get(f"{base}/.well-known/smart-configuration", headers={"Accept": "application/json"}, timeout=20)
        r.raise_for_status()
        _smart_config_cache[base] = r.json()
    return _smart_config_cache[base]


def account_base(account: str) -> str:
    acct = config.load_account(account)
    if not acct:
        raise AuthError(
            f"Account '{account}' isn't set up. Use `login` with account='{account}' and its health_system."
        )
    return acct["fhir_base"]


# ---------- token storage ----------

def _write_json_600(path, data: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)


def load_tokens(account: str) -> dict | None:
    try:
        return json.loads(config.token_file(account).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_tokens(account: str, fhir_base: str, tok: dict, keep: dict | None = None) -> None:
    """Write tokens. `keep` carries fields (e.g. patient_name) from the previous file."""
    config.ensure_account_dir(account)
    tok = dict(tok)
    for k, v in (keep or {}).items():
        tok.setdefault(k, v)
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    tok["fhir_base"] = config.normalize_base(fhir_base)
    tok["client_id"] = config.client_id_for(fhir_base)
    _write_json_600(config.token_file(account), tok)


def update_token_fields(account: str, **fields) -> None:
    tok = load_tokens(account)
    if tok:
        tok.update(fields)
        _write_json_600(config.token_file(account), tok)


def clear_tokens(account: str) -> bool:
    try:
        config.token_file(account).unlink()
        return True
    except FileNotFoundError:
        return False


def _token_request(fhir_base: str, data: dict) -> dict:
    endpoint = smart_configuration(fhir_base)["token_endpoint"]
    client_id = config.client_id_for(fhir_base)
    key = config.private_key_for(fhir_base)
    data = dict(data, client_id=client_id)
    if key.exists():
        data["client_assertion_type"] = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        data["client_assertion"] = keys.client_assertion(client_id, endpoint, key)
    r = httpx.post(endpoint, data=data, headers={"Accept": "application/json"}, timeout=30)
    if r.status_code != 200:
        raise AuthError(f"Token request failed ({r.status_code}): {r.text[:500]}")
    try:
        return r.json()
    except ValueError:
        blocked = "Request Rejected" in r.text
        raise AuthError(
            "The health system's firewall rejected the request (Request Rejected)."
            if blocked
            else f"Token endpoint returned a non-JSON response: {r.text[:200]}"
        ) from None


def _probe_with(fhir_base: str, redirect_uri: str) -> str:
    try:
        _token_request(
            fhir_base,
            {"grant_type": "authorization_code", "code": "probe", "redirect_uri": redirect_uri, "code_verifier": "x" * 50},
        )
        return "ok"
    except AuthError as e:
        if "Request Rejected" in str(e):
            return "blocked_by_firewall"
        m = re.search(r'"error"\s*:\s*"([^"]+)"', str(e))
        return m.group(1) if m else str(e)[:200]
    except ValueError:  # non-JSON body (e.g. a firewall's HTML page with status 200)
        return "blocked_by_firewall"


def probe_client(fhir_base: str) -> dict:
    """Send signed token requests with a fake code, once per registered redirect URI.

    Per redirect URI, Epic answers "invalid_grant" when it knows this client, accepts our key and
    the redirect URI (live), or "invalid_client" when the app hasn't reached that health system yet
    or the redirect URI isn't registered there. Returns {"live": bool, "relay": ..., "local": ...}.
    """
    results = {
        "relay": _probe_with(fhir_base, config.RELAY_REDIRECT_URI),
        "local": _probe_with(fhir_base, config.LOCAL_REDIRECT_URI),
    }
    results["live"] = "invalid_grant" in (results["relay"], results["local"])
    return results


@contextlib.contextmanager
def _token_lock(account: str):
    """Cross-process lock so only one process uses an account's (rolling) refresh token at a time."""
    config.ensure_account_dir(account)
    with open(config.token_lock(account), "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def get_valid_token(account: str) -> dict:
    """Return stored tokens, refreshing if possible. Raise AuthError if sign-in is needed."""
    base = account_base(account)
    tok = load_tokens(account)
    if not tok or config.normalize_base(tok.get("fhir_base", "")) != base:
        raise AuthError(f"Account '{account}' is not signed in. Use `login` with account='{account}'.")
    if time.time() < tok.get("expires_at", 0):
        return tok
    with _token_lock(account):
        tok = load_tokens(account) or tok  # another process may have refreshed while we waited
        if time.time() < tok.get("expires_at", 0):
            return tok
        if not tok.get("refresh_token"):
            raise AuthError(f"Session for '{account}' expired. Use `login` with account='{account}'.")
        try:
            new = _token_request(base, {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        except AuthError as e:
            raise AuthError(f"Could not refresh '{account}'; sign in again with `login`. ({e})") from e
        # Rolling refresh tokens: Epic returns a new one. Keep the old values only if omitted.
        keep = {k: tok[k] for k in ("patient", "refresh_token", "patient_name") if tok.get(k)}
        save_tokens(account, base, new, keep)
        return load_tokens(account)  # type: ignore[return-value]


# ---------- local HTTPS certificate ----------

def _ensure_cert() -> tuple[str, str]:
    """Use mkcert-style certs if present in CERT_DIR, else create a self-signed one."""
    config.ensure_base_dir()
    config.CERT_DIR.mkdir(exist_ok=True)
    cert = config.CERT_DIR / "localhost.pem"
    key = config.CERT_DIR / "localhost-key.pem"
    if cert.exists() and key.exists():
        return str(cert), str(key)

    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    pk = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.timezone.utc)
    c = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(pk.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=825))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(pk, hashes.SHA256())
    )
    fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    return str(cert), str(key)


# ---------- login flow ----------

def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _exchange_code(code: str, state: str) -> dict:
    with _lock:
        p = _pending
    if not p or state != p["state"]:
        raise AuthError("Login state mismatch or no login in progress. Start the login again.")
    base, account = p["fhir_base"], p["account"]
    tok = _token_request(
        base,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.REDIRECT_URI,
            "code_verifier": p["verifier"],
        },
    )
    # Only now commit the account's health system, so a failed login doesn't change it.
    config.save_account(account, base, p["health_system"])
    save_tokens(account, base, tok)
    # Record whose chart this is, so every result can be labeled with the patient's name.
    try:
        from . import fhir

        pt = fhir.read(account, "Patient", tok["patient"], tool="login")
        update_token_fields(account, patient_name=fhir.patient_display_name(pt))
    except Exception:  # noqa: BLE001
        pass
    return load_tokens(account) or tok


class _CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep stdio (MCP channel) clean
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        q = urllib.parse.parse_qs(parsed.query)
        ok, msg = False, ""
        try:
            if "error" in q:
                raise AuthError(f"{q['error'][0]}: {q.get('error_description', [''])[0]}")
            tok = _exchange_code(q["code"][0], q.get("state", [""])[0])
            who = tok.get("patient_name") or "the patient"
            ok, msg = True, f"Signed in to {who}'s record. You can close this tab and return to your assistant."
        except Exception as e:  # noqa: BLE001
            msg = f"Sign-in failed: {e}"
        with _lock:
            if _pending:
                _pending["result"] = (ok, msg)
        body = f"<html><body style='font-family:sans-serif;padding:2em'><h2>Epic MCP</h2><p>{msg}</p></body></html>"
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())
        threading.Thread(target=self.server.shutdown, daemon=True).start()


def start_login(account: str, fhir_base: str, health_system: str) -> str:
    """Begin a login for `account` at `fhir_base`. Starts the local callback listener; returns the sign-in URL."""
    global _pending
    account = config.validate_account(account)
    base = config.normalize_base(fhir_base)
    sc = smart_configuration(base)
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)

    with _lock:
        if _pending and _pending.get("server"):
            try:
                _pending["server"].shutdown()
                _pending["server"].server_close()
            except Exception:  # noqa: BLE001
                pass
        _pending = {
            "account": account,
            "fhir_base": base,
            "health_system": health_system,
            "state": state,
            "verifier": verifier,
            "server": None,
            "result": None,
        }

    try:
        cert, key = _ensure_cert()
        httpd = HTTPServer((config.CALLBACK_HOST, config.CALLBACK_PORT), _CallbackHandler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        with _lock:
            _pending["server"] = httpd
    except OSError:
        pass  # port busy: the user can still finish with complete_login(<redirect URL>)

    params = {
        "response_type": "code",
        "client_id": config.client_id_for(base),
        "redirect_uri": config.REDIRECT_URI,
        "scope": config.SCOPES,
        "state": state,
        "aud": base,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{sc['authorization_endpoint']}?{urllib.parse.urlencode(params)}"


def complete_login_from_url(redirect_url: str) -> dict:
    """Finish a login by pasting the full URL the browser was redirected to."""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(redirect_url.strip()).query)
    if "error" in q:
        raise AuthError(f"{q['error'][0]}: {q.get('error_description', [''])[0]}")
    if "code" not in q:
        raise AuthError("That URL has no `code` parameter. Paste the full address from the browser bar.")
    tok = _exchange_code(q["code"][0], q.get("state", [""])[0])
    with _lock:
        if _pending:
            _pending["result"] = (True, "Signed in.")
    return tok


def pending_result() -> tuple[bool, str] | None:
    with _lock:
        return _pending.get("result") if _pending else None


def pending_account() -> str | None:
    with _lock:
        return _pending.get("account") if _pending else None


def wait_for_login(timeout: float = 300) -> tuple[bool, str]:
    end = time.time() + timeout
    while time.time() < end:
        r = pending_result()
        if r:
            return r
        time.sleep(0.5)
    return False, "Timed out waiting for MyChart sign-in."
