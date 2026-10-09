"use strict";
const $ = (id) => document.getElementById(id);
let csrf = "", state = null, initial = true, activeTab = "overview", previewData = null, selectedDraftContact = null, selectedSource = null, selectedNotes = null, editId = null;
const recipients = new Map();
const el = (tag, text, className) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (className) n.className = className; return n; };
const empty = (node, text) => { node.replaceChildren(el("p", text, "empty")); };
function toast(message, error = false) { const node = $("toast"); node.textContent = message; node.className = "toast" + (error ? " error" : ""); node.hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => node.hidden = true, error ? 9000 : 4500); }
async function api(path, data) {
  const response = await fetch(path, { method: data === undefined ? "GET" : "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: data === undefined ? undefined : JSON.stringify(data) });
  const result = await response.json(); if (!response.ok) throw new Error(result.error || "The request could not be completed"); return result;
}
async function act(button, fn) { button.disabled = true; try { await fn(); } catch (e) { toast(e.message, true); } finally { button.disabled = false; } }
function button(label, fn, className = "outline") { const node = el("button", label, className); node.type = "button"; node.addEventListener("click", () => act(node, fn)); return node; }
function badge(status) { return el("span", status.replaceAll("_", " "), "badge " + ({ uncertain: "bad", needs_review: "bad", failed: "bad", interrupted: "warn", missed: "warn", paused: "neutral", canceled: "neutral", not_sent: "warn" }[status] || "")); }
function when(epoch, zone = state?.settings.timezone || "Asia/Kolkata") { return new Intl.DateTimeFormat(undefined, { timeZone: zone, dateStyle: "medium", timeStyle: "medium" }).format(new Date(epoch * 1000)); }
function tab(name) {
  if (!$("page-" + name)) return;
  activeTab = name;
  document.querySelectorAll(".page").forEach(n => n.hidden = n.id !== "page-" + name);
  document.querySelectorAll("[data-tab]").forEach(n => { n.classList.toggle("active", n.dataset.tab === name); n.setAttribute("aria-current", n.dataset.tab === name ? "page" : "false"); });
  const url = new URL(location.href); url.searchParams.set("view", name); history.replaceState(null, "", url.pathname + url.search);
  if (name === "outreach" && !outreach) void loadOutreach();
}
document.querySelectorAll("[data-tab],[data-go]").forEach(n => n.addEventListener("click", () => tab(n.dataset.tab || n.dataset.go)));
document.querySelector(".wordmark").addEventListener("click", e => { e.preventDefault(); tab("overview"); });

function picker(id, onSelect, { multi = false, groups = false } = {}) {
  const container = $(id), controls = el("div", undefined, "picker-controls"), input = el("input"), results = el("div", undefined, "picker-results");
  input.placeholder = groups ? "Search groups, e.g. Notes" : "Search saved name or phone"; input.setAttribute("aria-label", groups ? "Search groups" : "Search contacts"); results.hidden = true;
  let suffix = null;
  const search = button("Find", async () => {
    if (!input.value.trim()) { results.hidden = true; return; }
    const list = await api(groups ? "/api/groups?q=" + encodeURIComponent(input.value) : "/api/contacts?q=" + encodeURIComponent(input.value) + "&suffix=" + (suffix?.checked ? "1" : "0"));
    results.replaceChildren(); results.hidden = false;
    if (!list.length) { empty(results, "No saved match. Try a different name."); return; }
    for (const person of list) {
      const row = button("", () => { onSelect(person); if (!multi) results.hidden = true; else row.setAttribute("aria-pressed", recipients.has(person.jid) ? "true" : "false"); }, "person");
      const info = el("span"); info.append(el("span", person.name), el("small", person.phone || person.jid)); row.append(info, el("span", multi ? "Select" : "Choose")); row.setAttribute("aria-pressed", multi && recipients.has(person.jid) ? "true" : "false"); results.append(row);
    }
  });
  controls.append(input, search); container.replaceChildren(controls);
  if (multi) { const label = el("label", undefined, "check picker-suffix"); suffix = el("input"); suffix.type = "checkbox"; label.append(suffix, document.createTextNode("Only names ending with this suffix")); container.append(label); }
  container.append(results);
  input.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); search.click(); } });
  return { input, search };
}
function selectedRecipients() {
  $("schedule-selected").replaceChildren();
  for (const person of recipients.values()) $("schedule-selected").append(button(person.name + " ×", () => { recipients.delete(person.jid); selectedRecipients(); }, "chip"));
  invalidatePreview();
}
function invalidatePreview() { previewData = null; $("schedule-confirm").hidden = true; empty($("schedule-preview"), "Review the current message and selection to see exact send times."); }
picker("schedule-picker", p => { if (recipients.has(p.jid)) recipients.delete(p.jid); else recipients.set(p.jid, p); selectedRecipients(); }, { multi: true });
picker("draft-picker", p => { selectedDraftContact = p; $("draft-recipient").textContent = p.name + " · " + (p.phone || p.jid); });
picker("notes-picker", p => {
  selectedNotes = p; $("notes-selected").textContent = p.name; const saved = state.contacts.find(c => c.jid === p.jid);
  $("contact-notes").value = saved?.notes || ""; $("contact-suppressed").checked = !!saved?.suppressed; $("contact-reason").value = saved?.reason || "";
});
function sourcePicker() {
  selectedSource = null; $("source-selected").textContent = "Choose an exact chat.";
  const kind = $("source-kind").value;
  $("source-help").textContent = { group: "The group must contain only you. Membership is checked before commands and before replies.", watch: "When this contact writes, suggest a reply in your self-chat. Nothing is automatically sent to the contact.", delegate: "This contact receives text answers using their own conversation and your shared business brief. They cannot run PC tasks, schedule sends or access other chats." }[kind];
  picker("source-picker", p => { selectedSource = p; $("source-selected").textContent = p.name + " · " + p.jid; }, { groups: kind === "group" });
}
sourcePicker(); $("source-kind").addEventListener("change", sourcePicker);

function scheduleItem(job, compact = false) {
  const item = el("div", undefined, "item"), head = el("div", undefined, "item-head"); head.append(el("strong", job.name), badge(job.state)); item.append(head, el("small", when(job.due, job.zone) + " · " + job.zone + " · " + job.recurrence));
  item.append(el("p", compact ? job.message.slice(0, 160) : job.message));
  if (!compact) {
    item.append(el("code", job.id.slice(0, 8) + " · " + job.recipient)); const actions = el("div", undefined, "actions");
    for (const [label, action] of [["Pause", "pause"], ["Resume", "resume"], ["Cancel", "cancel"]]) {
      if (action === "pause" && job.state !== "pending" || action === "resume" && job.state !== "paused" || action === "cancel" && !["pending", "paused", "missed", "needs_review"].includes(job.state)) continue;
      actions.append(button(label, async () => { await api("/api/schedules/action", { id: job.id, action }); await refresh(); }));
    }
    if (["pending", "paused", "missed"].includes(job.state)) actions.append(button("Edit time / message", () => {
      editId = job.id; $("edit-zone").textContent = "Local time in " + job.zone; $("edit-date").value = ""; $("edit-message").value = job.message; $("edit-dialog").showModal();
    }));
    if (job.state === "needs_review") item.append(el("p", "The send outcome may be uncertain. Check the contact's chat before scheduling another message.", "field-hint"));
    item.append(actions);
  }
  return item;
}
function renderSchedules() {
  const list = $("schedule-list"); list.replaceChildren(); const rows = state.schedules.filter(x => $("schedule-filter").value === "all" || ["pending", "paused", "missed", "needs_review", "sending", "not_sent"].includes(x.state)).sort((a,b) => a.due-b.due);
  if (!rows.length) empty(list, "No schedules here yet. Your first one starts above."); else rows.forEach(x => list.append(scheduleItem(x)));
}
function renderSources() {
  const list = $("source-list"); list.replaceChildren();
  for (const source of state.sources) {
    const card = el("article", undefined, "panel"), head = el("div", undefined, "item-head"); head.append(el("h3", source.label), badge(source.enabled ? "enabled" : "disabled")); card.append(head, el("p", { self: "Your owner self-chat", group: "Private owner group", watch: "Suggest replies to your self-chat", delegate: "Limited text assistant" }[source.kind]));
    card.append(el("code", source.jid));
    if (["self", "group"].includes(source.kind)) card.append(el("p", source.relay_until > Date.now()/1000 ? "Forwarding to Codex is on." : "Forwarding is off. Send a toggle word to turn it on.", "field-hint"));
    card.append(button(source.enabled ? "Disable chat" : "Enable chat", async () => { await api("/api/sources", { ...source, enabled: !source.enabled }); await refresh(); })); list.append(card);
  }
}
function renderDrafts() {
  const list = $("draft-list"); list.replaceChildren();
  if (!state.drafts.length) empty(list, "Your drafts will live here. Start with one contact and a little direction.");
  for (const draft of state.drafts) {
    const card = el("article", undefined, "panel"), head = el("div", undefined, "item-head"); head.append(el("h3", draft.name), badge(draft.send_status || "draft"));
    card.append(head, el("small", "Revision " + draft.revision + " · " + when(draft.created)), el("div", draft.body, "draft-body"));
    const actions = el("div", undefined, "actions"); actions.append(button("Refine this direction", () => { selectedDraftContact = { jid: draft.recipient, name: draft.name }; $("draft-recipient").textContent = draft.name; $("draft-feedback").focus(); $("draft-form").scrollIntoView({ behavior: "smooth", block: "start" }); }));
    actions.append(button("Copy", async () => { await navigator.clipboard.writeText(draft.body); toast("Draft copied"); }));
    if (!draft.send_status) actions.append(button("Review & send", async () => {
      const dialog = $("confirm-dialog"); $("confirm-title").textContent = draft.name; $("confirm-body").textContent = draft.body; dialog.returnValue = "cancel"; dialog.showModal();
      dialog.addEventListener("close", async () => { if (dialog.returnValue !== "send") return; try { const result = await api("/api/drafts/send", { id: draft.id }); toast("Message status: " + result.status, result.status !== "acknowledged"); await refresh(); } catch(e) { toast(e.message,true); } }, { once: true });
    }, ""));
    card.append(actions); list.append(card);
  }
  const pending = state.jobs.filter(j => ["draft","workflow"].includes(j.kind) && ["pending","running","failed"].includes(j.state)); const jobs = $("draft-jobs"); jobs.replaceChildren();
  pending.slice(0,5).forEach(j => { const row = el("div", undefined, "banner"); row.append(badge(j.state), document.createTextNode(" " + (j.state === "failed" ? j.result : "Your " + j.kind + " is " + j.state + "."))); jobs.append(row); });
}

// Outreach is a display-only snapshot, deliberately separate from reply drafts and send APIs.
let outreach = null, outreachLoading = false, outreachFilter = "new", outreachSelected = null;
const outreachGroups = [
  ["new", "New introductions", r => r.kind === "introduction" && !r.wording_approved],
  ["approved", "Approved wording", r => r.kind === "introduction" && r.wording_approved],
  ["followup", "Follow-ups", r => r.kind === "followup"],
  ["unresolved", "Unresolved contacts", r => r.contact_status === "not_visible_in_synced_contacts"],
  ["all", "All drafts", () => true],
];
const contactLabel = row => ({ synced_saved_contact_matches_supplied_number: "Contact matched", not_visible_in_synced_contacts: "Contact unresolved", not_checked: "Recheck contact" }[row.contact_status]);
const wordingLabel = row => row.wording_approved ? "Wording approved" : row.kind === "followup" ? "Follow-up to review" : "New draft";
const reviewDate = value => when(Date.parse(value) / 1000);
const calendarDate = value => new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "UTC" }).format(new Date(value + "T12:00:00Z"));

async function loadOutreach() {
  if (outreachLoading) return;
  outreachLoading = true; $("outreach-reload").disabled = true; $("outreach-content").setAttribute("aria-busy", "true");
  $("outreach-feedback").textContent = "Loading saved review...";
  try {
    outreach = await api("/api/outreach");
    $("outreach-feedback").replaceChildren();
    $("outreach-content").hidden = !outreach.available;
    if (!outreach.available) {
      $("outreach-snapshot").textContent = "No review snapshot imported yet.";
      empty($("outreach-feedback"), "Import the researched review.json with the local outreach importer, then select Reload review. No send queue is involved.");
      return;
    }
    $("outreach-snapshot").textContent = "Research snapshot: " + reviewDate(outreach.updated_at) + ". Contact matches and replies are recorded checks, not live status.";
    const filters = $("outreach-filters"); filters.replaceChildren();
    for (const [key, label, match] of outreachGroups) {
      const control = button("", () => { outreachFilter = key; outreachSelected = null; $("outreach-workspace").classList.remove("show-detail"); renderOutreach(); }, "outreach-filter");
      control.dataset.filter = key; control.append(el("span", label), el("strong", String(outreach.entries.filter(match).length))); filters.append(control);
    }
    renderOutreach();
  } catch (error) {
    outreach = null; $("outreach-content").hidden = true;
    $("outreach-snapshot").textContent = "The review could not be loaded. No messages or schedules were changed.";
    $("outreach-feedback").replaceChildren(el("p", error.message + ". Use Reload review to try again.", "banner"));
  } finally {
    outreachLoading = false; $("outreach-reload").disabled = false; $("outreach-content").setAttribute("aria-busy", "false");
  }
}

function renderOutreach() {
  if (!outreach?.available) return;
  const query = $("outreach-search").value.trim().normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const match = outreachGroups.find(([key]) => key === outreachFilter)[2];
  const rows = outreach.entries.filter(r => match(r) && (r.name + " " + r.organization + " " + r.saved_contact_names.join(" ")).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().includes(query));
  document.querySelectorAll("[data-filter]").forEach(n => n.setAttribute("aria-pressed", String(n.dataset.filter === outreachFilter)));
  if (!rows.some(r => r.id === outreachSelected)) outreachSelected = rows[0]?.id || null;
  $("outreach-results").textContent = rows.length + " of " + outreach.entries.length + " drafts";
  const list = $("outreach-list"); list.replaceChildren();
  for (const row of rows) {
    const item = el("li"), control = button("", () => {
      outreachSelected = row.id;
      list.querySelectorAll("[data-entry]").forEach(n => n.setAttribute("aria-pressed", String(n.dataset.entry === row.id)));
      renderOutreachDetail(row); $("outreach-workspace").classList.add("show-detail");
      $("outreach-person").focus({ preventScroll: true });
      if (matchMedia("(max-width: 920px)").matches) $("outreach-detail").scrollIntoView({ block: "start" });
    }, "outreach-person");
    control.dataset.entry = row.id; control.setAttribute("aria-pressed", String(row.id === outreachSelected));
    control.append(el("strong", row.name), el("small", row.organization));
    const labels = el("span", undefined, "outreach-labels"); labels.append(badge(wordingLabel(row)), el("span", contactLabel(row), row.contact_status === "not_visible_in_synced_contacts" ? "outreach-held" : "muted"));
    control.append(labels); item.append(control); list.append(item);
  }
  if (!rows.length) { const item = el("li"); empty(item, "No matching drafts. Try another name or filter."); list.append(item); $("outreach-workspace").classList.remove("show-detail"); }
  renderOutreachDetail(rows.find(r => r.id === outreachSelected));
}

function renderOutreachDetail(row) {
  const detail = $("outreach-detail"); detail.replaceChildren();
  if (!row) { empty(detail, "Select a draft to read its message and research."); return; }
  detail.append(button("Back to contacts", () => {
    $("outreach-workspace").classList.remove("show-detail");
    const selected = [...$("outreach-list").querySelectorAll("[data-entry]")].find(n => n.dataset.entry === row.id); selected?.focus();
  }, "outline outreach-back"));
  const head = el("div", undefined, "outreach-detail-head"), intro = el("div");
  intro.append(el("span", row.kind === "followup" ? "A CONSIDERED FOLLOW-UP" : "THE FIRST CONVERSATION", "eyebrow"));
  const title = el("h2", row.name); title.id = "outreach-person"; title.tabIndex = -1;
  intro.append(title, el("p", row.organization)); head.append(intro, badge(wordingLabel(row))); detail.append(head);
  detail.append(el("div", row.body, "draft-body outreach-message"));
  const actions = el("div", undefined, "outreach-copy"); actions.append(button("Copy message", async () => { await navigator.clipboard.writeText(row.body); toast("Exact draft copied. Nothing sent or scheduled."); }), el("span", "Read-only draft. No send action.", "muted")); detail.append(actions);
  if (row.wording_approved) detail.append(el("p", "This wording was approved earlier. The recipient and a future send time still need confirmation.", "field-hint"));

  const facts = el("div", undefined, "outreach-facts"), contact = el("section"), timing = el("section");
  contact.append(el("h3", "Contact check"), el("strong", contactLabel(row)));
  if (row.saved_contact_names.length) contact.append(el("p", row.saved_contact_names.join(" / ")));
  if (row.contact_checked_at_utc) contact.append(el("small", "Checked " + reviewDate(row.contact_checked_at_utc), "muted"));
  if (row.contact_status === "not_visible_in_synced_contacts") contact.append(el("p", "Not found in the synced connection. This does not mean the contact is missing from your phone."));
  if (row.kind === "followup") {
    contact.append(el("p", row.incoming_messages_at_recheck === 0 ? "No incoming reply at the last synced-chat check." : "Incoming replies at last check: " + (row.incoming_messages_at_recheck ?? "not recorded")));
    if (row.history_rechecked_at_utc) contact.append(el("small", "History checked " + reviewDate(row.history_rechecked_at_utc), "muted"));
    contact.append(el("p", "Recheck the conversation before deciding on a follow-up."));
  }
  timing.append(el("h3", "Timing, not a schedule"));
  if (row.proposal) {
    const epoch = Date.parse(row.proposal.utc) / 1000;
    timing.append(badge(epoch <= Date.now() / 1000 ? "Past proposal - choose a new time" : "Proposed only"));
    for (const zone of row.proposal.zones) timing.append(el("p", when(epoch, zone) + " · " + zone));
    const yourZone = state?.settings.timezone || "Asia/Kolkata";
    if (!row.proposal.zones.includes(yourZone)) timing.append(el("small", "Your time: " + when(epoch, yourZone) + " · " + yourZone, "muted"));
    timing.append(el("p", row.proposal.zones.length > 1 ? "Exact recipient timezone is unconfirmed. These are the possible local times, not a confirmed location." : "Based on the researched location, not a verified current location."));
  } else if (row.review_not_before) {
    timing.append(el("strong", "Review from " + calendarDate(row.review_not_before)), el("p", "This is a review date, not an automatic send date. No sending time has been chosen."));
  } else timing.append(el("strong", "Time not selected"), el("p", "Confirm the recipient's timezone and a future local-time slot first."));
  facts.append(contact, timing); detail.append(facts);
  if (row.blockers.length) {
    const section = el("section", undefined, "outreach-requirements"), list = el("ul"); section.append(el("h3", "Before anything is scheduled"));
    row.blockers.forEach(reason => list.append(el("li", reason))); section.append(list); detail.append(section);
  }
  const research = el("details", undefined, "outreach-research"); research.append(el("summary", "Research, sources & verification"));
  if (row.research_note) research.append(el("p", row.research_note));
  if (row.listed_base) research.append(el("p", "Reported location: " + row.listed_base));
  if (row.research_checked_at_utc) research.append(el("p", "Research checked: " + reviewDate(row.research_checked_at_utc)));
  if (row.contact_evidence_note) research.append(el("p", row.contact_evidence_note));
  if (row.history_scope) research.append(el("p", row.history_scope));
  if (row.timing_note) research.append(el("p", row.timing_note));
  if (row.draft_approval_note) research.append(el("p", row.draft_approval_note));
  const sources = el("ul");
  for (const [index, url] of row.sources.entries()) { const item = el("li"), link = el("a", "Source " + (index + 1) + ": " + new URL(url).hostname); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; item.append(link); sources.append(item); }
  if (row.profile_url) { const item = el("li"), link = el("a", "Public profile"); link.href = row.profile_url; link.target = "_blank"; link.rel = "noopener noreferrer"; item.append(link); sources.append(item); }
  research.append(sources, el("p", "Message checksum verified on import and load. The displayed wording is unchanged.", "field-hint")); detail.append(research);
}
$("outreach-search").addEventListener("input", () => { $("outreach-workspace").classList.remove("show-detail"); renderOutreach(); });
$("outreach-reload").addEventListener("click", loadOutreach);
function aliasRow(word = "", action = "draft") {
  const row = el("div", undefined, "alias-row"), input = el("input"), select = el("select"); input.value = word; input.placeholder = "your-word"; input.pattern = "[a-z][a-z0-9_-]{0,31}"; input.required = true; input.setAttribute("aria-label", "Command word"); select.setAttribute("aria-label", "Command action");
  for (const [value,label] of [["toggle","Toggle forwarding"],["on","Forwarding on"],["off","Forwarding off"],["draft","Draft a reply"],["status","Connection status"],["help","Show commands"],["schedules","List schedules"]]) { const option = el("option",label); option.value = value; select.append(option); } select.value = action;
  row.append(input, select, button("Remove", () => row.remove(), "quiet")); $("alias-rows").append(row);
}
function renderActivity() {
  const jobs = $("job-list"); jobs.replaceChildren(); if (!state.jobs.length) empty(jobs,"No work has run yet.");
  for (const job of state.jobs) { const row = el("div", undefined, "item"), head = el("div", undefined, "item-head"); head.append(el("strong", job.kind), badge(job.state)); row.append(head, el("small", when(job.created)), el("p", job.result || "Awaiting result")); if (job.state === "pending") row.append(button("Cancel queued work", async () => { await api("/api/jobs/cancel", { id: job.id }); await refresh(); })); jobs.append(row); }
  const audit = $("activity-list"); audit.replaceChildren(); if (!state.audit.length) empty(audit,"The desk is ready. Future actions will appear here.");
  state.audit.forEach(a => { const row = el("div",undefined,"item"); row.append(el("small",when(a.created)),el("p",a.summary)); audit.append(row); });
  for(const a of state.attempts||[]){const row=el("div",undefined,"item"),head=el("div",undefined,"item-head");head.append(el("strong",a.name||a.recipient),badge(a.status));row.append(head,el("small","Scheduled "+when(a.due)),el("p",a.detail));audit.append(row);}
  const approvals = $("approval-list"); approvals.replaceChildren();
  for (const request of state.approvals) {
    const card = el("article",undefined,"panel"), event = JSON.parse(request.payload), p = event.params;
    card.append(el("span","CODEX NEEDS YOUR DECISION","eyebrow"),el("h2","Review a requested action"),el("p",p.reason || request.method),el("pre", JSON.stringify({command:p.command,cwd:p.cwd,changes:p.fileChanges,grantRoot:p.grantRoot},null,2),"approval-details"));
    if (request.state === "pending") { const actions = el("div",undefined,"actions"); for (const [label,approve] of [["Approve once",true],["Decline",false]]) actions.append(button(label,async()=>{await api("/api/approvals",{id:request.id,approve});await refresh();})); card.append(actions); }
    else card.append(el("p","Answer this request in the Codex desktop session.","field-hint")); approvals.append(card);
  }
}
function render() {
  const { settings } = state, connected = state.connection.connected && state.connection.authenticated;
  const startWord = Object.entries(settings.aliases).find(([, action]) => action === "on" || action === "toggle")?.[0] || "pc: on";
  $("alias-intro").textContent = "Send " + startWord + " in your self-chat to start forwarding messages to your selected Codex session.";
  $("bridge-dot").classList.toggle("connected", !!connected); $("bridge-status").textContent = connected ? "WhatsApp connected" : "Bridge unavailable";
  $("resource-status").textContent = state.memory_mb + " MB controller · " + Math.floor(state.uptime/60) + "m running";
  $("pause-all").textContent = settings.paused ? "Resume scheduled sends" : "Pause scheduled sends";
  const unhealthy = Object.entries(state.workers || {}).filter(([, worker]) => worker.state !== "running");
  $("connection-banner").hidden = connected && !settings.paused && !unhealthy.length;
  $("connection-banner").textContent = unhealthy.length ? "Background work needs attention: " + unhealthy.map(([name, worker]) => name + " (" + worker.state + "): " + worker.last_error).join("; ") : !connected ? (state.connection.message || "The bridge is connecting. If it was already running before the update, restart Start-WhatsApp.ps1.") : "Scheduled sends are paused. You can still draft replies and use control chats.";
  const pending = state.schedules.filter(s => s.state === "pending").sort((a,b) => a.due-b.due);
  $("count-pending").textContent = pending.length; $("count-drafts").textContent = state.drafts.filter(d=>!d.send_status).length; $("count-relays").textContent = state.sources.filter(s=>s.enabled && s.relay_until>Date.now()/1000).length;
  $("up-next").replaceChildren(); if (!pending.length) empty($("up-next"),"Nothing scheduled. A good moment to plan your next conversation."); else pending.slice(0,3).forEach(s=>$("up-next").append(scheduleItem(s,true)));
  const attention = [...state.schedules.filter(s=>["needs_review","missed","not_sent"].includes(s.state)).map(s=>({label:s.name,status:s.state,detail:when(s.due,s.zone)})),...state.jobs.filter(j=>["failed","interrupted"].includes(j.state)).map(j=>({label:j.kind,status:j.state,detail:j.result}))];
  $("attention").replaceChildren(); if (!attention.length) empty($("attention"),"All clear. Anything that needs your decision will appear here.");
  attention.slice(0,8).forEach(a=>{const row=el("div",undefined,"item"),head=el("div",undefined,"item-head");head.append(el("strong",a.label),badge(a.status));row.append(head,el("p",a.detail));$("attention").append(row);});
  $("spacing-hint").textContent = "Recipients are spaced " + settings.spacing_seconds + " seconds apart. A send more than " + settings.grace_seconds + " seconds late is marked missed. Times are shown for every recipient at review.";
  if (initial) {
    $("target-thread").value=settings.target_thread; $("business-profile").value=settings.profile; $("context-zone").value=settings.timezone; $("context-model").value=settings.model; $("context-spacing").value=settings.spacing_seconds; $("context-grace").value=settings.grace_seconds; $("context-ttl").value=settings.command_ttl; $("context-idle").value=settings.relay_idle; $("schedule-zone").value=settings.timezone;
    $("alias-rows").replaceChildren(); Object.entries(settings.aliases).forEach(([word,action])=>aliasRow(word,action)); initial=false;
  }
  renderSchedules(); renderSources(); renderDrafts(); renderActivity();
  $("workflow-list").replaceChildren(); state.workflows.forEach(w=>{const card=el("article",undefined,"panel");card.append(el("h3",w.alias),el("p",w.instructions),button(w.enabled?"Disable":"Enable",async()=>{await api("/api/workflows",{...w,enabled:!w.enabled});await refresh();}),button("Edit",()=>{$("workflow-alias").value=w.alias;$("workflow-instructions").value=w.instructions;$("workflow-alias").focus();},"quiet"));$("workflow-list").append(card);});
  $("suppression-list").replaceChildren(); const suppressed=state.contacts.filter(c=>c.suppressed); if(suppressed.length){const panel=el("article",undefined,"panel");panel.append(el("h2","Do not contact")); suppressed.forEach(c=>{const row=el("div",undefined,"item");row.append(el("strong",c.name),el("p",c.reason||"Automated messages paused for this person"));panel.append(row);});$("suppression-list").append(panel);}
}
let refreshing=false;
async function refresh() { if(refreshing)return;refreshing=true;try{state=await api("/api/snapshot");render();}finally{refreshing=false;} }
function form(id, fn) { $(id).addEventListener("submit", e => { e.preventDefault(); act($(id).querySelector('button[type="submit"]'), fn); }); }
form("schedule-form",async()=>{const data={request_id:crypto.randomUUID().replaceAll("-",""),recipients:[...recipients.values()],message:$("schedule-message").value,local_datetime:$("schedule-date").value,timezone:$("schedule-zone").value,recurrence:$("schedule-repeat").value,until_date:$("schedule-until").value,stop_on_reply:$("schedule-reply").checked};const result=await api("/api/schedules/preview",data);previewData={...data,reviewed_spacing:result.reviewed_spacing,reviewed_grace:result.reviewed_grace};$("schedule-preview").replaceChildren();for(const p of result.recipients){const row=el("div",undefined,"item");row.append(el("strong",p.name),el("small",when(p.due,result.timezone)+" · "+result.timezone+" · "+result.recurrence),el("p",p.message));$("schedule-preview").append(row);}$("schedule-confirm").hidden=false;$("schedule-confirm").textContent="Confirm & schedule "+result.recipients.length+" message(s)";});
$("schedule-form").addEventListener("input",invalidatePreview);
$("schedule-confirm").addEventListener("click",()=>act($("schedule-confirm"),async()=>{if(!previewData)return;const result=await api("/api/schedules",previewData);toast(result.ids.length+" message(s) scheduled");previewData=null;$("schedule-confirm").hidden=true;empty($("schedule-preview"),"Your messages are saved in the schedule below.");await refresh();}));
$("schedule-filter").addEventListener("change",renderSchedules);
form("draft-form",async()=>{if(!selectedDraftContact)throw new Error("Choose a saved contact first");await api("/api/drafts",{recipient:selectedDraftContact.jid,feedback:$("draft-feedback").value});toast("Draft requested. It will appear here when ready.");await refresh();});
form("session-form",async()=>{await api("/api/settings",{target_thread:$("target-thread").value.trim()});toast("Codex conversation saved");await refresh();});
$("check-codex").addEventListener("click",()=>act($("check-codex"),async()=>{const r=await api("/api/codex/check",{thread_id:$("target-thread").value.trim()});$("session-detail").textContent="Connected to "+r.id+" · "+(r.status?.type||"available")+" · "+(r.cwd||"");await refresh();}));
form("source-form",async()=>{if(!selectedSource)throw new Error("Choose an exact chat first");await api("/api/sources",{jid:selectedSource.jid,label:selectedSource.name,kind:$("source-kind").value,enabled:true});toast("Chat enabled");await refresh();});
$("add-alias").addEventListener("click",()=>aliasRow());
form("aliases-form",async()=>{const aliases={};for(const row of $("alias-rows").children){const key=row.querySelector("input").value.trim().toLowerCase();if(aliases[key])throw new Error("Each command word must be unique");aliases[key]=row.querySelector("select").value;}await api("/api/settings",{aliases});toast("Command words saved");await refresh();});
form("workflow-form",async()=>{await api("/api/workflows",{alias:$("workflow-alias").value.trim().toLowerCase(),instructions:$("workflow-instructions").value,enabled:true});toast("Workflow saved");await refresh();});
form("context-form",async()=>{await api("/api/settings",{profile:$("business-profile").value,timezone:$("context-zone").value.trim(),model:$("context-model").value.trim(),spacing_seconds:Number($("context-spacing").value),grace_seconds:Number($("context-grace").value),command_ttl:Number($("context-ttl").value),relay_idle:Number($("context-idle").value)});toast("Business context saved");await refresh();});
form("contact-form",async()=>{if(!selectedNotes)throw new Error("Choose a contact first");await api("/api/contact",{jid:selectedNotes.jid,notes:$("contact-notes").value,suppressed:$("contact-suppressed").checked,reason:$("contact-reason").value});toast("Contact context saved");await refresh();});
$("pause-all").addEventListener("click",()=>act($("pause-all"),async()=>{await api("/api/settings",{paused:!state.settings.paused});await refresh();}));
$("off-all").addEventListener("click",()=>act($("off-all"),async()=>{await api("/api/forwarding/off",{});toast("Forwarding stopped. Work already in Codex may still finish.");await refresh();}));
$("edit-close").addEventListener("click",()=>$("edit-dialog").close());
form("edit-form",async()=>{await api("/api/schedules/action",{id:editId,action:"edit",local_datetime:$("edit-date").value,message:$("edit-message").value});$("edit-dialog").close();toast("Saved as paused. Review and resume when ready.");await refresh();});
async function login(token) { const result=await api("/session",{token});csrf=result.csrf;await ready(); }
form("login-form",async()=>{await login($("access-key").value);$("access-key").value="";});
async function ready(){ $("login").hidden=true;$("app").hidden=false;$("today").textContent=new Intl.DateTimeFormat(undefined,{weekday:"long",month:"long",day:"numeric"}).format(new Date());await refresh();tab(new URLSearchParams(location.search).get("view") || "overview"); }
(async()=>{try{const fragment=new URLSearchParams(location.hash.slice(1));const token=fragment.get("token");history.replaceState(null,"",location.pathname+location.search);if(token)await login(token);else{csrf=(await api("/api/session")).csrf;await ready();}}catch(e){$("app").hidden=true;$("login").hidden=false;$("login-error").textContent=e.message;}})();
setInterval(()=>{if(!document.hidden && !$("app").hidden && !document.querySelector("dialog[open]"))refresh().catch(e=>{$("connection-banner").hidden=false;$("connection-banner").textContent=e.message;});},6000);
