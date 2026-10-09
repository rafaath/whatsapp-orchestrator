import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
import whatsapp


class LocalConnectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "messages.db"
        self.addCleanup(patch.stopall)
        patch.object(whatsapp, "MESSAGES_DB_PATH", str(self.db)).start()
        patch.object(whatsapp, "STORE_DIR", Path(self.temp.name)).start()
        with sqlite3.connect(self.db) as connection:
            connection.executescript("""
                CREATE TABLE chats(jid TEXT PRIMARY KEY, name TEXT, last_message_time TEXT);
                CREATE TABLE contacts(jid TEXT PRIMARY KEY, phone_number TEXT, name TEXT);
                CREATE TABLE messages(id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TEXT, is_from_me BOOLEAN, media_type TEXT);
                INSERT INTO chats VALUES('15551234567@s.whatsapp.net','Example','2026-10-04T12:00:00.000000000Z');
                INSERT INTO chats VALUES('998877@lid','Hidden Contact','2026-10-04T12:00:00.000000000Z');
                INSERT INTO contacts VALUES('998877@lid','15557654321','Hidden Contact');
                INSERT INTO contacts VALUES('15557654321@s.whatsapp.net','15557654321','Hidden Contact');
                INSERT INTO messages VALUES('same-id','15551234567@s.whatsapp.net','15551234567','fixture one','2026-10-04T12:00:00.000000000Z',0,'');
                INSERT INTO messages VALUES('second-id','15551234567@s.whatsapp.net','15551234567','fixture two','2026-10-04T12:00:00.000000000Z',0,'');
                INSERT INTO messages VALUES('same-id','998877@lid','998877','hidden fixture','2026-10-04T12:00:00.000000000Z',0,'');
            """)

    def test_chat_metadata_with_and_without_messages(self):
        self.assertEqual(len(whatsapp.list_chats()), 2)
        self.assertIsNone(whatsapp.list_chats(include_last_message=False)[0].last_message)
        self.assertIsNone(whatsapp.get_chat("15551234567@s.whatsapp.net", False).last_message)

    def test_exact_phone_lookup_and_lid_mapping(self):
        self.assertEqual(whatsapp.get_direct_chat_by_contact("15551234567").jid, "15551234567@s.whatsapp.net")
        self.assertIsNone(whatsapp.get_direct_chat_by_contact("5551234567"))
        self.assertEqual(whatsapp.get_direct_chat_by_contact("15557654321").jid, "998877@lid")
        contacts = whatsapp.search_contacts("Hidden")
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0].phone_number, "15557654321")
        self.assertEqual(contacts[0].jid, "998877@lid")

    def test_message_context_is_chat_scoped(self):
        with self.assertRaisesRegex(ValueError, "multiple chats"):
            whatsapp.get_message_context("same-id")
        context = whatsapp.get_message_context("same-id", chat_jid="998877@lid")
        self.assertEqual(context.message.content, "hidden fixture")
        messages = whatsapp.list_messages(chat_jid="998877@lid", include_context=True)
        self.assertIn("hidden fixture", messages)
        self.assertNotIn("fixture one", messages)
        tied = whatsapp.get_message_context("same-id", chat_jid="15551234567@s.whatsapp.net")
        self.assertEqual([message.id for message in tied.after], ["second-id"])

    def test_bounds_and_utc_timestamp(self):
        for kwargs in ({"limit": -1}, {"limit": 101}, {"page": -1}, {"context_before": 100}):
            with self.assertRaises(ValueError):
                whatsapp.list_messages(**kwargs)
        self.assertEqual(whatsapp.database_timestamp("2026-10-04T14:00:00+02:00"), "2026-10-04T12:00:00.000000000Z")

    def test_read_only_store_and_no_protocol_output_on_error(self):
        with whatsapp.open_database() as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM messages")
        self.db.unlink()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(RuntimeError):
            whatsapp.list_chats()
        self.assertEqual(output.getvalue(), "")
        self.assertFalse(self.db.exists())

    def test_uncertain_send_has_no_retry(self):
        (Path(self.temp.name) / "api-token").write_text("fixture-token")
        with patch("requests.Session") as factory:
            session = factory.return_value.__enter__.return_value
            session.post.side_effect = requests.ReadTimeout()
            success, message = whatsapp.send_message("15551234567", "fixture")
            self.assertFalse(success)
            self.assertIn("do not automatically retry", message)
            self.assertEqual(session.post.call_count, 1)
            self.assertFalse(session.trust_env)
            self.assertEqual(session.post.call_args.kwargs["timeout"], (3, 170))


if __name__ == "__main__":
    unittest.main()
