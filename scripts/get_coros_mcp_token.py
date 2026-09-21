"""Consigue COROS_MCP_CLIENT_ID y COROS_MCP_REFRESH_TOKEN.

Ejecuta esto UNA VEZ en tu ordenador (no en GitHub Actions):

    pip install requests
    python scripts/get_coros_mcp_token.py

A diferencia de Strava, **no hay que crear ninguna app**: el MCP oficial de
COROS acepta registro dinámico de clientes, así que el script se registra solo
(cliente público, sin client_secret) y te da el client_id.

Después:

1. Se abre el navegador → inicias sesión con tu cuenta de COROS y autorizas.
2. El script escucha en http://127.0.0.1:43123/callback y recoge el `code` él
   solo (si no puede, te pide que lo pegues).
3. Imprime los dos valores que van como secretos de GitHub:
       COROS_MCP_CLIENT_ID
       COROS_MCP_REFRESH_TOKEN

Es OAuth con PKCE, el mismo flujo que el cliente oficial de COROS. El `code`
vale un solo uso y caduca en minutos; el refresh token que se obtiene con él es
lo que usa el workflow cada madrugada.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import sys
import time
import urllib.parse
import webbrowser

import coros_mcp

PUERTO_CALLBACK = 43123
REDIRECT_URI = f"http://127.0.0.1:{PUERTO_CALLBACK}/callback"
ESPERA_SEGUNDOS = 300


def pkce():
    """(verifier, challenge) S256, como hace el cliente oficial de COROS."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def _html(titulo, detalle):
    return (f"<!doctype html><meta charset='utf-8'><title>{titulo}</title>"
            f"<body style='font-family:system-ui;padding:40px;text-align:center'>"
            f"<h2>{titulo}</h2><p>{detalle}</p>"
            "<p style='color:#888'>Puedes cerrar esta pestaña y volver a la terminal.</p>")


class _Callback(http.server.BaseHTTPRequestHandler):
    """Guarda el `code` del callback. `state` y `codigo` son de clase (por llamada)."""

    state_esperado = None
    codigo = None
    fallo = None

    def do_GET(self):  # noqa: N802 (firma de BaseHTTPRequestHandler)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = (query.get("code") or [None])[0]
        state = (query.get("state") or [None])[0]
        if code and state == self.state_esperado:
            type(self).codigo = code
            cuerpo, titulo = _html("¡Listo!", "Autorización recibida."), "OK"
        elif self.path.startswith("/favicon"):
            self.send_response(204)
            self.end_headers()
            return
        else:
            type(self).fallo = f"state no coincide o falta code (state={state!r})"
            cuerpo, titulo = _html("No cuadra", "El state no coincide: abre el enlace de la terminal."), "ERROR"
        data = cuerpo.encode("utf-8")
        self.send_response(200 if titulo == "OK" else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass  # sin ruido en la terminal


def espera_codigo(puerto=PUERTO_CALLBACK, state="", timeout=ESPERA_SEGUNDOS):
    """Espera el callback y devuelve el `code`, o None si se acaba el tiempo.

    `handle_request()` con timeout de 1 s: así se puede mirar el deadline sin
    dejar el proceso colgado para siempre.
    """
    handler = type("H", (_Callback,), {"state_esperado": state, "codigo": None, "fallo": None})
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", puerto), handler)
    except OSError as exc:
        print(f"   (no pude escuchar en 127.0.0.1:{puerto} — {exc}; tendrás que pegar el code a mano)")
        return None
    server.timeout = 1
    deadline = time.time() + timeout
    try:
        while time.time() < deadline and handler.codigo is None:
            server.handle_request()
    finally:
        server.server_close()
    if handler.fallo:
        print(f"   aviso: {handler.fallo}")
    return handler.codigo


def main():
    issuer = (input(f"Issuer de COROS MCP [{coros_mcp.DEFAULT_ISSUER}]: ").strip()
              or coros_mcp.DEFAULT_ISSUER).rstrip("/")
    mcp_url = f"{issuer}/mcp"

    print("\n1) Registrando un cliente público en COROS (no hace falta client_secret)…")
    client_id = coros_mcp.register_client(issuer)
    print(f"   client_id = {client_id}")

    verifier, challenge = pkce()
    state = secrets.token_urlsafe(24)
    url = coros_mcp.authorize_url(client_id, challenge, state, mcp_url,
                                  issuer=issuer, redirect_uri=REDIRECT_URI)

    print("\n2) Abre este enlace e inicia sesión con tu cuenta de COROS:")
    print(f"   {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass  # sin navegador (SSH, contenedor…): el enlace de arriba vale igual
    print(f"\n   Esperando la autorización en {REDIRECT_URI} "
          f"(máx. {ESPERA_SEGUNDOS // 60} min)…")

    code = espera_codigo(state=state)
    if not code:
        code = input("\n   No llegó el callback. Pega aquí el 'code' de la URL: ").strip()
    if not code:
        raise SystemExit("Sin code no hay token; vuelve a ejecutar el script.")

    tokens = coros_mcp.exchange_code(client_id, code, verifier, issuer=issuer,
                                     redirect_uri=REDIRECT_URI)
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise SystemExit("COROS no devolvió refresh_token: el scope offline_access no se concedió.")

    print("\n¡Listo! Añade estos 2 secretos en GitHub → Settings → Secrets and variables → Actions:")
    print(f"  COROS_MCP_CLIENT_ID = {client_id}")
    print(f"  COROS_MCP_REFRESH_TOKEN = {refresh}")
    print(f"\n(access_token válido ~{tokens.get('expires_in', '?')} s; el workflow lo refresca solo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
