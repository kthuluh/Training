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

# A partir de cuántas horas de antigüedad marcamos los datos como "de ayer".
STALE_AFTER_H = 26


def load(path: Path | str | None = None) -> dict | None:
    """Devuelve el dict de coros_data.json o None si no hay nada utilizable."""
    p = Path(path) if path else COROS_DATA_PATH
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # JSON roto, medio escrito, etc.
        print(f"aviso: no pude leer {p.name} ({exc}); sigo sin datos de Coros.")
        return None
    if not isinstance(data, dict):
        return None
    return data


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
