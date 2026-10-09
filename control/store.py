"""Durable controller state. All timestamps are UTC epoch seconds."""

import json
import hashlib
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULTS = {
    "timezone": "Asia/Kolkata", "command_ttl": 180, "relay_idle": 1800,
    "grace_seconds": 300, "spacing_seconds": 30, "paused": False,
    "profile": "", "model": "", "target_thread": "",
    "aliases": {"pineapple": "toggle", "orange": "toggle", "mango": "draft"},
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(
 jid TEXT PRIMARY KEY,label TEXT NOT NULL,kind TEXT NOT NULL,enabled INTEGER NOT NULL,
 enabled_at REAL NOT NULL,relay_until REAL NOT NULL DEFAULT 0,thread_id TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS workflows(alias TEXT PRIMARY KEY,instructions TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY,source TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,
 created REAL NOT NULL,updated REAL NOT NULL,result TEXT NOT NULL DEFAULT '',thread_id TEXT NOT NULL DEFAULT '',turn_id TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(state,created);
CREATE TABLE IF NOT EXISTS events(
 chat TEXT NOT NULL,message_id TEXT NOT NULL,received REAL NOT NULL,outcome TEXT NOT NULL,
 PRIMARY KEY(chat,message_id));
CREATE TABLE IF NOT EXISTS choices(source TEXT PRIMARY KEY,payload TEXT NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS contacts(jid TEXT PRIMARY KEY,name TEXT NOT NULL,notes TEXT NOT NULL DEFAULT '',suppressed INTEGER NOT NULL DEFAULT 0,reason TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,source TEXT NOT NULL,recipient TEXT NOT NULL,name TEXT NOT NULL,body TEXT NOT NULL,revision INTEGER NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,source TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,method TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,payload_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schedules(
 id TEXT PRIMARY KEY,batch TEXT NOT NULL,name TEXT NOT NULL,recipient TEXT NOT NULL,message TEXT NOT NULL,
 due REAL NOT NULL,zone TEXT NOT NULL,local_time TEXT NOT NULL,recurrence TEXT NOT NULL,until_date TEXT NOT NULL,
 state TEXT NOT NULL,grace INTEGER NOT NULL,stop_on_reply INTEGER NOT NULL,created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS schedule_due ON schedules(state,due);
CREATE TABLE IF NOT EXISTS attempts(
 id TEXT PRIMARY KEY,schedule_id TEXT,due REAL,recipient TEXT NOT NULL,message TEXT NOT NULL,
 status TEXT NOT NULL,created REAL NOT NULL,detail TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,created REAL NOT NULL,kind TEXT NOT NULL,summary TEXT NOT NULL,ref TEXT NOT NULL DEFAULT '');
"""


def uid():
    return uuid.uuid4().hex


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def validate_wire_text(text):
    if not text.strip() or len(text.encode("utf-8")) > 16000:
        raise ValueError("Message is empty or exceeds the connector's 16000-byte text limit. Shorten it before sending")


def local_epoch(value, zone):
    """Reject DST gaps/folds instead of silently choosing the wrong send time."""
    try:
        naive = datetime.fromisoformat(value)
        tz = ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("Choose a valid local date/time and IANA timezone, such as Asia/Kolkata") from exc
    if naive.tzinfo is not None:
        raise ValueError("Use a local date/time without an offset, and select its timezone")
    candidates = set()
    for fold in (0, 1):
        aware = naive.replace(tzinfo=tz, fold=fold)
        if aware.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == naive:
            candidates.add(aware.timestamp())
    if len(candidates) != 1:
        raise ValueError("This local time is missing or occurs twice due to daylight saving; choose another time")
    return candidates.pop()


def next_due(schedule, after):
    if schedule["recurrence"] == "once":
        return None
    tz = ZoneInfo(schedule["zone"])
    previous = datetime.fromtimestamp(schedule["due"], tz)
    # Skip missed occurrences rather than releasing a backlog on wake-up.
    date = max(previous.date() + timedelta(days=1), datetime.fromtimestamp(after, tz).date())
    for _ in range(370):
        if schedule["until_date"] and date.isoformat() > schedule["until_date"]:
            return None
        eligible = schedule["recurrence"] != "weekdays" or date.weekday() < 5
        eligible = eligible and (schedule["recurrence"] != "weekly" or date.weekday() == previous.weekday())
        if eligible:
            try:
                candidate = local_epoch(f"{date.isoformat()}T{schedule['local_time']}", schedule["zone"])
                if candidate > after:
                    return candidate
            except ValueError:
                pass  # This occurrence is skipped, never shifted to another wall-clock time.
        date += timedelta(days=1)
    return None


def validate_occurrence(due, zone, recurrence, until):
    local = datetime.fromtimestamp(due, ZoneInfo(zone))
    if recurrence not in {"once", "daily", "weekdays", "weekly"}:
        raise ValueError("Invalid recurrence")
    if recurrence == "weekdays" and local.weekday() >= 5:
        raise ValueError("Choose a weekday for the first send of a weekdays schedule")
    if until:
        datetime.strptime(until, "%Y-%m-%d")
        if len(until) != 10 or local.date().isoformat() > until:
            raise ValueError("A recipient's first send falls after the selected end date")
    return local


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript(SCHEMA)
            for key, value in DEFAULTS.items():
                db.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (key, json_text(value)))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def rows(self, sql, args=()):
        with self.db() as db:
            return [dict(row) for row in db.execute(sql, args)]

    def execute(self, sql, args=()):
        with self.db() as db:
            return db.execute(sql, args).rowcount

    def settings(self):
        return {r["key"]: json.loads(r["value"]) for r in self.rows("SELECT * FROM settings")}

    def set(self, key, value, db=None):
        sql = "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        if db is not None:
            db.execute(sql, (key, json_text(value)))
        else:
            self.execute(sql, (key, json_text(value)))

    def audit(self, kind, summary, ref="", db=None):
        sql, args = "INSERT INTO audit(created,kind,summary,ref) VALUES(?,?,?,?)", (time.time(), kind, summary[:1500], ref)
        if db is not None:
            db.execute(sql, args)
        else:
            self.execute(sql, args)

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE jobs SET state='interrupted',result='Controller restarted during this job; inspect the target session before retrying' WHERE state IN ('running','submitted')")
            db.execute("UPDATE attempts SET status='uncertain',detail='Controller stopped after reserving the attempt; not retried' WHERE status='sending'")
            db.execute("UPDATE schedules SET state='needs_review' WHERE state='sending'")
            db.execute("UPDATE approvals SET state='expired' WHERE state='pending'")
            # Restart never re-enables command forwarding in an unattended chat.
            db.execute("UPDATE sources SET relay_until=0")
            db.execute("DELETE FROM choices WHERE expires<?", (time.time(),))

    def job(self, source, kind, payload, db=None):
        ident, now = uid(), time.time()
        def save(conn):
            control = kind in {"command", "reply"}
            count = conn.execute("SELECT count(*) FROM jobs WHERE state IN ('pending','running','submitted') AND (kind IN ('command','reply'))=?", (int(control),)).fetchone()[0]
            if count >= (500 if control else 100):
                raise ValueError("The work queue is full; finish or cancel pending work first")
            conn.execute("INSERT INTO jobs(id,source,kind,payload,state,created,updated) VALUES(?,?,?,?,'pending',?,?)", (ident, source, kind, json_text(payload), now, now))
        if db is None:
            with self.db() as conn:
                save(conn)
        else:
            save(db)
        return ident

    def update_job(self, ident, state, result="", **fields):
        if not set(fields) <= {"thread_id", "turn_id"}:
            raise ValueError("Unknown job field")
        cols = ",".join(f"{name}=?" for name in fields)
        self.execute("UPDATE jobs SET state=?,result=?,updated=?" + (","+cols if cols else "") + " WHERE id=?", (state, result[:16000], time.time(), *fields.values(), ident))

    def create_schedules(self, data):
        recipients = data.get("recipients", [])
        if not isinstance(recipients, list) or not 1 <= len(recipients) <= 100:
            raise ValueError("Select between 1 and 100 recipients per batch")
        message = str(data.get("message", "")).strip()
        if not message or len(message) > 8000:
            raise ValueError("Message must contain 1 to 8000 characters")
        recurrence = data.get("recurrence", "once")
        if recurrence not in {"once", "daily", "weekdays", "weekly"}:
            raise ValueError("Invalid recurrence")
        zone = data.get("timezone", self.settings()["timezone"])
        due = local_epoch(data.get("local_datetime", ""), zone)
        if due < time.time()-5:
            raise ValueError("Choose a future send time")
        until = data.get("until_date", "")
        if until:
            datetime.strptime(until, "%Y-%m-%d")
            if until < data["local_datetime"][:10]:
                raise ValueError("End date must not be earlier than the first send")
        grace = int(data.get("grace_seconds", self.settings()["grace_seconds"]))
        if not 0 <= grace <= 86400:
            raise ValueError("Grace period must be between 0 and 86400 seconds")
        batch = str(data.get("request_id", uid()))
        if not batch.isalnum() or not 16 <= len(batch) <= 64:
            raise ValueError("Invalid batch identifier")
        now, result = time.time(), []
        spacing = self.settings()["spacing_seconds"]
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        seen = set()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT payload_hash FROM batches WHERE id=?", (batch,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise ValueError("Batch identifier already used for different content")
                return [r[0] for r in db.execute("SELECT id FROM schedules WHERE batch=?", (batch,))]
            db.execute("INSERT INTO batches VALUES(?,?)", (batch, fingerprint))
            for person in recipients:
                jid, name = str(person["jid"]), str(person.get("name") or person["jid"])
                if jid in seen:
                    continue
                seen.add(jid)
                if not jid.endswith(("@s.whatsapp.net", "@lid")) or not jid.split("@")[0].isdigit():
                    raise ValueError("Schedules require exact individual contacts")
                blocked = db.execute("SELECT suppressed FROM contacts WHERE jid=?", (jid,)).fetchone()
                if blocked and blocked[0]:
                    raise ValueError(f"{name} is on your do-not-contact list")
                ident = uid()
                offset_due = due+len(result)*spacing
                wall_time = validate_occurrence(offset_due, zone, recurrence, until).strftime("%H:%M:%S")
                personalized = message.replace("{{name}}", name)
                if len(personalized) > 8000:
                    raise ValueError("Personalized message is too long")
                validate_wire_text(personalized)
                db.execute("INSERT INTO schedules VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (ident, batch, name[:150], jid, personalized, offset_due, zone, wall_time, recurrence, until, "pending", grace, int(bool(data.get("stop_on_reply", True))), now))
                result.append(ident)
            self.audit("schedule", f"Scheduled {len(result)} recipient(s) in {zone}", batch, db=db)
        return result

    def change_schedule(self, ident, action, data=None):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM schedules WHERE id=?", (ident,)).fetchone()
            if not row:
                raise ValueError("Schedule not found")
            if row["state"] == "sending":
                raise ValueError("This send has already started; its outcome will be recorded")
            if action == "cancel":
                db.execute("UPDATE schedules SET state='canceled' WHERE id=?", (ident,))
            elif action == "pause" and row["state"] == "pending":
                db.execute("UPDATE schedules SET state='paused' WHERE id=?", (ident,))
            elif action == "resume" and row["state"] == "paused":
                if row["due"] < time.time():
                    raise ValueError("This time has passed. Reschedule to a future date first")
                db.execute("UPDATE schedules SET state='pending' WHERE id=?", (ident,))
            elif action == "edit" and row["state"] in {"pending", "paused", "missed", "needs_review"}:
                due = local_epoch(data["local_datetime"], row["zone"])
                body = str(data.get("message", row["message"])).strip()
                if due <= time.time() or not 1 <= len(body) <= 8000:
                    raise ValueError("Choose a future time and a nonempty message")
                validate_wire_text(body)
                validate_occurrence(due,row["zone"],row["recurrence"],row["until_date"])
                uncertain = db.execute("SELECT 1 FROM attempts WHERE schedule_id=? AND status='uncertain'", (ident,)).fetchone()
                if uncertain:
                    raise ValueError("This schedule has an uncertain send. Check the chat before creating a new schedule")
                db.execute("UPDATE schedules SET due=?,local_time=?,message=?,state='paused' WHERE id=?", (due, data["local_datetime"][11:19], body, ident))
            else:
                raise ValueError("This action is not available for the schedule's current state")
            self.audit("schedule", f"Schedule {action}", ident, db=db)

    def finish_schedule(self, schedule, status, detail="", now=None):
        now = time.time() if now is None else now
        following = next_due(schedule, now) if status in {"acknowledged", "missed"} else None
        state = "pending" if following else {"acknowledged": "completed", "uncertain": "needs_review", "not_sent": "pending", "deferred": "pending"}.get(status, status)
        self.execute("UPDATE schedules SET state=?,due=? WHERE id=? AND state NOT IN ('canceled','paused')", (state, following or schedule["due"], schedule["id"]))
        self.audit("send", detail or status, schedule["id"])
