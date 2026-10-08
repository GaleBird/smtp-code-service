from __future__ import annotations

import unittest
from email.message import EmailMessage

from app.mail_parser import (
    extract_code,
    extract_sender,
    extract_subject,
    extract_text_from_message,
    make_local_part,
    normalize_mailbox,
)


class TestMailParser(unittest.TestCase):
    def test_extract_code_standard(self):
        self.assertEqual(extract_code("Your verification code is 123456."), "123456")
        self.assertEqual(extract_code("Code: 654-321 valid for 5 min"), "654321")
        self.assertEqual(extract_code("OTP: 889 001"), "889001")

    def test_extract_code_html_stripping(self):
        html = """
        <html>
        <head>
          <style>.code-789012 { color: red; }</style>
          <script>var secret = 345678;</script>
        </head>
        <body>
          <p>Please enter your verification code: <strong>718808</strong></p>
        </body>
        </html>
        """
        self.assertEqual(extract_code(html), "718808")

    def test_extract_code_negatives(self):
        self.assertEqual(extract_code("Order #123456"), "")
        self.assertEqual(extract_code("Phone 1234567890"), "")
        self.assertEqual(extract_code(""), "")
        self.assertEqual(extract_code("No numbers here"), "")

    def test_normalize_mailbox(self):
        self.assertEqual(normalize_mailbox("User <User@Example.COM>"), "user@example.com")
        self.assertEqual(normalize_mailbox("  Test@domain.org  "), "test@domain.org")
        self.assertEqual(normalize_mailbox("<service@domain.org>"), "service@domain.org")

    def test_make_local_part(self):
        res1 = make_local_part("my-prefix!", 12)
        self.assertTrue(res1.startswith("my-prefix"))
        self.assertTrue(res1.endswith("00012"))

        res2 = make_local_part("@@@", 5)
        self.assertTrue(res2.startswith("mail"))
        self.assertTrue(res2.endswith("00005"))

    def test_extract_email_components(self):
        msg = EmailMessage()
        msg["Subject"] = "Test Subject"
        msg["From"] = "Alice <alice@example.com>"
        msg.set_content("Hello World! Your code is 998877.")

        raw_bytes = msg.as_bytes()
        self.assertEqual(extract_subject(raw_bytes), "Test Subject")
        self.assertEqual(extract_sender(raw_bytes), "Alice <alice@example.com>")
        self.assertIn("Hello World!", extract_text_from_message(raw_bytes))


if __name__ == "__main__":
    unittest.main()
