"""Slow, personalized job-search emails to HR contacts.

Sends a few dozen emails a day through Gmail with random gaps, one contact per
company, and remembers who was emailed so each run picks up where the last stopped.
Dry run by default: nothing is sent unless you pass --send.

    python3 send.py                 # preview today's batch
    python3 send.py --send          # send today's batch
    python3 send.py --followup --send   # one follow-up to people who haven't replied
    python3 send.py --check         # scan inbox for replies and bounces only
"""

from __future__ import annotations

import argparse
import csv
import email.header
import email.utils
import imaplib
import os
import random
import re
import smtplib
import subprocess
import sys
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"  # gitignored: contacts, send log, skip list
CONTACTS = DATA / "hr_contacts.csv"
LOG = DATA / "sent_log.csv"
LOG_FIELDS = ["email", "company", "kind", "sent_at", "message_id"]
# Everyone who replied or bounced; never emailed again.
SKIP = DATA / "auto_skip.csv"
SKIP_FIELDS = ["email", "company", "reason", "found_at", "subject"]
# Stop new sends if this share of first emails bounces (protects the Gmail account),
# judged once at least MIN_SAMPLE have gone out.
MAX_BOUNCE_RATE = 0.08
MIN_SAMPLE = 10
# Results from an email verification service (see import_verified.py). When present,
# only verified addresses get first emails.
VERIFIED = DATA / "verified.csv"


def read_verified() -> dict[str, str] | None:
    if not VERIFIED.exists():
        return None
    with VERIFIED.open(newline="") as f:
        return {r["email"]: r["status"] for r in csv.DictReader(f)}


def load_env() -> None:
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


def read_template(name: str) -> tuple[str, str]:
    text = (HERE / name).read_text()
    subject, _, body = text.partition("\n")
    return subject.removeprefix("Subject:").strip(), body.strip() + "\n"


def read_log() -> list[dict]:
    if not LOG.exists():
        return []
    with LOG.open(newline="") as f:
        return list(csv.DictReader(f))


def append_log(row: dict) -> None:
    new = not LOG.exists()
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def read_skip() -> dict[str, dict]:
    if not SKIP.exists():
        return {}
    with SKIP.open(newline="") as f:
        return {r["email"]: r for r in csv.DictReader(f)}


def accepts_mail(domain: str) -> bool:
    """True if the domain has an MX record (or, failing that, an A record) to deliver to."""
    for rtype in ("MX", "A"):
        out = subprocess.run(["dig", "+short", "+time=3", "+tries=1", rtype, domain],
                             capture_output=True, text=True, check=False).stdout
        if out.strip():
            return True
    return False


def notify(title: str, text: str) -> None:
    script = f"display notification {json_str(text)} with title {json_str(title)} sound name \"Glass\""
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


def json_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


FREE_DOMAINS = {"gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com",
                "live.com", "icloud.com", "rediffmail.com", "proton.me"}
AUTO_REPLY = re.compile(r"automatic reply|auto.?reply|out of (the )?office|autoreply", re.I)


def header(raw: bytes, name: str) -> str:
    msg = email.message_from_bytes(raw)
    value = msg.get(name, "")
    return str(email.header.make_header(email.header.decode_header(value))) if value else ""


def scan_inbox(cfg: dict, log: list[dict]) -> dict[str, dict]:
    """Find replies and bounces from people you emailed; add them to the skip list."""
    skip = read_skip()
    sent = {r["email"]: r for r in log}
    if not sent:
        return skip
    domains = {e.split("@")[1]: r for e, r in sent.items() if e.split("@")[1] not in FREE_DOMAINS}
    first_sent = min(datetime.fromisoformat(r["sent_at"]) for r in log)
    since = (first_sent - timedelta(days=1)).strftime("%d-%b-%Y")
    me = cfg["user"].lower()
    found: list[dict] = []

    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(cfg["user"], cfg["password"])
        imap.select('"[Gmail]/All Mail"', readonly=True)
        _, data = imap.search(None, f'(SINCE "{since}")')
        ids = data[0].split()
        for i in range(0, len(ids), 300):
            _, msgs = imap.fetch(
                b",".join(ids[i : i + 300]),
                "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT X-FAILED-RECIPIENTS)] BODY.PEEK[TEXT]<0.4000>)",
            )
            # Each message arrives as separate (descriptor, bytes) pieces; group them by sequence number.
            grouped: dict[bytes, dict[str, bytes]] = {}
            # Only the first piece of a message starts with its sequence number
            # ("18930 (BODY[TEXT]..."); later pieces (" BODY[HEADER...") belong to it.
            seq = b""
            for p in msgs:
                if isinstance(p, tuple):
                    first = p[0].split()[0]
                    if first.isdigit():
                        seq = first
                    key = "head" if b"HEADER.FIELDS" in p[0] else "body"
                    grouped.setdefault(seq, {})[key] = p[1] or b""
            for g in grouped.values():
                head, body = (None, g.get("head", b"")), (None, g.get("body", b""))
                sender = email.utils.parseaddr(header(head[1], "From"))[1].lower()
                subject = header(head[1], "Subject")[:120]
                if not sender or sender == me:
                    continue
                if "mailer-daemon" in sender or "postmaster" in sender:
                    failed = header(head[1], "X-Failed-Recipients").lower()
                    text = (failed + " " + body[1].decode(errors="ignore")).lower()
                    for addr in sent:
                        if addr in text:
                            found.append({"email": addr, "reason": "bounced", "subject": subject})
                    continue
                row = sent.get(sender) or domains.get(sender.split("@")[-1])
                if row:
                    reason = "auto-reply" if AUTO_REPLY.search(subject) else "replied"
                    found.append({"email": row["email"], "reason": reason, "subject": subject,
                                  "from": sender})

    new = [f for f in found if f["email"] not in skip]
    # A real reply beats an earlier auto-reply for the same person.
    upgrades = [f for f in found if f["reason"] == "replied"
                and skip.get(f["email"], {}).get("reason") == "auto-reply"]
    for f in new + upgrades:
        skip[f["email"]] = {"email": f["email"], "company": sent[f["email"]]["company"],
                            "reason": f["reason"], "subject": f["subject"],
                            "found_at": datetime.now().isoformat(timespec="seconds")}
    if new or upgrades:
        with SKIP.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=SKIP_FIELDS)
            w.writeheader()
            w.writerows(skip.values())

    replies = {f["email"]: f for f in new + upgrades if f["reason"] == "replied"}
    for f in new + upgrades:
        print(f"  {f['reason']:10} {f['email']}  ({f['subject']})")
    if replies:
        names = ", ".join(sent[e]["company"] for e in replies)
        notify("HR replied! Check Gmail", f"{len(replies)} new: {names}")
    return skip


def pick_batch(args, log: list[dict], cfg: dict, auto_skip: dict[str, dict]) -> list[dict]:
    with CONTACTS.open(newline="") as f:
        contacts = list(csv.DictReader(f))
    emailed = {r["email"] for r in log}
    skip = {e.strip().lower() for e in cfg["skip"].split(",") if e.strip()} | set(auto_skip)

    if not args.followup:
        # A company whose HR bounced hasn't really been reached, so another HR there may be tried.
        bounced = {e for e, r in auto_skip.items() if r["reason"] == "bounced"}
        done_companies = {r["company"].lower() for r in log if r["email"] not in bounced}
        verified = read_verified()
        allowed = {"valid", "risky"} if cfg["allow_risky"] else {"valid"}
        batch, seen = [], set()
        for c in contacts:
            co = c["company"].lower()
            if c["email"] in emailed or c["email"] in skip:
                continue
            if verified is not None and verified.get(c["email"]) not in allowed:
                continue
            if cfg["one_per_company"] and (co in done_companies or co in seen):
                continue
            seen.add(co)
            batch.append(c)
        return batch

    # Follow-ups: first email sent at least N days ago, no follow-up yet, no reply.
    cutoff = datetime.now() - timedelta(days=cfg["followup_days"])
    followed = {r["email"] for r in log if r["kind"] == "followup"}
    firsts = {r["email"]: r for r in log if r["kind"] == "first"}
    by_email = {c["email"]: c for c in contacts}
    return [
        {**by_email[e], "message_id": r["message_id"]}
        for e, r in firsts.items()
        if e in by_email and e not in followed and e not in skip
        and datetime.fromisoformat(r["sent_at"]) <= cutoff
    ]


def build(contact: dict, subject: str, body: str, cfg: dict) -> EmailMessage:
    fields = {**contact, "my_name": cfg["name"], "resume_link": cfg["resume_link"], "phone": cfg["phone"]}
    msg = EmailMessage()
    msg["From"] = email.utils.formataddr((cfg["name"], cfg["user"]))
    msg["To"] = contact["email"]
    msg["Subject"] = subject.format(**fields)
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=cfg["user"].split("@")[-1])
    if contact.get("message_id"):  # thread the follow-up under the first email
        msg["In-Reply-To"] = msg["References"] = contact["message_id"]
    msg.set_content(body.format(**fields))
    return msg


def main() -> None:
    load_env()
    p = argparse.ArgumentParser()
    p.add_argument("--send", action="store_true", help="actually send (default is a dry run)")
    p.add_argument("--followup", action="store_true", help="send the one follow-up instead")
    p.add_argument("--limit", type=int, help="override DAILY_LIMIT for this run")
    p.add_argument("--check", action="store_true", help="only scan the inbox for replies and bounces")
    args = p.parse_args()

    cfg = {
        "user": os.environ.get("GMAIL_ADDRESS", ""),
        "password": os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", ""),
        "name": os.environ.get("YOUR_NAME", ""),
        "resume_link": os.environ.get("RESUME_LINK", ""),
        "phone": os.environ.get("PHONE", ""),
        "limit": args.limit or int(os.environ.get("DAILY_LIMIT", "30")),
        "min_gap": int(os.environ.get("MIN_GAP_SECONDS", "90")),
        "max_gap": int(os.environ.get("MAX_GAP_SECONDS", "300")),
        "followup_days": int(os.environ.get("FOLLOWUP_AFTER_DAYS", "5")),
        "one_per_company": os.environ.get("ONE_PER_COMPANY", "true").lower() == "true",
        "skip": os.environ.get("SKIP_EMAILS", ""),
        "allow_risky": os.environ.get("ALLOW_RISKY", "false").lower() == "true",
    }
    if args.send and not (cfg["user"] and cfg["password"] and cfg["name"]):
        sys.exit("Set GMAIL_ADDRESS, GMAIL_APP_PASSWORD and YOUR_NAME in job-outreach/.env first.")
    if args.send and not cfg["resume_link"]:
        sys.exit("Set RESUME_LINK in job-outreach/.env first, or the emails will have an empty resume link.")

    log = read_log()
    auto_skip = read_skip()
    if cfg["password"]:
        print("Checking inbox for replies and bounces...")
        try:
            auto_skip = scan_inbox(cfg, log)
        except (imaplib.IMAP4.error, OSError) as e:
            print(f"  inbox check failed: {e}")
            if args.followup and args.send:  # never follow up blind: they may have replied
                sys.exit("Skipping follow-ups today because the inbox couldn't be checked.")
    if args.check:
        counts: dict[str, int] = {}
        for r in auto_skip.values():
            counts[r["reason"]] = counts.get(r["reason"], 0) + 1
        print(f"Skip list: {counts or 'empty'}")
        return

    # Once a verified list is in use, judge bounces only on verified sends, so bounces
    # from the old unverified list don't keep sending paused forever.
    verified = read_verified()
    firsts = [r for r in log if r["kind"] == "first" and (verified is None or r["email"] in verified)]
    bounced = sum(1 for r in firsts if auto_skip.get(r["email"], {}).get("reason") == "bounced")
    if not args.followup and len(firsts) >= MIN_SAMPLE and bounced / len(firsts) > MAX_BOUNCE_RATE:
        notify("Job outreach paused", f"{bounced} of {len(firsts)} emails bounced. Sending stopped.")
        sys.exit(f"Paused: {bounced}/{len(firsts)} emails bounced (over {MAX_BOUNCE_RATE:.0%}). "
                 "Too many bounces can get the Gmail account blocked.")

    today = datetime.now().date().isoformat()
    sent_today = sum(1 for r in log if r["sent_at"].startswith(today))
    room = max(0, cfg["limit"] - sent_today)

    kind = "followup" if args.followup else "first"
    subject, body = read_template("followup.txt" if args.followup else "template.txt")
    pending = pick_batch(args, log, cfg, auto_skip)
    batch = []
    for c in pending:  # skip companies whose domain no longer accepts email
        if len(batch) >= room:
            break
        if args.followup or accepts_mail(c["email"].split("@")[1]):
            batch.append(c)
        else:
            print(f"  skipping {c['email']}: domain has no mail server")
    print(f"{len(pending)} {kind} emails pending, {sent_today} sent today, sending {len(batch)} now"
          f"{'' if args.send else ' (DRY RUN)'}\n")

    if not args.send:
        for c in batch[:3]:
            print("-" * 60)
            print(build(c, subject, body, {**cfg, "user": cfg["user"] or "you@gmail.com"}).as_string())
        for c in batch[3:]:
            print(f"  would send -> {c['email']:45} {c['company']}")
        return

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(cfg["user"], cfg["password"])
        for n, c in enumerate(batch, 1):
            msg = build(c, subject, body, cfg)
            try:
                smtp.send_message(msg)
            except smtplib.SMTPRecipientsRefused:
                print(f"[{n}/{len(batch)}] refused  {c['email']}")
                continue
            except smtplib.SMTPDataError as e:  # Gmail rate limit or block: stop for today
                sys.exit(f"Gmail stopped sending ({e.smtp_code}). Try again tomorrow.")
            append_log({"email": c["email"], "company": c["company"], "kind": kind,
                        "sent_at": datetime.now().isoformat(timespec="seconds"),
                        "message_id": msg["Message-ID"]})
            print(f"[{n}/{len(batch)}] sent  {c['email']}  ({c['company']})")
            if n < len(batch):
                time.sleep(random.randint(cfg["min_gap"], cfg["max_gap"]))


if __name__ == "__main__":
    main()
