# WhatsApp control desk

Status: implemented locally, with verification evidence below. This document records the decisions and verification boundaries for this local personal tool.

## Requested flows

- Owner self-chat can toggle forwarding to a selected Codex conversation. Aliases are editable; pineapple and orange initially toggle the same mode. A dedicated `pc: off` always disables forwarding.
- `mango <contact> | <optional feedback>` requests a draft using bounded chat history, a saved business brief, contact notes, and the previous draft. Ambiguous contacts require an explicit selection; fuzzy matches are suggestions, never permission to send.
- A local browser desk manages control chats, aliases, Codex conversation targets, context, drafts, scheduled messages, recurring schedules, suppression, and activity.
- Schedule recipient lists are explicit snapshots. Contact suffix searches help select recipients; future contacts do not silently enter an existing schedule.
- Custom prompt workflows use the same authorization and job queue. A new computer action requires an explicit handler and validated inputs at the dispatch boundary, not another independent watcher script. Incoming text is never evaluated as code.
- Additional owner-only groups such as Notes can be enabled after checking exact group identity and membership. Delegated contacts can be enabled explicitly for bounded draft workflows; they do not inherit the owner's computer access.

## Runtime design

One existing Go whatsmeow bridge maintains WhatsApp. It persists a live-event journal separately from history sync and offers authenticated long polling, account identity, owner-group verification, and durable text send attempts. A Python service uses SQLite and the standard-library HTTP server. It serves static HTML/CSS/JavaScript; no frontend build process, database server, Redis, or always-running model loop.

Seven lightweight threads cover live events, command dispatch, Codex submission, draft generation, schedule dispatch, Codex results, and outgoing control replies. Toggling forwarding off does not wait behind a long model response or a pending Codex submission. Only one draft/workflow generation runs at a time. Jobs and message attempts survive restart. A crash after a send starts produces an uncertain status, not an automatic duplicate. A process lock prevents two controllers from dispatching the same queue. Queue sizes and context sizes are bounded.

The desk listens on loopback only. A separate session token authenticates the UI and MCP management API. Browser cookies are HttpOnly, SameSite=Strict. Mutations require same origin and a CSRF header. HTML uses textContent for user data and a restrictive content security policy. Neither WhatsApp session credentials nor Codex credentials reach browser JavaScript.

## Codex integration evidence

Observed locally: Codex CLI 0.159.2, managed app-server 0.160.0, existing Unix control socket. The protocol JSON schemas were generated into the ignored `.cache/codex-protocol` folder with the installed CLI.

Official source, fetched through the user's Agent Reach/Jina route on 2026-10-04: https://developers.openai.com/codex/app-server/

The documented app-server API supports thread/read, thread/resume, thread/start, turn/start, turn/steer, turn/completed, and thread/unsubscribe. Unix transport uses a WebSocket HTTP upgrade; `codex app-server proxy` proxies bytes and is not itself a JSONL client. A maintained WebSocket client is preferable to implementing framing here. App-server transport is documented as experimental; this adapter must report failures explicitly and remain replaceable.

Live verification found that ephemeral threads cannot be resumed from a rollout, and a newly created persistent thread can temporarily have an empty rollout before its first turn completes. Drafts therefore use thread/start directly and remain ephemeral; forwarding resumes an existing selected conversation. A bounded fixture test completed an initial persistent turn, resumed it through the real adapter, submitted a prompt, then submitted another while active. Both submissions returned the same active turn ID and the expected final answer arrived. The fixture thread was archived afterward. The user's actual conversation was checked read-only, not given fabricated phone commands.

The controller attaches to the existing daemon. It does not type into terminals, edit Codex transcript files, or start a second process for the same active thread. Routing is bound to the selected thread ID. A missing thread/daemon is visible and queued inputs expire. It does not silently switch to another conversation. New draft/workflow threads have restricted tools and do not inherit unrestricted PC or messaging permissions. Final replies are routed only to the source that requested them.

## Trust and lifecycle

- Self-chat authorization is based on the linked account's PN/LID identities plus IsFromMe, never on a display name or a keyword alone.
- History, edits, delayed messages outside the configured lifetime, duplicates, and controller-generated replies cannot dispatch commands.
- Adding a source establishes a fresh activation time. Old events are not retrospectively authorized.
- Owner-only groups are checked against current participants before processing and before disclosing results.
- Delegated sources have separately enumerated actions and use their own chat context. They cannot query another contact or turn on owner session forwarding.
- Each message ID and chat ID pair is admitted once. Each outgoing attempt has a caller-supplied idempotency key and a persisted WhatsApp ID reserved before network transmission.
- Read receipts are not sent; locally observed pending replies are not claimed to be WhatsApp's authoritative unread count.
- Client transcripts and fetched material are data, not executable controller commands.

## Scheduling semantics

Timezone is explicit (initial suggestion Asia/Kolkata, editable). Nonexistent or ambiguous local times are rejected so the user can choose an unambiguous time. Supported recurrence: once, daily, weekdays, weekly. Occurrences keep the intended local wall-clock time across daylight-saving changes. Missing occurrences outside the grace period are marked missed, never burst-sent on restart. Offline-before-attempt waits inside the grace period; uncertain attempts never automatically retry. Pausing/canceling stops future occurrences. Stop-on-reply and recipient suppression are checked again immediately before dispatch. Opt-out phrases suppress future automation without treating incoming text as instructions.

## Verification plan

Use synthetic contacts, events, clocks, and Codex responses for authentication, replay, crash recovery, ambiguous names, model isolation, schedule recurrence/DST, pause/cancel, suppression, and duplicate dispatch checks. Check browser behavior on the local desk without sending messages. Perform a read-only Codex connection probe. No real contact campaigns or client messages are created as installation tests. Record actual RAM measurements and any remaining manual restart or phone verification steps.

## Scope boundaries

This change supplies the concrete workflows above and extension points. It does not harvest phone numbers, add unknown contacts, or enable unsolicited autonomous campaigns. A future request to research a business can provide leads for review; message recipients and content still need to be selected. Arbitrary future workflows are not claimed to be implemented. Windows must be awake and WSL/network available; an optional logon task can start the local services, but it cannot execute while the machine is off.

## Startup finding: Windows file permissions

The real Windows launch exposed an existing permissions bug: applying `icacls /inheritance:r` recursively while granting inheritable directory permissions left runtime files with empty ACLs. The owner could no longer read the desk token or open SQLite. The corrected script secures only the parent and resets descendants to inherit that parent's owner/SYSTEM grants. This changes permissions only, not database contents or linked sessions. Worker supervision records recovery state and quarantines claimed work after an unexpected worker failure rather than executing it twice. Runtime data and health are shown through the authenticated API; a rendered static page alone is not a service-health check.

## Verification evidence (2026-10-04 UTC)

- Offline tests exercise owner/PN/LID checks, activation time, stale events, duplicate events, group changes, ambiguous/fuzzy contacts, delegated-role boundaries, bounded context, schedule snapshots, spacing, midnight/end-date boundaries, DST gaps/folds, recurrence, pause/cancel, stop-on-reply, suppression, send uncertainty, worker recovery, Codex disconnection and HTTP authentication/CSRF/Host checks.
- The actual MCP stdio handshake exposes 24 tools; fixture calls pass and invalid inputs are rejected. Existing Python connector tests and Go tests/vet pass without WhatsApp traffic.
- The live desktop desk was inspected through Agent Reach's existing browser. An optional, isolated Chromium harness (`python -m control.check_browser EXISTING_CHROMIUM_PATH`) checks all seven screens at 1440, 390 and 320 pixels, real navigation, overflow, contact selection, personalized preview and confirmation. It uses synthetic contacts and a separate temporary database, not the live WhatsApp store. Screenshots are in ignored `.cache/browser-check/`.
- Browser checks use one existing Chromium process tree, not a newly installed browser or parallel browser farm. UI assets are static and do not need a bundler or development server. Headless temporary sockets use the normal Linux temporary directory to avoid the kernel's Unix-socket path-length limit under a long Windows path.
- Windows runtime ACL checks covered current-user and SYSTEM access only. Autostart is optional and must be registered independently by each operator; no existing logon task ships in a clone.
- Measured steady-state resident memory before the final code reload: approximately 35 MB controller, 41 MB bridge, 31 MB supervisor. These figures exclude Chrome, Codex, filesystem cache, and WSL's VM overhead. The system retained more than 25 GiB available RAM during checks; these are observed measurements, not a hard resource guarantee.
- Each operator must verify their own fresh `pc: status` in their enabled self-chat. A past installation's successful pairing or command check does not verify a new installation.
- A real restricted Codex draft fixture returned its expected short text. Live forwarding transport was verified with isolated synthetic prompts; no real customer transcript was uploaded for testing.
- The final reload reported all seven workers running, the expected linked account connected and the exact selected Codex conversation reachable. Combined resident memory was 104.7 MiB. Tests also cover UTF-8 byte limits and retrying only definite non-sends for short-lived control replies; uncertain outcomes remain non-retriable. Long control answers carry an explicit shortening notice.

## Extension and maintenance notes

1. Keep WhatsApp transport in the Go bridge and business workflows in the controller. Other transports can replace the bridge adapter without taking over Codex internals.
2. Add reusable text tasks from the desk. Add a new effectful action in the dispatcher/worker with input validation, a declared source role, audit records and tests. Never map arbitrary chat text to shell evaluation.
3. Future recipient discovery, richer recurrence, media commands, additional agents and PC delegation to another person are separate enhancements. Do not silently grant these because a source is already allowed to request a draft.
4. Keep saved business facts explicit. No business brief was invented during installation. The owner supplies it in the desk; fresh Codex sessions can retrieve it via the skill and MCP.
5. Send-attempt records are deliberately retained for deduplication. The live bridge event journal is pruned to 30 days at bridge startup. This is a single-account local SQLite service, not an unbounded multi-tenant platform.
6. Restarting the controller turns forwarding off. A target-session change also turns it off and cancels queued relay prompts. Already submitted Codex work may continue and must be checked before retrying.

## Read-only outreach review (2026-10-05)

The desk now has a separate **Outreach review** screen and a shortcut from Today. Direct link: `http://127.0.0.1:8766/?view=outreach` after signing into the local desk. It displays researched introductions and follow-ups, not the automatically generated Reply studio drafts. It does not import anything into `drafts`, `schedules`, `attempts`, or `jobs`.

- Filter new introductions, previously approved introduction wording, follow-ups, unresolved contacts, or all drafts. Search names, businesses, and matched saved contact names.
- Read/copy the exact message, inspect contact checks, proposed recipient and desk-local times, follow-up review dates, requirements, research notes and public source links. Multi-timezone uncertainty is shown explicitly. Past proposals are marked as past when the detail is opened. Review dates are never treated as send dates.
- Snapshot date and individual check timestamps are visible. Contact matches and no-reply observations are historical research results, not live phone address-book or conversation claims. Reload reloads the imported snapshot; it does not rescan WhatsApp or perform new research.
- Wording approval is informational. There are no approve, send or schedule controls on this screen and no outreach mutation API. Existing schedules stay under Scheduled messages and continue operating independently.
- The authenticated, same-origin `GET /api/outreach` reads only `runtime/outreach-review.json`. That file is ignored by Git and inherits the private runtime directory's Windows ACLs. It is not a public static asset. Recipient JIDs, phone numbers and raw filesystem evidence paths are omitted from the display projection.
- Import and load both validate the exact draft SHA-256, explicit timestamps, source URL schemes, contact status, size limits, and review-only flags. An invalid import leaves the previous snapshot intact. A corrupt existing snapshot produces an error, not a misleading empty review.

Import or replace the current review from WSL, without restarting services for subsequent data-only updates:

```bash
# From your own checkout:
.venv/bin/python -m control.outreach /absolute/path/to/review.json
```

The importer validates then atomically copies the snapshot using the Python standard library. It never imports the engine, calls WhatsApp/Codex, changes the original research file, or writes the controller database. Run **Reload review** to see a newly imported snapshot. There are no new dependencies, migrations or background workers. This is one current review snapshot, not an unsolicited campaign manager.

Verification: `python -m unittest control.test_outreach control.test_control -v` covers import idempotency, exact wording/approval retention, malformed input, unsafe URLs, unchanged original and queue files, missing versus corrupt state, auth/CSRF, and rejected outreach writes. The isolated `control.check_browser` harness checks all eight screens at desktop, 390px and 320px; outreach filters, search, timestamps, mobile list/detail navigation, deep links, empty/error recovery, and no queue writes from review. It uses synthetic contacts and a disposable database, not customer sends. Screenshots are under `.cache/browser-check/`.

Account-specific research snapshots and live verification records are not bundled with the source. A new operator imports only their own review data, if wanted. Viewing or importing that data does not change send authority.

## Sharing and manual operation

`Setup-WhatsApp.ps1` and `scripts/setup.sh` recreate ignored dependencies and build output for the new owner without starting services. The bridge derives identity from the phone that owner pairs; no phone number, selected Codex thread, existing client list or business brief is shipped. The source skill resolves its checkout from its symlink. Tokens and account-bound state remain in the ignored private runtime directory.

Follow `AGENTS.md`: the owner starts every server. Use `scripts/check_offline.py` for synthetic tests that do not open listeners; HTTP/MCP/browser tests require owner operation. `scripts/check_share.py` checks the actual staged index for private paths and common credential patterns and does not print values. It is not a complete secret scanner and does not inspect old Git history.

The current Codex adapter uses the current user's default app-server Unix control socket, not a bundled credential. The [official app-server documentation](https://developers.openai.com/codex/app-server/) was checked through Agent Reach's Jina route on 2026-10-09: it documents `--listen unix://`, the WebSocket handshake on Unix sockets, and the experimental status of the app-server transport. The installed CLI help confirmed that option and `codex mcp add NAME -- COMMAND`. Neither help inspection starts a server. Local setup documents the dependency explicitly rather than treating WhatsApp pairing as proof that model-backed keywords work.
