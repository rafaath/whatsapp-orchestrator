"""Synthetic, offline checks. No live contacts, model calls, or sends."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from .outreach import FILENAME, import_review, load_review, project_review, read_source
from .server import API


def fixture_review():
    body = "Hi Alex,\n\nI enjoyed reading about the way you built your neighborhood restaurant. The care you put into training your team stood out.\n\nI'm testing an outreach review with synthetic data only. This message is not connected to a real WhatsApp contact, and reviewing it cannot create a scheduled message.\n\nWould you be open to a short conversation about what has worked well for your team, and which day-to-day challenges are still taking more time than you would like?\n\nThere is no obligation. I'd be glad to share any useful findings with you afterward."
    base = {"id": "one", "name": "Alex Example", "organization": "Neighborhood Kitchen",
            "draft": body, "draft_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "send_authorized": False, "schedule": None, "draft_approved": False,
            "contact_status": "synced_saved_contact_matches_supplied_number", "saved_contact_names": ["Alex Example Client"],
            "contact_checked_at_utc": "2026-10-05T10:18:00Z",
            "matched_contact_jids": ["15550000002@s.whatsapp.net"],
            "source_url": "https://example.com/research", "profile_url": "https://example.com/alex",
            "research_note": "A synthetic research note; no live conversation or recipient.",
            "blockers": ["Approve exact wording", "Choose a future time"],
            "timing_proposal": {"utc": "2026-10-06T14:00:00Z", "iana_zone": "America/New_York", "basis": "Reported business location"}}
    approved = dict(base, id="two", name="Blair Example", draft_approved=True)
    unresolved = dict(base, id="three", name="Casey Example", contact_status="not_visible_in_synced_contacts", saved_contact_names=[], timing_proposal=None)
    alternative = dict(base, id="four", name="Drew Example", timing_proposal={"utc": "2026-10-06T10:00:00Z", "iana_zone": "UTC", "local_alternatives": {"Europe/Madrid": "2026-10-06T12:00:00+02:00", "Atlantic/Canary": "2026-10-06T11:00:00+01:00"}})
    followup = {key: base[key] for key in ("id", "draft", "draft_sha256", "send_authorized", "schedule")}
    followup.update(name="Eden Example", organization="Example Cafe", incoming_messages_at_recheck=0,
                    outgoing_messages_at_recheck=1, history_rechecked_at_utc="2026-10-05T10:27:00Z",
                    review_not_before="2026-10-07", preconditions=["Recheck for a reply before any follow-up"])
    return {"updated_at_utc": "2026-10-05T10:40:00Z", "remaining_leads": [base, approved, unresolved, alternative], "followups": [followup]}


class OutreachTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root/"review.json"
        self.data = fixture_review()

    def save(self):
        self.source.write_text(json.dumps(self.data), encoding="utf-8")

    def test_exact_wording_approval_and_separate_followups(self):
        result = project_review(self.data)
        self.assertEqual(len(result["entries"]), 5)
        self.assertEqual(result["entries"][0]["body"], self.data["remaining_leads"][0]["draft"])
        self.assertTrue(result["entries"][1]["wording_approved"])
        self.assertEqual(result["entries"][-1]["id"], "followup:one")
        self.assertIsNone(result["entries"][-1]["proposal"])
        self.assertEqual(result["entries"][3]["proposal"]["zones"], ["Europe/Madrid", "Atlantic/Canary"])
        self.assertEqual(result["entries"][-1]["review_not_before"], "2026-10-07")
        self.assertNotIn("matched_contact_jids", result["entries"][0])
        self.assertNotIn("15550000002", json.dumps(result))
        self.assertNotIn("send_authorized", result["entries"][0])

    def test_import_is_idempotent_and_does_not_touch_queue_or_original(self):
        runtime = self.root/"runtime"; runtime.mkdir()
        db = runtime/"control.db"; db.write_bytes(b"unmodified existing queue sentinel")
        self.save(); original = self.source.read_bytes()
        self.assertEqual(import_review(self.source, self.root), 5)
        first = load_review(self.root)
        self.assertEqual(import_review(self.source, self.root), 5)
        self.assertEqual(first, load_review(self.root))
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(db.read_bytes(), b"unmodified existing queue sentinel")
        self.assertEqual((runtime/FILENAME).read_bytes(), original)
        self.assertFalse(list(runtime.glob("*.tmp")))

    def test_invalid_import_leaves_previous_snapshot_intact(self):
        self.save(); import_review(self.source, self.root)
        previous = load_review(self.root)
        self.data["remaining_leads"][0]["draft"] += " unexpected edit"
        self.save()
        with self.assertRaisesRegex(ValueError, "checksum"):
            import_review(self.source, self.root)
        self.assertEqual(load_review(self.root), previous)

    def test_unsafe_or_misleading_input_is_rejected(self):
        for field, value in (("send_authorized", True), ("send_authorized", 0), ("schedule", {}),
                             ("draft_approved", 1), ("source_url", "javascript:alert(1)"),
                             ("source_url", "file:///private/data"), ("source_url", "https://user:pass@example.com"),
                             ("contact_checked_at_utc", "2026-10-05T10:00:00"), ("name", None),
                             ("contact_status", "ready_to_send"), ("draft_sha256", "bad"),
                             ("timing_proposal", {"utc": "2026-10-06T10:00:00Z", "iana_zone": "No/SuchZone"})):
            with self.subTest(field=field, value=value):
                data = copy.deepcopy(self.data); data["remaining_leads"][0][field] = value
                self.source.write_text(json.dumps(data))
                with self.assertRaises(ValueError): read_source(self.source)
        self.data["remaining_leads"].append(self.data["remaining_leads"][0])
        with self.assertRaisesRegex(ValueError, "unique"): project_review(self.data)

    def test_missing_corrupt_and_oversize_files_are_distinct(self):
        self.assertFalse(load_review(self.root)["available"])
        self.source.write_text("{broken")
        with self.assertRaises(ValueError): read_source(self.source)
        self.source.write_bytes(b" " * (2 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "limit"): read_source(self.source)

    def test_api_read_is_scoped_and_writes_are_not_supported(self):
        self.save(); import_review(self.source, self.root)
        api = API(SimpleNamespace(root=self.root, store=None))
        self.assertEqual(len(api.get("/api/outreach", {"path": ["/etc/passwd"]})["entries"]), 5)
        for path in ("/api/outreach", "/api/outreach/send", "/api/outreach/schedule"):
            with self.assertRaisesRegex(ValueError, "Unknown API route"): api.post(path, {})


if __name__ == "__main__": unittest.main()
