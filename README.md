# RO Tools — Refine Calculator & MVP Tracker

A local web app for Ragnarok Online. No Node.js or external dependencies required — the backend is a single PowerShell script.

---

## Project Structure

```
B:\Coding\ro-calc\
├── index.html              — Refine Calculator
├── serve-backend.ps1       — PowerShell backend server (port 9500)
├── topbar.js               — Shared top navigation bar
├── TODO.md                 — Pending features and known issues
├── README.md               — This file
└── mvp-tracker\
    ├── index.html          — MVP Tracker app
    └── data\               — All persistent data (JSON, auto-created on first run)
        ├── users.json
        ├── sessions.json
        ├── groups.json
        ├── kills_{groupId}.json
        ├── items.json      — Shared item DB (names + last sold prices)
        └── ...
```

---

## Starting the Backend

The backend must run as **Administrator** to bind to port 9500.

1. Right-click **Start** → *Windows PowerShell (Admin)* or *Terminal (Admin)*
2. Run:
   ```powershell
   cd B:\Coding\ro-calc
   .\serve-backend.ps1
   ```
3. You should see:
   ```
   MVP Tracker backend running on http://localhost:9500
   Press Ctrl+C to stop
   ```
4. Open your browser to **http://localhost:9500/mvp**

> If you just close the terminal window the backend stops. Leave it open while using the app.

---

## Restarting the Backend

You need to restart any time `serve-backend.ps1` has been updated (new API routes added, bug fixes, etc.).

### Option A — Use the restart script (recommended)

Open **PowerShell as Administrator** and run:
```powershell
powershell -ExecutionPolicy Bypass -File "B:\Coding\ro-calc\restart-backend.ps1"
```

> If you get *"running scripts is disabled on this system"*, that's Windows execution policy blocking `.ps1` files. The command above bypasses it for this one call only.  
> Alternatively, set it for the current session and then run normally:
> ```powershell
> Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
> cd B:\Coding\ro-calc
> .\restart-backend.ps1
> ```

This kills any existing instance and starts a fresh one automatically (runs hidden in background). Verify it worked by visiting `http://localhost:9500/api/auth/me` in your browser — it should return JSON.

### Option B — Close the window manually

Find the PowerShell window running the backend and close it (or press `Ctrl+C` inside it), then start again:
```powershell
cd B:\Coding\ro-calc
.\serve-backend.ps1
```

### Option C — Kill by PID when you can't find the window

**Step 1** — Find the PID:
```powershell
netstat -ano | findstr :9500
```
The last column is the PID.

**Step 2** — Stop it. Try these in order until one works:

```powershell
# Try 1: PowerShell (Admin)
Stop-Process -Id <PID> -Force
```

```cmd
# Try 2: Command Prompt (Admin) — often works when PowerShell can't
taskkill /F /PID <PID>
```

```
# Try 3: Task Manager
Ctrl+Shift+Esc → Details tab → find powershell.exe → End Task
(Right-click the column header to add "Command Line" to identify the right process)
```

> **Note:** Windows sometimes blocks `Stop-Process` even as Admin when the target process was started in a *different* elevated session. `taskkill` from cmd or Task Manager bypasses this restriction.

**Step 3** — Start again from an Admin PowerShell:
```powershell
cd B:\Coding\ro-calc
.\serve-backend.ps1
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Access is denied" on start | Run PowerShell as Administrator |
| Port already in use | Another instance is running — stop it first (Step 1 above) |
| App can't connect to backend | Go to `http://localhost:9500/api/auth/me` — if it returns JSON, the backend is running. If not, restart it. |
| Undo kill says "Error: Not found" | The DELETE endpoint was added recently. Restart the backend. Until then the app falls back to a soft-delete (kill is hidden in the UI but remains in the JSON file). |
| Items showing as "Item #12345" | Item name lookup needs the backend running. Restart it, then re-enter the item ID in the kill modal. |

---

## Data Backup

All data is in `mvp-tracker\data\`. Copy that folder to back up or migrate to another machine.

```powershell
# Quick backup
Copy-Item "B:\Coding\ro-calc\mvp-tracker\data" `
  "B:\Backups\ro-data-$(Get-Date -Format yyyyMMdd)" -Recurse
```

---

## Development / Preview

The app can also be served on port 8766 by the Claude Code preview tool. In that mode the frontend automatically proxies all API calls to `http://localhost:9500`, so the backend must still be running separately.
