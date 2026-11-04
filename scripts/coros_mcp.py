"""Sueño oficial de COROS vía MCP (mcp.coros.com).

Por qué existe este fichero
---------------------------
La librería no oficial `@pinta365/coros` que usa `scripts/coros_fetch.mjs` solo
llega al endpoint de EvoLab (`analyse/query`), que **no tiene horas de sueño**:
por eso la casilla "Sueño" del correo salía siempre en `—`. El sueño estaba en
la API móvil (`apieu.coros.com`), cuyo login desloguea la app del teléfono —
inaceptable en un cron que corre cada madrugada.

COROS publicó en 2026 su **MCP oficial** (https://mcp.coros.com/mcp, OAuth con
PKCE y `offline_access`), y entre sus herramientas está `querySleepOverview`:
"sleep score, main sleep duration, deep/light/REM ratios, wakefulness, sleep
window and nap information". Es API oficial, no desloguea nada y se refresca
con un refresh token igual que Strava en este mismo repo.

Cómo se usa
-----------
1. UNA VEZ, en tu ordenador:  `python scripts/get_coros_mcp_token.py`
   → te da `COROS_MCP_CLIENT_ID` y `COROS_MCP_REFRESH_TOKEN` (secretos de GitHub).
2. Cada madrugada el workflow ejecuta este script, que refresca el token,
   llama a `querySleepOverview` y escribe `coros_sleep.json`.
3. `coros_data.load()` superpone ese fichero sobre `coros_data.json`, así que
   el correo **y** el dashboard se enteran sin más cambios.

Diagnóstico (la primera vez, o si COROS cambia algo):
    python scripts/coros_mcp.py --schema   # herramientas + inputSchema de la herramienta de sueño
    python scripts/coros_mcp.py --dump     # la respuesta cruda de la llamada

Variables de entorno:
    COROS_MCP_CLIENT_ID       client_id del registro dinámico (paso 1)
    COROS_MCP_REFRESH_TOKEN   refresh token (paso 1)
    COROS_MCP_ACCESS_TOKEN    si está definido, se usa tal cual y se salta el
                              refresco (útil para pruebas o tokens de vida larga)
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
SLEEP_TOOL = "querySleepOverview"
SLEEP_TOOLS = (SLEEP_TOOL, "querySleepData")
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
        # MCP también puede responder mediante eventos SSE.
        chunks = [line[5:].lstrip() for line in resp.text.splitlines() if line.startswith("data:")]
        try:
            payload = json.loads("\n".join(chunks)) if chunks else {"_raw_text": resp.text[:500]}
        except ValueError:
            payload = {"_raw_text": resp.text[:500]}
    if not 200 <= resp.status_code < 300:
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


def resolve_issuer(issuer=DEFAULT_ISSUER):
    """Resolver el gateway global como hace el cliente oficial de COROS.

    Los clientes registrados en una región deben renovarse en esa misma región.
    Ante un descubrimiento fallido se detiene en vez de arriesgar credenciales
    contra un servidor distinto. Un issuer regional explícito no se redirige.
    """
    issuer = issuer.rstrip("/")
    allowed = {"https://mcp.coros.com", "https://mcpeu.coros.com",
               "https://mcpus.coros.com", "https://mcpcn.coros.com"}
    if issuer not in allowed:
        raise CorosMCPError("issuer COROS no permitido")
    if issuer != DEFAULT_ISSUER:
        return issuer
    url = issuer + "/.well-known/openid-configuration"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            payload = json.load(response)
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise CorosMCPError("no se pudo descubrir el issuer regional COROS") from exc
    regional = payload.get("issuer") if isinstance(payload, dict) else None
    regional = regional.rstrip("/") if isinstance(regional, str) else ""
    if regional not in allowed:
        raise CorosMCPError("COROS devolvió un issuer regional no válido")
    return regional


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
    if result.get("isError"):
        raise CorosMCPError("COROS rechazó la consulta de sueño (tools/call isError)")
    return result


# ---------------------------------------------------------------------------
# Argumentos de herramientas de sueño (compatibilidad con el esquema legacy)
# ---------------------------------------------------------------------------

_START_RE = re.compile(r"start|begin|from|since", re.I)
_END_RE = re.compile(r"end|to|until|through", re.I)
_DATE_RE = re.compile(r"date|day|time", re.I)


def build_arguments(schema, start: date, end: date) -> dict:
    """Rellena el rango de fechas según el `inputSchema` que declare el servidor.

    Para el servidor legacy se leen los parámetros del esquema:
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
        if n.is_integer() and 19000101 <= n <= 21001231:
            try:
                return datetime.strptime(str(int(n)), "%Y%m%d").date()
            except ValueError:
                return None
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
        if isinstance(value, str):
            match = re.fullmatch(r"\s*(?:(\d+)\s*h(?:ours?)?)?\s*(?:(\d+)\s*min(?:utes?)?)?\s*", value, re.I)
            if match and any(match.groups()):
                hours = int(match.group(1) or 0) + int(match.group(2) or 0) / 60
                return round(hours, 2) if 0.5 <= hours <= 16 else None
        return None
    if n <= 0:
        return None
    h = n / 3600 if n >= 1000 else (n / 60 if n > 24 else n)
    return round(h, 2) if 0.5 <= h <= 16 else None


def _hours_of(record: dict):
    for key in ("mainSleepMinutes", "mainSleepMinute", "mainSleepDurationMinutes",
                "totalMinutes", "totalDurationMinutes", "sleepDurationMinutes", "durationMinutes"):
        value = record.get(key)
        if value is not None:
            try:
                hours = float(value) / 60
                if 0.5 <= hours <= 16:
                    return round(hours, 2)
            except (ValueError, TypeError):
                pass
    for key in ("mainSleepSeconds", "totalDurationSeconds", "sleepDurationSeconds"):
        value = record.get(key)
        if value is not None:
            try:
                hours = float(value) / 3600
                if 0.5 <= hours <= 16:
                    return round(hours, 2)
            except (ValueError, TypeError):
                pass
    for key in ("sleep_hours",) + _HOURS_KEYS:
        if key in record:
            h = _to_hours(record[key])
            if h is not None:
                return h
    for key in ("mainSleep", "mainSleepData", "nightSleep", "sleep"):
        value = record.get(key)
        if isinstance(value, dict):
            h = _hours_of(value) or _to_hours(value)
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


def extract_records(obj, _depth=0, inherited_date=None):
    """Extrae registros anidados, sin confundir siestas con sueño principal."""
    if _depth > 10:
        return []
    if isinstance(obj, list):
        return [r for item in obj for r in extract_records(item, _depth + 1, inherited_date)]
    if isinstance(obj, str):
        try:
            parsed = json.loads(obj)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list, str)) and parsed != obj:
            return extract_records(parsed, _depth + 1, inherited_date)
        return [{"date": day, "sleep_hours": hours} for day, hours in _parse_sleep_text(obj).items()]
    if not isinstance(obj, dict):
        return []
    kind = str(obj.get("kind") or obj.get("type") or "").lower()
    if "nap" in kind or obj.get("isNap") is True:
        return []
    day = _date_of(obj) or inherited_date
    hours = _hours_of(obj)
    if day and hours is not None:
        return [{"date": day.isoformat(), "sleep_hours": hours}]
    records = []
    for key, value in obj.items():
        if "nap" in key.lower() or "siesta" in key.lower():
            continue
        nested_date = _to_date(key) or day
        if isinstance(value, (dict, list, str)) and key not in _DATE_KEYS:
            records.extend(extract_records(value, _depth + 1, nested_date))
    return records


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


def _parse_sleep_text(text):
    """Resumen textual oficial: fecha de despertar y sueño principal sin siestas."""
    dates = list(re.finditer(r"(?im)^[ \t]*(?:#{1,6}[ \t]*)?(?:sleep for[ \t]+|date[ \t]*:[ \t]*)?(\d{4}-\d{2}-\d{2}|\d{8})[ \t]*$", text))
    out = {}
    for i, match in enumerate(dates):
        end = dates[i + 1].start() if i + 1 < len(dates) else len(text)
        section = text[match.end():end]
        duration = re.search(r"(?im)^\s*Main Sleep(?:\s*\(asleep\))?\s*:\s*(?:(\d+)\s*h(?:ours?)?)?\s*(?:(\d+)\s*min(?:utes?)?)?", section)
        if not duration or not any(duration.groups()):
            continue
        hours = int(duration.group(1) or 0) + int(duration.group(2) or 0) / 60
        day = _to_date(match.group(1))
        if day and 0.5 <= hours <= 16:
            out[day.isoformat()] = round(hours, 2)
    return out


def parse_sleep(result: dict) -> list[dict]:
    """[{"date": "YYYY-MM-DD", "sleep_hours": 7.42}, ...] ordenado por fecha."""
    registros = extract_records(result_payload(result))
    out = {}
    for r in registros:
        d, h = _date_of(r), _hours_of(r)
        if d is None or h is None:
            continue
        out[d.isoformat()] = h
    if not out and isinstance(result, dict):
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                out.update(_parse_sleep_text(block["text"]))
    return [{"date": iso, "sleep_hours": out[iso]} for iso in sorted(out)]


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

def sleep_response_shape(result):
    """Diagnóstico sin fechas, valores sanitarios, textos ni credenciales."""
    payload = result_payload(result)
    blocks = (result or {}).get("content") or [] if isinstance(result, dict) else []
    texts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
    flags = []
    for word, pattern in (("fecha", r"\d{4}[-/]?\d{2}[-/]?\d{2}"),
                          ("sueño-principal", r"main.?sleep|sueño principal"),
                          ("duracion", r"duration|duraci[oó]n|\d+\s*(?:h|hours?|min)")):
        if any(re.search(pattern, t, re.I) for t in texts):
            flags.append(word)
    return f"JSON={type(payload).__name__}; textos={len(texts)}; indicadores={','.join(flags) or 'ninguno'}"


def parse_steps(result):
    """Pasos por fecha del MCP: JSON o texto, sin estimar días ausentes."""
    records = {}
    def walk(obj, inherited=None, depth=0):
        if depth > 10:
            return
        if isinstance(obj, list):
            for item in obj: walk(item, inherited, depth + 1)
        elif isinstance(obj, dict):
            day = _date_of(obj) or _to_date(obj.get("summaryDate")) or _to_date(obj.get("healthDate")) or inherited
            for key in ("steps", "step", "stepCount", "stepsCount", "totalSteps", "dailySteps"):
                value = obj.get(key)
                if isinstance(value, str): value = value.replace(",", "").strip()
                if isinstance(value, bool): continue
                try:
                    steps = float(value)
                    if day and steps.is_integer() and 0 <= steps <= 200000:
                        records[day.isoformat()] = int(steps)
                        break
                except (TypeError, ValueError):
                    pass
            for key, value in obj.items():
                if isinstance(value, (dict, list, str)) and key not in ("date", "day", "happenDay"):
                    walk(value, _to_date(key) or day, depth + 1)
        elif isinstance(obj, str):
            try:
                parsed = json.loads(obj)
            except ValueError:
                parsed = None
            if isinstance(parsed, (dict, list, str)) and parsed != obj:
                walk(parsed, inherited, depth + 1)
                return
            matches = list(re.finditer(r"\b(20\d{2}[-/]\d{2}[-/]\d{2}|20\d{6})\b", obj))
            for i, match in enumerate(matches):
                day = _to_date(match.group(1).replace("/", "-"))
                section = obj[match.end():matches[i+1].start() if i+1 < len(matches) else len(obj)]
                m = re.search(r"(?i)\b(?:steps?|step count|pasos)\s*[:=–-]\s*([\d,]+)", section)
                if day and m:
                    steps = int(m.group(1).replace(",", ""))
                    if steps <= 200000: records[day.isoformat()] = steps
    walk(result_payload(result))
    for block in (result or {}).get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            walk(block.get("text"))
    return [{"date": day, "steps": records[day]} for day in sorted(records)]


def fetch_sleep(days=None, *, client_id=None, refresh_token=None, access_token=None,
                issuer=None, mcp_url=None, token_store=None, health_out=None):
    """Devuelve (noches, nuevo_refresh_token). Lanza CorosMCPError si algo falla.

    `nuevo_refresh_token` es None cuando se entra con un access token directo
    (parámetro `access_token` o la variable COROS_MCP_ACCESS_TOKEN): en ese
    camino no se llama a /oauth2/token, así que no hay rotación que avisar.
    """
    issuer = (issuer or os.environ.get("COROS_MCP_ISSUER") or DEFAULT_ISSUER).rstrip("/")
    access = access_token or os.environ.get("COROS_MCP_ACCESS_TOKEN")
    if not access and not (client_id or os.environ.get("COROS_MCP_CLIENT_ID")):
        raise CorosMCPError("faltan COROS_MCP_CLIENT_ID / COROS_MCP_REFRESH_TOKEN")
    issuer = resolve_issuer(issuer)
    mcp_url = mcp_url or os.environ.get("COROS_MCP_URL") or f"{issuer}/mcp"
    if access:
        # Atajo: con un access token válido no hace falta refrescar nada.
        nuevo_refresh = None
    else:
        client_id = client_id or os.environ.get("COROS_MCP_CLIENT_ID")
        refresh_token = refresh_token or os.environ.get("COROS_MCP_REFRESH_TOKEN")
        if not client_id or not refresh_token:
            raise CorosMCPError(
                "faltan COROS_MCP_CLIENT_ID / COROS_MCP_REFRESH_TOKEN "
                "(genera unos con scripts/get_coros_mcp_token.py), "
                "o define COROS_MCP_ACCESS_TOKEN para saltarte el refresco")
        if token_store is not None:
            refresh_token = token_store.load(refresh_token)
        access, nuevo_refresh = refresh_access_token(client_id, refresh_token, issuer)
        # Guardar ANTES de cualquier llamada MCP: si falla el sueño, la
        # renovación de un solo uso no se debe perder.
        if token_store is not None and nuevo_refresh != refresh_token:
            token_store.save(nuevo_refresh)
    days = int(days or os.environ.get("COROS_SLEEP_DAYS") or 30)

    initialize(mcp_url, access)

    tools = list_tools(mcp_url, access)
    tool = next((t for name in SLEEP_TOOLS for t in tools if t.get("name") == name), None)
    if tool is None:
        nombres = ", ".join(sorted(str(t.get("name")) for t in tools)) or "ninguna"
        raise CorosMCPError(f"el servidor no ofrece herramientas de sueño compatibles ({', '.join(SLEEP_TOOLS)}). Herramientas: {nombres}")

    hoy = date.today()
    first = hoy - timedelta(days=days - 1)
    # Rangos acotados evitan servidores que limitan silenciosamente la consulta.
    # No se vuelve a renovar el token al consultar cada tramo.
    out = {}
    start = first
    shapes = []
    while start <= hoy:
        end = min(start + timedelta(days=29), hoy)
        args = build_arguments(tool.get("inputSchema"), start, end)
        if tool["name"] == "querySleepOverview":
            args = {"startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d")}
        result = call_tool(mcp_url, access, tool["name"], args)
        for row in parse_sleep(result):
            day = _to_date(row["date"])
            if day and first <= day <= hoy:
                out[row["date"]] = row
        shapes.append(sleep_response_shape(result))
        start = end + timedelta(days=1)
    if health_out is not None:
        health_tool = next((t for t in tools if t.get("name") == "queryDailyHealthData"), None)
        if health_tool:
            try:
                prop = ((health_tool.get("inputSchema") or {}).get("properties") or {}).get("days") or {}
                limit = prop.get("maximum") if isinstance(prop, dict) else None
                requested = min(30, int(limit)) if isinstance(limit, (int, float)) and not isinstance(limit, bool) and limit >= 1 else 30
                health = call_tool(mcp_url, access, "queryDailyHealthData", {"days": requested})
                health_out.extend(row for row in parse_steps(health)
                                  if (day := _to_date(row["date"])) and hoy - timedelta(days=29) <= day <= hoy)
                if not health_out:
                    print("COROS pasos: sin datos interpretables en queryDailyHealthData; " + sleep_response_shape(health))
            except (CorosMCPError, OSError):
                print("⚠ COROS pasos: no se pudo consultar queryDailyHealthData; el sueño sigue disponible.")
        else:
            print("⚠ COROS pasos: queryDailyHealthData no está disponible en esta cuenta.")
    if not out:
        # Sin datos de salud ni tokens: solo describe el tipo de respuesta.
        print("COROS sueño: 0 noches interpretables. Estructura (sin datos personales): " + "; ".join(sorted(set(shapes))))
    return [out[k] for k in sorted(out)], nuevo_refresh


def write_sleep(nights, path=None, source="COROS MCP oficial (sueño)", health_days=None):
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "days": nights,
        "health_days": health_days or [],
        "warnings": [] if nights else ["COROS no devolvió ninguna noche con horas de sueño."],
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
        issuer = resolve_issuer(issuer)
        mcp_url = os.environ.get("COROS_MCP_URL") or f"{issuer}/mcp"

    if "--schema" in argv:
        access = os.environ.get("COROS_MCP_ACCESS_TOKEN")
        if not access:
            raise CorosMCPError("--schema requiere COROS_MCP_ACCESS_TOKEN: no renueva tokens para evitar perder la rotación")
        initialize(mcp_url, access)
        tools = list_tools(mcp_url, access)
        print(f"{len(tools)} herramientas en {mcp_url}:")
        for t in tools:
            marca = "  ← sueño" if t.get("name") in SLEEP_TOOLS else ""
            print(f"  · {t.get('name')}{marca}")
        tool = next((t for name in SLEEP_TOOLS for t in tools if t.get("name") == name), None)
        if tool:
            print(f"\ninputSchema de {tool['name']}:")
            print(json.dumps(tool.get("inputSchema"), indent=2, ensure_ascii=False))
        return 0

    store = None
    if os.environ.get("GITHUB_ACTIONS") == "true" and not os.environ.get("COROS_MCP_ACCESS_TOKEN"):
        from coros_token_store import GitHubTokenStore, StateError
        if os.environ.get("GITHUB_REF") != "refs/heads/main":
            raise StateError("El estado MCP solo se actualiza desde main")
        resolved = resolve_issuer(issuer)
        store = GitHubTokenStore(os.environ.get("COROS_MCP_STATE_KEY"),
                                 os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY"),
                                 os.environ.get("COROS_MCP_CLIENT_ID"),
                                 os.environ.get("COROS_MCP_REFRESH_TOKEN"), resolved)
        issuer = resolved
    health_days = []
    nights, nuevo = fetch_sleep(issuer=issuer, token_store=store, health_out=health_days)
    if "--dump" in argv:
        print(json.dumps(nights, indent=2, ensure_ascii=False))
    p = write_sleep(nights, opt("--out"), health_days=health_days)
    print(f"{'✓' if nights else '⚠'} {p} escrito ({len(nights)} noches con sueño)")
    if nights:
        print(f"  última: {nights[-1]['date']} → {nights[-1]['sleep_hours']} h")
    print(f"  pasos: {len(health_days)} días con datos (MCP oficial)")
    if health_days:
        print(f"  último registro de pasos: {health_days[-1]['date']} → {health_days[-1]['steps']} pasos")
    if store is None and nuevo and nuevo != os.environ.get("COROS_MCP_REFRESH_TOKEN"):
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
    except Exception as exc:
        from coros_token_store import StateError
        if not isinstance(exc, StateError):
            raise
        print(f"✗ COROS MCP: {exc}")
        sys.exit(2)
