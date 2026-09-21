"""Sueño oficial de COROS vía MCP (mcp.coros.com).

Por qué existe este fichero
---------------------------
La librería no oficial `@pinta365/coros` que usa `scripts/coros_fetch.mjs` solo
llega al endpoint de EvoLab (`analyse/query`), que **no tiene horas de sueño**:
por eso la casilla "Sueño" del correo salía siempre en `—`. El sueño estaba en
la API móvil (`apieu.coros.com`), cuyo login desloguea la app del teléfono —
inaceptable en un cron que corre cada madrugada.

COROS publicó en 2026 su **MCP oficial** (https://mcp.coros.com/mcp, OAuth con
PKCE y `offline_access`), y entre sus herramientas está `querySleepData`:
"sleep score, main sleep duration, deep/light/REM ratios, wakefulness, sleep
window and nap information". Es API oficial, no desloguea nada y se refresca
con un refresh token igual que Strava en este mismo repo.

Cómo se usa
-----------
1. UNA VEZ, en tu ordenador:  `python scripts/get_coros_mcp_token.py`
   → te da `COROS_MCP_CLIENT_ID` y `COROS_MCP_REFRESH_TOKEN` (secretos de GitHub).
2. Cada madrugada el workflow ejecuta este script, que refresca el token,
   llama a `querySleepData` y escribe `coros_sleep.json`.
3. `coros_data.load()` superpone ese fichero sobre `coros_data.json`, así que
   el correo **y** el dashboard se enteran sin más cambios.

Diagnóstico (la primera vez, o si COROS cambia algo):
    python scripts/coros_mcp.py --schema   # herramientas + inputSchema de querySleepData
    python scripts/coros_mcp.py --dump     # la respuesta cruda de la llamada

Variables de entorno:
    COROS_MCP_CLIENT_ID       client_id del registro dinámico (paso 1)
    COROS_MCP_REFRESH_TOKEN   refresh token (paso 1)
    COROS_MCP_ISSUER          por defecto https://mcp.coros.com (eu/us/cn: mcpeu/mcpus/mcpcn)
    COROS_MCP_URL             por defecto {issuer}/mcp
    COROS_SLEEP_DAYS          noches a pedir (30)
    COROS_SLEEP_PATH          dónde escribir (coros_sleep.json)
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# A propósito sin dependencias: este script y get_coros_mcp_token.py se ejecutan
# en el ordenador del usuario con un `python3` pelado, así que nada de requests.
# (El cliente oficial de COROS hace lo mismo: solo urllib.)

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_ISSUER = "https://mcp.coros.com"
# `offline_access` es lo que hace que el servidor devuelva refresh_token: sin
# él habría que volver a pasar por el navegador cada hora.
DEFAULT_SCOPES = "openid offline_access mcp.tools"
# Cliente público (`token_endpoint_auth_method: none`): no hay client_secret,
# así que en GitHub solo hace falta guardar el refresh token.
DEFAULT_CLIENT_NAME = "Kthuluh daily brief"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:43123/callback"
SLEEP_TOOL = "querySleepData"
PROTOCOL_VERSION = "2025-06-18"
TIMEOUT = 30

SLEEP_OUT_PATH = Path(os.environ.get("COROS_SLEEP_PATH") or ROOT / "coros_sleep.json")


class CorosMCPError(RuntimeError):
    """Cualquier fallo del flujo: el workflow lo tolera y el correo sale igual."""


# ---------------------------------------------------------------------------
# HTTP básico
# ---------------------------------------------------------------------------

class _Response:
    """Lo mínimo que hace falta de una respuesta HTTP (forma de requests.Response)."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.text = body

    def json(self):
        return json.loads(self.text)


def _http(url, *, form=None, json_body=None, headers=None):
    """POST con urllib. Un error HTTP también vuelve como _Response (con su cuerpo)."""
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        content_type = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        content_type = "application/x-www-form-urlencoded"
    else:
        data, content_type = None, None
    cabeceras = dict(headers or {})
    if content_type:
        cabeceras["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=cabeceras, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return _Response(resp.status, resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:      # 4xx/5xx: el cuerpo dice qué pasó
        return _Response(exc.code, exc.read().decode("utf-8", "replace"))
    except urllib.error.URLError as exc:      # DNS, TLS, sin red…
        raise CorosMCPError(f"no pude conectar con {url}: {exc.reason}") from exc


def _post(url, *, form=None, json_body=None, headers=None):
    resp = _http(url, form=form, json_body=json_body, headers=headers)
    try:
        payload = resp.json()
    except ValueError:
        payload = {"_raw_text": resp.text[:500]}
    if resp.status_code != 200:
        raise CorosMCPError(f"{url} → HTTP {resp.status_code}: {_safe(payload)}")
    return payload


def _safe(payload):
    """Nunca volcar un token a un log de GitHub Actions."""
    if not isinstance(payload, dict):
        return str(payload)[:200]
    escondidas = {"access_token", "refresh_token", "id_token", "code", "code_verifier"}
    return {k: ("***" if k in escondidas else v) for k, v in payload.items()}


# ---------------------------------------------------------------------------
# OAuth: registro dinámico + refresco
# ---------------------------------------------------------------------------

def register_client(issuer=DEFAULT_ISSUER, client_name=DEFAULT_CLIENT_NAME,
                    redirect_uri=DEFAULT_REDIRECT_URI, scopes=DEFAULT_SCOPES):
    """Registro dinámico (RFC 7591) → client_id. Es lo que hace el cliente oficial."""
    payload = _post(
        f"{issuer.rstrip('/')}/connect/register",
        json_body={
            "client_name": client_name,
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": scopes,
            "token_endpoint_auth_method": "none",
        },
    )
    client_id = payload.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise CorosMCPError(f"el registro no devolvió client_id: {_safe(payload)}")
    return client_id


def authorize_url(client_id, code_challenge, state, mcp_url,
                  issuer=DEFAULT_ISSUER, redirect_uri=DEFAULT_REDIRECT_URI, scopes=DEFAULT_SCOPES):
    from urllib.parse import urlencode

    return (f"{issuer.rstrip('/')}/oauth2/authorize?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "resource": mcp_url,
        "state": state,
    }))


def exchange_code(client_id, code, code_verifier, issuer=DEFAULT_ISSUER,
                  redirect_uri=DEFAULT_REDIRECT_URI):
    return _post(f"{issuer.rstrip('/')}/oauth2/token", form={
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    })


def refresh_access_token(client_id, refresh_token, issuer=DEFAULT_ISSUER):
    """El paso que corre cada madrugada. Devuelve (access_token, refresh_token).

    COROS puede rotar el refresh token en la respuesta, así que se devuelve el
    nuevo; si no viene, sigue valiendo el antiguo.
    """
    payload = _post(f"{issuer.rstrip('/')}/oauth2/token", form={
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    })
    access = payload.get("access_token")
    if not isinstance(access, str) or not access:
        raise CorosMCPError(f"el refresco no devolvió access_token: {_safe(payload)}")
    nuevo = payload.get("refresh_token")
    return access, (nuevo if isinstance(nuevo, str) and nuevo else refresh_token)


# ---------------------------------------------------------------------------
# MCP (JSON-RPC por HTTP, sin sesión: el servidor oficial es stateless)
# ---------------------------------------------------------------------------

def _mcp_headers(access_token, token_type="Bearer"):
    return {
        "Authorization": f"{token_type} {access_token}",
        "Accept": "application/json, text/event-stream",
    }


def mcp_call(mcp_url, access_token, method, params=None, request_id=1, token_type="Bearer"):
    payload = _post(mcp_url, json_body={
        "jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {},
    }, headers=_mcp_headers(access_token, token_type))
    if "error" in payload:
        raise CorosMCPError(f"MCP {method} falló: {_safe(payload['error'])}")
    return payload


def initialize(mcp_url, access_token, client_name=DEFAULT_CLIENT_NAME):
    mcp_call(mcp_url, access_token, "initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": client_name, "version": "1.0.0"},
    }, request_id=1)


def list_tools(mcp_url, access_token):
    tools, cursor, req_id = [], None, 2
    while True:
        params = {"cursor": cursor} if cursor else {}
        result = mcp_call(mcp_url, access_token, "tools/list", params, request_id=req_id).get("result")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            raise CorosMCPError(f"tools/list sin lista de herramientas: {_safe(result)}")
        tools.extend(result["tools"])
        cursor = result.get("nextCursor")
        if not isinstance(cursor, str) or not cursor:
            return tools
        req_id += 1


def call_tool(mcp_url, access_token, name, arguments):
    result = mcp_call(mcp_url, access_token, "tools/call",
                      {"name": name, "arguments": arguments}, request_id=2).get("result")
    if not isinstance(result, dict):
        raise CorosMCPError(f"tools/call sin result: {_safe(result)}")
    return result


# ---------------------------------------------------------------------------
# Argumentos de querySleepData (el esquema lo dice el servidor, no nosotros)
# ---------------------------------------------------------------------------

_START_RE = re.compile(r"start|begin|from|since", re.I)
_END_RE = re.compile(r"end|to|until|through", re.I)
_DATE_RE = re.compile(r"date|day|time", re.I)


def build_arguments(schema, start: date, end: date) -> dict:
    """Rellena el rango de fechas según el `inputSchema` que declare el servidor.

    No sabemos (ni asumimos) cómo se llaman los parámetros de `querySleepData`:
    aquí se leen del esquema y se emparejan por nombre. Si el esquema menciona
    un formato YYYYMMDD se manda así; si no, ISO.
    """
    props = (schema or {}).get("properties") or {}
    args = {}
    if not props:
        # Sin esquema: el intento más probable, y --schema dirá la verdad.
        return {"startDate": start.isoformat(), "endDate": end.isoformat()}

    yyyymmdd = bool(re.search(r"yyyymmdd|yyyy-MM-dd|format", json.dumps(schema), re.I)
                    and re.search(r"yyyymmdd", json.dumps(schema), re.I))

    def valor(d: date) -> str:
        return d.strftime("%Y%m%d") if yyyymmdd else d.isoformat()

    for nombre in props:
        if _DATE_RE.search(nombre) or _START_RE.search(nombre) or _END_RE.search(nombre):
            if _START_RE.search(nombre):
                args[nombre] = valor(start)
            elif _END_RE.search(nombre):
                args[nombre] = valor(end)
            else:
                args.setdefault(nombre, valor(end))
    return args or {"startDate": start.isoformat(), "endDate": end.isoformat()}


# ---------------------------------------------------------------------------
# Parseo tolerante de la respuesta
# ---------------------------------------------------------------------------

_DATE_KEYS = ("date", "day", "happenDay", "sleepDate", "startDate", "nightDate", "dateTime")
_HOURS_KEYS = ("mainSleepDuration", "totalDuration", "sleepDuration", "totalSleepTime",
               "mainSleepTime", "duration", "sleepTime", "totalSleep", "sleepLength")


def _to_date(value):
    """YYYYMMDD | YYYY-MM-DD | epoch s/ms → date. None si no cuadra."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = float(value)
        if n > 1e12:      # milisegundos
            n /= 1000
        if n < 1e8:       # no es un epoch razonable
            return None
        try:
            return datetime.fromtimestamp(n, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    txt = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(txt.replace("/", "-") if fmt == "%Y/%m/%d" else txt, fmt).date()
        except ValueError:
            continue
    return None


def _to_hours(value):
    """Segundos / minutos / horas → horas. Mismo criterio que toHours() del .mjs."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        for k in ("value", "duration", "total", "minutes", "seconds"):
            if k in value:
                return _to_hours(value[k])
        return None
    if isinstance(value, (list, tuple)):
        total = 0.0
        for v in value:
            h = _to_hours(v)
            if h is None:
                return None
            total += h
        return round(total, 2) or None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    h = n / 3600 if n >= 1000 else (n / 60 if n > 24 else n)
    return round(h, 2) if 0.5 <= h <= 16 else None


def _hours_of(record: dict):
    for key in _HOURS_KEYS:
        if key in record:
            h = _to_hours(record[key])
            if h is not None:
                return h
    for value in record.values():            # {"mainSleep": {"duration": 26400}}
        if isinstance(value, dict):
            h = _to_hours(value)
            if h is not None:
                return h
    return None


def _date_of(record: dict):
    for key in _DATE_KEYS:
        if key in record:
            d = _to_date(record[key])
            if d is not None:
                return d
    return None


def extract_records(obj, _depth=0):
    """Busca la lista de noches dentro de una respuesta de forma arbitraria.

    COROS no documenta la forma exacta, así que se recorre el JSON hasta dar con
    una lista cuyos elementos tengan fecha y duración. Devuelve [] si no la hay.
    """
    if _depth > 6:
        return []
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            if any(_date_of(x) and _hours_of(x) for x in obj):
                return obj
        for item in obj:
            found = extract_records(item, _depth + 1)
            if found:
                return found
    elif isinstance(obj, dict):
        for value in obj.values():
            found = extract_records(value, _depth + 1)
            if found:
                return found
    return []


def result_payload(result: dict):
    """De un `result` de tools/call a datos: structuredContent o content[].text."""
    if not isinstance(result, dict):
        return None
    if isinstance(result.get("structuredContent"), (dict, list)):
        return result["structuredContent"]
    for bloque in result.get("content") or []:
        if isinstance(bloque, dict) and bloque.get("type") == "text":
            txt = bloque.get("text")
            if not isinstance(txt, str):
                continue
            try:
                return json.loads(txt)
            except ValueError:
                continue
    return None


def parse_sleep(result: dict) -> list[dict]:
    """[{"date": "YYYY-MM-DD", "sleep_hours": 7.42}, ...] ordenado por fecha."""
    registros = extract_records(result_payload(result))
    out = {}
    for r in registros:
        d, h = _date_of(r), _hours_of(r)
        if d is None or h is None:
            continue
        out[d.isoformat()] = h
    return [{"date": iso, "sleep_hours": out[iso]} for iso in sorted(out)]


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

def fetch_sleep(days=None, *, client_id=None, refresh_token=None, issuer=None, mcp_url=None):
    """Devuelve (noches, nuevo_refresh_token). Lanza CorosMCPError si algo falla."""
    issuer = (issuer or os.environ.get("COROS_MCP_ISSUER") or DEFAULT_ISSUER).rstrip("/")
    mcp_url = mcp_url or os.environ.get("COROS_MCP_URL") or f"{issuer}/mcp"
    client_id = client_id or os.environ.get("COROS_MCP_CLIENT_ID")
    refresh_token = refresh_token or os.environ.get("COROS_MCP_REFRESH_TOKEN")
    if not client_id or not refresh_token:
        raise CorosMCPError(
            "faltan COROS_MCP_CLIENT_ID / COROS_MCP_REFRESH_TOKEN "
            "(genera unos con scripts/get_coros_mcp_token.py)")
    days = int(days or os.environ.get("COROS_SLEEP_DAYS") or 30)

    access, nuevo_refresh = refresh_access_token(client_id, refresh_token, issuer)
    initialize(mcp_url, access)

    tools = list_tools(mcp_url, access)
    tool = next((t for t in tools if t.get("name") == SLEEP_TOOL), None)
    if tool is None:
        nombres = ", ".join(sorted(str(t.get("name")) for t in tools)) or "ninguna"
        raise CorosMCPError(f"el servidor no ofrece {SLEEP_TOOL}. Herramientas: {nombres}")

    hoy = date.today()
    args = build_arguments(tool.get("inputSchema"), hoy - timedelta(days=days - 1), hoy)
    result = call_tool(mcp_url, access, SLEEP_TOOL, args)
    return parse_sleep(result), nuevo_refresh


def write_sleep(nights, path=None, source="COROS MCP oficial (querySleepData)"):
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "days": nights,
        "warnings": [] if nights else ["querySleepData no devolvió ninguna noche con horas."],
    }
    p = Path(path or SLEEP_OUT_PATH)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    def opt(nombre, default=None):
        return argv[argv.index(nombre) + 1] if nombre in argv else default

    issuer = (os.environ.get("COROS_MCP_ISSUER") or DEFAULT_ISSUER).rstrip("/")
    mcp_url = os.environ.get("COROS_MCP_URL") or f"{issuer}/mcp"

    if "--schema" in argv:
        access, _ = refresh_access_token(os.environ["COROS_MCP_CLIENT_ID"],
                                         os.environ["COROS_MCP_REFRESH_TOKEN"], issuer)
        initialize(mcp_url, access)
        tools = list_tools(mcp_url, access)
        print(f"{len(tools)} herramientas en {mcp_url}:")
        for t in tools:
            marca = "  ← sueño" if t.get("name") == SLEEP_TOOL else ""
            print(f"  · {t.get('name')}{marca}")
        tool = next((t for t in tools if t.get("name") == SLEEP_TOOL), None)
        if tool:
            print("\ninputSchema de querySleepData:")
            print(json.dumps(tool.get("inputSchema"), indent=2, ensure_ascii=False))
        return 0

    nights, nuevo = fetch_sleep()
    if "--dump" in argv:
        print(json.dumps(nights, indent=2, ensure_ascii=False))
    p = write_sleep(nights, opt("--out"))
    print(f"✓ {p} escrito ({len(nights)} noches con sueño)")
    if nights:
        print(f"  última: {nights[-1]['date']} → {nights[-1]['sleep_hours']} h")
    if nuevo != os.environ.get("COROS_MCP_REFRESH_TOKEN"):
        print("  ⚠ COROS rotó el refresh token: actualiza el secreto COROS_MCP_REFRESH_TOKEN.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CorosMCPError as exc:
        print(f"✗ COROS MCP: {exc}")
        sys.exit(2)  # el workflow usa continue-on-error: el correo sale igual
    except KeyError as exc:
        print(f"✗ COROS MCP: falta la variable de entorno {exc}")
        sys.exit(2)
