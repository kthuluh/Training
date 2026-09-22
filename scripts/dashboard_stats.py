"""
Cálculos puros del dashboard: actividades de Strava + `coros_data.json` → números.

Lo usa `scripts/update_dashboard.py` para rellenar los bloques `AUTO:` de **todas**
las pestañas (antes solo se refrescaba la de Resumen general + un par de cosas de
Historial).

Este módulo a propósito **no hace red y no toca HTML**: recibe la lista de
actividades ya descargadas (JSON crudo de Strava) y el dict de `coros_data.json`,
y devuelve números/listas. Así se puede probar entero con fixtures, sin
credenciales ni conexión (`tests/test_dashboard.py`).

Regla de oro, igual que en el resto del repo: **si falta un dato se devuelve
`None`** (o lista vacía). Nunca se inventa un valor; quien pinta decide entonces
si conserva el valor manual que ya había en el HTML.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
MESES_LARGO = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
               "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

# Tipos de actividad de Strava que cuentan como "carrera".
RUN_TYPES = {"run", "trailrun", "virtualrun"}
# Tipos que también son volumen **a pie**: caminar y senderismo. Suman al
# volumen semanal/mensual del dashboard igual que las carreras (son kilómetros
# de carga real), pero se pintan aparte para que se vea cuánto es correr.
WALK_TYPES = {"walk", "hike"}
# Todo lo que cuenta como volumen: carrera + caminatas. Lo que NO está aquí
# (bici, natación, remo, elíptica…) registra kilómetros pero no es carga de
# carrera, así que queda fuera del volumen a propósito. Si quieres contar
# también la bici, añade "ride" a WALK_TYPES (o a RUN_TYPES si prefieres
# verla dentro de la barra de carrera).
FOOT_TYPES = RUN_TYPES | WALK_TYPES
# Tipos que cuentan como fuerza (lo que el plan pide 2x/semana).
STRENGTH_TYPES = {"weighttraining"}


# ---------------------------------------------------------------------------
# utilidades
# ---------------------------------------------------------------------------

def _num(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def as_number(v):
    """float(v) o None. Es la red de seguridad para valores de Coros raros
    (un JSON editado a mano con `"resting_hr": "x"` no debe tumbar el cron)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _mean(values):
    v = [x for x in values if x is not None]
    if not v:
        return None
    return sum(v) / len(v)


def round_half_up(v):
    """round() de Python es bancario (0,5 → par); aquí queremos el de siempre."""
    return int(math.floor(float(v) + 0.5))


def fmt_es(v, decimals=1):
    """32.0 → '32,0' (coma decimal, como el resto del dashboard)."""
    if v is None:
        return "—"
    txt = f"{float(v):.{decimals}f}"
    return txt.replace(".", ",")


def fmt_time(seconds):
    """1290 → '21:30' ; 6360 → '1:46:00'."""
    if seconds is None:
        return "—"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def fmt_pace(seconds_per_km):
    """281.5 → '4:42/km'."""
    if seconds_per_km is None:
        return "—"
    s = int(round(seconds_per_km))
    m, sec = divmod(s, 60)
    return f"{m}:{sec:02d}/km"


def fmt_minutes(hours_or_min, hours=True):
    """1.87 (h) → '1h52m' ; 27 (min) → '27m' (para los deltas de sueño)."""
    if hours_or_min is None:
        return "—"
    total = int(round(float(hours_or_min) * 60)) if hours else int(round(float(hours_or_min)))
    h, m = divmod(abs(total), 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m"


def fmt_day(d):
    """date(2026, 9, 14) → '14 sep'."""
    if d is None:
        return "—"
    return f"{d.day} {MESES_LARGO[d.month - 1][:3]}"


def fmt_range(a, b):
    """('6 jul', '9 ago') → '6 jul–9 ago'."""
    if a is None or b is None:
        return "—"
    return f"{fmt_day(a)}–{fmt_day(b)}"


def fmt_week(monday):
    """Semana del 14 sep (a partir del lunes)."""
    return f"semana del {fmt_day(monday)}"


# ---------------------------------------------------------------------------
# actividades de Strava
# ---------------------------------------------------------------------------

def _local_date(a):
    raw = a.get("start_date_local") or a.get("start_date")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "")).date()
    except ValueError:
        return None


def normalize(raw_activities):
    """JSON crudo de `/athlete/activities` → lista ordenada de dicts homogéneos.

    Cada actividad queda con la fecha local, los km, el tiempo en movimiento y
    banderas ya resueltas (`is_run`, `is_strength`, `race`) para que el resto del
    código no tenga que saber de tipos de Strava.
    """
    out = []
    for a in raw_activities or []:
        if not isinstance(a, dict):
            continue
        d = _local_date(a)
        if d is None:
            continue
        stype = str(a.get("sport_type") or a.get("type") or "")
        km = (_num(a.get("distance")) or 0.0) / 1000
        out.append({
            "id": a.get("id"),
            "date": d,
            "name": str(a.get("name") or "").strip(),
            "type": stype,
            "km": round(km, 2),
            "moving_time": int(_num(a.get("moving_time")) or 0),
            "elapsed_time": int(_num(a.get("elapsed_time")) or 0),
            "elevation_m": _num(a.get("total_elevation_gain")) or 0.0,
            "avg_hr": _num(a.get("average_heartrate")),
            "race": int(_num(a.get("workout_type")) or -1) == 1,
            "is_run": stype.lower() in RUN_TYPES,
            "is_walk": stype.lower() in WALK_TYPES,
            "is_foot": stype.lower() in FOOT_TYPES,
            "is_strength": stype.lower() in STRENGTH_TYPES,
        })
    out.sort(key=lambda a: (a["date"], a["moving_time"]))
    return out


def runs(acts):
    return [a for a in acts if a["is_run"]]


def walks(acts):
    """Caminatas y senderismo (cuentan para el volumen, pero no son carrera)."""
    return [a for a in acts if a["is_walk"]]


def foot_activities(acts):
    """Todo lo que suma kilómetros a pie: carreras + caminatas."""
    return [a for a in acts if a["is_foot"]]


def strength_sessions(acts):
    return [a for a in acts if a["is_strength"]]


def week_start(d):
    return d - timedelta(days=d.weekday())  # lunes de esa semana


def week_label(monday):
    sunday = monday + timedelta(days=6)
    if monday.month == sunday.month:
        return f"{monday.day}-{sunday.day} {MESES[monday.month-1].lower()}"
    return f"{monday.day}{MESES[monday.month-1].lower()}-{sunday.day}{MESES[sunday.month-1].lower()}"


def in_week(d, monday):
    return week_start(d) == monday


def weekly_sessions(acts, monday):
    """Cuántas cosas se hicieron esa semana (lunes–domingo).

    `km` es el volumen **a pie** de la semana: carreras + caminatas/senderismo.
    `run_km` y `walk_km` lo desglosan (la bici, la natación… quedan fuera:
    registran kilómetros pero no son carga de carrera).
    """
    same = [a for a in acts if in_week(a["date"], monday)]
    run_km = round(sum(a["km"] for a in same if a["is_run"]), 1)
    walk_km = round(sum(a["km"] for a in same if a["is_walk"]), 1)
    return {
        "runs": sum(1 for a in same if a["is_run"]),
        "strength": sum(1 for a in same if a["is_strength"]),
        "walks": sum(1 for a in same if a["is_walk"]),
        "km": round(run_km + walk_km, 1),
        "run_km": run_km,
        "walk_km": walk_km,
    }


def weekly_km_map(acts, mondays):
    """{ 'YYYY-MM-DD'(lunes): km a pie de esa semana }."""
    return {m.isoformat(): weekly_sessions(acts, m)["km"] for m in mondays}


def weekly_km(acts, monday):
    """km a pie de esa semana (carrera + caminatas)."""
    return weekly_sessions(acts, monday)["km"]


def weekly_run_km(acts, monday):
    """km de carrera de esa semana (lo que contaba antes como 'volumen')."""
    return weekly_sessions(acts, monday)["run_km"]


def weekly_walk_km(acts, monday):
    """km caminando / de senderismo esa semana."""
    return weekly_sessions(acts, monday)["walk_km"]


def weekly_km_series(acts, mondays):
    """([total], [carrera], [caminando]) alineados con `mondays`, en una pasada.

    Los gráficos apilados necesitan las tres series a la vez y llamar a
    `weekly_sessions` por cada lunes es recorrer las actividades una vez por
    gráfico; aquí se agrupan de una sola vez.
    """
    index = {}
    for a in acts:
        if not a["is_foot"]:
            continue
        slot = index.setdefault(week_start(a["date"]).isoformat(), [0.0, 0.0])
        slot[0] += a["km"]
        if a["is_run"]:
            slot[1] += a["km"]
    totals, run, walk = [], [], []
    for m in mondays:
        km, km_run = index.get(m.isoformat(), (0.0, 0.0))
        totals.append(round(km, 1))
        run.append(round(km_run, 1))
        walk.append(round(km - km_run, 1))
    return totals, run, walk


def recent_mondays(today, n, include_current=False):
    """Últimos n lunes (de más antiguo a más reciente); por defecto sin la semana en curso."""
    this = week_start(today)
    last = this if include_current else this - timedelta(days=7)
    return [last - timedelta(weeks=i) for i in range(n - 1, -1, -1)]


def month_volume_split(acts, year):
    """{ 1..12: {'km': total a pie, 'run_km': carrera, 'walk_km': caminando} }."""
    out = {}
    for a in acts:
        if not a["is_foot"] or a["date"].year != year:
            continue
        slot = out.setdefault(a["date"].month, {"km": 0.0, "run_km": 0.0, "walk_km": 0.0})
        slot["km"] += a["km"]
        if a["is_run"]:
            slot["run_km"] += a["km"]
        else:
            slot["walk_km"] += a["km"]
    return {m: {k: round(v, 1) for k, v in vals.items()} for m, vals in out.items()}


def month_volume(acts, year):
    """{ 1..12: km a pie (carrera + caminatas) } (solo meses con datos)."""
    return {m: vals["km"] for m, vals in month_volume_split(acts, year).items()}


def month_run_volume(acts, year):
    """{ 1..12: km de carrera } — por si algo quiere solo lo que es correr."""
    return {m: vals["run_km"] for m, vals in month_volume_split(acts, year).items()}


def month_sessions(acts, year):
    """{ 1..12: {'runs': n, 'strength': n, 'walks': n} }."""
    out = {}
    for a in acts:
        if a["date"].year != year:
            continue
        slot = out.setdefault(a["date"].month, {"runs": 0, "strength": 0, "walks": 0})
        if a["is_run"]:
            slot["runs"] += 1
        elif a["is_strength"]:
            slot["strength"] += 1
        elif a["is_walk"]:
            slot["walks"] += 1
    return out


def races(acts, year=None):
    """Carreras (workout_type = Race en Strava), de la más reciente a la más antigua."""
    out = []
    for a in acts:
        if not a["race"] or a["km"] <= 0:
            continue
        if year is not None and a["date"].year != year:
            continue
        out.append({
            "name": a["name"] or "Carrera",
            "date": a["date"],
            "km": a["km"],
            "time_s": a["moving_time"] or None,
            "type": a["type"],
        })
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def best_effort(acts, target_km, tolerance=0.99):
    """Mejor tiempo equivalente a `target_km` entre las carreras de esa distancia.

    Aproximación honesta: coge las salidas que cubren al menos `target_km` (con un
    pequeño margen, porque Strava suele medir un pelín corto) y estima el tiempo
    al ritmo real de esa salida. No es el PR oficial de Strava, pero sí "tu mejor
    marca de los últimos 12 meses" y no depende de llamadas extra a la API.
    """
    best = None
    for a in acts:
        if not a["is_run"] or a["moving_time"] <= 0:
            continue
        if a["km"] < target_km * tolerance:
            continue
        pace = a["moving_time"] / a["km"]
        time_s = pace * target_km
        if best is None or time_s < best["time_s"]:
            best = {
                "time_s": time_s,
                "pace_s": pace,
                "name": a["name"],
                "date": a["date"],
                "km": a["km"],
            }
    return best


def strength_stats(acts, today, window_days=28):
    """Fuerza: última sesión y cuántas en la ventana actual y la anterior."""
    sessions = strength_sessions(acts)
    n_now = sum(1 for a in sessions if a["date"] > today - timedelta(days=window_days))
    n_prev = sum(
        1 for a in sessions
        if today - timedelta(days=2 * window_days) < a["date"] <= today - timedelta(days=window_days)
    )
    last = max((a["date"] for a in sessions), default=None)
    return {
        "last_date": last,
        "days_ago": (today - last).days if last else None,
        "n_now": n_now,
        "n_prev": n_prev,
        "n_total": len(sessions),
    }


def hard_runs(acts, hr_ceiling, today, days=28):
    """Salidas "fáciles" que en realidad van por encima del techo de Z2.

    Usa la FC media de la actividad (la trae la lista de Strava, sin pedir
    streams). Solo cuenta salidas con dato de pulso.
    """
    since = today - timedelta(days=days)
    window = [a for a in runs(acts) if a["date"] >= since]
    with_hr = [a for a in window if a["avg_hr"]]
    if not with_hr:
        return {"n_runs": len(window), "n_with_hr": 0, "n_hard": 0,
                "ceiling": hr_ceiling, "avg_hr": None, "worst": None}
    hard = [a for a in with_hr if a["avg_hr"] >= hr_ceiling]
    worst = max(with_hr, key=lambda a: a["avg_hr"])
    return {
        "n_runs": len(window),
        "n_with_hr": len(with_hr),
        "n_hard": len(hard),
        "ceiling": hr_ceiling,
        "avg_hr": _mean([a["avg_hr"] for a in with_hr]),
        "worst": {"name": worst["name"], "date": worst["date"], "avg_hr": worst["avg_hr"]},
    }


def volume_ramp(acts, today, weeks=12, lookback_weeks=4):
    """El pico de volumen de las últimas semanas y desde dónde venías.

    Devuelve None si no hay datos suficientes. `pct` = subida relativa
    pico/valles (None si el valle era 0 km, p. ej. tras vacaciones).
    """
    mondays = recent_mondays(today, weeks)
    km = {m: weekly_km(acts, m) for m in mondays}
    if not any(v > 0 for v in km.values()):
        return None
    peak_m = max(mondays, key=lambda m: km[m])
    peak_km = km[peak_m]
    candidates = [m for m in mondays if 0 < (peak_m - m).days <= lookback_weeks * 7]
    if not candidates or peak_km <= 0:
        return {"peak_monday": peak_m, "peak_km": peak_km, "trough_monday": None, "trough_km": None,
                "span_weeks": None, "pct": None}
    trough_m = min(candidates, key=lambda m: km[m])
    trough_km = km[trough_m]
    return {
        "peak_monday": peak_m,
        "peak_km": peak_km,
        "trough_monday": trough_m,
        "trough_km": trough_km,
        "span_weeks": max(1, (peak_m - trough_m).days // 7),
        "pct": ((peak_km - trough_km) / trough_km) if trough_km > 0 else None,
    }


def trend(acts, coros, today, weeks=4):
    """Últimas `weeks` semanas completas vs las `weeks` anteriores.

    Devuelve volumen (Strava), FC reposo y sueño (Coros, solo los días con dato).
    Cualquiera de los tres puede venir con `now`/`prev` a None si no hay datos.
    """
    this = week_start(today)
    now_start = this - timedelta(weeks=weeks)
    now_end = this - timedelta(days=1)
    prev_start = this - timedelta(weeks=2 * weeks)
    prev_end = this - timedelta(weeks=weeks) - timedelta(days=1)

    def window_mondays(start):
        return [start + timedelta(weeks=i) for i in range(weeks)]

    def vol_mean(start, getter=weekly_km):
        vals = [getter(acts, m) for m in window_mondays(start)]
        return round(sum(vals) / len(vals), 1) if any(v > 0 for v in vals) else None

    vol_now, vol_prev = vol_mean(now_start), vol_mean(prev_start)
    # Lo mismo pero solo con las carreras: sirve para decir cuánto del volumen
    # es correr y cuánto es caminar (None si no hay ninguna carrera).
    vol_run_now, vol_run_prev = vol_mean(now_start, weekly_run_km), vol_mean(prev_start, weekly_run_km)

    def coros_mean(key, start, end):
        vals = []
        for d in (coros or {}).get("days", []):
            try:
                day = date.fromisoformat(str(d.get("date"))[:10])
            except (ValueError, TypeError):
                continue
            if start <= day <= end:
                n = as_number(d.get(key))
                if n is not None:
                    vals.append(n)
        return round(sum(vals) / len(vals), 2) if vals else None, len(vals)

    rhr_now, rhr_n_now = coros_mean("resting_hr", now_start, now_end)
    rhr_prev, rhr_n_prev = coros_mean("resting_hr", prev_start, prev_end)
    sleep_now, sleep_n_now = coros_mean("sleep_hours", now_start, now_end)
    sleep_prev, sleep_n_prev = coros_mean("sleep_hours", prev_start, prev_end)
    steps_now, steps_n_now = coros_mean("steps", now_start, now_end)

    return {
        "weeks": weeks,
        "now": {"start": now_start, "end": now_end},
        "prev": {"start": prev_start, "end": prev_end},
        "vol": {"now": vol_now, "prev": vol_prev},
        "vol_run": {"now": vol_run_now, "prev": vol_run_prev},
        "rhr": {"now": rhr_now, "prev": rhr_prev, "n_now": rhr_n_now, "n_prev": rhr_n_prev},
        "sleep": {"now": sleep_now, "prev": sleep_prev, "n_now": sleep_n_now, "n_prev": sleep_n_prev},
        "steps": {"now": steps_now, "n_now": steps_n_now},
    }


# ---------------------------------------------------------------------------
# Coros (lo que trae `coros_data.json`)
# ---------------------------------------------------------------------------

def _coros_days(coros, today=None, days=None):
    out = []
    for d in (coros or {}).get("days", []):
        try:
            day = date.fromisoformat(str(d.get("date"))[:10])
        except (ValueError, TypeError):
            continue
        if today is not None and days is not None and not (today - timedelta(days=days) <= day <= today):
            continue
        rec = dict(d)
        rec["_date"] = day
        out.append(rec)
    return out


def coros_weekly_map(coros):
    """{ 'YYYY-MM-DD'(lunes): semana } a partir de `weeks` del JSON."""
    return {w["week_start"]: w for w in (coros or {}).get("weeks", []) if w.get("week_start")}


def coros_month_rhr(coros, year):
    """{ 1..12: {'avg': bpm, 'n': días con dato} } para el gráfico mensual."""
    buckets = {}
    for d in _coros_days(coros):
        n = as_number(d.get("resting_hr"))
        if d["_date"].year != year or n is None:
            continue
        buckets.setdefault(d["_date"].month, []).append(n)
    return {m: {"avg": round(sum(v) / len(v), 1), "n": len(v)} for m, v in buckets.items()}


def coros_month_steps(coros, year):
    buckets = {}
    for d in _coros_days(coros):
        n = as_number(d.get("steps"))
        if d["_date"].year != year or n is None:
            continue
        buckets.setdefault(d["_date"].month, []).append(n)
    return {m: round(sum(v) / len(v)) for m, v in buckets.items()}


def sleep_short_nights(coros, today, days=90, n=3):
    """Las noches más cortas de los últimos `days` días: [{date, hours, steps}]."""
    nights = [d for d in _coros_days(coros, today, days) if as_number(d.get("sleep_hours")) is not None]
    nights.sort(key=lambda d: as_number(d["sleep_hours"]))
    return [
        {"date": d["_date"], "hours": as_number(d["sleep_hours"]), "steps": as_number(d.get("steps"))}
        for d in nights[:n]
    ]


def high_step_days(coros, today, days=90, n=3, min_steps=None):
    """Los días de más pasos (y cuánto se durmió esa noche)."""
    rows = [d for d in _coros_days(coros, today, days) if as_number(d.get("steps")) is not None]
    rows.sort(key=lambda d: as_number(d["steps"]), reverse=True)
    if min_steps is not None:
        rows = [d for d in rows if as_number(d["steps"]) >= min_steps]
    return [
        {"date": d["_date"], "steps": as_number(d["steps"]), "hours": as_number(d.get("sleep_hours"))}
        for d in rows[:n]
    ]


# ---------------------------------------------------------------------------
# Análisis del sueño (bloque de Hábitos + línea del correo)
#
# Lo que hay y lo que no: la API **web** de Coros (la que usa coros_fetch.mjs a
# través de @pinta365/coros) no tiene endpoint de sueño. El script busca claves
# sueltas en la respuesta del día (SLEEP_KEYS) y, si tu cuenta las trae,
# `sleep_hours` se rellena solo. Las **fases** (ligero/profundo/REM) quedan
# fuera a propósito: exigen la API móvil con claves sacadas del APK y la llamada
# desloguea el reloj del móvil — no es algo que un cron de GitHub deba hacer.
#
# De ahí la regla de este bloque: si `sleep_hours` no viene, `n == 0`, todo va a
# None y quien pinta **conserva el valor manual**. Nunca se inventa una noche.
# ---------------------------------------------------------------------------

SLEEP_TARGET_HOURS = 7.5   # objetivo de sueño; se pisa con la env SLEEP_TARGET_HOURS
SLEEP_WINDOW_DAYS = 30     # noches que mira el bloque de Hábitos


def sleep_series(coros, today, days=SLEEP_WINDOW_DAYS):
    """[(date, horas)] de las últimas `days` noches con dato, en orden cronológico.

    La ventana es de exactamente `days` noches (hoy incluida), no `days + 1`:
    así las tarjetas, el veredicto y los puntos del gráfico cuentan lo mismo.
    Solo entran los días con `sleep_hours` numérico — si Coros no lo expone la
    lista sale vacía, y no con ceros, que falsearían cualquier media.
    """
    desde = today - timedelta(days=days - 1)
    out = []
    for d in (coros or {}).get("days", []):
        try:
            day = date.fromisoformat(str(d.get("date"))[:10])
        except (ValueError, TypeError):
            continue
        if not desde <= day <= today:
            continue
        h = as_number(d.get("sleep_hours"))
        if h is not None:
            out.append((day, h))
    out.sort(key=lambda r: r[0])
    return out


def sleep_analysis(coros, today, days=SLEEP_WINDOW_DAYS, target=SLEEP_TARGET_HOURS):
    """Todo lo que necesita el bloque de sueño: media, rango, deuda, tendencia y veredicto.

    Devuelve siempre el mismo dict. Con `n == 0` (Coros no expone el sueño) los
    campos van a None y `verdict` es `"nodata"`: el dashboard deja el texto
    manual y el correo no pinta la línea.
    """
    series = sleep_series(coros, today, days)
    hours = [h for _, h in series]
    out = {
        "n": len(hours), "days": days, "target": target,
        "mean": None, "lo": None, "hi": None,
        "below": 0, "on_target_pct": None, "debt_h": None,
        "last7": None, "prev7": None, "delta7": None,
        "worst": None, "best": None,
        "series": series, "verdict": "nodata",
    }
    if not hours:
        return out

    out["mean"] = round(sum(hours) / len(hours), 2)
    out["lo"], out["hi"] = min(hours), max(hours)
    out["below"] = sum(1 for h in hours if h < target)
    out["on_target_pct"] = round(100 * (len(hours) - out["below"]) / len(hours))
    out["debt_h"] = round(sum(max(0.0, target - h) for h in hours), 1)

    worst_i = min(range(len(hours)), key=lambda i: hours[i])
    best_i = max(range(len(hours)), key=lambda i: hours[i])
    out["worst"] = {"date": series[worst_i][0], "hours": hours[worst_i]}
    out["best"] = {"date": series[best_i][0], "hours": hours[best_i]}

    # Tendencia 7 vs 7 solo si hay dos semanas enteras con dato: con menos,
    # comparar sería ruido.
    if len(hours) >= 14:
        out["last7"] = round(sum(hours[-7:]) / 7, 2)
        out["prev7"] = round(sum(hours[-14:-7]) / 7, 2)
        out["delta7"] = round(out["last7"] - out["prev7"], 2)

    if out["mean"] >= target:
        out["verdict"] = "ok"
    elif out["mean"] >= target - 0.5:
        out["verdict"] = "close"
    else:
        out["verdict"] = "debt"
    return out


def sleep_verdict(a):
    """Frase del veredicto, compartida por el dashboard y el correo.

    Devuelve None con `"nodata"`: así los dos sitios callan a la vez en lugar de
    decir cosas distintas sobre la misma noche.
    """
    if not a or a.get("verdict") == "nodata":
        return None
    mean, target = a["mean"], a["target"]
    noches = "noche" if a["n"] == 1 else "noches"
    head = f"{fmt_minutes(mean)} de media en {a['n']} {noches}"
    if a["verdict"] == "ok":
        txt = f"{head} — por encima del objetivo de {fmt_minutes(target)}."
    elif a["verdict"] == "close":
        txt = f"{head} — a {fmt_minutes(target - mean)} del objetivo de {fmt_minutes(target)}."
    else:
        txt = (f"{head} — {fmt_minutes(target - mean)} por noche por debajo del objetivo de "
               f"{fmt_minutes(target)}: {fmt_minutes(a['debt_h'])} de deuda acumulada.")
    if a["delta7"] is not None:
        direccion = "sube" if a["delta7"] > 0 else ("baja" if a["delta7"] < 0 else "se mantiene")
        txt += f" La última semana {direccion} {fmt_minutes(abs(a['delta7']))} respecto a la anterior."
    return txt


# ---------------------------------------------------------------------------
# zonas de FC (Karvonen) — mismo criterio que la tabla del dashboard
# ---------------------------------------------------------------------------

ZONE_PCTS = [("Z1", 0.50, 0.60), ("Z2", 0.60, 0.70), ("Z3", 0.70, 0.80),
             ("Z4", 0.80, 0.90), ("Z5", 0.90, 1.00)]


def karvonen(hr_rest, hr_max):
    """{'Z1': (lo, hi), ...} sin solapes: cada zona empieza donde acaba la anterior +1.

    Con hr_rest=50 / hr_max=182 da exactamente la tabla que ya había en el HTML
    (Z1 116–129, Z2 130–142, Z3 143–156, Z4 157–169, Z5 170–182).
    """
    hrr = hr_max - hr_rest
    out = {}
    prev = None
    for name, lo_pct, hi_pct in ZONE_PCTS:
        hi = round_half_up(hr_rest + hrr * hi_pct)
        lo = round_half_up(hr_rest + hrr * lo_pct) if prev is None else prev + 1
        out[name] = (lo, hi)
        prev = hi
    return out


def zone_table(hr_rest, hr_max):
    """Filas listas para la tabla de zonas (incluye el rango completo)."""
    zones = karvonen(hr_rest, hr_max)
    return [{"name": name, "lo": zones[name][0], "hi": zones[name][1]} for name, _, _ in ZONE_PCTS]
