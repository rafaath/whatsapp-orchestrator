"""Behavioral checks use synthetic chats and fake transports. They never send WhatsApp messages."""

import json
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import requests

from control.engine import Engine, whatsapp
from control.server import API, Server, make_handler
from control.store import Store, local_epoch, next_due

OWNER="15550000001@s.whatsapp.net"
PERSON="15550000002@s.whatsapp.net"
OTHER="15550000003@s.whatsapp.net"
GROUP="123456789@g.us"


class FakeBridge:
    def __init__(self):self.sent=[];self.status="acknowledged";self.owner_only=True
    def call(self,route,data=None,**_):
        if route=="status":return {"authenticated":True,"connected":True,"owner_jid":OWNER,"owner_lid":"10001@lid","tail":0}
        if route.startswith("group"):return {"owner_only":self.owner_only}
        if route=="send":self.sent.append(data);return {"status":self.status,"message_id":"FAKE"}
        return {"events":[]}


class FakeCodex:
    def __init__(self):self.ws=None;self.error="";self.calls=[]
    def inspect(self,thread):
        if not thread:raise ValueError("Missing thread")
        return {"id":thread,"status":{"type":"idle"}}
    def relay(self,*args):self.calls.append(args);return "turn-test"
    def draft(self,prompt,*args):self.calls.append(prompt);return "Fixture draft"
    def close(self):pass


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.bridge=FakeBridge();self.codex=FakeCodex()
        self.engine=Engine(self.root,self.bridge,self.codex);self.store=self.engine.store
        self.engine.owner=OWNER;self.engine.owner_lid="10001@lid"
        self.engine.connection=self.bridge.call("status")
        self.api=API(self.engine)
        dbfile=self.root/"messages.db"
        with sqlite3.connect(dbfile) as db:
            db.executescript("CREATE TABLE contacts(jid TEXT PRIMARY KEY,name TEXT,phone_number TEXT); CREATE TABLE messages(id TEXT,chat_jid TEXT,content TEXT,is_from_me INTEGER,timestamp TEXT);")
            db.executemany("INSERT INTO contacts VALUES(?,?,?)",[(OWNER,"Me","15550000001"),(PERSON,"Rohan Client","15550000002"),(OTHER,"Rohan Client","15550000003"),("10002@lid","Rohan Client","15550000002")])
        self.patch=patch.object(whatsapp,"MESSAGES_DB_PATH",str(dbfile));self.patch.start();self.addCleanup(self.patch.stop)
        self.store.execute("INSERT INTO sources VALUES(?,?,'self',1,0,0,'')",(OWNER,"Me"))
        self.store.set("target_thread","thread-test")

    def event(self,**fields):
        return dict({"seq":1,"message_id":"EV1","chat_jid":OWNER,"sender_jid":OWNER,"self_chat":True,"from_owner":True,"group":False,"sent_at":time.time(),"received_at":time.time(),"text":"pineapple"},**fields)

    def schedule(self,**kwargs):
        date=datetime.fromtimestamp(time.time()+300,timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        data={"recipients":[{"jid":PERSON,"name":"Rohan"}],"message":"Hello {{name}}","local_datetime":date,"timezone":"UTC","request_id":"a"*32,"stop_on_reply":True}
        data.update(kwargs)
        ids=self.store.create_schedules(data)
        return self.store.rows("SELECT * FROM schedules WHERE id=?",(ids[0],))[0],data

    def test_live_admission_deduplicates_and_checks_owner(self):
        self.engine.admit(self.event())
        self.engine.admit(self.event())
        self.engine.admit(self.event(seq=2,message_id="EV2",from_owner=False))
        self.engine.admit(self.event(seq=3,message_id="EV3",sent_at=time.time()-900))
        self.engine.admit(self.event(seq=4,message_id="EV4",sent_at=time.time()+900))
        self.engine.admit(self.event(seq=5,message_id="EV5",chat_jid=PERSON,self_chat=False))
        self.assertEqual(len(self.store.rows("SELECT * FROM jobs")),1)
        self.assertEqual(self.store.settings()["cursor"],5)

    def test_source_activation_does_not_authorize_old_events(self):
        self.store.execute("UPDATE sources SET enabled_at=?",(time.time(),))
        self.engine.admit(self.event(sent_at=time.time()-100))
        self.assertFalse(self.store.rows("SELECT * FROM jobs"))

    def test_lid_self_chat_maps_to_owner(self):
        self.engine.admit(self.event(chat_jid="10001@lid",sender_jid="10001@lid"))
        self.assertEqual(self.store.rows("SELECT source FROM jobs")[0]["source"],OWNER)

    def test_watch_contact_uses_known_lid_mapping(self):
        self.api.post("/api/sources", {"jid":PERSON,"kind":"watch","label":"Fixture"})
        self.engine.admit(self.event(chat_jid="10002@lid",sender_jid="10002@lid",self_chat=False,from_owner=False,text="Please help"))
        job = self.store.rows("SELECT source FROM jobs")[0]
        self.assertEqual(job["source"],PERSON)
        self.assertEqual(self.engine.source("10002@lid")["kind"],"watch")
        self.api.post("/api/sources", {"jid":"10002@lid","kind":"delegate","label":"Fixture"})
        self.assertEqual(self.engine.source(PERSON)["kind"],"delegate")
        self.assertEqual(len(self.store.rows("SELECT jid FROM sources WHERE enabled=1 AND kind IN ('watch','delegate')")),1)
        self.assertFalse(self.bridge.sent)

    def test_control_group_is_rechecked(self):
        self.store.execute("INSERT INTO sources VALUES(?,?,'group',1,0,0,'')",(GROUP,"Notes"))
        self.bridge.owner_only=False
        with self.assertRaisesRegex(ValueError,"another participant"):self.engine.verify_source(self.engine.source(GROUP))
        self.assertIsNone(self.engine.source(GROUP))
        self.assertFalse(self.bridge.sent)

    def test_toggle_routes_and_off_cancels_queued_prompts(self):
        self.engine.dispatch(self.engine.source(OWNER),"pineapple","first")
        self.assertGreater(self.engine.source(OWNER)["relay_until"],time.time())
        self.engine.dispatch(self.engine.source(OWNER),"Please help with this","second")
        self.assertEqual(len(self.store.rows("SELECT * FROM jobs WHERE kind='relay' AND state='pending'")),1)
        self.engine.dispatch(self.engine.source(OWNER),"orange","third")
        self.assertEqual(self.engine.source(OWNER)["relay_until"],0)
        self.assertEqual(self.store.rows("SELECT state FROM jobs WHERE kind='relay'")[0]["state"],"canceled")
        self.assertFalse(self.bridge.sent)

    def test_off_is_processed_while_codex_submission_is_waiting(self):
        self.store.execute("UPDATE sources SET relay_until=?",(time.time()+500,))
        self.store.job(OWNER,"relay",{"text":"fixture"})
        entered, release = threading.Event(), threading.Event()
        def blocked(*_):
            entered.set();release.wait(5);return "turn-test"
        with patch.object(self.codex,"relay",side_effect=blocked):
            relay = threading.Thread(target=self.engine.relays)
            commands = threading.Thread(target=self.engine.commands)
            relay.start();commands.start()
            try:
                self.assertTrue(entered.wait(3))
                self.store.job(OWNER,"command",{"event":self.event(text="pc: off")})
                deadline = time.monotonic()+3
                while self.engine.source(OWNER)["relay_until"] and time.monotonic()<deadline:
                    time.sleep(.02)
                self.assertEqual(self.engine.source(OWNER)["relay_until"],0)
                self.assertFalse(self.bridge.sent)
            finally:
                self.engine.stop.set();release.set();relay.join(3);commands.join(3)

    def test_ambiguous_contact_does_not_draft_until_selected(self):
        self.engine.resolve_draft(OWNER,"Rohan Client","keep it short","event-a")
        self.assertFalse(self.store.rows("SELECT * FROM jobs WHERE kind='draft'"))
        self.engine.dispatch(self.engine.source(OWNER),"pc: select 2","select-a")
        drafts=self.store.rows("SELECT * FROM jobs WHERE kind='draft'")
        self.assertEqual(len(drafts),1)
        self.assertEqual(json.loads(drafts[0]["payload"])["feedback"],"keep it short")
        self.assertFalse(self.bridge.sent)

    def test_fuzzy_contact_requires_selection(self):
        self.engine.resolve_draft(OWNER,"Rhon Clent","","event-a")
        self.assertTrue(self.store.rows("SELECT * FROM choices"))
        self.assertFalse(self.store.rows("SELECT * FROM jobs WHERE kind='draft'"))

    def test_context_is_bounded_and_includes_notes_and_revision(self):
        with sqlite3.connect(whatsapp.MESSAGES_DB_PATH) as db:
            db.executemany("INSERT INTO messages VALUES(?,?,?,?,?)",[(str(i),PERSON,"message"+str(i),0,"2026-01-01T00:00:%02d.000000000Z"%i) for i in range(40)])
        self.store.set("profile","We build restaurant tools.")
        self.store.execute("INSERT INTO contacts VALUES(?,?,?,0,'')",(PERSON,"Rohan","Discussed a demo"))
        self.store.execute("INSERT INTO drafts VALUES('draft','job',?,?,?,? ,1,?)",(OWNER,PERSON,"Rohan","Earlier draft",time.time()))
        ctx=self.engine.context(PERSON)
        self.assertEqual(len(ctx["untrusted_chat_history"]),30)
        self.assertEqual(ctx["contact_notes"],"Discussed a demo")
        self.assertEqual(ctx["previous_draft"],"Earlier draft")

    def test_schedule_snapshot_idempotency_and_personalization(self):
        schedule,data=self.schedule()
        self.assertEqual(schedule["message"],"Hello Rohan")
        self.assertEqual(self.store.create_schedules(data),[schedule["id"]])
        with self.assertRaisesRegex(ValueError,"different content"):self.store.create_schedules(dict(data,message="Changed"))

    def test_multiple_recipients_have_reviewable_spacing(self):
        first,data=self.schedule(recipients=[{"jid":PERSON,"name":"First"},{"jid":OTHER,"name":"Second"}])
        rows=self.store.rows("SELECT * FROM schedules ORDER BY due")
        self.assertEqual(rows[1]["due"]-rows[0]["due"],30)

    def test_weekdays_and_end_date_validate_each_first_send(self):
        from datetime import timedelta
        today=datetime.now(timezone.utc).date()
        saturday=today+timedelta(days=(5-today.weekday())%7+7)
        with self.assertRaisesRegex(ValueError,"weekday"):
            self.schedule(local_datetime=saturday.isoformat()+"T09:00",recurrence="weekdays")
        date=(today+timedelta(days=5)).isoformat()
        with self.assertRaisesRegex(ValueError,"end date"):
            self.schedule(local_datetime=date+"T23:59:45",until_date=date,recipients=[{"jid":PERSON,"name":"First"},{"jid":OTHER,"name":"Second"}])
        self.assertFalse(self.store.rows("SELECT id FROM schedules"))

    def test_preview_must_be_rechecked_after_sending_preferences_change(self):
        _,data=self.schedule()
        data["request_id"]="b"*32
        preview=self.api.post("/api/schedules/preview",data)
        data.update(reviewed_spacing=preview["reviewed_spacing"],reviewed_grace=preview["reviewed_grace"])
        self.store.set("spacing_seconds",60)
        with self.assertRaisesRegex(ValueError,"preferences changed"):
            self.api.post("/api/schedules",data)

    def test_suffix_search_is_literal_and_deduplicates_known_aliases(self):
        self.assertEqual(len(self.engine.contacts("Client",suffix=True)),2)
        self.assertFalse(self.engine.contacts("%",suffix=True))

    def test_delegated_contact_cannot_issue_owner_commands(self):
        self.api.post("/api/sources",{"jid":PERSON,"kind":"delegate","label":"Fixture"})
        self.engine.dispatch(self.engine.source(PERSON),"pc: on","delegated-event")
        jobs=self.store.rows("SELECT * FROM jobs")
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]["kind"],"workflow")
        self.assertEqual(json.loads(jobs[0]["payload"])["recipient"],PERSON)
        self.assertEqual(self.engine.source(PERSON)["relay_until"],0)
        self.assertFalse(self.codex.calls)
        self.assertFalse(self.bridge.sent)

    def test_send_is_only_attempted_once_even_after_uncertainty(self):
        self.bridge.status="uncertain"
        first=self.engine.deliver("attempt123",PERSON,"hello")
        again=self.engine.deliver("attempt123",PERSON,"hello")
        self.assertEqual(first["status"],"uncertain");self.assertEqual(again["status"],"uncertain")
        self.assertEqual(len(self.bridge.sent),1)
        with self.assertRaises(ValueError):self.engine.deliver("attempt123",PERSON,"different")

    def test_control_reply_retries_only_definite_non_sends(self):
        self.engine.reply(OWNER,"fixture","reply-test")
        job=self.engine.claim(("reply",))
        with patch.object(self.bridge,"call",side_effect=ConnectionError("Offline")), patch.object(self.engine.stop,"wait",return_value=False):
            self.engine.deliver_reply(job,json.loads(job["payload"]))
        self.assertEqual(self.store.rows("SELECT state FROM jobs")[0]["state"],"pending")
        self.bridge.status="uncertain"
        job=self.engine.claim(("reply",))
        self.engine.deliver_reply(job,json.loads(job["payload"]))
        self.assertEqual(self.store.rows("SELECT state FROM jobs")[0]["state"],"uncertain")
        self.assertIsNone(self.engine.claim(("reply",)))
        self.assertEqual(len(self.bridge.sent),1)

    def test_multibyte_message_limit_is_checked_before_sending(self):
        with self.assertRaisesRegex(ValueError,"16000-byte"):
            self.engine.deliver("oversize",PERSON,chr(0x0915)*6000)
        self.assertFalse(self.bridge.sent)
        self.assertFalse(self.store.rows("SELECT * FROM attempts"))

    def test_long_control_reply_is_explicitly_shortened(self):
        self.engine.reply(OWNER,chr(0x0915)*6000,"long-reply")
        body=json.loads(self.store.rows("SELECT payload FROM jobs")[0]["payload"])["text"]
        self.assertLessEqual(len(body.encode("utf-8")),16000)
        self.assertIn("Shortened for WhatsApp",body)

    def test_paused_and_missed_schedules_never_send(self):
        schedule,_=self.schedule()
        self.store.execute("UPDATE schedules SET due=?",(time.time()-400,))
        self.store.set("paused",True);self.engine.schedule_tick()
        self.assertFalse(self.bridge.sent)
        self.store.set("paused",False);self.engine.schedule_tick()
        self.assertEqual(self.store.rows("SELECT state FROM schedules")[0]["state"],"missed")
        self.assertFalse(self.bridge.sent)

    def test_recipient_reply_stops_schedule_across_lid_alias(self):
        schedule,_=self.schedule()
        self.store.execute("UPDATE schedules SET due=?",(time.time()-1,))
        stamp=whatsapp.database_timestamp(datetime.now(timezone.utc).isoformat())
        with sqlite3.connect(whatsapp.MESSAGES_DB_PATH) as db:db.execute("INSERT INTO messages VALUES('reply','10002@lid','Interested',0,?)",(stamp,))
        self.engine.schedule_tick()
        self.assertEqual(self.store.rows("SELECT state FROM schedules")[0]["state"],"suppressed")
        self.assertFalse(self.bridge.sent)

    def test_contact_optout_suppresses_all_aliases(self):
        self.engine.admit(self.event(chat_jid="10002@lid",sender_jid="10002@lid",self_chat=False,from_owner=False,text="STOP"))
        self.assertTrue(self.engine.blocked(PERSON))
        self.assertEqual(self.engine.deliver("attempt-a",PERSON,"hello")["status"],"suppressed")
        self.assertFalse(self.bridge.sent)

    def test_due_schedule_ack_and_no_duplicate_next_tick(self):
        schedule,_=self.schedule()
        self.store.execute("UPDATE schedules SET due=?",(time.time()-1,))
        self.engine.schedule_tick();self.engine.schedule_tick()
        self.assertEqual(len(self.bridge.sent),1)
        self.assertEqual(self.store.rows("SELECT state FROM schedules")[0]["state"],"completed")

    def test_restart_marks_inflight_uncertain_and_turns_relay_off(self):
        schedule,_=self.schedule()
        self.store.execute("UPDATE schedules SET state='sending'")
        self.store.execute("INSERT INTO attempts VALUES('send',?,0,?,?,'sending',0,'')",(schedule["id"],PERSON,"hello"))
        self.store.execute("UPDATE sources SET relay_until=?",(time.time()+500,))
        self.store.recover()
        self.assertEqual(self.store.rows("SELECT status FROM attempts")[0]["status"],"uncertain")
        self.assertEqual(self.store.rows("SELECT state FROM schedules")[0]["state"],"needs_review")
        self.assertEqual(self.engine.source(OWNER)["relay_until"],0)
        self.assertFalse(self.bridge.sent)

    def test_dst_and_recurrence_are_wall_clock_correct(self):
        for stamp in ["2026-03-08T02:30","2026-11-01T01:30"]:
            with self.assertRaises(ValueError):local_epoch(stamp,"America/New_York")
        schedule={"due":local_epoch("2026-03-07T09:00","America/New_York"),"zone":"America/New_York","local_time":"09:00:00","recurrence":"daily","until_date":""}
        following=next_due(schedule,schedule["due"])
        self.assertEqual(following-schedule["due"],23*3600)
        after=local_epoch("2026-03-12T12:00","America/New_York")
        self.assertGreater(next_due(schedule,after),after)

    def test_api_validates_role_and_ambiguous_duplicates(self):
        with self.assertRaises(ValueError):self.api.post("/api/sources",{"jid":PERSON,"kind":"self"})
        with self.assertRaises(ValueError):self.api.post("/api/settings",{"spacing_seconds":0})

    def test_worker_recovery_quarantines_claimed_work(self):
        ident = self.store.job(OWNER, "reply", {"text": "hello"})
        self.engine.claim(("reply",))
        self.engine.recover_worker("replies")
        self.assertEqual(self.store.rows("SELECT state FROM jobs WHERE id=?", (ident,))[0]["state"], "interrupted")
        self.assertFalse(self.bridge.sent)

    def test_worker_failure_restarts_without_replaying_work(self):
        name, calls = "drafts", []
        self.engine.worker_health[name] = {"state": "starting", "restarts": 0, "last_error": ""}
        def worker():
            calls.append(True)
            if len(calls) == 1:
                raise sqlite3.OperationalError("storage unavailable")
            self.engine.stop.set()
        with patch.object(self.engine.stop, "wait", return_value=False):
            self.engine.run_worker(name, worker)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.engine.worker_health[name]["restarts"], 1)
        self.assertEqual(self.engine.worker_health[name]["state"], "stopped")

    def test_no_send_when_bridge_unavailable_or_account_changes(self):
        with patch.object(self.bridge, "call", side_effect=ConnectionError("Offline")):
            self.assertEqual(self.engine.deliver("no-send", PERSON, "hello")["status"], "not_sent")
        self.store.set("account_jid", OTHER)
        self.assertEqual(self.engine.deliver("no-send", PERSON, "hello")["status"], "deferred")
        self.assertFalse(self.bridge.sent)
        self.assertFalse(self.store.rows("SELECT * FROM attempts"))

    def test_reserved_off_cannot_be_overridden_by_aliases(self):
        self.store.set("aliases", {"off": "on"})
        self.store.execute("UPDATE sources SET relay_until=?", (time.time()+500,))
        self.engine.dispatch(self.engine.source(OWNER), "pc: off", "reserved-off")
        self.assertEqual(self.engine.source(OWNER)["relay_until"], 0)

    def test_codex_disconnect_marks_submitted_work_for_review(self):
        ident = self.store.job(OWNER, "relay", {"text": "fixture"})
        self.store.update_job(ident, "submitted", thread_id="thread-test", turn_id="turn-test")
        self.engine.notifications.put({"method": "controller/disconnected", "params": {}})
        thread = threading.Thread(target=self.engine.results)
        thread.start()
        try:
            deadline = time.monotonic()+3
            while time.monotonic()<deadline:
                if self.store.rows("SELECT state FROM jobs WHERE id=?",(ident,))[0]["state"]=="interrupted": break
                time.sleep(.02)
            self.assertEqual(self.store.rows("SELECT state FROM jobs WHERE id=?",(ident,))[0]["state"], "interrupted")
            self.assertFalse(self.codex.calls)
            self.assertFalse(self.bridge.sent)
        finally:
            self.engine.stop.set(); thread.join(3)

    def test_http_auth_csrf_and_host_validation(self):
        token="a"*64
        server=Server(("127.0.0.1",0),make_handler(self.api,token))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        url=f"http://127.0.0.1:{server.server_port}"
        with requests.Session() as client:
            client.trust_env=False;client.headers["Host"]="127.0.0.1:8766"
            self.assertEqual(client.get(url+"/api/status").status_code,403)
            self.assertEqual(client.get(url+"/api/outreach").status_code,403)
            self.assertEqual(client.post(url+"/session",json={"token":token},headers={"Origin":"https://bad.example"}).status_code,403)
            login=client.post(url+"/session",json={"token":token},headers={"Origin":"http://127.0.0.1:8766"})
            self.assertEqual(login.status_code,200)
            self.assertEqual(client.get(url+"/api/outreach").json(), {"available":False,"entries":[]})
            self.assertEqual(client.post(url+"/api/outreach",json={},headers={"X-CSRF-Token":login.json()["csrf"]}).status_code,400)
            self.assertEqual(client.post(url+"/api/settings",json={"paused":True}).status_code,403)
            self.assertEqual(client.post(url+"/api/settings",json={"paused":True},headers={"X-CSRF-Token":login.json()["csrf"]}).status_code,200)
            self.assertEqual(client.get(url+"/api/status",headers={"Host":"attacker.example"}).status_code,403)


if __name__=="__main__":unittest.main()
