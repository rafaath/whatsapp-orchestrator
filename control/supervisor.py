"""Keep the two local services running; own and stop only processes started here."""

import fcntl
import os
import signal
import subprocess
import sys
import threading
import time

from .engine import Bridge, ROOT


def main():
    runtime=ROOT/"runtime"
    runtime.mkdir(exist_ok=True)
    lock=(runtime/"supervisor.lock").open("a+")
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit("WhatsApp services are already running")
    stopped=threading.Event()
    children=[]
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stopped.set())
    try:
        try:
            Bridge(runtime).call("status")
            existing=True
            print("Using the existing WhatsApp bridge",flush=True)
        except RuntimeError as exc:
            raise SystemExit(str(exc)+". Stop the previous PowerShell bridge window, then run Start-ControlDesk.ps1.")
        except Exception:
            existing=False
        specs=[] if existing else [("bridge",[str(ROOT/"bin/whatsapp-bridge")])]
        specs.append(("desk",[sys.executable,"-u","-m","control.server"]))
        logfiles=[]
        for name,args in specs:
            log=runtime/(name+"-service.log")
            if log.exists() and log.stat().st_size>2*1024*1024:
                log.replace(runtime/(name+"-service.previous.log"))
            handle=log.open("ab",buffering=0);logfiles.append(handle)
            child=subprocess.Popen(args,cwd=ROOT,stdout=handle,stderr=handle)
            children.append({"name":name,"args":args,"process":child,"log":handle,"failures":0,"started":time.monotonic()})
        print("WhatsApp bridge and control desk are running. Ctrl+C stops services started by this window.",flush=True)
        while not stopped.wait(2):
            for child in children:
                process=child["process"]
                if process.poll() is None:continue
                child["failures"]=1 if time.monotonic()-child["started"]>120 else child["failures"]+1
                if child["failures"]>5:
                    raise RuntimeError(child["name"]+" repeatedly stopped. Review runtime/"+child["name"]+"-service.log")
                if stopped.wait(min(30,2**child["failures"])):break
                child["process"]=subprocess.Popen(child["args"],cwd=ROOT,stdout=child["log"],stderr=child["log"])
                child["started"]=time.monotonic()
    finally:
        for child in children:
            if child["process"].poll() is None:child["process"].terminate()
        for child in children:
            try:child["process"].wait(timeout=8)
            except subprocess.TimeoutExpired:child["process"].kill();child["process"].wait()
        for child in children:child["log"].close()
        lock.close()


if __name__=="__main__":main()
