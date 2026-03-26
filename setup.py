"""
setup.py
Interactive setup wizard — configures the IT Email Router from scratch.
Writes a valid .env file and validates all credentials before finishing.

Run with:
    python setup.py
"""

import getpass
import json
import os
import sys
import re

# Suppress noisy gRPC/absl log messages on Windows before any Google libs load
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")


# ── Colours (work on Windows 10+ and all Linux/macOS terminals) ──────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
DIM    = "\033[2m"


def header(text: str) -> None:
    width = 62
    print(f"\n{CYAN}{'═' * width}{RESET}")
    print(f"{CYAN}  {BOLD}{text}{RESET}")
    print(f"{CYAN}{'═' * width}{RESET}\n")


def step(n: int, total: int, text: str) -> None:
    print(f"{BOLD}[{n}/{total}] {text}{RESET}")


def ok(text: str) -> None:
    print(f"  {GREEN}✓{RESET}  {text}")


def warn(text: str) -> None:
    print(f"  {YELLOW}⚠{RESET}  {text}")


def err(text: str) -> None:
    print(f"  {RED}✗{RESET}  {text}")


def _secret_input(prompt: str) -> str:
    """
    Read a secret value showing * for each character typed.
    Works on Windows (msvcrt) and Unix (termios).
    Falls back to plain getpass if neither is available.
    """
    sys.stdout.write(prompt)
    sys.stdout.flush()
    value = ""

    try:
        # Windows
        import msvcrt
        while True:
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):          # Enter — done
                sys.stdout.write("\n")
                break
            elif ch == "\x03":               # Ctrl+C
                raise KeyboardInterrupt
            elif ch in ("\x08", "\x7f"):    # Backspace
                if value:
                    value = value[:-1]
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
            elif ch == "\x00" or ch == "\xe0":  # Special key prefix — skip next
                msvcrt.getwch()
            else:
                value += ch
                sys.stdout.write("*")
                sys.stdout.flush()
    except ImportError:
        # Unix fallback
        try:
            import termios, tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.cbreak(fd)
                while True:
                    ch = sys.stdin.read(1)
                    if ch in ("\r", "\n"):
                        sys.stdout.write("\n")
                        break
                    elif ch == "\x03":
                        raise KeyboardInterrupt
                    elif ch in ("\x08", "\x7f"):
                        if value:
                            value = value[:-1]
                            sys.stdout.write("\b \b")
                            sys.stdout.flush()
                    else:
                        value += ch
                        sys.stdout.write("*")
                        sys.stdout.flush()
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            # Last resort — plain getpass (no stars)
            sys.stdout.write("\n")
            value = getpass.getpass(prompt)

    return value


def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    """Prompt the user for input. Shows * for secret fields."""
    display = f"  {CYAN}›{RESET} {prompt}"
    if default:
        display += f" {DIM}[{default}]{RESET}"
    display += ": "

    while True:
        try:
            value = _secret_input(display) if secret else input(display)
        except (KeyboardInterrupt, EOFError):
            print("\n\nSetup cancelled.")
            sys.exit(0)

        # Strip all whitespace including invisible characters that can sneak
        # in when pasting into getpass on Windows — these corrupt API keys
        # and cause cryptic "Illegal header value" errors from gRPC.
        value = value.strip().strip("\r\n\t\x00\xa0")
        if not value and default:
            return default
        if value:
            return value
        print(f"  {RED}This field is required.{RESET}")


def ask_email(prompt: str, default: str = "") -> str:
    """Ask for and validate an email address."""
    while True:
        value = ask(prompt, default=default)
        if re.match(r"^[^@]+@[^@]+\.[^@]+$", value):
            return value
        err("That doesn't look like a valid email address. Try again.")


def confirm(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    try:
        answer = input(f"  {CYAN}›{RESET} {prompt}{DIM}{suffix}{RESET}: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\n\nSetup cancelled.")
        sys.exit(0)
    if not answer:
        return default
    return answer in ("y", "yes")


# ── Credential validators ─────────────────────────────────────────────────────

def validate_gemini(api_key: str) -> tuple[bool, str]:
    """
    Make a minimal Gemini API call to verify the key works.
    Tries gemini-2.0-flash first, falls back to gemini-2.0-flash-lite
    if the primary model is quota-limited.
    Returns (success, model_name).
    """
    import google.generativeai as genai
    genai.configure(api_key=api_key)

    models_to_try = [
        ("gemini-2.0-flash",      "Gemini 2.0 Flash"),
        ("gemini-2.0-flash-lite", "Gemini 2.0 Flash-Lite"),
        ("gemini-1.5-flash",      "Gemini 1.5 Flash"),
    ]

    for model_id, model_label in models_to_try:
        try:
            model = genai.GenerativeModel(model_id)
            resp  = model.generate_content("Reply with just the word: OK")
            if "ok" in resp.text.strip().lower():
                return True, model_id
        except Exception as exc:
            exc_str = str(exc)
            if "429" in exc_str or "quota" in exc_str.lower():
                warn(f"{model_label} quota exceeded — trying next model...")
                continue
            err(f"Gemini validation failed: {exc_str[:200]}")
            return False, ""

    err("All Gemini models are quota-limited. Wait a few minutes and try again,\n"
        "  or go to console.cloud.google.com and create a new project for a fresh quota.")
    return False, ""


def validate_m365(tenant_id: str, client_id: str, client_secret: str, mailbox: str) -> bool:
    """Attempt to acquire an M365 token and read the mailbox."""
    try:
        import msal
        import requests
        app = msal.ConfidentialClientApplication(
            client_id=client_id,
            client_credential=client_secret,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
        )
        result = app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )
        if "access_token" not in result:
            err(f"Token error: {result.get('error_description', result)}")
            return False

        # Try a lightweight Graph API call
        headers = {"Authorization": f"Bearer {result['access_token']}"}
        url     = f"https://graph.microsoft.com/v1.0/users/{mailbox}/mailFolders/Inbox"
        resp    = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            return True
        err(f"Graph API returned {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as exc:
        err(f"M365 validation failed: {exc}")
        return False


def validate_gmail(client_id: str, client_secret: str, refresh_token: str) -> bool:
    """Verify Gmail credentials by fetching the profile."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        SCOPES = [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.modify",
        ]
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )
        creds.refresh(Request())
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = service.users().getProfile(userId="me").execute()
        return bool(profile.get("emailAddress"))
    except Exception as exc:
        err(f"Gmail validation failed: {exc}")
        return False


# ── .env writer ───────────────────────────────────────────────────────────────

def write_env(values: dict) -> None:
    lines = [
        "# Generated by setup.py — do not commit this file\n",
        "\n# ── Email provider ──────────────────────────────────────────\n",
        f"EMAIL_PROVIDER={values['EMAIL_PROVIDER']}\n",
        "\n# ── Gemini API ──────────────────────────────────────────────\n",
        f"GEMINI_API_KEY={values['GEMINI_API_KEY']}\n",
        f"GEMINI_MODEL={values.get('GEMINI_MODEL', 'gemini-2.0-flash')}\n",
    ]

    if values["EMAIL_PROVIDER"] == "m365":
        lines += [
            "\n# ── Microsoft 365 ───────────────────────────────────────────\n",
            f"M365_TENANT_ID={values['M365_TENANT_ID']}\n",
            f"M365_CLIENT_ID={values['M365_CLIENT_ID']}\n",
            f"M365_CLIENT_SECRET={values['M365_CLIENT_SECRET']}\n",
            f"M365_MAILBOX={values['M365_MAILBOX']}\n",
        ]
    else:
        lines += [
            "\n# ── Gmail ───────────────────────────────────────────────────\n",
            f"GMAIL_CLIENT_ID={values['GMAIL_CLIENT_ID']}\n",
            f"GMAIL_CLIENT_SECRET={values['GMAIL_CLIENT_SECRET']}\n",
            f"GMAIL_REFRESH_TOKEN={values['GMAIL_REFRESH_TOKEN']}\n",
            f"GMAIL_ADDRESS={values['GMAIL_ADDRESS']}\n",
        ]

    lines += [
        "\n# ── Polling ─────────────────────────────────────────────────\n",
        f"POLL_INTERVAL_SECONDS={values.get('POLL_INTERVAL_SECONDS', '60')}\n",
        "\n# ── Department routing ──────────────────────────────────────\n",
        f"EMAIL_HELP_DESK={values['EMAIL_HELP_DESK']}\n",
        f"EMAIL_NETWORKING={values['EMAIL_NETWORKING']}\n",
        f"EMAIL_CYBERSECURITY={values['EMAIL_CYBERSECURITY']}\n",
        f"EMAIL_SYSADMIN={values['EMAIL_SYSADMIN']}\n",
        f"EMAIL_FALLBACK={values['EMAIL_FALLBACK']}\n",
        "\n# ── Classification ──────────────────────────────────────────\n",
        f"CONFIDENCE_THRESHOLD={values.get('CONFIDENCE_THRESHOLD', '0.70')}\n",
        "FALLBACK_QUEUE_PATH=fallback_queue.jsonl\n",
    ]

    with open(".env", "w", encoding="utf-8") as f:
        f.writelines(lines)


# ── Main wizard ───────────────────────────────────────────────────────────────

def main() -> None:
    os.system("cls" if os.name == "nt" else "clear")

    print(f"""
{CYAN}{BOLD}
  ██████████████████████████████████████████████
  ██                                          ██
  ██     IT Email Router — Setup Wizard       ██
  ██                                          ██
  ██████████████████████████████████████████████
{RESET}
  This wizard will configure the router and validate
  your credentials before writing the .env file.

  You will need:
    • A Gemini API key  (free — aistudio.google.com)
    • M365 OR Gmail credentials (your choice)
    • Email addresses for each department
""")

    if os.path.exists(".env"):
        warn(".env file already exists.")
        if not confirm("Overwrite it?", default=False):
            print("\n  Setup cancelled — existing .env kept.\n")
            sys.exit(0)

    values: dict = {}

    # ── Step 1: Gemini ────────────────────────────────────────────────────────
    header("Step 1 of 4 — Gemini API Key")
    print(f"  Get a free key at: {CYAN}https://aistudio.google.com/apikey{RESET}\n")

    while True:
        values["GEMINI_API_KEY"] = ask("Paste your Gemini API key", secret=True)
        print(f"\n  Validating Gemini API key...", end=" ", flush=True)
        valid, model_id = validate_gemini(values["GEMINI_API_KEY"])
        if valid:
            values["GEMINI_MODEL"] = model_id
            ok(f"Gemini API key is valid (using {model_id})\n")
            break
        err("Key validation failed. Please check the key and try again.")
        if not confirm("Try a different key?"):
            sys.exit(1)

    # ── Step 2: Email provider ────────────────────────────────────────────────
    header("Step 2 of 4 — Email Provider")
    print("  Which platform are you routing emails from?\n")
    print(f"  {BOLD}1{RESET}  Microsoft 365 (Exchange / Outlook)")
    print(f"  {BOLD}2{RESET}  Gmail / Google Workspace\n")

    while True:
        choice = ask("Enter 1 or 2")
        if choice in ("1", "2"):
            break
        err("Please enter 1 or 2.")

    if choice == "1":
        values["EMAIL_PROVIDER"] = "m365"
        print(f"""
  {BOLD}M365 App Registration steps:{RESET}
  1. Go to {CYAN}https://portal.azure.com{RESET} → Azure Active Directory
  2. App registrations → New registration → name it 'IT Email Router'
  3. Note the {BOLD}Application (client) ID{RESET} and {BOLD}Directory (tenant) ID{RESET}
  4. Certificates & secrets → New client secret → copy the value
  5. API permissions → Microsoft Graph → Application:
     Mail.Read, Mail.ReadWrite, Mail.Send → Grant admin consent
""")
        while True:
            values["M365_TENANT_ID"]     = ask("Directory (Tenant) ID")
            values["M365_CLIENT_ID"]     = ask("Application (Client) ID")
            values["M365_CLIENT_SECRET"] = ask("Client Secret", secret=True)
            values["M365_MAILBOX"]       = ask_email("Shared mailbox address (e.g. it-support@company.com)")

            print(f"\n  Validating M365 credentials...", end=" ", flush=True)
            if validate_m365(
                values["M365_TENANT_ID"],
                values["M365_CLIENT_ID"],
                values["M365_CLIENT_SECRET"],
                values["M365_MAILBOX"],
            ):
                ok("M365 credentials are valid\n")
                break
            if not confirm("Try again?"):
                sys.exit(1)

    else:
        values["EMAIL_PROVIDER"] = "gmail"
        print(f"""
  {BOLD}Gmail API setup steps:{RESET}
  1. Go to {CYAN}https://console.cloud.google.com{RESET} → New project
  2. APIs & Services → Enable APIs → search 'Gmail API' → Enable
  3. APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID
  4. Application type: Desktop app → Download JSON → save as client_secret.json
  5. Run: {BOLD}python providers/gmail_auth.py --credentials client_secret.json{RESET}
  6. Paste the three values printed by that script below
""")
        while True:
            values["GMAIL_CLIENT_ID"]     = ask("GMAIL_CLIENT_ID from gmail_auth.py output")
            values["GMAIL_CLIENT_SECRET"] = ask("GMAIL_CLIENT_SECRET", secret=True)
            values["GMAIL_REFRESH_TOKEN"] = ask("GMAIL_REFRESH_TOKEN", secret=True)
            values["GMAIL_ADDRESS"]       = ask_email("Your Gmail address")

            print(f"\n  Validating Gmail credentials...", end=" ", flush=True)
            if validate_gmail(
                values["GMAIL_CLIENT_ID"],
                values["GMAIL_CLIENT_SECRET"],
                values["GMAIL_REFRESH_TOKEN"],
            ):
                ok("Gmail credentials are valid\n")
                break
            if not confirm("Try again?"):
                sys.exit(1)

    # ── Step 3: Department routing ────────────────────────────────────────────
    header("Step 3 of 4 — Department Email Addresses")
    print("  Where should each category of email be forwarded?\n")

    values["EMAIL_HELP_DESK"]    = ask_email("Help Desk")
    values["EMAIL_NETWORKING"]   = ask_email("Networking")
    values["EMAIL_CYBERSECURITY"]= ask_email("Cybersecurity")
    values["EMAIL_SYSADMIN"]     = ask_email("System Administrator")
    values["EMAIL_FALLBACK"]     = ask_email("Fallback / Human Review queue")

    # ── Step 4: Settings ──────────────────────────────────────────────────────
    header("Step 4 of 4 — Settings")
    values["POLL_INTERVAL_SECONDS"] = ask("Poll interval in seconds", default="60")
    values["CONFIDENCE_THRESHOLD"]  = ask("Confidence threshold (0.0–1.0)", default="0.70")

    # ── Write .env ────────────────────────────────────────────────────────────
    print(f"\n  Writing .env file...", end=" ", flush=True)
    write_env(values)
    ok(".env written\n")

    # ── Done ──────────────────────────────────────────────────────────────────
    provider_label = "Microsoft 365" if values["EMAIL_PROVIDER"] == "m365" else "Gmail"
    print(f"""
{GREEN}{BOLD}  ✓ Setup complete!{RESET}

  Provider : {provider_label}
  AI model : gemini-2.0-flash

  {BOLD}Next steps:{RESET}

  Run the test suite (no emails sent yet — uses mock data):
  {CYAN}  python tests/test_emails.py{RESET}

  Start with Docker:
  {CYAN}  docker compose up -d{RESET}

  Watch live logs:
  {CYAN}  docker compose logs -f{RESET}

""")


if __name__ == "__main__":
    main()
