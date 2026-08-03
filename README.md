# Job Alert Agent

Checks 27 target companies (creator economy / fintech / payments / messaging /
AI-consumer, per your criteria) for new **Product Designer** and
**Product Manager** roles in the US, and emails you the new ones every day.

How it works, in short: each company's public job-board API (Greenhouse,
Lever, or Ashby — auto-detected) is queried, matched against your role
keywords and US-location filter, deduped against what you've already been
sent, and the new matches are emailed to you. It runs for free on GitHub
Actions on a daily schedule.

## 1. Create the GitHub repo

1. Go to github.com → **New repository** → name it e.g. `job-alert-agent` →
   Create.
2. Upload all the files from this folder to it (drag-and-drop on the GitHub
   web UI works fine, or use `git push` if you're comfortable with git).

## 2. Get a Gmail App Password (so the agent can send email as you)

Since you gave `yuezeldahu@gmail.com` as the recipient, the easiest setup is
to send *from* that same Gmail account using an **App Password** (not your
normal password):

1. Go to https://myaccount.google.com/apppasswords
   (you'll need 2-Step Verification turned on first — Google will prompt you)
2. Create an app password named "job alert agent"
3. Copy the 16-character password it gives you

## 3. Add secrets to the GitHub repo

In your new repo: **Settings → Secrets and variables → Actions → New
repository secret**. Add these three:

| Name | Value |
|---|---|
| `SMTP_USER` | `yuezeldahu@gmail.com` |
| `SMTP_PASS` | the 16-character app password from step 2 |
| `ALERT_TO` | `yuezeldahu@gmail.com` (can be a different address if you want) |

## 4. Test it

Go to the **Actions** tab in your repo → **Daily job alert** → **Run
workflow** → Run workflow. It'll take ~1-2 minutes. Check the run's logs:
you'll see one line per company like:

```
[ok] Ramp: resolved to ashby:ramp (34 total postings)
[skip] Stripe: could not auto-detect ATS from slug guesses ['stripe']
```

`[ok]` companies are being monitored correctly. `[skip]` companies couldn't
be auto-matched (usually because they run a custom in-house careers site,
like Stripe) — these get listed at the bottom of your email instead, with a
direct link, so you never lose visibility on them even though they're not
auto-checked.

You should get an email within a minute or two of the run finishing.

## 5. That's it

It's scheduled to run daily at 13:00 UTC (6am PT / 9am ET) — edit the cron
line in `.github/workflows/daily-job-alert.yml` if you want a different
time. First run will email you a large batch (everything currently open,
since nothing's been "seen" yet); after that you'll only get genuinely new
postings.

---

## Editing criteria later

- **Role keywords** — edit `ROLE_KEYWORDS` at the top of `job_agent.py`
- **Location filter** — edit `US_INCLUDE_PATTERNS` / `NON_US_EXCLUDE_PATTERNS`
  in `job_agent.py` if you want to open this up to remote-anywhere, etc.
- **Companies** — add/remove entries in `companies.yaml`. For a new company,
  give it 2-3 `slug_guesses` (usually just the lowercase company name) and
  the agent will auto-detect the right ATS on the next run.
- **Frequency** — change the `cron` schedule in the workflow file
  ([crontab.guru](https://crontab.guru) helps write these).

## Notes on the "similar companies" list

Your seed list already spans the four categories you flagged (creator
economy, messaging, fintech/payments) across all three tiers, so I used it
as-is rather than generating an additional list — let me know if you want it
expanded further (e.g. more AI-companion or marketplace names).

## Companies not on a standard ATS

A few companies in your list (e.g. Stripe) run fully custom in-house careers
sites rather than Greenhouse/Lever/Ashby. These can't be reliably
auto-monitored via a public JSON API, so the agent will always list them as
"unresolved" with a direct careers-page link in your email rather than
silently dropping them.
