"""Ejecuta esto UNA VEZ en tu ordenador (no en GitHub Actions) para obtener
COROS_MCP_CLIENT_ID y COROS_MCP_REFRESH_TOKEN.

A diferencia de Strava, aquí **no hay que crear ninguna app**: el MCP oficial de
COROS acepta registro dinámico de clientes, así que el script se registra solo
(cliente público, sin client_secret) y te da el client_id.

    pip install requests
    python scripts/get_coros_mcp_token.py

Después guarda en GitHub → Settings → Secrets and variables → Actions → Secrets:
    COROS_MCP_CLIENT_ID
    COROS_MCP_REFRESH_TOKEN

Es OAuth con PKCE, igual que el cliente oficial de COROS: se abre el navegador,
inicias sesión con tu cuenta de COROS, y pegas de vuelta el `code` de la URL de
redirección. Ese `code` vale un solo uso y unos minutos; el refresh token que se
obtiene con él es lo que usa el workflow cada madrugada.
"""

import base64
import hashlib
import secrets
import webbrowser

import coros_mcp

issuer = input(f"Issuer de COROS MCP [{coros_mcp.DEFAULT_ISSUER}]: ").strip() or coros_mcp.DEFAULT_ISSUER
issuer = issuer.rstrip("/")
mcp_url = f"{issuer}/mcp"

print("\n1) Registrando un cliente público en COROS (no hace falta client_secret)…")
client_id = coros_mcp.register_client(issuer)
print(f"   client_id = {client_id}")

verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
state = secrets.token_urlsafe(24)
url = coros_mcp.authorize_url(client_id, challenge, state, mcp_url, issuer=issuer)

print("\n2) Se abre el navegador: inicia sesión con tu cuenta de COROS y autoriza.")
print("   Al terminar te llevará a http://127.0.0.1:43123/callback?code=XXXX&state=…")
print("   (la página no cargará; es normal. Copia el valor de 'code' de la barra de direcciones.)")
if not webbrowser.open(url):
    print(f"\n   No pude abrir el navegador. Ábrelo tú: {url}")

code = input("\n3) Pega aquí el 'code': ").strip()

tokens = coros_mcp.exchange_code(client_id, code, verifier, issuer=issuer)
refresh = tokens.get("refresh_token")
if not refresh:
    raise SystemExit("COROS no devolvió refresh_token; el scope offline_access no se concedió.")

print("\n¡Listo! Guarda esto como secretos de GitHub:")
print(f"COROS_MCP_CLIENT_ID = {client_id}")
print(f"COROS_MCP_REFRESH_TOKEN = {refresh}")
print(f"\n(access_token válido ~{tokens.get('expires_in', '?')} s; el workflow lo refresca solo)")
