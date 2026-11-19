# Epic MCP

Epic MCP is a local, open-source [Model Context Protocol](https://modelcontextprotocol.io) server. It lets a patient use an AI assistant to read **their own** health records from MyChart (Epic) through Epic's standard patient-access FHIR R4 APIs.

> **Status:** early development. Not yet ready for general use.

## What it does

After you sign in with your own MyChart account, Epic MCP can read:

- **Your medications:** current and past medication orders at each health system you connect, including medications that health system recorded from other sources (for example during a medication review).
- **Your prescribers:** the name and credentials of the clinician who ordered each medication.
- **Your basic demographics:** used only to identify your patient record.

Epic MCP reads nothing else. All access is **read-only**. Epic MCP cannot change anything in your medical record, send messages or schedule appointments.

## How it works

1. You run Epic MCP on your own computer.
2. It opens your browser to your health system's MyChart login page. Epic MCP never sees your MyChart password.
3. You approve access. MyChart redirects your browser to a small static relay page, [`sacknovi.github.io/epic-mcp/callback`](docs/callback.html), which forwards the one-time sign-in code to Epic MCP at `https://localhost:8765/callback` on your own machine. On a phone, the page shows a **Copy sign-in link** button instead, so you can paste the link into your assistant. (The relay exists because some health systems' firewalls reject any sign-in that mentions `localhost`.)
4. Epic MCP exchanges the code for tokens. The exchange requires Epic MCP's private key and a one-time PKCE secret, so the code is useless to anyone else, and it expires within minutes.
5. Your AI assistant calls Epic MCP's tools, such as `get_medications`. Epic MCP fetches the data from your health system's FHIR API and returns it.

Epic MCP uses OAuth 2.0 with SMART on FHIR and PKCE. It authenticates as a confidential client with a signed JWT (`private_key_jwt`, RS384). The private key never leaves your computer, and only the public keys are published: [`jwks-nonprod.json`](jwks-nonprod.json) for Epic's sandbox and [`jwks-prod.json`](jwks-prod.json) for real health systems. Epic issues rolling refresh tokens, so you stay signed in between uses.

## Setup

Requirements: [uv](https://docs.astral.sh/uv/) and Python 3.12 or newer. uv installs Python if needed.

```bash
git clone https://github.com/sacknovi/epic-mcp.git
cd epic-mcp
uv sync

# Register with Claude Code
claude mcp add --scope user epic-mcp -- uv --directory "$PWD" run epic-mcp
```

Then ask your assistant something like "What medications am I on?" It will give you a MyChart sign-in link the first time.

### Multiple MyChart accounts

One Epic MCP server can hold several MyChart sign-ins, for example yours and a family member's, each at its own health system. Every tool takes an `account` argument (the default is `me`), and every result names the account, the patient and the health system. Just ask:

- "Sign in to Mom's MyChart at OptumCare East." This calls `find_health_system("OptumCare")`, then `login(account="mom", health_system="OptumCare East")`.
- "Compare my meds with Mom's."
- "Which accounts are set up?" This calls `list_accounts`.

With MyChart **proxy access**, sign in as yourself and choose the other person at the patient-selection step. That becomes its own account, using your own credentials.

Each account keeps its health system (`account.json`), tokens and access log in `~/.epic-mcp/profiles/<account>/`. Health systems are looked up in Epic's public [endpoint directory](https://open.epic.com/Endpoints/R4) and its larger [brands directory](https://open.epic.com/Endpoints/Brands), which lists patient-facing names such as "Northwell Health." Both are cached for a day. Epic's sandbox uses the sandbox client ID and the nonprod key; every real health system uses the production client ID and the prod key. The app keys and the localhost certificate are shared. Only one sign-in can be in progress at a time.

### Command-line use

```bash
uv run epic-mcp find optum                          # look up a health system
uv run epic-mcp probe "OptumCare East"              # does this health system recognize the app yet? (no sign-in)
uv run epic-mcp login -s "OptumCare East"           # sign in (account "me")
uv run epic-mcp -a mom login -s "Some Health System"  # add a second account
uv run epic-mcp -a mom login                        # sign in again (uses the saved health system)
uv run epic-mcp accounts          # list accounts and sign-in status
uv run epic-mcp -a mom meds       # print active medications (--status all for history)
uv run epic-mcp -a mom log        # show that account's access log
uv run epic-mcp -a mom logout     # delete that account's tokens
```

### Tools

| Tool | What it does |
|---|---|
| `list_accounts` | Accounts, with health system, whose record each is, and sign-in status |
| `find_health_system` | Search Epic's directory for a health system by name |
| `login` / `complete_login` / `login_status` / `logout` | Manage MyChart sign-in per account |
| `get_medications` | Medication list. Filter by `active` (default), `all`, `completed`, `stopped`, and so on |
| `get_patient` | Name and birth date, to confirm whose record it is |
| `get_access_log` | Every data fetch Epic MCP has made |

### Your own key pair

If you register your own Epic app, run `uv run epic-mcp keygen --env nonprod` and `uv run epic-mcp keygen --env prod`. Each creates a private key in `~/.epic-mcp/` and writes the matching public `jwks-<env>.json`. Host those files at https URLs and enter them as the app's Non-Production and Production JWK Set URLs. Epic MCP uses the nonprod key with the sandbox and the prod key everywhere else; set `EPIC_KEY_ENV` to override. Without a private key, Epic MCP acts as a public client, which gets no refresh tokens.

### Configuration

| Variable | Meaning |
|---|---|
| `EPIC_MCP_ACCOUNT` | Default account name. The default is `me` |
| `EPIC_MCP_BASE` | Base folder for keys, the certificate and profiles. The default is `~/.epic-mcp` |
| `EPIC_CALLBACK_PORT` | Local sign-in callback port. The default is `8765` |
| `EPIC_REDIRECT` | `relay` (default) sends MyChart to the GitHub Pages relay page; `local` sends it straight to `https://localhost:8765/callback`. The redirect URI used must be registered with Epic |
| `EPIC_RELAY_URI` | Relay page URL, if you host your own copy of `docs/callback.html` |
| `EPIC_SCOPES` | Override the requested SMART scopes (for troubleshooting a health system's sign-in) |

All local data lives in `~/.epic-mcp/`: `jwt-private-key-nonprod.pem` and `jwt-private-key-prod.pem` (mode 600), `certs/`, and `profiles/<account>/` with each account's `tokens.json` (mode 600) and `access_log.jsonl`. Delete that folder to remove everything.

The first sign-in uses a self-signed certificate for `https://localhost:8765`, so the browser will warn once. To avoid the warning, put [mkcert](https://github.com/FiloSottile/mkcert) certificates at `~/.epic-mcp/certs/localhost.pem` and `localhost-key.pem`.

## Connecting a new health system

For this app, which uses refresh tokens and a confidential-client key, Epic does **not** distribute the production client automatically. For each health system:

1. On fhir.epic.com, go to **Build Apps → Review & Manage Downloads**, choose the health system, and activate **Non-Production**, then **Production**, using the JWK Set URLs (`jwks-nonprod.json` and `jwks-prod.json`).
2. Wait for the sync, which can take up to about 12 hours. Check with `uv run epic-mcp probe "<health system>"`: `invalid_grant` means the system recognizes the app; `invalid_client` means it doesn't yet. The probe tests both the relay and the localhost redirect, and reports `blocked_by_firewall` when a firewall rejects the request.
3. Sign in: `uv run epic-mcp -a <account> login -s "<health system>"`.

Troubleshooting notes:

- **"The request is invalid" on the MyChart page after signing in:** the directory may list an outdated FHIR address for that health system. Check the `authorization_endpoint` in `<base>/.well-known/smart-configuration`, and if it points to a different host, sign in with that host's FHIR base URL (`-s https://...`). Mount Sinai Health System needed this.
- **A firewall "Access denied" or "Request Rejected" page:** use the relay redirect (the default). NYU Langone and Northwell need it.
- Epic's "Outside Record" medication APIs carry payer-exchange data and generally aren't authorized for patient apps. They are not the source of other organizations' entries you may see in the MyChart app. To see those, connect each organization as its own account.

## Privacy

- **No central server.** Epic MCP runs entirely on your own computer. The developer receives none of your data.
- **Tokens stay local.** Access tokens are kept on your machine and are never uploaded anywhere.
- **The sign-in relay sees only a one-time code.** The relay is a static GitHub Pages page with no server-side code. The one-time sign-in code passes through your browser to it, so GitHub's servers may log the request. The code can't be used without the private key on your computer, and it expires within minutes. The page loads nothing else and sends nothing anywhere.
- **Data goes only where you send it.** Health data is sent to the AI assistant you connect Epic MCP to. Check that assistant's privacy terms.
- **You can revoke access.** You can withdraw access at any time from MyChart, typically under "Linked Apps" or "Manage App Access," or by deleting Epic MCP's local token files.

## Data accessed (FHIR R4 resources)

| Resource | Purpose |
|---|---|
| Patient | Identify your record |
| MedicationRequest | Medication list |
| Practitioner | Prescriber name and credentials |

## Disclaimer

Epic MCP is an independent project. It is not affiliated with, endorsed by or sponsored by Epic Systems Corporation or any health system. "Epic" and "MyChart" are trademarks of Epic Systems Corporation. This tool does not provide medical advice. Always confirm medication details with your care team.

## Terms and privacy

- [Terms of Use](TERMS.md)
- [Privacy & Data Use Disclosure](PRIVACY.md)

## License

MIT
