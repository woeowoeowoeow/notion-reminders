# Notion reminders

GitHub Actions imports Canvas and D2L calendar assignments into Notion,
sends task reminders through ntfy, and emails a daily morning brief.

## Scheduling and rate limits

Calendar feeds are fetched once, but each assignment currently requires a
Notion lookup and a create/update. A 105-assignment sync can therefore make
210 Notion requests. `notion_http.py` spaces requests by at least 0.5 seconds
and retries HTTP 429 up to five times, honoring `Retry-After`. Persistent
errors fail the workflow. Ambiguous network/server errors are not retried
automatically, to avoid repeating a create that may already have succeeded.
All five production workflows use the same concurrency group and queue,
so they cannot compete with one another for the connection's request budget.
Other applications using the same workspace can still cause rate limits.

The morning brief is scheduled at 10:17 and 11:17 UTC to cover Eastern
daylight saving time. It sends after 6 AM Eastern, including late starts,
and records the Eastern delivery date on the `automation-state` branch.
The second run and manual reruns skip a date already recorded. Runs delayed
past midnight use the current Eastern day; they do not send yesterday's brief.
GitHub schedules are best effort; delivery at exactly 6 AM is not guaranteed.

## Delivery receipt setup

Before running the morning brief, create `automation-state` from `main` and
add `morning-brief.json` there with:

```json
{"last_sent_date": null}
```

The brief job has `contents: write` permission to update this receipt using
its built-in `GITHUB_TOKEN`; no new secret is needed. Do not protect that
branch against these updates. CI runs only for pushes to `main` and pull
requests targeting `main`, so daily receipt commits do not trigger CI.
Receipt reads fail closed on missing, malformed, unauthorized or unavailable
state. Fix the state problem before rerunning.

The receipt is written immediately after SMTP accepts the message. SMTP and
GitHub cannot commit atomically: a crash or receipt-write failure in between
can cause a duplicate on rerun. Such write failures fail the workflow. Check
whether the email arrived before rerunning a failed brief; if needed, update
the receipt to that Eastern date. A receipt means SMTP accepted delivery,
not that the email reached the inbox.

Local runs require the usual Notion/email environment variables plus
`BRIEF_STATE_FILE` pointing to an initialized JSON receipt with the same
format. Serialize local runs yourself. Never commit local credentials.

## Inactivity warnings

GitHub can disable scheduled workflows in public repositories after 60 days
without repository activity. Deploying a change refreshes activity; daily
delivery receipts also produce useful commits on `automation-state`.
Monitor GitHub's warning emails rather than assuming scheduled runs alone
prevent disablement. If a workflow is disabled, re-enable it from the Actions
tab and make a relevant repository update as needed.

## Verification

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Tests use a localhost HTTP server and fake SMTP; no live Notion changes,
notifications, or emails are sent.
