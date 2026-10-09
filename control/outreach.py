"""Read-only outreach snapshots. Importing a review never touches the send queue."""

import argparse
import hashlib
import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

MAX_BYTES = 2 * 1024 * 1024
FILENAME = "outreach-review.json"


def text(value, label, maximum=8000):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"Invalid {label} in outreach review")
    return value


def strings(value, label):
    if not isinstance(value, list) or len(value) > 50:
        raise ValueError(f"Invalid {label} in outreach review")
    return [text(item, label) for item in value]


def timestamp(value):
    value = text(value, "timestamp", 80)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Outreach timestamps need an explicit timezone")
    return value


def web_url(value):
    parsed = urlsplit(text(value, "source link", 4000))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Outreach sources must be public HTTP(S) links")
    return value


def project_review(data):
    """Whitelist display fields; never expose recipient JIDs or import send authority."""
    if not isinstance(data, dict):
        raise ValueError("Expected an outreach review object")
    updated = timestamp(data.get("updated_at_utc") or data.get("as_of_utc"))
    entries, seen = [], set()
    for group, kind in (("remaining_leads", "introduction"), ("followups", "followup")):
        rows = data.get(group)
        if not isinstance(rows, list) or len(rows) > 500:
            raise ValueError(f"Expected up to 500 {group} in outreach review")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid outreach entry")
            if row.get("send_authorized") is not False or row.get("schedule") is not None:
                raise ValueError("Only unscheduled, unauthorized review drafts can be imported")
            ident = text(row.get("id"), "entry ID", 160)
            key = kind + ":" + ident
            if not ident or key in seen:
                raise ValueError("Outreach entry IDs must be nonempty and unique per group")
            seen.add(key)
            body = text(row.get("draft"), "message")
            digest = hashlib.sha256(body.encode()).hexdigest()
            if not body.strip() or row.get("draft_sha256") != digest:
                raise ValueError(f"Message checksum mismatch for {ident}; review the source before importing")
            approved = row.get("draft_approved", False)
            if type(approved) is not bool:
                raise ValueError("Draft approval must be a boolean")
            contact = row.get("contact_status", "not_checked")
            if contact not in {"not_checked", "synced_saved_contact_matches_supplied_number", "not_visible_in_synced_contacts"}:
                raise ValueError("Unknown outreach contact status")
            entry = {"id": key, "kind": kind, "name": text(row.get("name"), "contact name", 300),
                     "organization": text(row.get("organization", ""), "organization", 500),
                     "body": body, "sha256": digest, "wording_approved": approved,
                     "contact_status": contact,
                     "saved_contact_names": strings(row.get("saved_contact_names", []), "saved names"),
                     "blockers": strings(row.get("blockers", row.get("preconditions", [])), "review requirements")}
            for field in ("draft_approval_note", "contact_evidence_note", "history_scope", "research_note",
                          "listed_base", "zone_status", "timing_note"):
                entry[field] = text(row.get(field) or "", field)
            for field in ("contact_checked_at_utc", "history_rechecked_at_utc", "research_checked_at_utc", "last_synced_message"):
                entry[field] = timestamp(row[field]) if row.get(field) else None
            zone = row.get("tentative_iana_zone") or row.get("tentative_iana_zone_from_prior_research")
            if zone:
                ZoneInfo(text(zone, "timezone", 100))
            entry["tentative_zone"] = zone
            entry["review_not_before"] = date.fromisoformat(row["review_not_before"]).isoformat() if row.get("review_not_before") else None
            for field in ("incoming_messages_at_recheck", "outgoing_messages_at_recheck"):
                value = row.get(field)
                if value is not None and (type(value) is not int or value < 0):
                    raise ValueError("Invalid outreach history count")
                entry[field] = value
            urls = strings(row.get("source_urls", []), "sources")
            if row.get("source_url"):
                urls.append(row["source_url"])
            entry["sources"] = list(dict.fromkeys(web_url(url) for url in urls))
            entry["profile_url"] = web_url(row["profile_url"]) if row.get("profile_url") else None
            proposal = row.get("timing_proposal")
            entry["proposal"] = None
            if proposal is not None:
                if not isinstance(proposal, dict):
                    raise ValueError("Invalid timing proposal")
                utc = timestamp(proposal.get("utc"))
                alternatives = proposal.get("local_alternatives") or {}
                if not isinstance(alternatives, dict) or len(alternatives) > 8:
                    raise ValueError("Invalid alternative timezones in outreach review")
                zones = list(alternatives) or [proposal.get("iana_zone")]
                for zone in zones:
                    ZoneInfo(text(zone, "proposal timezone", 100))
                entry["proposal"] = {"utc": utc, "zones": zones,
                                     "basis": text(proposal.get("basis", ""), "timing basis")}
            entries.append(entry)
    return {"available": True, "updated_at": updated, "entries": entries}


def read_source(path):
    with path.open("rb") as file:
        raw = file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Outreach review exceeds the 2 MiB limit")
    try:
        return raw, project_review(json.loads(raw))
    except (TypeError, KeyError) as exc:
        raise ValueError("Invalid outreach review structure; restore a validated review file") from exc


def load_review(root):
    try:
        raw, review = read_source(Path(root)/"runtime"/FILENAME)
    except FileNotFoundError:
        return {"available": False, "entries": []}
    review["revision"] = hashlib.sha256(raw).hexdigest()
    return review


def import_review(source, root):
    raw, review = read_source(Path(source))
    runtime = Path(root)/"runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    # Validate first, then replace atomically so a reader never sees a partial batch.
    fd, temporary = tempfile.mkstemp(prefix="outreach-", suffix=".tmp", dir=runtime)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, runtime/FILENAME)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return len(review["entries"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import a private, read-only outreach review. No sends or schedules.")
    parser.add_argument("source", type=Path, help="Path to the researched review.json")
    args = parser.parse_args()
    count = import_review(args.source, Path(__file__).resolve().parents[1])
    print(f"Imported {count} review drafts. No messages sent or scheduled.")
