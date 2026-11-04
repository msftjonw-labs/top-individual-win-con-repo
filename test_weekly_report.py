import json
import os
import tempfile
import unittest
from datetime import date
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

import weekly_report


class WeeklyReportTests(unittest.TestCase):
    def setUp(self):
        self.repo = {
            "repo": "person/container-manager",
            "url": "https://github.com/person/container-manager",
            "description": "Desktop manager for WSL containers <Windows>",
            "score": 67.25,
            "tier": "B",
            "last_comment_excerpt": "Add support for volumes & network inspection",
            "last_comment_url": "https://github.com/person/container-manager/issues/1",
        }

    def test_summaries_are_single_sentences_and_suggestions(self):
        for description in ("Compose CLI", "Host Compute Service", "Windows base images",
                            "container monitor", "Testcontainers integration tests",
                            "Desktop manager", "container runtime", ""):
            with self.subTest(description=description):
                problem, scenario = weekly_report.summaries({**self.repo, "description": description, "repo": "owner/project"})
                self.assertEqual(problem.count("."), 1)
                self.assertEqual(scenario.count("."), 1)
                self.assertTrue(scenario.startswith("A potential addition is "))

    def test_entries_are_ranked_and_limited_to_ten(self):
        entries = weekly_report.report_entries([{**self.repo, "score": score} for score in range(12)])
        self.assertEqual([repo["score"] for repo in entries], list(range(11, 1, -1)))
        with self.assertRaises(ValueError):
            weekly_report.report_entries([])

    def test_pdf_and_email_attachment(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "report-history" / "top-individual-win-con-repo-2026-11-04.pdf"
            weekly_report.generate_pdf([self.repo], pdf, date(2026, 11, 4))
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF-"))
            message = weekly_report.build_email([self.repo], pdf, date(2026, 11, 4),
                                                "sender@example.com", "recipient@example.com")
            parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
            attachment, = parsed.iter_attachments()
            self.assertEqual(attachment.get_filename(), pdf.name)
            self.assertEqual(attachment.get_content_type(), "application/pdf")
            self.assertEqual(attachment.get_payload(decode=True), pdf.read_bytes())
            body = parsed.get_body(preferencelist=("plain",)).get_content()
            self.assertIn("67.25/100", body)
            self.assertIn("Problem solved:", body)
            self.assertIn("Potential scenario:", body)

    def test_main_saves_exact_filenames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "results.json"
            results.write_text(json.dumps([self.repo]))
            with patch.object(weekly_report, "ROOT", root), patch("sys.argv", ["weekly_report.py", "--results", str(results), "--to", "reader@example.com"]):
                weekly_report.main()
            today = weekly_report.datetime.now(weekly_report.PST).date().isoformat()
            stem = f"top-individual-win-con-repo-{today}"
            self.assertTrue((root / "report-history" / f"{stem}.pdf").is_file())
            self.assertTrue((root / "report-history" / f"{stem}.eml").is_file())
            draft = BytesParser(policy=policy.default).parsebytes((root / "report-history" / f"{stem}.eml").read_bytes())
            self.assertEqual(draft["To"], "reader@example.com")

    def test_send_requires_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "Missing email configuration"):
                weekly_report.send_email(None)

    def test_send_uses_tls_and_authenticated_smtp(self):
        configuration = {"SMTP_HOST": "smtp.example.com", "SMTP_USERNAME": "sender",
                         "SMTP_PASSWORD": "test-password", "EMAIL_FROM": "sender@example.com",
                         "EMAIL_TO": "recipient@example.com"}
        with patch.dict(os.environ, configuration, clear=True), patch("weekly_report.smtplib.SMTP") as smtp:
            message = object()
            weekly_report.send_email(message)
            smtp.assert_called_once_with("smtp.example.com", 587, timeout=60)
            client = smtp.return_value.__enter__.return_value
            client.starttls.assert_called_once()
            client.login.assert_called_once_with("sender", "test-password")
            client.send_message.assert_called_once_with(message)


if __name__ == "__main__":
    unittest.main()