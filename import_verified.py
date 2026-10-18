"""Load results from an email verification service into data/verified.csv.

Works with exports from MillionVerifier, NeverBounce, ZeroBounce, Bouncer and similar:
it finds the email column and the result column by name and maps each service's
wording onto valid / risky / invalid / unknown.

    python3 import_verified.py ~/Downloads/results.csv

After this, send.py only emails addresses marked valid (plus risky ones if
ALLOW_RISKY=true in .env).
"""

import csv
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data" / "verified.csv"

STATUS = {
    "valid": {"valid", "ok", "good", "deliverable", "safe", "verified"},
    "risky": {"catch-all", "catchall", "catch_all", "accept_all", "accept-all", "acceptall",
              "risky", "role", "role_based"},
    "invalid": {"invalid", "bad", "undeliverable", "disposable", "spamtrap", "do_not_mail",
                "abuse", "error", "syntax_error", "no_mx", "mailbox_not_found"},
}
EMAIL_COLS = ("email", "email address", "e-mail", "emailaddress")
RESULT_COLS = ("result", "status", "quality", "verification result", "zb status",
               "email status", "state", "millionverifier result", "neverbounce result")


def classify(value: str) -> str:
    v = value.strip().lower().replace(" ", "_")
    for label, words in STATUS.items():
        if v in words or v.replace("_", "-") in words:
            return label
    return "unknown"


def pick(header: list[str], wanted: tuple[str, ...]) -> int:
    lower = [h.strip().lower() for h in header]
    for w in wanted:
        if w in lower:
            return lower.index(w)
    for i, h in enumerate(lower):  # e.g. "MillionVerifier Result" variants
        if any(w in h for w in wanted):
            return i
    sys.exit(f"Couldn't find a column like {wanted[0]!r} in: {header}")


def main(path: str) -> None:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    header, data = rows[0], rows[1:]
    e_col, r_col = pick(header, EMAIL_COLS), pick(header, RESULT_COLS)

    out, counts, raw = {}, {}, {}
    for r in data:
        if len(r) <= max(e_col, r_col) or "@" not in r[e_col]:
            continue
        label = classify(r[r_col])
        out[r[e_col].strip().lower()] = label
        counts[label] = counts.get(label, 0) + 1
        raw.setdefault(label, set()).add(r[r_col].strip())

    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["email", "status"])
        w.writerows(sorted(out.items()))
    print(f"Saved {len(out)} results to {OUT}")
    for label in ("valid", "risky", "invalid", "unknown"):
        if label in counts:
            print(f"  {label:8} {counts[label]:5}   (service said: {', '.join(sorted(raw[label]))})")


if __name__ == "__main__":
    main(sys.argv[1])
