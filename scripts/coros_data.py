"""Lectura compartida de `coros_data.json` (lo escribe scripts/coros_fetch.mjs).

Lo usan `daily_brief.py` (correo) y `update_dashboard.py` (dashboard).

Todo es tolerante a fallos a propósito: si el archivo no existe, está viejo o
le falta un campo, las funciones devuelven `None` y el correo / dashboard sale
solo con Strava, sin reventar. Coros es una API no oficial, así que "a veces no
hay datos" es el caso normal, no una excepción.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Se puede apuntar a otro JSON con COROS_DATA_PATH (útil para pruebas locales).
COROS_DATA_PATH = Path(os.environ.get("COROS_DATA_PATH") or ROOT / "coros_data.json")
# Sueño del MCP oficial de COROS (lo escribe scripts/coros_mcp.py). La librería
# no oficial de EvoLab no tiene horas de sueño; el MCP oficial sí, así que van
# en ficheros distintos y aquí se superponen.
SLEEP_PATH = Path(os.environ.get("COROS_SLEEP_PATH") or ROOT / "coros_sleep.json")
SLEEP_SOURCE = "COROS MCP oficial (querySleepData)"

# A partir de cuántas horas de antigüedad marcamos los datos como "de ayer".
STALE_AFTER_H = 26


def load(path: Path | str | None = None, sleep_path: Path | str | None = None) -> dict | None:
    """`coros_data.json` con el sueño del MCP oficial superpuesto, o None.

    Sin `coros_sleep.json` devuelve el payload tal cual (mismo objeto, sin
    copias), así que quien no use el MCP oficial no nota el cambio.
    """
    p = Path(path) if path else COROS_DATA_PATH
    data = None
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # JSON roto, medio escrito, etc.
            print(f"aviso: no pude leer {p.name} ({exc}); sigo sin datos de Coros.")
            data = None
        if data is not None and not isinstance(data, dict):
            data = None
    return overlay_sleep(data, load_sleep(sleep_path))


def load_sleep(path: Path | str | None = None) -> dict[str, float]:
    """`{"YYYY-MM-DD": horas}` del MCP oficial de COROS; {} si no hay nada.

    Tolerante a propósito: fichero ausente, JSON roto o noches sin horas se
    traducen en "no hay sueño", nunca en una excepción.
    """
    p = Path(path) if path else SLEEP_PATH
    if not p.exists():
        return {}
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"aviso: no pude leer {p.name} ({exc}); sigo sin sueño.")
        return {}
    out = {}
    for dia in (payload or {}).get("days", []) if isinstance(payload, dict) else []:
        if not isinstance(dia, dict):
            continue
        iso, h = _to_date(dia.get("date")), dia.get("sleep_hours")
        if iso is None:
            continue
        try:
            h = float(h)
        except (TypeError, ValueError):
            continue
        if 0.5 <= h <= 16:
            out[iso.isoformat()] = round(h, 2)
    return out


def overlay_sleep(data: dict | None, hours: dict[str, float], source: str = SLEEP_SOURCE):
    """Copia de `data` con las noches de `hours` fusionadas en `days`.

    El MCP oficial manda sobre lo que hubiera (es la fuente autorizada de sueño;
    la librería de EvoLab no la trae). Sin noches que aportar devuelve el `data`
    original intacto. Con noches y sin `coros_data.json` fabrica un payload
    mínimo, para que el correo pueda pintar el sueño aunque EvoLab esté caído.
    """
    if not hours:
        return data

    out = dict(data) if isinstance(data, dict) else {}
    days = [dict(d) if isinstance(d, dict) else d for d in (out.get("days") or [])]
    vistos = set()
    for dia in days:
        if not isinstance(dia, dict):
            continue
        iso = str(dia.get("date"))[:10]
        if iso in hours:
            dia["sleep_hours"] = hours[iso]
            vistos.add(iso)
    for iso in sorted(set(hours) - vistos):
        days.append({"date": iso, "sleep_hours": hours[iso], "has_data": True})
    days.sort(key=lambda d: str(d.get("date"))[:10] if isinstance(d, dict) else "")
    out["days"] = days

    ultimo = max(hours)
    latest = out.get("latest") if isinstance(out.get("latest"), dict) else None
    if latest is not None and str(latest.get("date"))[:10] == ultimo:
        latest = dict(latest)
        latest["sleep_hours"] = hours[ultimo]
        out["latest"] = latest
    elif latest is None or not latest.get("date"):
        out["latest"] = {"date": ultimo, "sleep_hours": hours[ultimo], "has_data": True}
    if out.get("sleep_hours") is None:
        out["sleep_hours"] = hours[ultimo]   # clave plana legacy: la lee summary()

    available = dict(out.get("available") or {})
    available["sleep_hours"] = True
    out["available"] = available
    out["sleep_source"] = source
    # El aviso "sleep_hours no disponible" deja de ser verdad.
    out["warnings"] = [w for w in (out.get("warnings") or [])
                       if "sleep_hours no disponible" not in str(w)]
    return out



def _to_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def generated_at(data: dict | None) -> datetime | None:
    if not data:
        return None
    raw = data.get("generated_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def is_stale(data: dict | None, now: datetime | None = None, hours: float = STALE_AFTER_H) -> bool:
    ts = generated_at(data)
    if ts is None:
        return True
    now = now or datetime.now(ts.tzinfo)
    return (now - ts).total_seconds() > hours * 3600


def day(data: dict | None, which: str = "latest") -> dict:
    """`latest` = objeto con la métrica del día más reciente con datos."""
    if not data:
        return {}
    if which == "latest":
        return data.get("latest") or {}
    return {}


def summary(data: dict | None) -> dict:
    """Resumen plano para el correo, con claves de respaldo legacy.

    Devuelve siempre dict (posiblemente con valores None) para que quien lo
    pinte no tenga que comprobar mil cosas.
    """
    latest = day(data, "latest") or {}
    days = (data or {}).get("days") or []
    yesterday_iso = None
    ref = _to_date(latest.get("date")) or date.today()
    yesterday_iso = (ref - timedelta(days=1)).isoformat()
    yesterday = next((d for d in days if d.get("date") == yesterday_iso), {})

    def pick(key: str):
        for src in (latest, data or {}, yesterday):
            v = src.get(key)
            if v is not None:
                return v
        return None

    return {
        "date": latest.get("date"),
        "resting_hr": pick("resting_hr"),
        "hrv": pick("hrv"),
        "hrv_base": pick("hrv_base"),
        "sleep_hours": pick("sleep_hours"),
        "load_short": pick("load_short"),
        "load_long": pick("load_long"),
        "load_ratio": pick("load_ratio"),
        "fatigue": pick("fatigue"),
        "performance": pick("performance"),
        "stale": is_stale(data),
        "available": (data or {}).get("available") or {},
        "warnings": (data or {}).get("warnings") or [],
        # Lo pone `overlay_sleep` cuando el sueño viene del MCP oficial de COROS.
        "sleep_source": (data or {}).get("sleep_source"),
    }


def by_date(data: dict | None) -> dict[str, dict]:
    """`{ "YYYY-MM-DD": dia }` para poder casar con las semanas del dashboard."""
    return {d["date"]: d for d in (data or {}).get("days", []) if d.get("date")}


def weekly(data: dict | None) -> dict[str, dict]:
    """`{ "lunes (YYYY-MM-DD)": {resting_hr, hrv, sleep_hours, load_ratio} }`."""
    out = {}
    for w in (data or {}).get("weeks", []):
        ws = w.get("week_start")
        if ws:
            out[ws] = w
    return out


def fmt_hours(v) -> str:
    """7.5 → '7 h 30 m'; None → '—'."""
    if v is None:
        return "—"
    try:
        total_min = round(float(v) * 60)
    except (TypeError, ValueError):
        return "—"
    return f"{total_min // 60} h {total_min % 60:02d} m"


def fmt_num(v, unit: str = "", decimals: int = 0) -> str:
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    txt = f"{n:.{decimals}f}" if decimals else str(int(round(n)))
    return f"{txt} {unit}".strip() if unit else txt
