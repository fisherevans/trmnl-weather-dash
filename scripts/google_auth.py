#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "google-auth-oauthlib>=1.0",
#     "google-auth>=2.0",
# ]
# ///
"""One-shot OAuth bootstrap for the Google Calendar source.

Run this once locally against the OAuth client JSON you downloaded from
Google Cloud Console. It opens a browser, asks you to grant the
read-only calendar scope to your account, and writes a refresh-token
JSON to the path you specify. Mount both files into the deployed
container; the source reads the token at fetch time and refreshes
access tokens automatically.

usage:
  uv run scripts/google_auth.py \\
    --credentials ~/Downloads/client_secret_*.json \\
    --token ./google-token.json

Re-run only when the refresh token is revoked (rare - happens when you
change your Google password, revoke the app from your account, or after
6 months of zero use for unverified apps).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--credentials", required=True, type=Path,
                    help="Path to the OAuth client JSON from Google Cloud Console")
    ap.add_argument("--token", required=True, type=Path,
                    help="Path to write the refreshable token JSON")
    ap.add_argument("--port", type=int, default=0,
                    help="Local callback port; 0 picks a free one (default)")
    args = ap.parse_args(argv)

    if not args.credentials.exists():
        print(f"credentials file not found: {args.credentials}", file=sys.stderr)
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(args.credentials), SCOPES)
    creds = flow.run_local_server(port=args.port, open_browser=True)
    args.token.write_text(creds.to_json())
    print(f"wrote {args.token}")
    print("  granted scopes: calendar.readonly")
    print("  the source can now mint access tokens on demand")
    return 0


if __name__ == "__main__":
    sys.exit(main())
