"""Opt-in UI checks with an existing Chromium binary and synthetic contacts only.

No browser installation, WhatsApp traffic, Codex calls, or production DB writes.
Run: python -m control.check_browser /path/to/chromium
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from websockets.sync.client import connect

from . import server as http
from .engine import ROOT
from .test_control import ControllerTests
from .outreach import import_review
from .test_outreach import fixture_review


def main():
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_file():
        raise SystemExit("Pass the path to an already installed Chromium binary")
    checks = ControllerTests()
    checks.setUp()
    review_source = checks.root/"review.json"
    review_source.write_text(json.dumps(fixture_review()), encoding="utf-8")
    import_review(review_source, checks.root)
    http.PORT = 8767
    api = http.API(checks.engine)
    server = http.Server(("127.0.0.1", http.PORT), http.make_handler(api, "a"*64))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    output = ROOT/".cache/browser-check"
    output.mkdir(parents=True, exist_ok=True)
    browser, ws = None, None
    def stop_browser():
        if browser and browser.poll() is None:
            browser.terminate()
            try: browser.wait(timeout=10)
            except subprocess.TimeoutExpired: browser.kill();browser.wait()
    try:
        with ExitStack() as cleanup:
            profile = cleanup.enter_context(tempfile.TemporaryDirectory(dir=ROOT/".cache/tmp"))
            # Chromium's Unix socket names exceed the kernel limit under the long Windows TMPDIR.
            browser = subprocess.Popen([sys.argv[1], "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-background-networking", "--remote-debugging-port=0", "--user-data-dir="+profile, "about:blank"], env=dict(os.environ,TMPDIR="/tmp"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            cleanup.callback(stop_browser)
            active = Path(profile)/"DevToolsActivePort"
            deadline = time.monotonic()+20
            while not active.exists() and time.monotonic()<deadline:
                if browser.poll() is not None: raise RuntimeError("Chromium stopped during startup")
                time.sleep(.1)
            port = int(active.read_text().splitlines()[0])
            with requests.Session() as client:
                client.trust_env = False
                deadline = time.monotonic()+10
                page = None
                while time.monotonic()<deadline and page is None:
                    pages = client.get(f"http://127.0.0.1:{port}/json", timeout=5).json()
                    page = next((p for p in pages if p["type"]=="page" and p.get("webSocketDebuggerUrl")), None)
                    if page is None: time.sleep(.1)
                if page is None: raise RuntimeError("Chromium did not create its test tab")
            ws = connect(page["webSocketDebuggerUrl"], open_timeout=5)
            cleanup.callback(ws.close)
            sequence, errors = 0, []
            def call(method, params=None):
                nonlocal sequence
                sequence += 1
                ws.send(json.dumps({"id":sequence,"method":method,"params":params or {}}))
                while True:
                    message=json.loads(ws.recv(timeout=20))
                    if message.get("method")=="Runtime.exceptionThrown": errors.append(message["params"])
                    if message.get("id")==sequence:
                        if "error" in message: raise RuntimeError(message["error"])
                        return message.get("result",{})
            def js(expression):
                result=call("Runtime.evaluate",{"expression":expression,"awaitPromise":True,"returnByValue":True})
                if "exceptionDetails" in result: raise RuntimeError(result["exceptionDetails"])
                return result.get("result",{}).get("value")
            call("Runtime.enable")
            call("Page.enable")
            call("Page.navigate",{"url":"http://127.0.0.1:8767/#token="+"a"*64})
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                if js("typeof state !== 'undefined' && state !== null && !document.getElementById('app').hidden"): break
                time.sleep(.1)
            else: raise RuntimeError("UI did not sign in to the synthetic fixture")
            for width,height in [(1440,1000),(390,844),(320,740)]:
                call("Emulation.setDeviceMetricsOverride",{"width":width,"height":height,"deviceScaleFactor":1,"mobile":False})
                for name in ("overview","outreach","schedule","drafts","connections","workflows","context","activity"):
                    js(f"document.querySelector('[data-tab={name}]').click()")
                    result=js("({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,visible:[...document.querySelectorAll('.page')].filter(x=>!x.hidden).length})")
                    assert result["visible"]==1, result
                    assert result["scrollWidth"]<=width, (name,result)
                js("tab('outreach')")
                deadline=time.monotonic()+5
                while not js("typeof outreach !== 'undefined' && outreach?.available && !outreachLoading"):
                    if time.monotonic()>deadline: raise RuntimeError("Outreach snapshot did not load")
                    time.sleep(.1)
                assert js("document.querySelectorAll('#outreach-list [data-entry]').length===3")
                js("document.querySelector('#outreach-list [data-entry]').click()")
                assert js("document.getElementById('outreach-person').textContent==='Alex Example'")
                assert js("document.getElementById('outreach-detail').textContent.includes('America/New_York')")
                assert js("document.documentElement.scrollWidth<=innerWidth")
                if width!=320:
                    import base64
                    js("window.scrollTo(0,0); Promise.all(document.getAnimations().map(a=>a.finished.catch(()=>{})))" if width>920 else "document.getElementById('outreach-detail').scrollIntoView(); Promise.all(document.getAnimations().map(a=>a.finished.catch(()=>{})))")
                    screenshot=call("Page.captureScreenshot",{"format":"png"})["data"]
                    (output/f"outreach-{width}.png").write_bytes(base64.b64decode(screenshot))
                if width<=920:
                    assert js("getComputedStyle(document.querySelector('.outreach-directory')).display==='none'")
                    js("document.querySelector('.outreach-back').click()")
                    assert js("document.activeElement.dataset.entry==='introduction:one'")
                js("document.querySelector('[data-filter=approved]').click()")
                assert js("document.querySelectorAll('#outreach-list [data-entry]').length===1 && document.getElementById('outreach-list').textContent.includes('Blair')")
                js("document.querySelector('[data-filter=followup]').click(); document.querySelector('#outreach-list [data-entry]').click()")
                assert js("document.getElementById('outreach-detail').textContent.includes('review date, not an automatic send date')")
                js("document.querySelector('[data-filter=unresolved]').click()")
                assert js("document.getElementById('outreach-list').textContent.includes('Casey')")
                js("document.querySelector('[data-filter=all]').click(); document.getElementById('outreach-search').value='Missing'; document.getElementById('outreach-search').dispatchEvent(new Event('input'))")
                assert js("document.querySelectorAll('#outreach-list [data-entry]').length===0")
                js("document.getElementById('outreach-search').value='Example Cafe'; document.getElementById('outreach-search').dispatchEvent(new Event('input'))")
                assert js("document.querySelectorAll('#outreach-list [data-entry]').length===1")
                js("document.getElementById('outreach-search').value=''; document.getElementById('outreach-search').dispatchEvent(new Event('input')); document.querySelector('[data-filter=new]').click()")
                assert not checks.store.rows("SELECT id FROM schedules")
                assert not checks.store.rows("SELECT id FROM drafts")
                js("tab('overview')")
                js("Promise.all(document.getAnimations().map(a=>a.finished.catch(()=>{})))")
                assert js("document.querySelector('.wordmark').getBoundingClientRect().bottom <= document.querySelector('.sidebar-foot').getBoundingClientRect().top")
                if width!=320:
                    import base64
                    screenshot=call("Page.captureScreenshot",{"format":"png"})["data"]
                    (output/f"desk-{width}.png").write_bytes(base64.b64decode(screenshot))
            # Verify a fresh deep link, explicit error handling, and the missing-snapshot state.
            call("Page.navigate",{"url":"http://127.0.0.1:8767/?view=outreach"})
            deadline=time.monotonic()+5
            while not js("typeof outreach !== 'undefined' && outreach?.available && !outreachLoading"):
                if time.monotonic()>deadline: raise RuntimeError("Outreach deep link did not reopen")
                time.sleep(.1)
            assert js("!document.getElementById('page-outreach').hidden")
            private_review = checks.root/"runtime/outreach-review.json"
            private_review.write_text("{invalid")
            js("loadOutreach()")
            assert js("document.getElementById('outreach-content').hidden && document.getElementById('outreach-snapshot').textContent.includes('could not be loaded')")
            private_review.unlink()
            js("loadOutreach()")
            assert js("document.getElementById('outreach-snapshot').textContent.includes('No review snapshot')")
            import_review(review_source, checks.root)
            js("loadOutreach()")
            assert js("!document.getElementById('outreach-content').hidden")
            assert not checks.store.rows("SELECT id FROM schedules")
            # Exercise browser form wiring against only the fixture API.
            js("tab('schedule'); document.querySelector('#schedule-picker input').value='Client'; document.querySelector('#schedule-picker button').click()")
            deadline=time.monotonic()+5
            while not js("document.querySelectorAll('#schedule-picker .person').length===2"):
                if time.monotonic()>deadline: raise RuntimeError("Fixture contact search did not return both contacts")
                time.sleep(.1)
            js("document.querySelector('#schedule-picker .person').click(); document.getElementById('schedule-message').value='Hello {{name}}'; document.getElementById('schedule-zone').value='UTC'")
            future=(datetime.now(timezone.utc)+timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
            js(f"document.getElementById('schedule-date').value={json.dumps(future)}; document.getElementById('schedule-form').requestSubmit()")
            deadline=time.monotonic()+5
            while js("document.getElementById('schedule-confirm').hidden"):
                if time.monotonic()>deadline: raise RuntimeError("Schedule preview did not appear")
                time.sleep(.1)
            assert js("document.getElementById('schedule-preview').textContent.includes('Hello Rohan Client')")
            js("document.getElementById('schedule-confirm').click()")
            deadline=time.monotonic()+5
            while not checks.store.rows("SELECT id FROM schedules"):
                if time.monotonic()>deadline: raise RuntimeError("Browser schedule confirmation did not persist")
                time.sleep(.1)
            assert not checks.bridge.sent
            assert not errors, errors
            print("PASS: 8 screens at 1440, 390 and 320px; no horizontal overflow; outreach filters, search, local times and mobile navigation without queue writes; fixture schedule flow; no browser exceptions.")
            print("Screenshots:", output)
    finally:
        if ws: ws.close()
        stop_browser()
        server.shutdown();server.server_close()
        checks.doCleanups()


if __name__ == "__main__": main()
