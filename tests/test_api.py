from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


class KudosApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "test-kudos.sqlite3"
        self.app = create_app(
            database_path,
            session_secret=b"test-only-secret",
            cooldown_seconds=0,
            duplicate_window_seconds=120,
            blocked_terms=("badword",),
        )
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.switch_to("alex")

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def switch_to(self, user_id: str) -> dict:
        response = self.client.post("/api/auth/switch", json={"userId": user_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["user"]

    def send_kudos(self, *, recipient_id: str = "sam", message: str = "Great teamwork!") -> object:
        return self.client.post(
            "/api/kudos",
            json={"recipientId": recipient_id, "message": message},
        )

    def test_seed_profiles_and_public_feed_require_authentication(self) -> None:
        profiles = self.client.get("/api/auth/profiles")
        self.assertEqual(profiles.status_code, 200)
        self.assertEqual(profiles.json()["profiles"][0]["role"], "admin")

        self.client.post("/api/auth/logout")
        feed = self.client.get("/api/kudos")
        self.assertEqual(feed.status_code, 401)
        self.assertEqual(feed.json()["error"]["code"], "UNAUTHENTICATED")

    def test_user_can_submit_kudos_and_feed_returns_new_entry(self) -> None:
        response = self.send_kudos(message="  Thanks for your thoughtful review!  ")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["item"]["message"], "Thanks for your thoughtful review!")
        self.assertEqual(response.json()["item"]["giver"]["id"], "alex")

        feed = self.client.get("/api/kudos")
        self.assertEqual(feed.status_code, 200)
        self.assertEqual(feed.headers["Cache-Control"], "no-store")
        self.assertEqual(feed.json()["items"][0]["message"], "Thanks for your thoughtful review!")

    def test_rejects_empty_oversized_self_and_inactive_recipient(self) -> None:
        empty = self.send_kudos(message="  ")
        self.assertEqual(empty.status_code, 422)
        self.assertIn("message", empty.json()["error"]["fieldErrors"])

        oversized = self.send_kudos(message="x" * 501)
        self.assertEqual(oversized.status_code, 422)

        self_kudos = self.send_kudos(recipient_id="alex")
        self.assertEqual(self_kudos.status_code, 422)
        self.assertEqual(self_kudos.json()["error"]["code"], "SELF_KUDOS_NOT_ALLOWED")

        inactive = self.client.post("/api/kudos", json={"recipientId": "nobody", "message": "Well done"})
        self.assertEqual(inactive.status_code, 404)

    def test_message_is_normalized_and_unsafe_markup_remains_plain_text(self) -> None:
        response = self.send_kudos(message="  <script>alert('hello')</script>  ")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["item"]["message"], "<script>alert('hello')</script>")

    def test_blocks_configured_inappropriate_content(self) -> None:
        response = self.send_kudos(message="This contains a badword.")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "CONTENT_NOT_ALLOWED")

    def test_duplicate_submission_is_rejected(self) -> None:
        first = self.send_kudos(message="A unique thank you!")
        second = self.send_kudos(message="A unique thank you!")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["error"]["code"], "DUPLICATE_KUDOS")

    def test_general_sender_cooldown_returns_retry_after(self) -> None:
        app = create_app(
            Path(self.temp_dir.name) / "cooldown.sqlite3",
            session_secret=b"cooldown-test",
            cooldown_seconds=60,
            duplicate_window_seconds=0,
            blocked_terms=(),
        )
        with TestClient(app) as client:
            client.post("/api/auth/switch", json={"userId": "alex"})
            first = client.post("/api/kudos", json={"recipientId": "sam", "message": "First"})
            second = client.post("/api/kudos", json={"recipientId": "jordan", "message": "Second"})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 429)
        self.assertGreater(int(second.headers["Retry-After"]), 0)

    def test_colleague_search_excludes_current_user(self) -> None:
        response = self.client.get("/api/users?query=Alex")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("alex", [user["id"] for user in response.json()["users"]])

    def test_feed_cursor_paginates_without_duplicates(self) -> None:
        for index in range(22):
            response = self.send_kudos(
                recipient_id="sam",
                message=f"Pagination kudos {index}",
            )
            self.assertEqual(response.status_code, 201, response.text)

        first_page = self.client.get("/api/kudos?limit=20").json()
        self.assertEqual(len(first_page["items"]), 20)
        self.assertIsNotNone(first_page["nextCursor"])

        second_page = self.client.get(
            "/api/kudos",
            params={"limit": 20, "cursor": first_page["nextCursor"]},
        ).json()
        first_ids = {item["id"] for item in first_page["items"]}
        second_ids = {item["id"] for item in second_page["items"]}
        self.assertFalse(first_ids.intersection(second_ids))
        self.assertGreaterEqual(len(first_page["items"]) + len(second_page["items"]), 25)

    def test_invalid_feed_cursor_returns_validation_error(self) -> None:
        response = self.client.get("/api/kudos?cursor=not-a-valid-cursor")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "INVALID_CURSOR")

    def test_non_admin_cannot_moderate_and_admin_hide_is_audited(self) -> None:
        created = self.send_kudos(message="Please celebrate this work.")
        kudos_id = created.json()["item"]["id"]
        forbidden = self.client.patch(
            f"/api/kudos/{kudos_id}/hide",
            json={"reason": "Inappropriate"},
        )
        self.assertEqual(forbidden.status_code, 403)

        self.switch_to("admin")
        missing_reason = self.client.patch(f"/api/kudos/{kudos_id}/hide", json={"reason": " "})
        self.assertEqual(missing_reason.status_code, 422)
        self.assertIn("reason", missing_reason.json()["error"]["fieldErrors"])

        hidden = self.client.patch(
            f"/api/kudos/{kudos_id}/hide",
            json={"reason": "Reviewed by the admin team."},
        )
        self.assertEqual(hidden.status_code, 200, hidden.text)
        self.assertFalse(hidden.json()["isVisible"])
        self.assertNotIn(kudos_id, [item["id"] for item in self.client.get("/api/kudos").json()["items"]])

        with closing(sqlite3.connect(Path(self.temp_dir.name) / "test-kudos.sqlite3")) as connection:
            audit = connection.execute(
                """
                SELECT is_visible, moderated_by, moderated_at, reason_for_moderation, moderation_action
                FROM kudos WHERE id = ?
                """,
                (kudos_id,),
            ).fetchone()
        self.assertEqual(audit[0], 0)
        self.assertEqual(audit[1], "admin")
        self.assertIsNotNone(audit[2])
        self.assertEqual(audit[3], "Reviewed by the admin team.")
        self.assertEqual(audit[4], "hidden")

    def test_admin_delete_soft_hides_record_and_logs_reason(self) -> None:
        created = self.send_kudos(message="Keep this row for audit.")
        kudos_id = created.json()["item"]["id"]
        self.switch_to("admin")
        response = self.client.request(
            "DELETE",
            f"/api/kudos/{kudos_id}",
            json={"reason": "Contains private details."},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "deleted")

        with closing(sqlite3.connect(Path(self.temp_dir.name) / "test-kudos.sqlite3")) as connection:
            row = connection.execute(
                "SELECT is_visible, reason_for_moderation, moderation_action FROM kudos WHERE id = ?",
                (kudos_id,),
            ).fetchone()
        self.assertEqual(row, (0, "Contains private details.", "deleted"))
        self.assertNotIn(kudos_id, [item["id"] for item in self.client.get("/api/kudos").json()["items"]])

    def test_already_moderated_item_does_not_overwrite_audit(self) -> None:
        created = self.send_kudos(message="Audit should remain stable.")
        kudos_id = created.json()["item"]["id"]
        self.switch_to("admin")
        first = self.client.patch(f"/api/kudos/{kudos_id}/hide", json={"reason": "First reason."})
        second = self.client.request(
            "DELETE",
            f"/api/kudos/{kudos_id}",
            json={"reason": "Second reason."},
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)

        with closing(sqlite3.connect(Path(self.temp_dir.name) / "test-kudos.sqlite3")) as connection:
            reason, action = connection.execute(
                "SELECT reason_for_moderation, moderation_action FROM kudos WHERE id = ?",
                (kudos_id,),
            ).fetchone()
        self.assertEqual((reason, action), ("First reason.", "hidden"))


if __name__ == "__main__":
    unittest.main()
