"""Loopback-only control desk and authenticated API for the UI and MCP."""

import argparse
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import signal
import threading
import time
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from .engine import Engine, ROOT, whatsapp
from .outreach import load_review
from .store import DEFAULTS, local_epoch, validate_occurrence, validate_wire_text

PORT = 8766


def load_token(path):
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        token = path.read_text().strip()
        if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
            raise RuntimeError("Control token is invalid; restore it before starting")
        return token
    token = secrets.token_hex(32)
    with os.fdopen(fd, "w") as file:
        file.write(token)
    return token


class API:
    def __init__(self, engine):
        self.e = engine
        self.store = engine.store

    def snapshot(self):
        sources = self.store.rows("SELECT * FROM sources ORDER BY kind,label")
        return {"connection": self.e.connection, "codex": {"connected": self.e.codex.ws is not None, "error": self.e.codex.error},
                "workers": self.e.worker_health,
                "settings": self.store.settings(), "sources": sources,
                "workflows": self.store.rows("SELECT * FROM workflows ORDER BY alias"),
                "schedules": self.store.rows("SELECT * FROM schedules ORDER BY CASE WHEN state IN ('pending','paused','needs_review') THEN 0 ELSE 1 END,due DESC LIMIT 200"),
                "drafts": self.store.rows("SELECT d.*,a.status AS send_status FROM drafts d LEFT JOIN attempts a ON a.id='draft-send:'||d.id ORDER BY d.created DESC LIMIT 40"),
                "jobs": self.store.rows("SELECT id,source,kind,state,created,result FROM jobs WHERE kind NOT IN ('reply','command') ORDER BY created DESC LIMIT 40"),
                "audit": self.store.rows("SELECT * FROM audit ORDER BY id DESC LIMIT 50"),
                "attempts": self.store.rows("SELECT a.id,a.recipient,a.status,a.created,a.detail,a.due,s.name FROM attempts a LEFT JOIN schedules s ON s.id=a.schedule_id WHERE a.schedule_id IS NOT NULL ORDER BY a.created DESC LIMIT 50"),
                "contacts": self.store.rows("SELECT * FROM contacts ORDER BY name LIMIT 200"),
                "approvals": self.store.rows("SELECT * FROM approvals WHERE state IN ('pending','desktop_required') ORDER BY created DESC LIMIT 20"),
                "uptime": int(time.time()-self.e.started), "memory_mb": round(rss_mb(),1)}

    def get(self, path, query):
        if path == "/api/snapshot": return self.snapshot()
        if path == "/api/outreach": return load_review(self.e.root)
        if path == "/api/status":
            return {"connection":self.e.connection,"workers":self.e.worker_health,"target_thread":self.store.settings()["target_thread"],"paused":self.store.settings()["paused"],"sources":self.store.rows("SELECT jid,label,kind,enabled,relay_until FROM sources"),"memory_mb":round(rss_mb(),1)}
        if path in {"/api/schedules", "/api/drafts"}:
            limit=int(query.get("limit",["20"])[0])
            if not 1<=limit<=100:raise ValueError("Limit must be 1..100")
            table="schedules" if path.endswith("schedules") else "drafts"
            recipient=query.get("recipient",[""])[0]
            if recipient:return self.store.rows(f"SELECT * FROM {table} WHERE recipient=? ORDER BY created DESC LIMIT ?",(recipient,limit))
            return self.store.rows(f"SELECT * FROM {table} ORDER BY created DESC LIMIT ?",(limit,))
        if path == "/api/contacts": return self.e.contacts(query.get("q", [""])[0], query.get("suffix", ["0"])[0] == "1")
        if path == "/api/groups":
            return [{"jid": c.jid, "name": c.name} for c in whatsapp.list_chats(query=query.get("q", ["Notes"])[0], limit=30, include_last_message=False) if c.is_group]
        if path == "/api/context":
            return {"business_brief": self.store.settings()["profile"], "contact_notes": self.store.rows("SELECT * FROM contacts WHERE jid=?", (query.get("jid", [""])[0],))}
        raise ValueError("Unknown API route")

    def post(self, path, data):
        if path == "/api/settings":
            allowed = set(DEFAULTS)
            if not set(data) <= allowed: raise ValueError("Unknown setting")
            current = self.store.settings()
            changed = dict(current, **data)
            ZoneInfo(changed["timezone"])
            for key, minimum, maximum in (("command_ttl",30,600),("relay_idle",60,14400),("grace_seconds",0,86400),("spacing_seconds",5,3600)):
                if type(changed[key]) is not int or not minimum <= changed[key] <= maximum: raise ValueError(f"{key} must be between {minimum} and {maximum}")
            if type(changed["paused"]) is not bool: raise ValueError("Paused must be a boolean")
            if len(changed["profile"]) > 24000 or len(changed["model"]) > 150: raise ValueError("Context or model name is too long")
            import re
            aliases = changed["aliases"]
            if not isinstance(aliases, dict) or len(aliases)>30: raise ValueError("Use at most 30 aliases")
            for alias,action in aliases.items():
                if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}",alias) or action not in {"toggle","on","off","draft","status","help","schedules"}: raise ValueError("Invalid alias or action")
            workflows = {w["alias"] for w in self.store.rows("SELECT alias FROM workflows")}
            if set(aliases) & workflows: raise ValueError("A command word is already used by a custom workflow")
            if changed["target_thread"] and changed["target_thread"] != current["target_thread"]: self.e.codex.inspect(changed["target_thread"])
            with self.store.db() as db:
                for key,value in data.items(): self.store.set(key,value,db)
                if changed["target_thread"] != current["target_thread"]:
                    db.execute("UPDATE sources SET relay_until=0")
                    db.execute("UPDATE jobs SET state='canceled',result='Target session changed before dispatch' WHERE kind='relay' AND state='pending'")
                self.store.audit("settings", "Control settings updated", db=db)
            return {"saved":True}
        if path == "/api/codex/check": return self.e.codex.inspect(data.get("thread_id") or self.store.settings()["target_thread"])
        if path == "/api/sources":
            jid,kind = str(data.get("jid","")),data.get("kind")
            enabled = bool(data.get("enabled", True))
            if kind == "self":
                if jid != self.e.owner: raise ValueError("Self-chat must be the linked account")
            elif kind == "group":
                if enabled and not self.e.bridge.call("group?jid="+jid)["owner_only"]: raise ValueError("Only a group containing your account alone can be an owner control chat")
                if not jid.endswith("@g.us"): raise ValueError("Select an exact group")
            elif kind in {"watch","delegate"}:
                with whatsapp.open_database() as db:
                    if not db.execute("SELECT 1 FROM contacts WHERE jid=?",(jid,)).fetchone(): raise ValueError("Select an exact saved contact")
                if jid in {self.e.owner,self.e.owner_lid}: raise ValueError("Your own account uses the self-chat role")
            else: raise ValueError("Unknown control chat role")
            thread = str(data.get("thread_id", ""))
            if thread: self.e.codex.inspect(thread)
            old = self.store.rows("SELECT * FROM sources WHERE jid=?",(jid,))
            activated = old[0]["enabled_at"] if old and old[0]["enabled"] and enabled and old[0]["kind"]==kind else time.time()
            with self.store.db() as db:
                if kind in {"watch", "delegate"} and enabled:
                    for alternate in self.e.chat_aliases(jid):
                        db.execute("UPDATE sources SET enabled=0,relay_until=0 WHERE jid=? AND jid<>?", (alternate,jid))
                db.execute("INSERT INTO sources VALUES(?,?,?,?,?,0,?) ON CONFLICT(jid) DO UPDATE SET label=excluded.label,kind=excluded.kind,enabled=excluded.enabled,enabled_at=excluded.enabled_at,relay_until=0,thread_id=excluded.thread_id",(jid,str(data.get("label") or jid)[:150],kind,int(enabled),activated,thread))
            self.store.audit("source","Control chat updated: "+kind,jid)
            return {"saved":True}
        if path == "/api/forwarding/off":
            self.store.execute("UPDATE sources SET relay_until=0")
            self.store.execute("UPDATE jobs SET state='canceled',result='Forwarding stopped from the desk' WHERE kind='relay' AND state='pending'")
            return {"stopped":True}
        if path == "/api/workflows":
            import re
            alias=str(data.get("alias", "")).lower()
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}",alias) or alias in self.store.settings()["aliases"] or alias in {"off","on","send","approve","deny","select","status","help","draft","cancel","schedules","toggle"}: raise ValueError("Choose a unique workflow keyword")
            instructions=str(data.get("instructions", "")).strip()
            if not 1 <= len(instructions) <= 12000: raise ValueError("Provide 1 to 12000 characters of instructions")
            self.store.execute("INSERT INTO workflows VALUES(?,?,?) ON CONFLICT(alias) DO UPDATE SET instructions=excluded.instructions,enabled=excluded.enabled",(alias,instructions,int(bool(data.get("enabled",True)))))
            return {"saved":True}
        if path == "/api/contact":
            jid=str(data.get("jid",""))
            with whatsapp.open_database() as db:
                row=db.execute("SELECT name FROM contacts WHERE jid=?",(jid,)).fetchone()
            if not row: raise ValueError("Select a saved contact")
            notes=str(data.get("notes",""));reason=str(data.get("reason",""))
            if len(notes)>12000 or len(reason)>500: raise ValueError("Contact notes are too long")
            for alternate in self.e.chat_aliases(jid):
                self.store.execute("INSERT INTO contacts VALUES(?,?,?,?,?) ON CONFLICT(jid) DO UPDATE SET name=excluded.name,notes=excluded.notes,suppressed=excluded.suppressed,reason=excluded.reason",(alternate,row[0],notes,int(bool(data.get("suppressed",False))),reason))
            return {"saved":True}
        if path in {"/api/schedules","/api/schedules/preview"}:
            people=[];seen=set()
            for person in data.get("recipients",[]):
                with whatsapp.open_database() as db:
                    row=db.execute("SELECT name,phone_number FROM contacts WHERE jid=?",(person.get("jid"),)).fetchone()
                if not row: raise ValueError("A selected contact no longer exists")
                key=row[1] or person["jid"]
                if key in seen: continue
                seen.add(key)
                if self.e.blocked(person["jid"]): raise ValueError(f"{row[0]} is on your do-not-contact list")
                people.append({"jid":person["jid"],"name":row[0]})
            if not 1<=len(people)<=100: raise ValueError("Select 1 to 100 contacts")
            data=dict(data,recipients=people)
            settings = self.store.settings()
            if "reviewed_spacing" in data and (data["reviewed_spacing"] != settings["spacing_seconds"] or data.get("reviewed_grace") != settings["grace_seconds"]):
                raise ValueError("Sending preferences changed. Review this batch again before confirming")
            if path.endswith("preview"):
                due=local_epoch(data.get("local_datetime",""),data.get("timezone",self.store.settings()["timezone"]))
                body=str(data.get("message", "")).strip()
                if due<time.time() or not body or len(body)>8000: raise ValueError("Enter a message and a future send time")
                recipients=[dict(p,message=body.replace("{{name}}",p["name"]),due=due+i*settings["spacing_seconds"]) for i,p in enumerate(people)]
                for p in recipients:
                    validate_occurrence(p["due"],data.get("timezone",settings["timezone"]),data.get("recurrence","once"),data.get("until_date",""))
                    if len(p["message"])>8000: raise ValueError("Personalized message is too long")
                    validate_wire_text(p["message"])
                return {"recipients":recipients,"timezone":data.get("timezone",settings["timezone"]),"recurrence":data.get("recurrence","once"),"reviewed_spacing":settings["spacing_seconds"],"reviewed_grace":settings["grace_seconds"]}
            return {"ids":self.store.create_schedules(data)}
        if path == "/api/schedules/action":
            self.store.change_schedule(str(data.get("id","")),data.get("action"),data)
            return {"saved":True}
        if path == "/api/drafts": return {"job_id":self.e.request_draft(data.get("recipient",""),str(data.get("feedback","")))}
        if path == "/api/drafts/send": return self.e.send_draft(str(data.get("id","")))
        if path == "/api/jobs/cancel":
            self.store.execute("UPDATE jobs SET state='canceled',result='Canceled before execution' WHERE id=? AND state='pending'",(data.get("id"),))
            return {"saved":True}
        if path == "/api/approvals":
            self.e.decide_approval(str(data.get("id","")),data.get("approve") is True)
            return {"saved":True}
        raise ValueError("Unknown API route")


def rss_mb():
    try:
        with open("/proc/self/status") as file:
            for line in file:
                if line.startswith("VmRSS:"): return int(line.split()[1])/1024
    except OSError: pass
    return 0


def make_handler(api, token):
    session = hmac.new(token.encode(),b"browser-session",hashlib.sha256).hexdigest()
    csrf = hmac.new(token.encode(),b"browser-csrf",hashlib.sha256).hexdigest()
    web = ROOT/"control/web"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass

        def respond(self,value,status=200,headers=None,kind="application/json"):
            body=json.dumps(value,ensure_ascii=False).encode() if kind=="application/json" else value
            self.send_response(status)
            for key,val in {"Content-Type":kind,"Content-Length":str(len(body)),"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer","Content-Security-Policy":"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",**(headers or {})}.items():self.send_header(key,val)
            try:
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionError, TimeoutError):
                pass  # Closing a browser tab is not a controller failure.

        def guard(self,write=False):
            host=self.headers.get("Host","")
            if host not in {f"127.0.0.1:{PORT}",f"localhost:{PORT}"}: raise PermissionError("Use the local desk address")
            origin=self.headers.get("Origin")
            if origin and origin != "http://"+host: raise PermissionError("Cross-origin requests are not allowed")
            authorization=self.headers.get("Authorization","")
            if authorization and hmac.compare_digest(authorization,"Bearer "+token):return
            cookie=SimpleCookie()
            try:cookie.load(self.headers.get("Cookie",""))
            except Exception:raise PermissionError("Open the desk from Start-ControlDesk.ps1")
            if "wa_desk" not in cookie or not hmac.compare_digest(cookie["wa_desk"].value,session):raise PermissionError("Open the desk from Start-ControlDesk.ps1 to sign in")
            if write and not hmac.compare_digest(self.headers.get("X-CSRF-Token",""),csrf):raise PermissionError("Refresh the desk before saving")

        def do_GET(self):
            try:
                parsed=urlparse(self.path)
                if parsed.path in {"/","/app.js","/style.css"}:
                    if self.headers.get("Host","") not in {f"127.0.0.1:{PORT}",f"localhost:{PORT}"}:raise PermissionError("Invalid host")
                    name={"/":"index.html","/app.js":"app.js","/style.css":"style.css"}[parsed.path]
                    kind={"/":"text/html; charset=utf-8","/app.js":"text/javascript; charset=utf-8","/style.css":"text/css; charset=utf-8"}[parsed.path]
                    self.respond((web/name).read_bytes(),kind=kind);return
                self.guard()
                if parsed.path=="/api/session":self.respond({"csrf":csrf});return
                self.respond(api.get(parsed.path,parse_qs(parsed.query)))
            except PermissionError as exc:self.respond({"error":str(exc)},403)
            except (ValueError,KeyError) as exc:self.respond({"error":str(exc)},400)
            except Exception as exc:self.respond({"error":str(exc)[:500]},503)

        def do_POST(self):
            try:
                if self.path!="/session":self.guard(write=True)
                length=int(self.headers.get("Content-Length","0"))
                if not 0<length<=128*1024 or self.headers.get("Content-Type","").split(";")[0]!="application/json":raise ValueError("Provide a JSON body below 128 KiB")
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict):raise ValueError("Expected a JSON object")
                if self.path=="/session":
                    host=self.headers.get("Host","")
                    if host not in {f"127.0.0.1:{PORT}",f"localhost:{PORT}"} or self.headers.get("Origin")!="http://"+host:raise PermissionError("Open the local desk")
                    if not hmac.compare_digest(str(data.get("token","")),token):raise PermissionError("Invalid desk access key")
                    self.respond({"csrf":csrf},headers={"Set-Cookie":f"wa_desk={session}; HttpOnly; SameSite=Strict; Path=/"});return
                self.respond(api.post(self.path,data))
            except PermissionError as exc:self.respond({"error":str(exc)},403)
            except (ValueError,KeyError,TypeError) as exc:self.respond({"error":str(exc)},400)
            except Exception as exc:self.respond({"error":str(exc)[:500]},503)

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads=True
    request_queue_size=16

    def __init__(self,*args,**kwargs):
        self.slots=threading.BoundedSemaphore(12)
        super().__init__(*args,**kwargs)

    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request);return
        request.settimeout(20)
        try:super().process_request(request,address)
        except Exception:self.slots.release();raise

    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--thread",help="Initial Codex conversation ID")
    args=parser.parse_args()
    runtime=ROOT/"runtime"
    runtime.mkdir(exist_ok=True)
    lock=(runtime/"control.lock").open("a+")
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit("The control desk is already running on http://127.0.0.1:8766")
    token=load_token(runtime/"control-token")
    engine=Engine()
    if args.thread and not engine.store.settings()["target_thread"]:
        engine.codex.inspect(args.thread)
        engine.store.set("target_thread",args.thread)
    server=Server(("127.0.0.1",PORT),make_handler(API(engine),token))
    engine.start()
    def shutdown(*_):
        engine.close()
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,shutdown)
    signal.signal(signal.SIGINT,shutdown)
    print("WhatsApp Control Desk: http://127.0.0.1:8766",flush=True)
    print("Use Start-ControlDesk.ps1 to open an authenticated browser window.",flush=True)
    try:server.serve_forever(poll_interval=.5)
    finally:engine.close();server.server_close();lock.close()


if __name__=="__main__":main()
