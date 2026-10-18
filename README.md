# job-outreach

Slow, personalized job-search emails to HR contacts, sent from your own Gmail once a day.

It is built to land in the inbox rather than spam, and to never annoy anyone twice:

- **Slow:** a daily limit (15–30) with a random 1.5–5 minute gap between emails
- **Personal:** each email uses the HR's first name and company, in plain text with no attachments
- **One per company:** only the first HR at each company is contacted
- **One follow-up:** sent after 5 days, in the same thread, only to people who haven't replied
- **Reads your inbox first:** anyone who replies, auto-replies or bounces is never emailed again,
  and you get a macOS notification when an HR replies
- **Protects your account:** sending pauses automatically if more than 8% of emails bounce
- **Verified lists:** import results from an email verification service and only valid
  addresses are emailed (`import_verified.py`); domains with no mail server are skipped
- **Resumable:** a send log means each run picks up where the last one stopped and never double-sends

Only Python's standard library is needed to send; `pdfplumber` is only for converting a PDF list.

## Setup

1. **Contacts.** Put your list at `data/hr_contacts.csv` with columns
   `name,first_name,email,title,company`, or convert a PDF table:
   ```bash
   pip install pdfplumber
   python3 pdf_to_csv.py "Company Wise HR Contacts.pdf"
   ```
2. **Settings.** `cp .env.example .env` and fill it in. The Gmail App Password comes from
   Google Account → Security → 2-Step Verification → App passwords.
3. **Email text.** Edit `template.txt` and `followup.txt`. The first line is the subject.
   Available fields: `{first_name}`, `{name}`, `{company}`, `{title}`, `{my_name}`,
   `{resume_link}`, `{phone}`.
4. **Verify the list (strongly recommended for old lists).** Upload the addresses to a
   verification service (MillionVerifier, NeverBounce, ZeroBounce...), download the full
   results CSV, then:
   ```bash
   python3 import_verified.py ~/Downloads/results.csv
   ```
   Set `ALLOW_RISKY=true` in `.env` to also email catch-all addresses.
5. **Preview** (sends nothing):
   ```bash
   python3 send.py
   ```

## Running

```bash
python3 send.py --send               # today's new emails
python3 send.py --followup --send    # follow-ups that are due
python3 send.py --check              # only scan the inbox for replies and bounces
```

`daily.sh` runs both send steps and keeps the Mac awake while sending.

### Every day automatically (macOS)

Create `~/Library/LaunchAgents/com.job-outreach.plist`, replacing `/path/to/job-outreach`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.job-outreach</string>
  <key>ProgramArguments</key><array><string>/path/to/job-outreach/daily.sh</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/path/to/job-outreach/data/logs/daily.log</string>
  <key>StandardErrorPath</key><string>/path/to/job-outreach/data/logs/daily.log</string>
</dict>
</plist>
```

```bash
mkdir -p data/logs
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.job-outreach.plist
```

If the Mac is asleep at 10:00, the job runs when it wakes. To stop it:

```bash
launchctl bootout gui/$(id -u)/com.job-outreach
```

## Files

| Path | What it is | Committed |
|---|---|---|
| `send.py` | Sender, follow-ups, inbox scan | yes |
| `template.txt`, `followup.txt` | Email text | yes |
| `daily.sh` | What the daily schedule runs | yes |
| `pdf_to_csv.py` | PDF table → CSV | yes |
| `import_verified.py` | Verification-service results → `data/verified.csv` | yes |
| `.env` | Gmail address, App Password, phone, resume link | **no** |
| `data/` | Contacts, send log, skip list, run logs | **no** |

## Be a good sender

Email people one at a time, honestly, about something relevant to them, and stop when they
ask. Every email includes a line offering to stop, and any reply ends contact automatically.
