# IT Email Router

Automatically classifies incoming IT support emails and routes them to the correct department using AI.

**Stack:** Python · Microsoft Graph API · Gemini AI · Docker

---

## Departments supported

| Category | Routed to |
|---|---|
| Help Desk | `EMAIL_HELP_DESK` |
| Networking | `EMAIL_NETWORKING` |
| Cybersecurity | `EMAIL_CYBERSECURITY` |
| System Administrator | `EMAIL_SYSADMIN` |
| Unknown / Low confidence | `EMAIL_FALLBACK` (human review) |

---

## Quick start

### Step 1 — Get a Gemini API key (free)

1. Go to [https://aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Sign in with a Google account
3. Click **Create API key**
4. Copy the key — you'll paste it into `.env` in Step 3

Free tier limits (as of 2025): 1,500 requests/day on Gemini 2.0 Flash. Sufficient for most enterprise IT mailboxes.

---

### Step 2 — Register an app in Azure AD (for M365 access)

1. Go to [https://portal.azure.com](https://portal.azure.com) → **Azure Active Directory** → **App registrations** → **New registration**
2. Name it `IT Email Router`, leave defaults, click **Register**
3. Note the **Application (client) ID** and **Directory (tenant) ID**
4. Go to **Certificates & secrets** → **New client secret** → copy the value immediately
5. Go to **API permissions** → **Add a permission** → **Microsoft Graph** → **Application permissions**:
   - `Mail.Read`
   - `Mail.ReadWrite`
   - `Mail.Send`
6. Click **Grant admin consent**

---

### Step 3 — Configure environment

```bash
cp .env.example .env
# Open .env and fill in all values
```

---

### Step 4 — Test without M365 credentials first

Run the mock test suite to validate AI classification and routing logic before touching real email:

```bash
# Install dependencies
pip install -r requirements.txt

# Run the test pipeline (uses mock emails, calls real Gemini API)
python tests/test_emails.py
```

You should see a table showing each test email's classification and routing decision.

---

### Step 5 — Run with Docker (production)

```bash
# Build the image
docker compose build

# Start (runs in background, restarts automatically)
docker compose up -d

# Watch live logs
docker compose logs -f

# Stop
docker compose down
```

---

## File structure

```
email-router/
├── providers/
│   ├── base.py          — shared email interface (StandardEmail)
│   └── m365.py          — Microsoft Graph API polling
├── classifier.py        — Gemini AI classification
├── router.py            — routing decisions
├── fallback.py          — human-review queue
├── main.py              — entry point / polling loop
├── config.py            — environment variable loading
├── tests/
│   ├── mock_provider.py — simulates incoming emails
│   └── test_emails.py   — full pipeline test (no M365 needed)
├── .env.example         — config template
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Adding Gmail support later

1. Create `providers/gmail.py` implementing `BaseEmailProvider`
2. Return `StandardEmail` objects — identical shape to M365
3. In `main.py`, swap `M365Provider()` for `GmailProvider()` — nothing else changes

---

## Monitoring

- **Logs:** `docker compose logs -f` or check `email_router.log`
- **Fallback queue:** inspect `fallback_queue.jsonl` — one JSON record per unclassified email
- **Note:** Email bodies are never written to logs or the queue file. Only metadata is stored.

---

## Security notes

- Secrets live in `.env` only — never in source code
- App runs as non-root user inside Docker
- Email bodies are truncated to 2,000 characters before AI processing
- Prompt injection defence is active in the classifier system prompt
- Dependencies are pinned in `requirements.txt`
- M365 app registration uses least-privilege permissions only
