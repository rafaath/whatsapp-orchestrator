# Local WhatsApp workspace

- Do not start, restart or run servers yourself. This includes the WhatsApp bridge, Control Desk, MCP, Codex app-server, browser-check servers, and HTTP test servers. Give the owner commands to run manually. Do not enable autostart automatically.
- Never delete runtime databases, credentials, linked devices or queued work to troubleshoot. Do not replay an uncertain send.
- `runtime/`, `.cache/`, `.tools/`, `bin/`, `.venv/`, account configuration, tokens, exports and pairing images are private local files. Never commit or publish them. Do not copy another person's runtime into a fresh installation.
- Before staging or publishing, review the file list and run `python3 scripts/check_share.py` against the Git index after staging. It checks common credential patterns and private file paths; it is not a proof that arbitrary content is safe to publish.
- Respect explicit limits on Git operations. Preparing or staging files is not permission to commit, change a remote, or push.
- Keep setup relative to the checkout and the current user's home. No personal usernames, phone numbers, business briefs or Codex conversation IDs in defaults.
- Offline fixture tests are allowed when they do not open listeners or start servers. `python3 scripts/check_offline.py` deliberately omits the HTTP-server test and the MCP/browser smoke tests. Live phone verification remains owner-operated.
