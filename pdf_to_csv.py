"""Turn an HR-contacts PDF table (SNo, Name, Email, Title, Company) into data/hr_contacts.csv.

    pip install pdfplumber
    python3 pdf_to_csv.py "Company Wise HR Contacts.pdf"
"""

import csv
import re
import sys
from pathlib import Path

import pdfplumber

OUT = Path(__file__).resolve().parent / "data" / "hr_contacts.csv"
VALID = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


def main(pdf: str) -> None:
    rows = []
    with pdfplumber.open(pdf) as doc:
        for page in doc.pages:
            # Drop large watermark letters that otherwise leak into cells.
            page = page.filter(lambda o: o.get("object_type") != "char" or o.get("size", 0) < 10)
            for table in page.extract_tables():
                for r in table:
                    if r and r[0] and r[0].strip().isdigit():
                        rows.append([(c or "").replace("\n", " ").strip() for c in r])

    seen, out, bad = set(), [], []
    for r in rows:
        _, name, addr, title, company = (r + [""] * 5)[:5]
        addr = addr.replace(" ", "").lower()  # emails wrapped across lines gain a space
        if not VALID.match(addr):
            bad.append(r)
            continue
        if addr in seen:
            continue
        seen.add(addr)
        out.append({"name": name, "first_name": name.split()[0].title() if name else "",
                    "email": addr, "title": title, "company": company.rstrip(", ")})

    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["name", "first_name", "email", "title", "company"])
        w.writeheader()
        w.writerows(out)
    print(f"{len(rows)} rows, {len(out)} saved to {OUT}, {len(bad)} unreadable")
    for r in bad:
        print("  skipped:", r)


if __name__ == "__main__":
    main(sys.argv[1])
