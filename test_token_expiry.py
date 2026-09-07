import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from token_expiry import (
    collect_token_statuses,
    expiring_token_reminders,
    format_token_statuses,
)


UTC = dt.timezone.utc


class FakeResponse:
    def __init__(self, *, payload=None, text="", status_code=200):
        self._payload = payload or {}
        self.text = text
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.content = (text or json.dumps(self._payload)).encode()

    def json(self):
        return self._payload


class FakeHttp:
    def post(self, url, **_kwargs):
        if url.endswith("/introspect"):
            return FakeResponse(payload={"active": True, "exp": 1_800_000_000})
        return FakeResponse(text='''
          <GetTokenStatusResponse xmlns="urn:ebay:apis:eBLBaseComponents">
            <Ack>Success</Ack><TokenStatus><Status>Active</Status>
            <ExpirationTime>2027-01-15T00:00:00Z</ExpirationTime></TokenStatus>
          </GetTokenStatusResponse>
        ''')


class TokenExpiryTests(unittest.TestCase):
    def test_provider_dates_and_configured_amazon_date(self):
        now = dt.datetime(2026, 9, 1, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            (base_dir / "tokens.json").write_text(json.dumps({
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_at": now.timestamp() + 7200,
            }))
            (base_dir / "amazon_credentials.json").write_text(json.dumps({
                "refresh_token": "amazon-refresh",
            }))
            env = {
                "EBAY_CLIENT_ID": "client",
                "EBAY_CLIENT_SECRET": "secret",
                "EBAY_OLDAUTH_TOKEN": "legacy",
                "AMAZON_REFRESH_TOKEN_AUTHORIZED_AT": "2026-02-10",
            }

            statuses = collect_token_statuses(base_dir, env=env, now=now, http=FakeHttp())
        by_key = {item["key"]: item for item in statuses}

        self.assertFalse(by_key["ebay_oauth_access"]["alertable"])
        self.assertEqual(by_key["ebay_oauth_refresh"]["source"], "eBay")
        self.assertTrue(by_key["ebay_authnauth"]["expires_at"].startswith("2027-01-15"))
        self.assertTrue(161 < by_key["amazon_lwa_refresh"]["days_left"] < 163)

    def test_only_known_manual_tokens_generate_fourteen_day_reminders(self):
        statuses = [
            {"key": "auto", "label": "Auto", "days_left": 0.1,
             "expires_at": "2026-09-01T02:00:00+00:00", "alertable": False},
            {"key": "known", "label": "Known", "days_left": 13.2,
             "expires_at": "2026-09-15T00:00:00+00:00", "alertable": True},
            {"key": "later", "label": "Later", "days_left": 14.1,
             "expires_at": "2026-09-16T00:00:00+00:00", "alertable": True},
            {"key": "unknown", "label": "Unknown", "days_left": None,
             "expires_at": None, "alertable": False},
        ]

        reminders = expiring_token_reminders(statuses, within_days=14)

        self.assertEqual(
            [item["alert_key"] for item in reminders], ["token-expiry:known"]
        )
        self.assertIn("14 day(s)", reminders[0]["message"])

    def test_status_message_lists_unknown_without_creating_an_expiry_claim(self):
        text = format_token_statuses([{
            "label": "Amazon LWA refresh token",
            "days_left": None,
            "note": "no provider expiration date",
        }])

        self.assertIn("unknown", text)
        self.assertIn("no provider expiration date", text)


if __name__ == "__main__":
    unittest.main()
