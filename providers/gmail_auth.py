"""
providers/gmail_auth.py
Run this ONCE to generate your Gmail OAuth refresh token.
After running it, paste the printed token into your .env file.
You never need to run this again unless you revoke access.

Usage:
    python providers/gmail_auth.py --credentials path/to/client_secret.json
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


def main():
    parser = argparse.ArgumentParser(description="Generate Gmail OAuth refresh token")
    parser.add_argument(
        "--credentials",
        required=True,
        help="Path to client_secret.json downloaded from Google Cloud Console",
    )
    args = parser.parse_args()

    if not os.path.exists(args.credentials):
        print(f"\n✗ File not found: {args.credentials}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("  Gmail OAuth Setup")
    print("=" * 60)
    print("\nA browser window will open. Sign in with the Gmail account")
    print("you want to use as your IT support mailbox.")
    print("\nIf you see a warning saying 'Google hasn't verified this app',")
    print("click 'Advanced' → 'Go to [app name] (unsafe)' — this is normal")
    print("for apps you register yourself.\n")
    input("Press Enter to open the browser...")

    flow = InstalledAppFlow.from_client_secrets_file(args.credentials, SCOPES)
    creds = flow.run_local_server(port=0)

    # Read client_id and client_secret from the credentials file
    with open(args.credentials) as f:
        client_data = json.load(f)

    client = client_data.get("installed") or client_data.get("web", {})
    client_id     = client.get("client_id", "")
    client_secret = client.get("client_secret", "")

    print("\n" + "=" * 60)
    print("  ✓ Authentication successful!")
    print("=" * 60)
    print("\nAdd these lines to your .env file:\n")
    print(f"GMAIL_CLIENT_ID={client_id}")
    print(f"GMAIL_CLIENT_SECRET={client_secret}")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print(f"GMAIL_ADDRESS=your-gmail@gmail.com")
    print("\n" + "=" * 60)
    print("  Keep these values secret. Never commit them to git.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
