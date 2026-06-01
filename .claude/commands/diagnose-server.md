# Diagnose server startup and port issues

Help the user get the dev server running when it won't load in the browser. Work through these checks in order, stopping as soon as the root cause is found:

## 1 — Is uvicorn actually running?
Check for a running uvicorn process. If none, print the start command from CLAUDE.md and stop.

## 2 — What port is the server on?
Read `src/rag_bachelor/config.py` → `app_port` to get the configured port.

## 3 — Is something else on that port?
On Windows (PowerShell):
```
netstat -ano | findstr :<port>
```
On Linux/Mac:
```
lsof -i :<port>
```
If another PID is listening, identify it:
- Windows: `tasklist /FI "PID eq <pid>"`
- Linux: `ps -p <pid> -o comm=`

**Known conflict:** VS Code Dev Container extension (`Code.exe`) claims ports listed in `.devcontainer/devcontainer.json` → `forwardPorts` on the Windows host, even when the container isn't running the app. If VS Code is the culprit, the fix is to change `app_port` to a port NOT in `forwardPorts`.

## 4 — Does the server respond locally?
While uvicorn is running, test from the same machine:
```
curl http://127.0.0.1:<port>/
```
If curl works but the browser doesn't → browser proxy or IPv6 issue. Try `http://127.0.0.1:<port>` explicitly (not `localhost`).

## 5 — Does the page load but hang?
If the browser connects but the page never finishes loading, the event loop is blocked. Run `/audit-async` to find blocking synchronous calls in async route handlers.

## 6 — Report
Summarise what was found and what was (or needs to be) fixed.
