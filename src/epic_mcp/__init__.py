"""Epic MCP: read your own MyChart medication list from an AI assistant."""

from __future__ import annotations

import argparse
import json
import sys
import threading


def main() -> None:
    parser = argparse.ArgumentParser(prog="epic-mcp", description=__doc__)
    parser.add_argument("-a", "--account", help="MyChart account name (default: me)")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="Run the MCP server over stdio (default)")
    lg = sub.add_parser("login", help="Sign in to MyChart from the terminal")
    lg.add_argument("-s", "--health-system", help="Health system name, FHIR base URL, or 'sandbox' (required for a new account)")
    fd = sub.add_parser("find", help="Search Epic's directory for a health system")
    fd.add_argument("query")
    sub.add_parser("logout", help="Delete stored tokens")
    sub.add_parser("status", help="Show sign-in status")
    sub.add_parser("accounts", help="List accounts")
    m = sub.add_parser("meds", help="Print your medication list")
    m.add_argument("--status", default="active")
    sub.add_parser("log", help="Print the access log")
    k = sub.add_parser("keygen", help="Create a confidential-client key pair and write its public JWK Set")
    k.add_argument("--env", choices=["nonprod", "prod"], required=True, help="Sandbox (nonprod) or production key")
    k.add_argument("--out", help="Where to write the public JWK Set (default: jwks-<env>.json)")
    k.add_argument("--overwrite", action="store_true", help="Replace an existing private key")
    args = parser.parse_args()

    from . import auth, config, server

    acct = config.validate_account(args.account or config.DEFAULT_ACCOUNT)

    if args.cmd in (None, "serve"):
        server.mcp.run("stdio")
    elif args.cmd == "login":
        from . import directory

        if args.health_system:
            base, hs = directory.resolve(args.health_system)
        else:
            saved = config.load_account(acct)
            if not saved:
                sys.exit(f"'{acct}' is a new account: pass --health-system (search with `epic-mcp find <name>`).")
            base, hs = saved["fhir_base"], saved["health_system"]
        url = auth.start_login(acct, base, hs)
        print(f"Account: {acct}   Health system: {hs}")
        print("Open this link in your browser and sign in to MyChart:\n")
        print(url, "\n")
        print("If the final page doesn't load, paste the full address from the browser bar here")
        print("and press Enter (or just wait for automatic completion).")

        def read_paste():
            line = sys.stdin.readline().strip()
            if line:
                try:
                    auth.complete_login_from_url(line)
                except Exception as e:  # noqa: BLE001
                    print(f"Error: {e}")

        threading.Thread(target=read_paste, daemon=True).start()
        ok, msg = auth.wait_for_login()
        print(("✓ " if ok else "✗ ") + msg)
        sys.exit(0 if ok else 1)
    elif args.cmd == "logout":
        print(json.dumps(server.logout(acct), indent=2))
    elif args.cmd == "status":
        print(json.dumps(server.login_status(acct), indent=2))
    elif args.cmd == "accounts":
        print(json.dumps(server.list_accounts(), indent=2))
    elif args.cmd == "meds":
        print(json.dumps(server.get_medications(acct, args.status), indent=2))
    elif args.cmd == "find":
        print(json.dumps(server.find_health_system(args.query), indent=2))
    elif args.cmd == "keygen":
        from . import keys

        path = config.private_key_path(args.env)
        out = args.out or f"jwks-{args.env}.json"
        if not path.exists() or args.overwrite:
            keys.generate_private_key(path, overwrite=args.overwrite)
            print(f"Private key written to {path} (keep it secret).")
        else:
            print(f"Using existing private key {path}.")
        with open(out, "w") as f:
            json.dump(keys.jwks(path), f, indent=2)
            f.write("\n")
        print(f"Public JWK Set written to {out}. Host it at an https URL and register it with Epic.")
    elif args.cmd == "log":
        print(json.dumps(server.get_access_log(acct), indent=2))
