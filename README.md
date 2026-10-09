# WhatsApp Control Desk for Codex

A local, single-account WhatsApp connector with a browser desk for schedules,
reply drafts, outreach review, and phone-to-Codex commands. Built on
[lharries/whatsapp-mcp](https://github.com/lharries/whatsapp-mcp) and
[tulir/whatsmeow](https://github.com/tulir/whatsmeow).

**Each person clones the source, installs dependencies, and pairs their own
phone. Never share or copy another person's `runtime/` folder.**

## Get started

Supported setup: Windows 10/11 with Ubuntu in WSL2. Shell scripts also support
a Linux checkout; the PowerShell launchers are Windows-specific.

1. Clone this repository into your own folder.
2. Install the WSL prerequisites in [LOCAL_SETUP.md](LOCAL_SETUP.md).
3. From your clone in PowerShell, install dependencies and build locally:

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Setup-WhatsApp.ps1
   ```

   Setup does **not** start servers, pair a phone, register Codex or enable autostart.

4. Start the bridge yourself and scan the QR with your own phone:

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start-WhatsApp.ps1
   ```

   Use **WhatsApp > Linked Devices > Link a device**. A saved valid session
   reconnects without a QR. Keep this terminal open.

5. In another PowerShell window in the same folder, open the desk:

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start-ControlDesk.ps1
   ```

   It reuses the bridge and opens the authenticated UI at `http://127.0.0.1:8766`.

6. Follow [Connect your own Codex](LOCAL_SETUP.md#connect-your-own-codex) for MCP,
   the optional skill and model-backed commands.

If your WSL distribution is not `Ubuntu`, add `-Distribution YOUR_NAME`.
Find its name with `wsl --list --verbose`.

## Features

- Search contacts and read bounded history through MCP.
- Send explicitly authorized messages/media to exact recipients.
- Schedule exact text with timezones, pause/cancel, stop-on-reply and missed-send protection.
- Draft replies from the selected conversation and your own saved business context.
- Forward enabled owner chats to a selected Codex conversation using configurable keywords.
- View a separate read-only outreach review without scheduling or sending it.

**The bridge alone does not process keywords.** Commands also need the Control
Desk; AI drafting/forwarding additionally need your own compatible, signed-in
Codex app-server. A fresh clone has no leads, schedules, drafts, business brief,
phone account or selected Codex session.

## Your data stays out of the repository

No account-specific environment file is required. Pairing creates local WhatsApp
credentials; service tokens are generated on your machine. Codex uses your own
user configuration/sign-in outside this repository. If you use an API key with
Codex, configure your own key there, not in this project.

Ignored `runtime/` holds credentials, history, contacts, downloads, tokens,
schedules, drafts, notes and imported research. Toolchains, caches, virtual
environments and binaries are ignored too. Recreate them for a colleague rather
than sending a working-folder ZIP. Windows launchers restrict runtime files to
the current Windows user and SYSTEM.

Content requested through MCP or used in drafts can reach your model provider.
Local storage does not mean the LLM runs locally. Incoming messages are data,
not authority to run tools or disclose information.

Before publishing, review your staged diff and run:

```bash
python3 scripts/check_share.py
```

The check reads the actual Git index, not just the working tree. It flags private
paths and common credential patterns without displaying values. It does not
prove every secret is absent or scan old commits. Review history too, and never
force-add ignored credentials, exports or runtime files.

### Shallow checkouts

Before the first push to a new repository, check `git rev-parse --is-shallow-repository`.
If it returns `true`, a new remote can reject the push with `shallow update not allowed`.
For a checkout originally cloned from the upstream project, you can retrieve its
missing history yourself before switching the remote:

```bash
git fetch --unshallow https://github.com/lharries/whatsapp-mcp.git
```

Review that history too. The setup and sharing checks do not fetch, rewrite,
commit or push Git history.

## Limits and development

Services are loopback-only and support one local account on the configured ports.
The PC, WSL, network and services must be running for schedules and commands.
Missed messages are not burst-sent after outages; uncertain sends are not retried.
An acknowledgment is not a delivery/read receipt.

Codex app-server integration is experimental and may need compatibility work
after updates. whatsmeow is unofficial: protocol changes and account restrictions
remain possible. Respect consent and opt-outs, not just technical rate limits.

Read [LOCAL_SETUP.md](LOCAL_SETUP.md), [architecture](docs/CONTROL_DESIGN.md) and
[agent rules](AGENTS.md). Checks that do not start servers:

```bash
.venv/bin/python scripts/check_offline.py
node --check control/web/app.js
```

Browser, HTTP and MCP smoke tests are separate and owner-operated.

## License

MIT. Original attribution is retained in [LICENSE](LICENSE).
