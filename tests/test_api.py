from __future__ import annotations

import tempfile
from pathlib import Path

from aiohttp.test_utils import AioHTTPTestCase

from app.api import build_web_app
from app.config import ServiceConfig
from app.storage import MessageStore


class TestAPI(AioHTTPTestCase):
    async def get_application(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test_messages.db"
        self.store = MessageStore(db_path)
        self.store.init()

        self.config = ServiceConfig(
            domains=("example.com", "sub.example.com"),
            api_token="test-secret-token",
            db_path=db_path,
        )

        web_dir = Path(__file__).resolve().parent.parent / "web"
        return build_web_app(self.config, self.store, web_dir=web_dir)

    async def tearDownAsync(self):
        await super().tearDownAsync()
        if hasattr(self, "temp_dir"):
            self.temp_dir.cleanup()

    async def test_health_check(self):
        resp = await self.client.request("GET", "/health")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data.get("ok"))
        self.assertIn("time", data)

    async def test_generate_email_auth_and_generation(self):
        # 1. Unauthorized
        resp = await self.client.request("POST", "/api/emails/generate")
        self.assertEqual(resp.status, 401)

        # 2. Authorized
        resp = await self.client.request(
            "POST",
            "/api/emails/generate?token=test-secret-token",
            json={"name": "demo", "domain": "example.com"},
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["email"].startswith("demo"))
        self.assertTrue(data["email"].endswith("@example.com"))

    async def test_get_code_and_domainless_lookup(self):
        # Insert a message into store
        self.store.insert(
            mailbox="tester@example.com",
            code="654321",
            subject="Your Login Code",
            sender="service@auth.org",
            body_text="Your code is 654321.",
            received_at="2026-10-08T12:00:00Z",
        )

        # Lookup with full address
        resp = await self.client.request(
            "GET",
            "/get-code?token=test-secret-token&email=tester@example.com",
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["code"], "654321")
        self.assertEqual(data["mailbox"], "tester@example.com")

        # Lookup without domain suffix
        resp_nodomain = await self.client.request(
            "GET",
            "/get-code?token=test-secret-token&email=tester",
        )
        self.assertEqual(resp_nodomain.status, 200)
        data_nodomain = await resp_nodomain.json()
        self.assertEqual(data_nodomain["code"], "654321")
        self.assertEqual(data_nodomain["mailbox"], "tester@example.com")

    async def test_ui_messages_and_detail(self):
        msg_id = self.store.insert(
            mailbox="alice@example.com",
            code="112233",
            subject="Welcome!",
            sender="team@example.com",
            body_text="Welcome to the platform.",
            received_at="2026-10-08T12:05:00Z",
        )

        # List messages without domain suffix
        resp = await self.client.request("GET", "/api/ui/messages?mailbox=alice")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["mailbox"], "alice@example.com")
        self.assertEqual(len(data["messages"]), 1)
        self.assertEqual(data["messages"][0]["id"], msg_id)

        # Get message detail
        resp_detail = await self.client.request("GET", f"/api/ui/message/{msg_id}")
        self.assertEqual(resp_detail.status, 200)
        detail = await resp_detail.json()
        self.assertEqual(detail["code"], "112233")
        self.assertEqual(detail["subject"], "Welcome!")

    async def test_ui_index_and_static_files(self):
        resp_index = await self.client.request("GET", "/")
        self.assertEqual(resp_index.status, 200)
        html = await resp_index.text()
        self.assertIn("<title>FlashMail</title>", html)
        self.assertIn("example.com", html)

        # Static assets
        resp_css = await self.client.request("GET", "/static/style.css")
        self.assertEqual(resp_css.status, 200)

        resp_js = await self.client.request("GET", "/static/app.js")
        self.assertEqual(resp_js.status, 200)
