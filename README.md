# Epic MCP

Epic MCP is a local, open-source [Model Context Protocol](https://modelcontextprotocol.io) server. It lets a patient use an AI assistant to read **their own** health records from MyChart (Epic) through Epic's standard patient-access FHIR R4 APIs.

> **Status:** early development. Not yet ready for general use.

## What it does

After you sign in with your own MyChart account, Epic MCP can read:

- **Your medications:** current and past medication orders, including ones recorded by other organizations.
- **Your basic demographics:** used only to identify your patient record.

Epic MCP reads nothing else. All access is **read-only**. Epic MCP cannot change anything in your medical record, send messages or schedule appointments.

## How it works

1. You run Epic MCP on your own computer.
2. It opens your browser to your health system's MyChart login page. Epic MCP never sees your MyChart password.
3. You approve access. Epic sends a short-lived access token back to Epic MCP at `https://localhost:8765/callback`, which is on your own machine.
4. Your AI assistant calls Epic MCP's tools, such as `get_medications`. Epic MCP fetches the data from your health system's FHIR API and returns it.

Epic MCP uses OAuth 2.0 with SMART on FHIR and PKCE, running as a public client.

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

### Command-line use

```bash
uv run epic-mcp login     # sign in to MyChart
uv run epic-mcp meds      # print active medications (--status all for history)
uv run epic-mcp log       # show the access log
uv run epic-mcp logout    # delete stored tokens
```

### Tools

| Tool | What it does |
|---|---|
| `login` / `complete_login` / `login_status` / `logout` | Manage MyChart sign-in |
| `get_medications` | Medication list. Filter by `active` (default), `all`, `completed`, `stopped`, and so on |
| `get_patient` | Name and birth date, to confirm whose record it is |
| `get_access_log` | Every data fetch Epic MCP has made |

### Configuration

By default, Epic MCP connects to Epic's **sandbox**, which has test patients only. To use a real health system, set:

| Variable | Meaning |
|---|---|
| `EPIC_FHIR_BASE` | Your health system's FHIR R4 base URL, from Epic's endpoint directory |
| `EPIC_CLIENT_ID` | The production client ID |
| `EPIC_MCP_HOME` | Data folder. The default is `~/.epic-mcp` |

All local data lives in `~/.epic-mcp/`: `tokens.json` (mode 600), `access_log.jsonl` and `certs/`. Delete that folder to remove everything.

The first sign-in uses a self-signed certificate for `https://localhost:8765`, so the browser will warn once. To avoid the warning, put [mkcert](https://github.com/FiloSottile/mkcert) certificates at `~/.epic-mcp/certs/localhost.pem` and `localhost-key.pem`.

## Privacy

- **No central server.** Epic MCP runs entirely on your own computer. The developer receives none of your data.
- **Tokens stay local.** Access tokens are kept on your machine and are never uploaded anywhere.
- **Data goes only where you send it.** Health data is sent to the AI assistant you connect Epic MCP to. Check that assistant's privacy terms.
- **You can revoke access.** You can withdraw access at any time from MyChart, typically under "Linked Apps" or "Manage App Access," or by deleting Epic MCP's local token files.

## Data accessed (FHIR R4 resources)

| Resource | Purpose |
|---|---|
| Patient | Identify your record |
| MedicationRequest | Medication list (signed orders and outside records) |

## Disclaimer

Epic MCP is an independent project. It is not affiliated with, endorsed by or sponsored by Epic Systems Corporation or any health system. "Epic" and "MyChart" are trademarks of Epic Systems Corporation. This tool does not provide medical advice. Always confirm medication details with your care team.

## Terms and privacy

- [Terms of Use](TERMS.md)
- [Privacy & Data Use Disclosure](PRIVACY.md)

## License

MIT
