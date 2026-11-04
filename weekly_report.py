#!/usr/bin/env python3
"""Generate and optionally email the weekly Windows-container repository report."""
import argparse
import html
import json
import os
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent
PST = timezone(timedelta(hours=-8), name="PST")
CAPABILITIES = (
    (r"compose|orchestrat", "It reduces manual coordination of multi-container applications", "multi-service integration tests with startup-order and health checks"),
    (r"host compute|\bhcs\b|hyper-v|process-isolat", "It simplifies managing native Windows container isolation, images and networking", "automated isolation and network compatibility tests across Windows releases"),
    (r"chocolatey|base images?|nanoserver|servercore", "It simplifies provisioning reproducible Windows container environments", "validated dependency installation across supported Windows base-image versions"),
    (r"monitor|\btop\b|wsltop|watcher", "It makes container resource usage and operational state easier to diagnose on Windows", "resource-threshold alerts and historical performance comparisons"),
    (r"testcontainers|integration tests", "It makes disposable container environments easier to use in automated tests", "parallel integration tests with guaranteed resource cleanup"),
    (r"desktop|manager|winui|dashboard|gui", "It reduces command-line overhead for managing containers on Windows", "bulk lifecycle operations and accessible multi-project management"),
    (r"runtime|engine", "It provides a runtime for executing container workloads on Windows", "cross-platform workload compatibility checks in continuous integration"),
)


def summaries(repo):
    description = re.sub(r"\s+", " ", repo.get("description") or "").strip()
    context = f"{description} {repo['repo']}"
    for pattern, problem, scenario in CAPABILITIES:
        if re.search(pattern, context, re.I):
            return problem + ".", "A potential addition is " + scenario + "."
    return (
        "It addresses container workflow needs on Windows, although its description does not establish a more specific problem.",
        "A potential addition is a repeatable Windows container compatibility test suite.",
    )


def report_entries(results):
    if not isinstance(results, list) or not results:
        raise ValueError("No ranked repositories are available; refusing to generate an empty report")
    entries = sorted(results, key=lambda repo: repo["score"], reverse=True)[:10]
    for repo in entries:
        if not repo.get("repo") or not repo.get("url"):
            raise ValueError("Each repository must have a name and source URL")
    return entries


def generate_pdf(entries, output, report_date):
    styles = getSampleStyleSheet()
    styles["BodyText"].spaceAfter = 7
    styles["BodyText"].leading = 14
    styles["Heading2"].textColor = colors.HexColor("#185b48")
    story = []

    def paragraph(text, style="BodyText"):
        story.append(Paragraph(text, styles[style]))

    paragraph("Top Individually Owned Windows Container Repositories", "Title")
    paragraph(f"Weekly report: {report_date.isoformat()} | Rolling activity window: 30 days")
    paragraph(
        "Ranked from the current GitHub search snapshot; native Windows containers are tier A, "
        "while Windows-host and WSL tooling are tier B. Problem summaries are capability-based "
        "inferences from repository descriptions; potential scenarios are suggestions, not verified roadmap commitments."
    )
    if len(entries) < 10:
        paragraph(f"Only {len(entries)} qualifying repositories were available; no extra entries were invented.")
    cell = ParagraphStyle("cell", parent=styles["BodyText"], fontSize=7, leading=9, spaceAfter=0)
    head = ParagraphStyle("head", parent=cell, textColor=colors.white, fontName="Helvetica-Bold")

    def c(text, style=cell):
        return Paragraph(text, style)

    rows = [[c(h, head) for h in ("#", "Repository", "Score", "Problem solved", "Potential scenario",
                                  "Tier / Stars / Forks", "Last PR / Merge", "Latest discussion")]]
    for rank, repo in enumerate(entries, 1):
        problem, scenario = summaries(repo)
        url = html.escape(repo["url"], quote=True)
        discussion = (
            f"{html.escape(str(repo.get('last_comment', '-')))}<br/>"
            f"{html.escape(str(repo.get('comment_semantics', 'none')))}"
        )
        if repo.get("last_comment_excerpt"):
            discussion += "<br/>" + html.escape(repo["last_comment_excerpt"])
        if repo.get("last_comment_url"):
            comment_url = html.escape(repo["last_comment_url"], quote=True)
            discussion += f'<br/><link href="{comment_url}">Source</link>'
        rows.append([
            c(str(rank)),
            c(f'{html.escape(repo["repo"])}<br/><link href="{url}">{url}</link>'),
            c(f"{repo['score']:.2f}/100"),
            c(html.escape(problem)),
            c(html.escape(scenario)),
            c(f"{html.escape(str(repo.get('tier', '-')))} / {repo.get('stars', 0)} / {repo.get('forks', 0)}"),
            c(f"{html.escape(str(repo.get('last_pr', '-')))}<br/>{html.escape(str(repo.get('last_merge', '-')))}"),
            c(discussion),
        ])
    table = Table(rows, repeatRows=1, colWidths=[18, 95, 38, 120, 120, 55, 60, 122])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#185b48")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef5f2")]),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.15 * inch))
    paragraph("Scoring and limitations", "Heading2")
    paragraph(
        "Weights: stars 15%, forks 10%, clones 10%, latest discussion 15%, latest PR 10%, "
        "latest merge 10%, human activity 15%, discussion semantics 15%. Stars, forks and "
        "activity are log-scaled; recency decays as exp(-days/15); tier B has a 0.85 multiplier. "
        "Unavailable clone data is omitted and its weight redistributed."
    )
    paragraph(
        "Known bot accounts are excluded, but a personal account does not prove human authorship. "
        "Activity is sampled from the latest 100 public events; counts can be incomplete. "
        "Discussion semantics use keyword heuristics, include issue/PR opening bodies, and may "
        "misclassify non-English text. User ownership does not establish employer independence."
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(output), pagesize=landscape(letter), rightMargin=42, leftMargin=42, topMargin=42, bottomMargin=42).build(story)


def build_email(entries, pdf, report_date, sender, recipient):
    message = EmailMessage(policy=SMTP)
    message["Subject"] = f"Weekly Windows container repository report - {report_date.isoformat()}"
    message["From"] = sender
    message["To"] = recipient
    lines = [
        "Weekly individually owned Windows container repository report",
        f"Report date: {report_date.isoformat()}",
        "Activity window: the previous 30 days.",
        "",
        "The attached PDF contains ranking scores, problem summaries and suggested scenarios.",
        "",
    ]
    for rank, repo in enumerate(entries, 1):
        problem, scenario = summaries(repo)
        lines.extend([
            f"{rank}. {repo['repo']} - {repo['score']:.2f}/100",
            f"Problem solved: {problem}",
            f"Potential scenario: {scenario}",
            repo["url"], "",
        ])
    lines.append("Scenario suggestions are not verified repository roadmap commitments.")
    message.set_content("\n".join(lines))
    message.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename=pdf.name)
    return message


def send_email(message):
    required = ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise ValueError("Missing email configuration: " + ", ".join(missing))
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=60) as client:
        client.starttls(context=ssl.create_default_context())
        client.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
        client.send_message(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=ROOT / "results.json")
    parser.add_argument("--to", dest="recipient", default=os.environ.get("EMAIL_TO", "recipient@example.invalid"))
    parser.add_argument("--send", action="store_true", help="Send through SMTP; otherwise generate a draft only")
    args = parser.parse_args()
    entries = report_entries(json.loads(args.results.read_text(encoding="utf-8")))
    report_date = datetime.now(PST).date()
    stem = f"top-individual-win-con-repo-{report_date.isoformat()}"
    pdf = ROOT / "report-history" / f"{stem}.pdf"
    generate_pdf(entries, pdf, report_date)
    message = build_email(entries, pdf, report_date, os.environ.get("EMAIL_FROM", "sender@example.invalid"),
                          args.recipient)
    draft = pdf.with_suffix(".eml")
    draft.write_bytes(message.as_bytes())
    print(f"PDF: {pdf}\nEmail draft: {draft}")
    if args.send:
        send_email(message)
        print("Email accepted by the SMTP server")


if __name__ == "__main__":
    main()