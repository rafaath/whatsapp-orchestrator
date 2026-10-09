# Local WhatsApp connection for Codex

This is a fork of `lharries/whatsapp-mcp`, updated for whatsmeow, the Python MCP SDK and a local Control Desk. No restaurant application is a dependency. Every operator uses their own clone, phone and Codex account. Never share a working-folder ZIP or copy another person's runtime, keys, history, business brief or schedules.

## Fresh clone prerequisites and setup

The supported Windows environment is Ubuntu in WSL2. Install these tools in that same WSL distribution, not just on Windows:

- Python 3.11+ at `/usr/bin/python3`.
- `uv` on PATH, for the locked Python dependencies.
- Stable Go meeting the `go` directive in `whatsapp-bridge/go.mod` (currently 1.26.0+).
- Git. Node.js is optional for syntax checks, not needed to run the UI.
- Your own Codex installation/sign-in for MCP, drafting or forwarding. Exact-text schedules and the WhatsApp bridge do not need a model API key.

Use the official [Go installer](https://go.dev/doc/install), [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/) and your distribution's Python packages. Check `go version`, `uv --version` and `python3 --version` inside WSL. This repository does not automatically install system tools or download a Go toolchain.

From PowerShell **in your cloned folder**:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Setup-WhatsApp.ps1
```

If the distribution is not named Ubuntu, add `-Distribution YOUR_NAME` to the PowerShell launchers. Find it with `wsl --list --verbose`. Setup secures the Windows runtime folder, installs locked Python dependencies and builds the Go bridge with two build workers. It does not start servers, pair a phone, register Codex, alter its configuration, or enable autostart. Downloading dependencies requires internet access. Stop services before rerunning setup for an update.

From Linux, or WSL after applying Windows runtime ACLs, use `bash scripts/setup.sh` in your clone. The launchers derive paths from their own folder, not from another operator's home.

No account-specific environment file is needed. The linked account and random service tokens are created locally. Codex uses your own login/configuration outside this repository. Any optional provider API key is yours to configure in Codex, never someone else's key from this checkout.

## Connect your own Codex

From WSL in your clone, register this launcher:

```bash
codex mcp add whatsapp-local -- bash "$PWD/scripts/mcp.sh"
```

This records user-level configuration without starting WhatsApp. Inspect any existing `whatsapp-local` registration before replacing it. Start a new Codex session to load the registration. To optionally discover this checkout's skill:

```bash
mkdir -p "$HOME/.agents/skills"
ln -s "$PWD/skills/whatsapp-local" "$HOME/.agents/skills/whatsapp-local"
```

Do not force-overwrite an existing skill. The skill resolves the checkout from its symlink and contains no personal username or phone number.

### App-server for model-backed commands

Drafting and forwarding use `$CODEX_HOME/app-server-control/app-server-control.sock`, with `CODEX_HOME` defaulting to `~/.codex`. The desk and Codex must use the same WSL distribution, user and Codex home. A Windows-native Codex process is not automatically that WSL daemon.

If your Codex client already provides the daemon, reuse it. Otherwise, after signing into Codex yourself, you can manually run this in a dedicated WSL terminal:

```bash
codex app-server --listen unix://
```

Keep that terminal open. Do not start a second daemon for the same session. Check `codex app-server --help` if the option is absent. The [official app-server documentation](https://developers.openai.com/codex/app-server/) describes Unix transport and marks the interface experimental. Compatibility with an arbitrary Codex version is not guaranteed. No remote listener is required.

In **Control chats**, choose your own persistent Codex conversation and use **Check connection**. Drafting uses an isolated text-only thread; forwarding targets the selected conversation with its existing permissions. Populate **Business context** with your own facts and choose your timezone before scheduling anything. A fresh install has no imported outreach, drafts, schedules or chosen Codex session.

## Start and link your phone

From Windows PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start-WhatsApp.ps1
```

Keep that terminal open. The launcher secures the Windows runtime folder and runs the bridge in WSL. On the first run, use **WhatsApp on your phone > Linked Devices > Link a device** to scan the displayed QR. The current code is also written to `runtime/pairing-qr.png` so it can be opened locally; it is removed when pairing completes or the bridge stops. Do not share it. Linking requires your phone; an existing Chrome or Windows WhatsApp login cannot be imported.

From WSL, after the runtime ACL has been applied:

```bash
# From your own clone:
bash scripts/bridge.sh
```

Later starts reuse the locally saved WhatsApp device session. Ctrl+C stops the bridge without intentionally unlinking the device. A failed or expired linking attempt stops; it does not repeatedly start new pairing attempts. Do not delete databases or unlink other devices as an automatic recovery step.

Only one bridge should run. It binds `127.0.0.1:8765`, before connecting to WhatsApp, so a second instance fails without opening a second WhatsApp connection. No browser is needed after this device is linked. WSL must stay running for the connection to remain available.

## Use it in Codex

The configured server name is `whatsapp-local`. Start a new Codex session to load a newly registered server, or use the client's MCP restart control when available. The optional skill is `$whatsapp-local`.

The server launches through `scripts/mcp.sh`; it exposes 24 tools: the original 13 for connection status, contacts, scoped history, messages and media, plus 11 for schedules, saved context, drafts and session selection. If the bridge is stopped or unlinked, the status tool explains the situation. MCP startup does not automatically start or link another WhatsApp device.

Ask naturally, for example, "Find the contact named Rohan" or "Show the last five messages in this chat." Sending needs an unambiguous recipient and your authorization for the message. No setup or verification test sends a message.

The MCP tools alone act when you ask. The separate control desk enables the explicitly configured phone commands and schedules described below. Neither marks chats read or exposes a public webhook.

## Open the control desk

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start-ControlDesk.ps1
```

This starts the local services in a minimized PowerShell window and opens an authenticated browser page at `http://127.0.0.1:8766`. Keep the service window running; closing just the browser is fine. The launcher reuses a compatible bridge if one is already running. For an old bridge without controller endpoints, close its original PowerShell window first, then launch the desk. Device credentials and messages are preserved.

Optional startup at Windows sign-in:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-Autostart.ps1
```

To use a different WSL distribution, add `-Distribution YOUR_NAME`. To remove the logon task, run the same command with `-Remove`. No administrator privilege or public network listener is required. Autostart cannot work while Windows is off, asleep, or signed out, and Codex tasks require its local app-server. Setup and coding agents must not enable autostart for you.

### When phone keywords do not work

The WhatsApp bridge, Control Desk and Codex connection are separate. The bridge alone does not handle keywords. After connecting, send a **new** `pc: status` from your phone to your own self-chat. This should reply without a model. For `mango` and forwarding, your Codex app-server must also be available; inspect Activity and the connection banner before retrying. `mango` needs a contact name, not only the keyword. Old/history-synced commands are deliberately not replayed after an outage. Do not test using a client chat.

### Phone commands

The linked account's self-chat is the initial owner control source. Additional groups must contain only that account. Enable an exact group such as Notes in **Control chats**; names alone never grant access. Forwarding is off after a restart and expires after 30 idle minutes by default.

| Message in an enabled owner chat | Result |
| --- | --- |
| `pineapple` or `orange` | Toggle forwarding on/off and receive a confirmation. Both are editable aliases for the same action. |
| Ordinary text while forwarding is on | Submit it to the exact saved Codex conversation; return its final answer here. The selected session's existing tool permissions still apply. |
| `pc: off` | Always stop new forwarding, regardless of alias edits. Already submitted work can still finish. |
| `mango Rohan Client` | Find the saved contact and draft a response using their recent chat, your brief, notes and the latest draft. Ambiguous/fuzzy names require selection. |
| `mango Rohan Client | Make it shorter and warmer` | Create a new draft revision. |
| `pc: select 2` | Choose from a recent contact-disambiguation list. |
| `pc: send DRAFT_ID` | Send that exact saved draft after your explicit approval. Drafting alone never sends it to the client. |
| `pc: schedules`, `pc: cancel ID`, `pc: status`, `pc: help` | Manage or inspect the controller. |

Aliases and reusable text-only prompt workflows live in **Commands & workflows**. They share the same owner checks and durable queue. Watched contacts produce suggested replies in your self-chat. Separately enabled delegated assistants answer only within that contact's own conversation using your shared business brief; they cannot control the PC or retrieve other contacts' history. No contacts are enabled in either role automatically.

### Schedules and business context

In **Scheduled messages**, search by name or contact suffix, select exact recipients, enter the message and local send time, then review each recipient's personalized text and time before confirming. `{{name}}` is the supported placeholder. Recurrence supports once, daily, weekdays or weekly. Pausing, editing, canceling, stop-on-reply and a do-not-contact list are available. An edited schedule is saved paused until you resume it. A suffix is a selection aid, not an instruction to keep adding new matching contacts.

Recipients are initially spaced 30 seconds apart; the desk shows this spacing before you confirm. The initial timezone is Asia/Kolkata and the late-send grace period is five minutes, both editable. Missed occurrences outside that window are not burst-sent when the computer wakes. A send with an uncertain outcome requires review instead of an automatic resend. A WhatsApp acknowledgment is not proof of delivery or reading. Do not use this to evade WhatsApp limits or contact people who have opted out.

Fill **Business context** with your actual services, tone, pricing boundaries and promises. Add per-contact notes when useful. A fresh Codex session can retrieve this through `get_saved_business_context`; the skill explains when to do so. Unknown business facts are not inferred. Drafts use your signed-in Codex configuration and a separate, ephemeral text-only thread with computer/messaging tools disabled. The optional model field controls these draft threads, not the model selected in your existing forwarded conversation. There is no extra API key to paste here, but Codex usage limits still apply.

The activity page shows jobs, schedule attempts and requests needing your attention. The desktop remains the place to answer Codex requests the controller does not support. `docs/CONTROL_DESIGN.md` documents trust boundaries, extension points and actual verification.

## Local layout

- `whatsapp-bridge/`: upgraded Go connection and authenticated local HTTP API.
- `whatsapp-mcp-server/`: Python MCP tools, database readers and offline checks.
- `scripts/`: WSL environment, bridge and MCP launchers.
- `control/`: event router, durable scheduler, Codex adapter, authenticated desk and offline tests.
- `Start-ControlDesk.ps1`, `Install-Autostart.ps1`: local UI and optional Windows logon task.
- `Start-WhatsApp.ps1`, `Secure-Runtime.ps1`: Windows launch and runtime access control.
- `skills/whatsapp-local/`: skill source, discoverable through a user-level symlink.
- `.tools/go/`: checksum-verified local Go toolchain; no system Go installation.
- `.venv/`: Python dependencies, using the existing WSL Python 3.12 interpreter.
- `.cache/`: downloads, dependency/build caches and temporary files.
- `bin/whatsapp-bridge`: compiled bridge. Startup does not rebuild it.
- `runtime/`: private WhatsApp session, message history, API token and requested attachments. Git ignores it.

The integrations outside this folder are Codex's MCP configuration entry, the skill-discovery symlink, and the optional per-user Windows logon task. None contains WhatsApp session secrets. Existing browser sessions and the older WhatsApp Relay plugin are not modified by this checkout.

## Safeguards and limits

- Local HTTP endpoints require a random bearer token read from `runtime/api-token`; it is never placed in Codex configuration, command-line arguments or logs. Browser-origin requests are rejected. The HTTP API is not intended for remote exposure.
- Windows ACLs restrict `runtime` to the current Windows user and SYSTEM. This matters because WSL's `/mnt/c` mount does not enforce ordinary Linux file modes reliably. Another program running as the same Windows user can still access that user's files; this is not isolation from a compromised user account.
- The bridge stores history locally as WhatsApp syncs it. Codex receives only requested tool results. Message contents are not printed by bridge application logs.
- Message database readers use SQLite read-only connections, bounded limits and explicit errors instead of silently creating empty databases.
- Phone lookup is exact. Contact metadata preserves phone/LID mappings when WhatsApp provides them. An unavailable mapping is not guessed.
- Media is limited to 32 MiB per upload or download. Download filenames are confined to hashed chat/message paths beneath `runtime/media`; remote filenames cannot choose arbitrary destinations.
- Sends are not retried automatically. A successful result is a server acknowledgment, not delivery or read confirmation. A timeout can mean the send happened but its acknowledgment was lost.
- Valid `.ogg` Opus voice messages work without conversion. Converting other formats into voice messages needs optional `ffmpeg`, which is not installed by this setup. Raw audio files can still use `send_file`.
- whatsmeow is an unofficial WhatsApp client. Server-side restrictions and future protocol changes can still require maintenance. This setup does not bypass device-linking restrictions.

## Checks without starting servers

From WSL:

```bash
.venv/bin/python scripts/check_offline.py
node --check control/web/app.js
python3 scripts/check_share.py
```

The offline runner uses temporary fixtures and fake transports, excluding HTTP, browser and MCP servers. Integration tests are owner-operated and must not be run by an agent under the manual-server rule. Rebuild/update using the setup command while services are stopped. Go build concurrency is limited to two workers; dependencies are pinned in `go.mod`/`go.sum` and `uv.lock`.

The sharing check inspects the actual Git index for private paths and common credential patterns, without printing secret values. It does not inspect all past commits or prove that arbitrary personal content is absent. Review the staged diff and repository history before publishing. Never force-add ignored runtime or exports. A fresh clone starts without an outreach review; import your own research using `.venv/bin/python -m control.outreach /absolute/path/to/your/review.json` only if wanted.

## Disconnect from Codex

```bash
codex mcp remove whatsapp-local
```

Remove only the `whatsapp-local` skill symlink if no longer wanted. Keep this folder and its runtime data to retain the linked session. Actual device revocation is a separate action under WhatsApp's Linked Devices on your phone.
