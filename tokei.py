"""Widget de bandeja: límites de uso de Claude (Windows).

Lee el token OAuth local de Claude Code y consulta el endpoint (no documentado)
/api/oauth/usage. El icono muestra el % de la ventana más crítica; al hacer clic
el menú muestra cada límite en detalle.

Arranca sin sesión. Con login OAuth (cualquier cuenta, igual que `claude /login`)
la sesión se guarda y persiste entre arranques hasta que se cierre sesión.

ponytail: una sola fuente de datos (endpoint no oficial, puede romperse). Poll
cada 5 min con backoff ante 429.
"""
import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

TOKEN_FILE = Path(os.environ.get("APPDATA", Path.home())) / "Tokei" / "token.json"  # sesión guardada
URL = "https://api.anthropic.com/api/oauth/usage"

# OAuth de Claude Code (no oficial, igual que `claude /login`). Flujo de SUSCRIPCIÓN.
OAUTH_CLIENT = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
OAUTH_REDIRECT = "https://platform.claude.com/oauth/code/callback"
OAUTH_SCOPE = "user:inference user:profile user:sessions:claude_code user:mcp_servers"
OAUTH_AUTHORIZE = "https://claude.ai/oauth/authorize"
OAUTH_TOKEN = "https://platform.claude.com/v1/oauth/token"
HEADERS = {
    "anthropic-beta": "oauth-2025-04-20",
    "User-Agent": "claude-code/1.0",  # ponytail: sin esto el rate-limit es inmediato
    "Accept": "application/json",
}
POLL = 300          # 5 min en condiciones normales
POLL_MAX = 1800     # techo del backoff: 30 min

# kind del API -> etiqueta legible. Lo que no esté aquí se muestra prettificado.
LABELS = {
    "session": "Sesión 5h",
    "weekly_all": "Semanal",
    "weekly_opus": "Opus semanal",
    "weekly_sonnet": "Sonnet semanal",
    "weekly_cowork": "Cowork semanal",
}


# ---- funciones puras (testeadas en --test) ---------------------------------

def parse_limits(data):
    """Devuelve [(label, pct, resets_iso|None, severity), ...] desde la respuesta."""
    rows = []
    for lim in data.get("limits") or []:
        pct = lim.get("percent")
        if pct is None:
            continue
        kind = lim.get("kind", "?")
        label = LABELS.get(kind, kind.replace("_", " ").capitalize())
        rows.append((label, float(pct), lim.get("resets_at"), lim.get("severity", "normal")))
    return rows


def session_pct(rows):
    """% del límite de sesión 5h (lo que muestra el icono), o None si no está."""
    return next((r[1] for r in rows if r[0] == LABELS["session"]), None)


def color_for(pct):
    if pct is None:
        return (160, 160, 160)          # gris: sin datos
    if pct >= 85:
        return (255, 40, 40)            # rojo vivo
    if pct >= 60:
        return (255, 190, 0)            # ámbar vivo
    return (0, 210, 90)                 # verde vivo


def fmt_reset(iso):
    """resets_at ISO -> hora local 'HH:MM' (o 'DD/MM HH:MM' si >24h)."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return ""
    delta = dt - datetime.now().astimezone()
    if delta.total_seconds() > 24 * 3600:
        return dt.strftime("%d/%m %H:%M")
    return dt.strftime("%H:%M")


def bar(pct, width=10):
    # Cuadros emoji (monocromos en el menú nativo, pero mantienen el diseño de barra).
    fill = "🟥" if pct >= 85 else "🟨" if pct >= 60 else "🟩"
    filled = max(0, min(width, round(pct / 100 * width)))
    return fill * filled + "⬜" * (width - filled)


def _child_cmd(arg):
    """Comando para relanzarse a sí mismo en otro modo (login/detail)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, arg]
    return [sys.executable, os.path.abspath(__file__), arg]


def make_icon(pct, color, size=64):
    """Círculo negro con el % en color; el número cambia de color según el uso."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, size - 1, size - 1], fill="black")
    s = "--" if pct is None else str(int(round(pct)))
    fsize = 34 if len(s) >= 3 else 48
    try:
        font = ImageFont.truetype("arialbd.ttf", fsize)
    except OSError:
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), s, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((size - w) / 2 - box[0], (size - h) / 2 - box[1]), s, font=font, fill=color)
    return img


# ---- red --------------------------------------------------------------------

def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def login():
    """Flujo OAuth PKCE en su PROPIO proceso (ventana normal, sin líos de foco).
    Abre el navegador, pide pegar el código, intercambia y guarda el token."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = _b64url(secrets.token_bytes(32))
    params = {
        "code": "true", "response_type": "code", "client_id": OAUTH_CLIENT,
        "redirect_uri": OAUTH_REDIRECT, "scope": OAUTH_SCOPE,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
    }
    webbrowser.open(OAUTH_AUTHORIZE + "?" + urllib.parse.urlencode(params))

    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.title("Iniciar sesión en Claude")
    tk.Label(root, text="Inicia sesión en el navegador y pega aquí el código:").pack(padx=18, pady=(16, 6))
    var = tk.StringVar()
    entry = tk.Entry(root, textvariable=var, width=56)
    entry.pack(padx=18, pady=4)
    out = {"code": None}
    btns = tk.Frame(root); btns.pack(pady=12)
    tk.Button(btns, text="Aceptar", width=12,
              command=lambda: (out.update(code=var.get().strip()), root.quit())).pack(side="left", padx=6)
    tk.Button(btns, text="Cancelar", width=12,
              command=lambda: (out.update(code=None), root.quit())).pack(side="left", padx=6)
    root.bind("<Return>", lambda e: (out.update(code=var.get().strip()), root.quit()))
    w, h = 430, 150
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 3
    root.geometry(f"{w}x{h}+{x}+{y}")
    root.lift(); root.focus_force(); entry.focus_set()
    root.mainloop()

    if not out["code"]:
        root.destroy(); return False
    code = out["code"].split("#")[0]              # el callback devuelve 'code#state'
    body = {
        "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
        "client_id": OAUTH_CLIENT, "redirect_uri": OAUTH_REDIRECT, "state": state,
    }
    r = _token_request(body)
    if r is not None and r.status_code == 200:
        save_tokens(r.json())
        messagebox.showinfo("Claude", "Sesión iniciada correctamente.", parent=root)
        ok = True
    else:
        detail = f"HTTP {r.status_code}\n{r.text[:800]}" if r is not None else "sin respuesta"
        messagebox.showerror("Claude", f"No se pudo iniciar sesión.\n\n{detail}", parent=root)
        ok = False
    root.destroy()
    return ok


def _token_request(body):
    """POST al endpoint de token; prueba form-urlencoded y luego JSON. Loguea la
    respuesta para diagnóstico. -> Response | None."""
    hdr = {"User-Agent": "claude-code/1.0", "Accept": "application/json"}
    last = None
    for kw in ({"data": body}, {"json": body}):
        try:
            last = requests.post(OAUTH_TOKEN, timeout=30, headers=hdr, **kw)
        except requests.RequestException:
            last = None
            continue
        try:
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            (TOKEN_FILE.parent / "login_debug.log").write_text(
                f"{'form' if 'data' in kw else 'json'} -> HTTP {last.status_code}\n{last.text[:800]}",
                encoding="utf-8")
        except OSError:
            pass
        if last.status_code == 200:
            return last
    return last


def save_tokens(d):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({
        "access_token": d["access_token"],
        "refresh_token": d.get("refresh_token"),
        "expires_at": time.time() + d.get("expires_in", 3600),
    }), encoding="utf-8")


def refresh_token(rt):
    """Renueva con el refresh_token. -> True si se renovó."""
    r = _token_request({
        "grant_type": "refresh_token", "refresh_token": rt, "client_id": OAUTH_CLIENT,
    })
    if r is not None and r.status_code == 200:
        save_tokens(r.json())
        return True
    return False


def get_token():
    """Token de la sesión iniciada (persiste hasta cerrar sesión). -> token | None."""
    if not TOKEN_FILE.exists():
        return None
    t = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    if t.get("expires_at", 0) - time.time() < 60 and t.get("refresh_token"):
        if refresh_token(t["refresh_token"]):
            t = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    return t.get("access_token")


def fetch_usage():
    """-> (rows, status). status: 'ok' | 'expirado' | 'rate-limit' | 'sin sesión' | 'error'."""
    try:
        token = get_token()
    except (OSError, KeyError, json.JSONDecodeError):
        return [], "sin sesión"
    if not token:
        return [], "sin sesión"
    try:
        r = requests.get(URL, headers={**HEADERS, "Authorization": f"Bearer {token}"}, timeout=20)
    except requests.RequestException:
        return [], "error"
    if r.status_code == 401:
        return [], "expirado"
    if r.status_code == 429:
        return [], "rate-limit"
    if r.status_code != 200:
        return [], "error"
    return parse_limits(r.json()), "ok"


# ---- app de bandeja ---------------------------------------------------------

def already_running():
    """True si ya hay otra instancia. ponytail: mutex con nombre vía ctypes (stdlib)."""
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW(None, False, "TokeiTrayWidget")  # handle vive con el proceso
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


def run_tray():
    if already_running():
        return
    import pystray

    state = {"rows": [], "status": "...", "interval": POLL}
    wake = threading.Event()
    stop = threading.Event()

    def build_menu():
        from pystray import Menu, MenuItem
        items = []
        if state["status"] == "ok" and state["rows"]:
            for label, pct, iso, _sev in state["rows"]:
                reset = fmt_reset(iso)
                txt = f"{label:<14} {bar(pct)} {int(round(pct))}%"
                if reset:
                    txt += f"  · resetea {reset}"
                items.append(MenuItem(txt, lambda i, it: None))
        else:
            msg = {
                "expirado": "Sesión expirada — inicia sesión de nuevo",
                "rate-limit": "Rate-limit, reintentando…",
                "sin sesión": "Sin sesión — usa 'Iniciar sesión'",
                "error": "Sin conexión con la API",
            }.get(state["status"], "Cargando…")
            items.append(MenuItem(msg, lambda i, it: None))

        def do_login(i, it):
            def worker():
                try:
                    import subprocess
                    subprocess.run(_child_cmd("--login"))   # proceso propio, sin robo de foco
                except Exception:
                    pass
                wake.set()
            threading.Thread(target=worker, daemon=True).start()

        def do_logout(i, it):
            TOKEN_FILE.unlink(missing_ok=True)
            wake.set()

        items.append(Menu.SEPARATOR)
        if TOKEN_FILE.exists():
            items.append(MenuItem("Cerrar sesión", do_logout))
        else:
            items.append(MenuItem("Iniciar sesión", do_login))
        items += [
            MenuItem("Actualizar ahora", lambda i, it: wake.set()),
            MenuItem("Salir", lambda i, it: (stop.set(), wake.set(), i.stop())),
        ]
        return Menu(*items)

    def refresh(icon):
        rows, status = fetch_usage()
        if status == "rate-limit":
            state["interval"] = min(POLL_MAX, state["interval"] * 2)
        else:
            state["interval"] = POLL
            if status == "ok":
                state["rows"] = rows
        state["status"] = status
        pct = session_pct(state["rows"]) if status == "ok" else None
        icon.icon = make_icon(pct, color_for(pct))
        icon.menu = build_menu()
        if status == "ok" and state["rows"]:
            icon.title = " | ".join(f"{l} {int(round(p))}%" for l, p, _, _ in state["rows"])
        else:
            icon.title = f"Claude: {state['status']}"
        icon.update_menu()

    def loop(icon):
        icon.visible = True
        while not stop.is_set():
            refresh(icon)
            wake.wait(state["interval"])
            wake.clear()

    icon = pystray.Icon("Tokei", make_icon(None, color_for(None)),
                        "Tokei — uso de Claude", menu=build_menu())
    icon.run(setup=loop)


# ---- self-check -------------------------------------------------------------

def _test():
    assert session_pct([]) is None
    assert session_pct([("Sesión 5h", 15.0, None, "normal"), ("Semanal", 90.0, None, "normal")]) == 15.0
    assert session_pct([("Semanal", 90.0, None, "normal")]) is None
    assert color_for(None) == (160, 160, 160)
    assert color_for(10) == (0, 210, 90)
    assert color_for(70) == (255, 190, 0)
    assert color_for(90) == (255, 40, 40)
    assert bar(0) == "⬜" * 10
    assert bar(100) == "🟥" * 10
    assert bar(50).count("🟩") == 5
    assert fmt_reset(None) == "" and fmt_reset("nope") == ""
    assert len(fmt_reset("2026-06-24T15:20:00+00:00")) in (5, 11)
    rows = parse_limits({"limits": [
        {"kind": "session", "percent": 15, "resets_at": "2026-06-24T15:20:00+00:00", "severity": "normal"},
        {"kind": "weekly_all", "percent": 2, "resets_at": None, "severity": "normal"},
        {"kind": "x", "percent": None},  # se ignora
    ]})
    assert [r[0] for r in rows] == ["Sesión 5h", "Semanal"]
    for p in (None, 0, 50, 100):
        assert make_icon(p, color_for(p)).size == (64, 64)
    # PKCE: verifier/challenge sin padding y challenge = b64url(sha256(verifier))
    v = _b64url(b"x" * 32)
    assert "=" not in v
    assert _b64url(hashlib.sha256(v.encode()).digest()) == base64.urlsafe_b64encode(
        hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    print("OK: todas las comprobaciones pasaron")


if __name__ == "__main__":
    if "--test" in sys.argv:
        _test()
    elif "--login" in sys.argv:
        login()
    else:
        run_tray()
