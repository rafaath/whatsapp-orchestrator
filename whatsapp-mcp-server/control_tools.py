"""Control desk tools use the same authenticated API as the local browser."""

import uuid
from urllib.parse import urlencode

import requests
from whatsapp import STORE_DIR


def call(route, data=None):
    try:
        token=(STORE_DIR/"control-token").read_text().strip()
    except OSError as exc:
        raise RuntimeError("Start Start-ControlDesk.ps1 to use schedules and saved drafting context") from exc
    with requests.Session() as session:
        session.trust_env=False
        response=session.request("GET" if data is None else "POST","http://127.0.0.1:8766/api/"+route,
                                 headers={"Authorization":"Bearer "+token},json=data,timeout=(3,110))
    result=response.json()
    if not response.ok:raise RuntimeError(result.get("error","Control request failed; check its state before repeating it"))
    return result


def register(mcp):
    @mcp.tool(annotations={"readOnlyHint":True})
    def get_automation_status() -> dict:
        """Check the local scheduler, authorized control chats and selected Codex session. Does not read messages."""
        return call("status")

    @mcp.tool(annotations={"readOnlyHint":True})
    def find_contacts_by_suffix(suffix: str) -> list[dict]:
        """Find up to 100 saved contacts whose names end with a suffix. Select exact recipients before scheduling; membership is not dynamic."""
        return call("contacts?"+urlencode({"q":suffix,"suffix":"1"}))

    @mcp.tool(annotations={"readOnlyHint":False,"idempotentHint":False})
    def schedule_messages(recipient_jids: list[str], message: str, local_datetime: str, timezone: str,
                          recurrence: str="once", until_date: str="", stop_on_reply: bool=True,
                          request_id: str="") -> dict:
        """Schedule user-authorized messages to exact saved contacts. Timezone is IANA (e.g. Asia/Kolkata); local_datetime is YYYY-MM-DDTHH:MM. Recurrence: once/daily/weekdays/weekly. Recipients are spaced using desk preferences. Use a unique 32-character alphanumeric request_id and reuse it if the request outcome is uncertain. No LLM runs when the schedule fires."""
        return call("schedules",{"recipients":[{"jid":jid} for jid in recipient_jids],"message":message,"local_datetime":local_datetime,"timezone":timezone,"recurrence":recurrence,"until_date":until_date,"stop_on_reply":stop_on_reply,"request_id":request_id or uuid.uuid4().hex})

    @mcp.tool(annotations={"readOnlyHint":True})
    def list_scheduled_messages(limit: int=20) -> list[dict]:
        """List recent scheduled messages with exact recipients, times and outcomes; limit 1..100."""
        return call("schedules?"+urlencode({"limit":limit}))

    @mcp.tool(annotations={"readOnlyHint":False})
    def change_scheduled_message(schedule_id: str, action: str, local_datetime: str="", message: str="") -> dict:
        """Pause, resume, cancel or edit an existing schedule. Edited schedules are paused for review. An uncertain send cannot be rescheduled automatically."""
        data={"id":schedule_id,"action":action}
        if local_datetime:data["local_datetime"]=local_datetime
        if message:data["message"]=message
        return call("schedules/action",data)

    @mcp.tool(annotations={"readOnlyHint":False})
    def request_reply_draft(recipient_jid: str, feedback: str="") -> dict:
        """Request an asynchronous draft using the selected chat, saved brief, notes and previous draft. This does not send the draft to the contact. Read the result with list_reply_drafts."""
        return call("drafts",{"recipient":recipient_jid,"feedback":feedback})

    @mcp.tool(annotations={"readOnlyHint":True})
    def list_reply_drafts(recipient_jid: str="", limit: int=10) -> list[dict]:
        """Read saved drafts for the requested contact, or recent drafts when no contact is supplied."""
        return call("drafts?"+urlencode({"recipient":recipient_jid,"limit":limit}))

    @mcp.tool(annotations={"readOnlyHint":False,"idempotentHint":True,"openWorldHint":True})
    def send_reply_draft(draft_id: str) -> dict:
        """Send this exact saved draft after the user authorizes it. Uses its persisted recipient and an idempotency key. Uncertain attempts are never sent again automatically."""
        return call("drafts/send",{"id":draft_id})

    @mcp.tool(annotations={"readOnlyHint":True})
    def get_saved_business_context(contact_jid: str="") -> dict:
        """Read the user's saved business brief and optional contact notes, so a fresh Codex chat can give a contextual answer."""
        return call("context?"+urlencode({"jid":contact_jid}))

    @mcp.tool(annotations={"readOnlyHint":False})
    def update_business_brief(brief: str) -> dict:
        """Replace the saved business brief with facts and preferences the user asked to remember. Do not silently infer promises, prices or confidential details."""
        return call("settings",{"profile":brief})

    @mcp.tool(annotations={"readOnlyHint":False})
    def select_forwarding_session(thread_id: str) -> dict:
        """Select the exact Codex conversation that authorized owner WhatsApp chats will forward to when forwarding is enabled. Verifies the session; does not turn forwarding on."""
        return call("settings",{"target_thread":thread_id})
