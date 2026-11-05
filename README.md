# top-individual-win-con-repo
This code fetches all the top repositories maintained by individuals that are highly interested in windows containers.

## Weekly PDF and Email

Install Python dependencies and authenticate the GitHub CLI before refreshing rankings:

```sh
python -m pip install -r requirements.txt
gh auth login
python score_repos.py
python weekly_report.py --to jonathan18186@gmail.com
```

Reports are saved under `report-history/top-individual-win-con-repo-yyyy-mm-dd.pdf`.
The date uses fixed PST (UTC-8). A matching `.eml` email draft includes the PDF attachment.
Each of the top ten repositories has its score, one sentence about the problem it solves,
and one sentence suggesting a potential additional scenario. Summaries are inferred from
descriptions, not independently verified; scenario suggestions are not roadmap commitments.

The workflow in `.github/workflows/weekly-report.yml` is prepared for Mondays at
09:00 fixed PST (17:00 UTC), including during daylight saving time. GitHub scheduled
runs can be delayed; this is not an exact-time delivery guarantee. The workflow must
be published to the default branch and Actions enabled before it can run.
Publishing this workflow to the default branch enables the Monday schedule when Actions is enabled.

Before enabling delivery, configure these under repository Settings > Secrets and
variables > Actions. Only `SMTP_PASSWORD` is read from Secrets; all other mail
settings are read from Variables:

| Name | Storage | Purpose |
| --- | --- | --- |
| `SMTP_HOST` | Variable | Your SMTP provider's hostname |
| `SMTP_PORT` | Variable | STARTTLS port, default 587 if unset |
| `SMTP_USERNAME` | Variable | SMTP login |
| `SMTP_PASSWORD` | Secret | SMTP password or provider app password |
| `EMAIL_FROM` | Variable | Provider-authorized sender address |
| `EMAIL_TO` | Variable | Recipient address, e.g. `jonathan18186@gmail.com` |

Only STARTTLS SMTP is supported; implicit TLS on port 465 is not supported.
For a local send, set the same environment variables and run `python weekly_report.py --send`.
Manual workflow runs send email by default; deselect `send_email` to generate and archive
the report without sending.
Scheduled runs refresh the rolling 30-day rankings and send the generated PDF.

Each run generates the table-format PDF and commits it to `report-history` on the
default branch before sending email, so an email delivery failure does not remove
the archived report. Runs on the same PST date update that date's PDF; earlier
versions remain in Git history. The workflow needs permission to push to the default
branch; branch protection or repository rules that block the Actions bot will fail
the archive step and prevent email delivery.
The report directory and ranking JSON are also uploaded as a downloadable Actions
artifact retained for 90 days. Only PDFs are committed, not email drafts or ranking JSON.
Email drafts contain the recipient and attachment, so treat artifacts as private mail data.
Artifact visibility follows repository access; public repositories expose their reports.

Run checks with `python -m unittest -v test_weekly_report.py`.
