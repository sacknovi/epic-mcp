"""Epic MCP: read your own MyChart medication list from an AI assistant."""

from __future__ import annotations

import argparse
import json
import sys
import threading


def main() -> None:
    parser = argparse.ArgumentParser(prog="epic-mcp", description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="Run the MCP server over stdio (default)")
    sub.add_parser("login", help="Sign in to MyChart from the terminal")
    sub.add_parser("logout", help="Delete stored tokens")
    sub.add_parser("status", help="Show sign-in status")
    m = sub.add_parser("meds", help="Print your medication list")
    m.add_argument("--status", default="active")
    sub.add_parser("log", help="Print the access log")
    k = sub.add_parser("keygen", help="Create the confidential-client key pair and write the public JWK Set")
    k.add_argument("--out", default="jwks.json", help="Where to write the public JWK Set (default: jwks.json)")
    k.add_argument("--overwrite", action="store_true", help="Replace an existing private key")
    args = parser.parse_args()

    from . import auth, config, server

    if args.cmd in (None, "serve"):
        server.mcp.run("stdio")
    elif args.cmd == "login":
        url = auth.start_login()
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
        print(json.dumps(server.logout(), indent=2))
    elif args.cmd == "status":
        print(json.dumps(server.login_status(), indent=2))
    elif args.cmd == "meds":
        print(json.dumps(server.get_medications(args.status), indent=2))
    elif args.cmd == "keygen":
        from . import keys

        if not keys.has_private_key() or args.overwrite:
            keys.generate_private_key(overwrite=args.overwrite)
            print(f"Private key written to {config.PRIVATE_KEY} (keep it secret).")
        else:
            print(f"Using existing private key {config.PRIVATE_KEY}.")
        with open(args.out, "w") as f:
            json.dump(keys.jwks(), f, indent=2)
            f.write("\n")
        print(f"Public JWK Set written to {args.out}. Host it at an https URL and register it with Epic.")
    elif args.cmd == "log":
        print(json.dumps(server.get_access_log(), indent=2))
