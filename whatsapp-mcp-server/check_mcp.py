"""Exercise the real stdio protocol using fixtures, never a linked WhatsApp account."""
import json
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path

from test_local import LocalConnectorTests


def run():
    fixture = LocalConnectorTests()
    fixture.setUp()
    root = Path(__file__).resolve().parent.parent
    environment = dict(os.environ, WHATSAPP_STORE_DIR=fixture.temp.name)
    process = subprocess.Popen(
        [str(root / ".venv/bin/python"), "-u", str(root / "whatsapp-mcp-server/main.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=environment,
    )
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)

    def request(identifier, method, params):
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if not selector.select(timeout=1):
                if process.poll() is not None:
                    raise AssertionError("MCP exited: " + process.stderr.read())
                continue
            line = process.stdout.readline()
            if not line:
                raise AssertionError("MCP stdout closed: " + process.stderr.read())
            response = json.loads(line)
            if response.get("id") == identifier:
                if "error" in response:
                    raise AssertionError(response["error"])
                return response["result"]
        raise AssertionError(f"MCP timed out during {method}")

    try:
        initialized = request(1, "initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "local-check", "version": "1"},
        })
        assert initialized["serverInfo"]["name"] == "whatsapp-local"
        process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        process.stdin.flush()
        tools = request(2, "tools/list", {})["tools"]
        names = {tool["name"] for tool in tools}
        assert len(names) == 24, names
        assert {"schedule_messages", "get_automation_status", "get_saved_business_context", "request_reply_draft"} <= names
        send_tool = next(tool for tool in tools if tool["name"] == "send_message")
        assert send_tool["annotations"]["readOnlyHint"] is False
        for identifier, name, arguments in [
            (3, "get_connection_status", {}),
            (4, "search_contacts", {"query": "Hidden"}),
            (5, "list_chats", {"include_last_message": False}),
            (6, "get_chat", {"chat_jid": "998877@lid", "include_last_message": False}),
            (7, "list_messages", {"chat_jid": "998877@lid", "include_context": True}),
            (8, "get_message_context", {"message_id": "same-id", "chat_jid": "998877@lid"}),
        ]:
            result = request(identifier, "tools/call", {"name": name, "arguments": arguments})
            assert not result.get("isError"), (name, result)
        invalid = request(9, "tools/call", {"name": "list_messages", "arguments": {"limit": -1}})
        assert invalid.get("isError"), invalid
        print("MCP stdio: initialized, 24 tools listed, 6 fixture calls passed, invalid input rejected")
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        selector.close()
        fixture.doCleanups()


if __name__ == "__main__":
    run()
