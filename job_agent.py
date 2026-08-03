#!/usr/bin/env python3
"""
Job alert agent.

Checks each company in companies.yaml for open Product Designer / Product
Manager roles (see ROLE_KEYWORDS below), filters to US locations, dedupes
against previously-seen postings, and emails you the new ones.

Run manually:      python job_agent.py
Run in CI:          see .github/workflows/daily-job-alert.yml
"""

import json
import os
import re
import smtplib
import ssl
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
import yaml

# ---------------------------------------------------------------------------
# Criteria — edit these to change what you're matching on
# ---------------------------------------------------------------------------

ROLE_KEYWORDS = [
    "founding designer",
    "product design lead",
    "lead product designer",
    "product designer",
    "sr. product designer",
    "senior product designer",
    "staff product designer",
    "design engineer",
    "head of product design",
    "product design manager",
    "director of product design",
    "vp of product design",
    "product manager",
]

# Postings are INCLUDED if their location text matches one of these...
US_INCLUDE_PATTERNS = [
    r"\bunited states\b", r"\bu\.s\.a?\.?\b", r"\bremote\b",
    r"\bnew york\b", r"\bnyc\b", r"\bsan francisco\b", r"\bsf\b",
    r"\blos angeles\b", r"\bseattle\b", r"\baustin\b", r"\bboston\b",
    r"\bchicago\b", r"\bdenver\b", r"\bmiami\b", r"\batlanta\b",
    r"\b(al|ak|az|ar|ca|co|ct|de|fl|ga|hi|id|il|in|ia|ks|ky|la|me|md|"
    r"ma|mi|mn|ms|mo|mt|ne|nv|nh|nj|nm|ny|nc|nd|oh|ok|or|pa|ri|sc|"
    r"sd|tn|tx|ut|vt|va|wa|wv|wi|wy)\b",
]
# ...unless they ALSO match one of these (catches "Remote - UK", "Remote (EMEA)" etc.)
NON_US_EXCLUDE_PATTERNS = [
    r"\buk\b", r"\bunited kingdom\b", r"\bengland\b", r"\blondon\b",
    r"\bireland\b", r"\bdublin\b", r"\bgermany\b", r"\bberlin\b",
    r"\bpoland\b", r"\bemea\b", r"\bapac\b", r"\bindia\b", r"\bcanada\b",
    r"\btoronto\b", r"\bsingapore\b", r"\baustralia\b", r"\bmexico\b",
    r"\bbrazil\b", r"\bfrance\b", r"\bparis\b", r"\bspain\b", r"\bnetherlands\b",
    r"\bamsterdam\b", r"\bjapan\b", r"\btokyo\b", r"\bchina\b", r"\bhong kong\b",
]

RECIPIENT_EMAIL = os.environ.get("ALERT_TO", "yuezeldahu@gmail.com")

STATE_DIR = os.path.dirname(os.path.abspath(__file__))
SEEN_JOBS_PATH = os.path.join(STATE_DIR, "seen_jobs.json")
RESOLVED_ATS_PATH = os.path.join(STATE_DIR, "resolved_ats.json")
COMPANIES_PATH = os.path.join(STATE_DIR, "companies.yaml")

HEADERS = {"User-Agent": "Mozilla/5.0 (job-alert-agent; personal use)"}
TIMEOUT = 15


# ---------------------------------------------------------------------------
# ATS fetchers — each returns a list of dicts: {id, title, location, url}
# ---------------------------------------------------------------------------

def fetch_greenhouse(slug):
    r = requests.get(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false",
        headers=HEADERS, timeout=TIMEOUT,
    )
    if r.status_code != 200:
        return None
    data = r.json()
    jobs = data.get("jobs")
    if jobs is None:
        return None
    return [
        {
            "id": f"greenhouse:{slug}:{j['id']}",
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
        }
        for j in jobs
    ]


def fetch_lever(slug):
    r = requests.get(
        f"https://api.lever.co/v0/postings/{slug}?mode=json",
        headers=HEADERS, timeout=TIMEOUT,
    )
    if r.status_code != 200:
        return None
    try:
        jobs = r.json()
    except ValueError:
        return None
    if not isinstance(jobs, list):
        return None
    return [
        {
            "id": f"lever:{slug}:{j.get('id')}",
            "title": j.get("text", ""),
            "location": (j.get("categories") or {}).get("location", ""),
            "url": j.get("hostedUrl", ""),
        }
        for j in jobs
    ]


def fetch_ashby(slug):
    r = requests.get(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
        headers=HEADERS, timeout=TIMEOUT,
    )
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    jobs = data.get("jobs")
    if jobs is None:
        return None
    return [
        {
            "id": f"ashby:{slug}:{j.get('id')}",
            "title": j.get("title", ""),
            "location": j.get("location", "") or j.get("locationName", ""),
            "url": j.get("jobUrl", "") or j.get("applyUrl", ""),
        }
        for j in jobs
    ]


ATS_FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
}


# ---------------------------------------------------------------------------
# ATS auto-detection (cached in resolved_ats.json across runs)
# ---------------------------------------------------------------------------

def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=True)


def resolve_company(company, resolved_cache):
    """Return (ats_type, slug, jobs) for a company, using/populating cache."""
    name = company["name"]
    cached = resolved_cache.get(name)
    if cached and cached.get("ats") in ATS_FETCHERS:
        jobs = ATS_FETCHERS[cached["ats"]](cached["slug"])
        if jobs is not None:
            return cached["ats"], cached["slug"], jobs
        # cached config stopped working — fall through and re-detect

    for slug in company.get("slug_guesses", []):
        for ats_name, fetcher in ATS_FETCHERS.items():
            jobs = fetcher(slug)
            if jobs is not None:
                resolved_cache[name] = {"ats": ats_name, "slug": slug}
                return ats_name, slug, jobs

    return None, None, None


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def title_matches(title):
    t = title.lower()
    return any(kw in t for kw in ROLE_KEYWORDS)


def location_is_us(location):
    loc = (location or "").lower()
    if any(re.search(p, loc) for p in NON_US_EXCLUDE_PATTERNS):
        return False
    return any(re.search(p, loc) for p in US_INCLUDE_PATTERNS)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def send_email(new_by_company, unresolved_companies):
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]

    total = sum(len(v) for v in new_by_company.values())
    subject = f"🎯 {total} new PM/PD job match{'es' if total != 1 else ''}" if total else \
              "Job alert: no new matches today"

    lines = []
    if total == 0:
        lines.append("No new matching postings today. The agent is running fine — just quiet out there.")
    for company, jobs in new_by_company.items():
        if not jobs:
            continue
        lines.append(f"\n{company}")
        lines.append("-" * len(company))
        for j in jobs:
            loc = j["location"] or "location n/a"
            lines.append(f"  • {j['title']}  [{loc}]\n    {j['url']}")

    if unresolved_companies:
        lines.append("\n\nCouldn't auto-detect job board for (check manually):")
        for c in unresolved_companies:
            lines.append(f"  • {c['name']}: {c.get('careers_url', 'n/a')}")

    body = "\n".join(lines)

    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, RECIPIENT_EMAIL, msg.as_string())

    print(f"Email sent: {subject}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    with open(COMPANIES_PATH) as f:
        companies = yaml.safe_load(f)["companies"]

    resolved_cache = load_json(RESOLVED_ATS_PATH, {})
    seen_jobs = load_json(SEEN_JOBS_PATH, {})  # {job_id: true}

    new_by_company = {}
    unresolved_companies = []

    for company in companies:
        name = company["name"]
        ats, slug, jobs = resolve_company(company, resolved_cache)

        if jobs is None:
            unresolved_companies.append(company)
            print(f"[skip] {name}: could not auto-detect ATS from slug guesses "
                  f"{company.get('slug_guesses')}")
            continue

        print(f"[ok] {name}: resolved to {ats}:{slug} ({len(jobs)} total postings)")

        matches = [j for j in jobs if title_matches(j["title"]) and location_is_us(j["location"])]
        new_matches = [j for j in matches if j["id"] not in seen_jobs]

        for j in matches:
            seen_jobs[j["id"]] = True

        if new_matches:
            new_by_company[f"[{company.get('tier','?')}] {name}"] = new_matches

    save_json(RESOLVED_ATS_PATH, resolved_cache)
    save_json(SEEN_JOBS_PATH, seen_jobs)

    if os.environ.get("SMTP_USER"):
        send_email(new_by_company, unresolved_companies)
    else:
        print("\nSMTP_USER not set — skipping email, printing results instead:\n")
        total = sum(len(v) for v in new_by_company.values())
        print(f"{total} new matches")
        for company, jobs in new_by_company.items():
            print(f"\n{company}")
            for j in jobs:
                print(f"  - {j['title']} [{j['location']}] {j['url']}")
        if unresolved_companies:
            print("\nUnresolved companies:")
            for c in unresolved_companies:
                print(f"  - {c['name']}: {c.get('careers_url')}")


if __name__ == "__main__":
    sys.exit(main())
