# Tokei — Claude usage tray widget (Windows)

System tray icon that shows your Claude usage limits. The number on the icon = **% of the 5h session limit** (green <60, amber 60–85, red >85) on a black background. Right-click opens a menu with every limit (session + weekly) including progress bar, % and reset time.

## Quick start (no build needed)

```powershell
py -m pip install -r requirements.txt
pythonw tokei.py   # pythonw = no console window
```

## Build to .exe + autostart

```powershell
./build.ps1
```

Generates `dist\Tokei.exe` and creates a shortcut in the Windows Startup folder so it launches automatically on login.

## Authentication

- **Starts without a session.** Right-click → "Iniciar sesión": the browser opens, you sign in to Claude and paste the code it shows you.
- The session is saved to `%APPDATA%\Tokei\token.json`, **persists across restarts** and auto-refreshes via refresh token.
- **Sign out** deletes the saved session; the widget returns to "no session".

## How it works

- Polls `GET https://api.anthropic.com/api/oauth/usage` every 5 min (with exponential backoff on rate-limit) using the active token.
- Uses the same OAuth PKCE flow as `claude /login` (subscription account).

## Disclaimer

- The endpoint is **unofficial and undocumented**: Anthropic may change or remove it without notice. If the icon stays grey/"no connection", the endpoint has likely changed.
- Uses your own local token; nothing is sent to third parties.
