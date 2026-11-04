"""
Correo diario de entrenamiento + dieta + resumen (Kthuluh).

Fuentes:
  - Strava API oficial (actividad de ayer + volumen de la semana).
  - Coros (OPCIONAL, no oficial): lee `coros_data.json`, que escribe
    `scripts/coros_fetch.mjs` con la librería @pinta365/coros. FC reposo,
    HRV nocturna y carga de entrenamiento (EvoLab). Si el archivo no existe
    o está caído, el correo se manda igual sin esos datos.
  - Sueño (OPCIONAL, API **oficial**): `coros_sleep.json`, que escribe
    `scripts/coros_mcp.py` contra el MCP oficial de COROS (`querySleepData`).
    La librería de EvoLab no tiene horas de sueño, por eso va aparte; se
    superpone en `coros_data.load()`. Si falta, la casilla sale en "—".

Variables de entorno requeridas (se configuran como GitHub Secrets):
  STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_REFRESH_TOKEN
  GMAIL_USER, GMAIL_APP_PASSWORD, EMAIL_TO
"""

import os
import smtplib
import ssl
import re
from html import escape
from zoneinfo import ZoneInfo
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # para importar coros_data

import coros_data
import dashboard_stats as stats   # análisis de sueño compartido con el dashboard

# ---------------------------------------------------------------------------
# 1. CONFIG — ajusta aquí si cambia algo de tu plan o tus datos
# ---------------------------------------------------------------------------

PLAN_START = date(2026, 9, 7)  # lunes = semana 1, día 1 del bloque 10K
WEIGHT_KG = 73
HR_REST = 50
HR_MAX = 182
# Objetivo de sueño del bloque de Hábitos (y de la línea del correo). Se puede
# pisar sin tocar el código: SLEEP_TARGET_HOURS=8 como variable del workflow.
SLEEP_TARGET_HOURS = float(os.environ.get("SLEEP_TARGET_HOURS") or stats.SLEEP_TARGET_HOURS)

# Si True y Coros responde con una FC reposo, las zonas Karvonen del correo se
# recalculan con ese valor en vez del HR_REST fijo de arriba. Útil si tu FC
# reposo ha cambiado; ponlo False si prefieres zonas estables semana a semana.
HR_REST_FROM_COROS = False

# Zonas de FC (Karvonen). `zones_for` permite recalcularlas con la FC reposo
# de hoy que viene de Coros si HR_REST_FROM_COROS está activado.
def hr_zone(pct_lo, pct_hi, hr_rest=HR_REST):
    hrr = HR_MAX - hr_rest
    lo = round(hr_rest + hrr * pct_lo)
    hi = round(hr_rest + hrr * pct_hi)
    return f"{lo}-{hi} bpm"


def zones_for(hr_rest=HR_REST):
    return {
        "Z1": hr_zone(0.50, 0.60, hr_rest),
        "Z2": hr_zone(0.60, 0.70, hr_rest),
        "Z3": hr_zone(0.70, 0.80, hr_rest),
        "Z4": hr_zone(0.80, 0.90, hr_rest),
        "Z5": hr_zone(0.90, 1.00, hr_rest),
    }


ZONES = zones_for()

# Plan semana a semana (20 semanas: 8 de bloque 10K + 12 de bloque 21K).
# facil / calidad / larga = descripción de la sesión ese día.
WEEKS = [
    # Bloque 1 — 10K (semanas 1-8)
    {"facil": "7-9 km Z2 (5:50-6:05/km)", "calidad": "6x800m @4:20-4:25/km, rec. 2min trote", "larga": "12-13 km Z2"},
    {"facil": "7-9 km Z2 (5:50-6:05/km)", "calidad": "6x800m @4:20-4:25/km, rec. 2min trote", "larga": "12-13 km Z2"},
    {"facil": "8-10 km Z2", "calidad": "5x1000m @4:25-4:30/km, rec. 90s", "larga": "14 km Z2, últimos 3 km a 5:00/km"},
    {"facil": "8-10 km Z2", "calidad": "5x1000m @4:25-4:30/km, rec. 90s", "larga": "14 km Z2, últimos 3 km a 5:00/km"},
    {"facil": "8 km Z2", "calidad": "4x1600m @4:30-4:35/km, rec. 3min", "larga": "15-16 km, con 5 km a ritmo objetivo (4:28-4:32/km)"},
    {"facil": "8 km Z2", "calidad": "4x1600m @4:30-4:35/km, rec. 3min", "larga": "15-16 km, con 5 km a ritmo objetivo (4:28-4:32/km)"},
    {"facil": "7 km Z2 suave", "calidad": "3x1000m @4:18-4:22/km, rec. 3min", "larga": "10 km Z2"},
    {"facil": "5 km Z2", "calidad": "4x400m suave + 2 progresivos a ritmo objetivo", "larga": None, "nota": "¡Carrera de 10K el domingo! Objetivo: <45:00."},
    # Bloque 2 — 21K (semanas 9-20)
    {"facil": "5 km Z2 suave", "calidad": "4x400m suave + 2 progresivos, sin forzar", "larga": "Cursa Bombers Barcelona — 10K", "nota": "Domingo 8 de noviembre: Cursa Bombers Barcelona (10K). La carrera sustituye la tirada larga."},
    {"facil": "4-5 km Z2 muy suave, solo si estás recuperado", "calidad": None, "larga": "8-10 km Z2 suave, solo si estás recuperado", "nota": "Semana de recuperación tras Bombers: prioriza descanso si persiste la fatiga."},
    {"facil": "8-9 km Z2", "calidad": "tempo semanal @4:50-5:00/km", "larga": "14 km progresiva"},
    {"facil": "5 km Z2 suave", "calidad": "4x400m suave + 2 progresivos, sin forzar", "larga": "Cursa Jean Bouin — 10K", "nota": "Domingo 29 de noviembre: Cursa Jean Bouin (10K). La carrera sustituye la tirada larga."},
    {"facil": "4-5 km Z2 muy suave, solo si estás recuperado", "calidad": None, "larga": "10-12 km Z2 suave, solo si estás recuperado", "nota": "Semana de recuperación tras Jean Bouin: prioriza descanso si persiste la fatiga."},
    {"facil": "6 km Z2 suave (descarga -30%)", "calidad": None, "larga": "11 km"},
    {"facil": "8 km Z2", "calidad": "ritmo 21K específico", "larga": "17-19 km, con 10-12 km a 4:44-4:48/km"},
    {"facil": "8 km Z2", "calidad": "ritmo 21K específico", "larga": "17-19 km, con 10-12 km a 4:44-4:48/km"},
    {"facil": "6 km Z2 suave (descarga)", "calidad": None, "larga": "13 km suave"},
    {"facil": "7-8 km Z2 suave", "calidad": "tempo controlado 3-4 km a 4:50-5:00/km", "larga": "14-15 km Z2 suave"},
    {"facil": "5-6 km Z2 suave", "calidad": "3x1000m a ritmo 21K, sin forzar", "larga": "10-12 km Z2 suave"},
    {"facil": "4-5 km Z2 muy suave + strides", "calidad": None, "larga": None, "nota": "¡Carrera de 21K el domingo! Objetivo: 1:40:00."},
]

# día de la semana (0=lunes) -> tipo de sesión
DAY_TO_SESSION = {0: "facil", 2: "calidad", 3: "facil", 6: "larga"}

DIET = {
    "descanso": {"kcal": "2.350-2.500 kcal", "prot": "117-131 g", "carbs": "220-365 g (3-5 g/kg)", "grasa": "75-95 g"},
    "facil": {"kcal": "2.600-2.800 kcal", "prot": "117-131 g", "carbs": "220-365 g (3-5 g/kg)", "grasa": "75-95 g"},
    "calidad": {"kcal": "2.900-3.200 kcal", "prot": "117-131 g", "carbs": "440-585 g (6-8 g/kg)", "grasa": "75-95 g"},
    "larga": {"kcal": "2.900-3.200 kcal", "prot": "117-131 g", "carbs": "440-585 g (6-8 g/kg)", "grasa": "75-95 g"},
}


# ---------------------------------------------------------------------------
# 2. STRAVA — actividad de ayer + volumen de los últimos 7 días
# ---------------------------------------------------------------------------

def strava_access_token():
    resp = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": os.environ["STRAVA_CLIENT_ID"],
            "client_secret": os.environ["STRAVA_CLIENT_SECRET"],
            "grant_type": "refresh_token",
            "refresh_token": os.environ["STRAVA_REFRESH_TOKEN"],
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def strava_activities(access_token, after_epoch, before_epoch):
    out = []
    for page in range(1, 11):
        resp = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"after": after_epoch, "before": before_epoch, "per_page": 200, "page": page},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        out.extend(batch)
        if len(batch) < 200:
            return out
    raise RuntimeError("Strava: demasiadas actividades para completar el periodo")


def fmt_pace(distance_m, moving_time_s):
    if not distance_m:
        return "-"
    sec_per_km = moving_time_s / (distance_m / 1000)
    m, s = divmod(int(sec_per_km), 60)
    return f"{m}:{s:02d}/km"


def get_strava_summary(today):
    # Límites locales: UTC cambia con el horario de verano en España.
    zone = ZoneInfo("Europe/Madrid")
    yesterday = today - timedelta(days=1)
    start = int(datetime.combine(today - timedelta(days=7), datetime.min.time(), tzinfo=zone).timestamp())
    end = int(datetime.combine(today, datetime.min.time(), tzinfo=zone).timestamp())
    try:
        raw = strava_activities(strava_access_token(), start, end)
        acts = [a for a in stats.normalize(raw) if today - timedelta(days=7) <= a["date"] < today]
    except (requests.RequestException, KeyError, ValueError, RuntimeError) as exc:
        print(f"Strava no disponible para el seguimiento: {type(exc).__name__}")
        return {"yesterday": "Datos de Strava no disponibles.", "week_km": "—", "activities": None}
    yesterday_acts = [a for a in acts if a["date"] == yesterday]
    y_text = " | ".join(f"{a['name']} ({a['type']}): {a['km']:.1f} km" for a in yesterday_acts)
    return {"yesterday": y_text or "No hay actividad registrada en Strava.",
            "week_km": round(sum(a["km"] for a in acts if a["is_foot"]), 1), "activities": acts}


# ---------------------------------------------------------------------------
# 3. COROS (opcional) — lee coros_data.json si existe
# ---------------------------------------------------------------------------

def get_coros_summary(today=None):
    """Resumen plano de Coros, o None si no hay archivo / está ilegible."""
    data = coros_data.load()
    if data is None:
        return None
    s = coros_data.summary(data)
    # Si el único dato es "stale" y todo lo demás vacío, tratamos como sin datos.
    if all(s.get(k) is None for k in ("resting_hr", "hrv", "sleep_hours", "load_ratio")):
        return None
    # Análisis de sueño de la ventana larga: el mismo cálculo y el mismo objetivo
    # que pinta el bloque de Hábitos del dashboard, para que el correo y el panel
    # no puedan contradecirse. Sale con n == 0 si Coros no expone sleep_hours, y
    # entonces _coros_html no pinta la línea (no se inventa una noche).
    s["sleep_30d"] = stats.sleep_analysis(data, today or date.today(),
                                          days=stats.SLEEP_WINDOW_DAYS,
                                          target=SLEEP_TARGET_HOURS)
    return s


# ---------------------------------------------------------------------------
# 4. Armar el plan y la dieta de hoy
# ---------------------------------------------------------------------------

def get_today_plan(today):
    days_in = (today - PLAN_START).days
    if days_in < 0:
        return None  # el plan todavía no empieza
    if days_in >= len(WEEKS) * 7:
        return {"week_num": len(WEEKS), "bloque": "21K", "session_type": "descanso",
                "session_desc": "Plan completado. Recuperación tras la carrera: descansa o camina suave según sensaciones.",
                "nota": "El plan finalizó el 24 de enero; prepara el siguiente ciclo antes de retomar la carga."}
    week_idx = days_in // 7
    week = WEEKS[week_idx]
    session_type = DAY_TO_SESSION.get(today.weekday(), "descanso")
    session_desc = week.get(session_type)
    # La carrera de 21K ya estaba prevista en la última semana; el domingo
    # sustituye la tirada larga y utiliza su pauta nutricional.
    if today == date(2027, 1, 24):
        session_type, session_desc = "larga", "Mitja de Granollers — 21K. Objetivo orientativo: 1:40:00."
    if today in (date(2026, 11, 5), date(2026, 11, 26)):
        session_desc = "4 km Z2 muy suave o descanso si hay fatiga"
    if session_desc is None:
        session_type = "descanso"
        session_desc = "Descanso o fuerza 25-30 min (sentadilla, zancada, puente de glúteo, core)."
    return {
        "week_num": week_idx + 1,
        "bloque": "10K" if week_idx < 8 else "21K",
        "session_type": session_type,
        "session_desc": session_desc,
        "nota": week.get("nota"),
    }


def _activity_summary(activities):
    """Actividad de ayer, incluyendo deportes distintos de carrera o fuerza."""
    if not activities:
        return "No hay actividades registradas en Strava; podría faltar alguna sincronización."
    items = []
    for a in activities:
        details = []
        if a.get("km", 0) > 0:
            details.append(f"{a['km']:.1f} km")
        if a.get("moving_time", 0) > 0:
            details.append(f"{round(a['moving_time'] / 60)} min en movimiento")
        if a.get("avg_hr") is not None:
            details.append(f"FC media {round(a['avg_hr'])} ppm")
        suffix = f" · {', '.join(details)}" if details else ""
        items.append(f"{a.get('name') or 'Actividad'} ({a.get('type') or 'tipo desconocido'}){suffix}")
    return " | ".join(items)


def _high_effort_indicators(activities):
    """Indicadores conservadores, no una medición clínica de la carga."""
    indicators = []
    for a in activities:
        hr = a.get("avg_hr")
        if hr is not None and hr >= stats.karvonen(HR_REST, HR_MAX)["Z4"][0]:
            indicators.append("FC media en Z4 o superior según las zonas de referencia")
        if a.get("is_run") and a.get("km", 0) >= 10:
            indicators.append("carrera de al menos 10 km")
        if a.get("is_run") and a.get("moving_time", 0) >= 3600:
            indicators.append("carrera de al menos 60 minutos")
        if a.get("race") and a.get("is_run"):
            indicators.append("actividad marcada como carrera")
    return list(dict.fromkeys(indicators))


def assess_adherence(today, acts):
    """Comparación orientativa del plan con todo lo registrado el día anterior."""
    yesterday = today - timedelta(days=1)
    previous, planned = get_today_plan(yesterday), get_today_plan(today)
    today_desc = (planned or {}).get("session_desc", "Plan aún no iniciado.")
    seen = None if acts is None else [a for a in acts if a.get("date") == yesterday]
    recorded = "Strava no está disponible; no se puede comprobar qué se hizo." if seen is None else _activity_summary(seen)
    if not previous or not planned or yesterday < PLAN_START or yesterday >= PLAN_START + timedelta(weeks=len(WEEKS)):
        return {"yesterday": "Ayer no había una sesión dentro del plan; no se puede evaluar el cumplimiento.",
                "yesterday_plan": "Fuera del plan.", "recorded": recorded, "today": today_desc,
                "advice": "Sin comparación disponible; ten en cuenta las actividades registradas y tus sensaciones."}
    if seen is None:
        return {"yesterday": "No se pudo consultar Strava; no se puede determinar el cumplimiento.",
                "yesterday_plan": previous["session_desc"], "recorded": recorded, "today": today_desc,
                "advice": "Comprueba tus registros y prioriza recuperación si notas fatiga."}

    running = [a for a in seen if a.get("is_run")]
    strength = [a for a in seen if a.get("is_strength")]
    other = [a for a in seen if not a.get("is_run") and not a.get("is_strength")]
    high = _high_effort_indicators(seen)
    prev_type = previous["session_type"]
    optional_run = prev_type != "descanso" and "o descanso" in previous["session_desc"].lower()
    flexible = prev_type == "descanso" or optional_run
    if optional_run and not seen:
        status = "Ayer no consta el rodaje opcional: el descanso también estaba previsto como opción. Comprueba si falta alguna actividad sin sincronizar."
    elif flexible and not seen:
        status = "Ayer no hay actividad registrada: coincide con la opción de descanso prevista. La fuerza era opcional; comprueba si falta alguna actividad sin sincronizar."
    elif flexible and running and not optional_run:
        status = "Ayer se registró una carrera no prevista en un día de descanso o fuerza opcional: se aparta del plan."
        if high:
            status += " Posible esfuerzo elevado: " + ", ".join(high) + "; puede reducir la recuperación."
        else:
            status += " Su intensidad real no puede determinarse solo por el registro."
        if strength:
            status += " También se registró fuerza opcional."
    elif optional_run and not running:
        status = "Ayer no consta el rodaje opcional; el descanso también estaba previsto como opción."
        if strength:
            status += " Se registró fuerza adicional."
        if other:
            status += " Hubo otras actividades: valora su esfuerzo antes de decidir la sesión de hoy."
    elif flexible and strength and not running:
        status = "Ayer se registró fuerza opcional, conforme a una de las opciones previstas."
        if other:
            status += " También hubo otras actividades; revisa su carga."
    elif flexible and not running and other:
        status = "Ayer se registraron otras actividades en un día flexible; no estaban descritas en el plan y su intensidad puede variar."
    elif running:
        status = "Ayer se registró una carrera del tipo previsto; solo se comprueba el tipo, no el ritmo ni el cumplimiento completo."
        if prev_type in ("facil", "larga"):
            distance = re.search(r"(?<!\w)(\d+(?:[.,]\d+)?)(?:\s*[-–]\s*(\d+(?:[.,]\d+)?))?\s*km\b", previous["session_desc"], re.I)
            if distance:
                low = float(distance.group(1).replace(",", "."))
                high_km = float((distance.group(2) or distance.group(1)).replace(",", "."))
                actual = sum(a.get("km", 0) for a in running)
                status += f" Distancia registrada: {actual:.1f} km; referencia prevista: {low:g}–{high_km:g} km."
                if actual < low * 0.8 or actual > high_km * 1.2:
                    status += " La distancia difiere de la referencia; puede incluir calentamiento o datos sin sincronizar."
        if prev_type == "facil" and any("Z4" in item for item in high):
            status += " La FC media registrada supera el techo de Z2; revisa si era un rodaje fácil o si hubo segmentos intensos."
        if strength or other:
            status += " También hubo otras actividades que pueden añadir carga."
    else:
        status = "Ayer no aparece la carrera prevista en Strava; podría no haberse registrado o sincronizado."
        if strength or other:
            status += " Sí se registraron otras actividades, indicadas arriba."

    race_today = today in (date(2026, 11, 8), date(2026, 11, 29), date(2027, 1, 24))
    if race_today:
        advice = "Día de carrera: no recuperes sesiones perdidas; ajusta el esfuerzo a tus sensaciones. Ante dolor o fatiga considerable, prioriza la salud y valora no competir."
    elif high:
        advice = "Por los indicadores de carga de ayer, prioriza descanso o rodaje Z2 suave en lugar de intensidad; si hay dolor o fatiga notable, descansa. No acumules sesiones."
    elif flexible and running and not optional_run:
        advice = "La carrera no estaba prevista ayer. Si fue exigente o notas fatiga, cambia la calidad de hoy por descanso o Z2 suave; no recuperes sesiones perdidas."
    elif other:
        advice = "Ten en cuenta el esfuerzo de las otras actividades; si fueron exigentes o hay fatiga, elige descanso o Z2 suave en lugar de calidad."
    elif prev_type != "descanso" and not running and not optional_run:
        advice = "No acumules la sesión perdida: mantén lo previsto si estás recuperado, o sustituye la calidad por descanso o rodaje suave."
    elif prev_type == "descanso" and not strength and planned["session_type"] == "descanso":
        advice = "Si estás recuperado, puedes hacer 20–25 min de fuerza suave opcional; si hay fatiga, descansa."
    elif prev_type == "descanso" and not seen:
        advice = "La fuerza era opcional; mantén la sesión prevista si estás recuperado y prioriza descanso ante dolor o fatiga."
    else:
        advice = "Mantén la sesión prevista si te encuentras bien; ante dolor o fatiga, prioriza descanso."
    return {"yesterday": status, "yesterday_plan": previous["session_desc"], "recorded": recorded,
            "today": today_desc, "advice": advice}


# ---------------------------------------------------------------------------
# 5. Componer y enviar el correo
# ---------------------------------------------------------------------------

def _coros_html(coros):
    """Bloque 'Recuperación (Coros)' del correo; tolera huecos y datos viejos."""
    if not coros:
        return (
            "<p style='color:#888'><i>Sin datos de Coros — FC reposo / sueño / HRV no incluidos hoy. "
            "(Para activarlos: README, paso 4.)</i></p>"
        )

    rhr = coros.get("resting_hr")
    hrv, hrv_base = coros.get("hrv"), coros.get("hrv_base")
    sleep = coros.get("sleep_hours")
    ratio = coros.get("load_ratio")

    def item(label, value):
        return (
            f"<td style='padding:8px 14px 8px 0;vertical-align:top'>"
            f"<div style='font-size:11px;text-transform:uppercase;letter-spacing:.4px;color:#888'>{label}</div>"
            f"<div style='font-size:17px;font-weight:600'>{value}</div></td>"
        )

    hrv_txt = "—"
    if hrv is not None:
        hrv_txt = f"{hrv:.0f} ms"
        if hrv_base:
            delta = hrv - hrv_base
            hrv_txt += f" <span style='font-size:12px;color:#888'>({delta:+.0f} vs base {hrv_base:.0f})</span>"

    sleep_txt = coros_data.fmt_hours(sleep) if sleep is not None else "—"
    rhr_txt = f"{rhr:.0f} bpm" if rhr is not None else "—"
    ratio_txt = f"{ratio:.2f}" if ratio is not None else "—"

    cells = "".join(
        [
            item("FC reposo", rhr_txt),
            item("Sueño", sleep_txt),
            item("HRV", hrv_txt),
            item("Ratio de carga", ratio_txt),
        ]
    )

    notes = []
    if rhr is not None and rhr >= HR_REST + 4:
        notes.append(f"FC reposo {rhr - HR_REST:.0f} bpm por encima de tu referencia ({HR_REST}): toca sesión suave si la notabas dura.")
    if hrv is not None and hrv_base and hrv < hrv_base - 8:
        notes.append(f"HRV baja vs su baseline ({hrv - hrv_base:+.0f} ms): considera recortar la calidad de hoy.")
    if ratio is not None:
        if ratio < 0.8:
            notes.append("Ratio de carga por debajo de 0.8 — hay margen para meter volumen.")
        elif ratio > 1.3:
            notes.append("Ratio de carga por encima de 1.3 — riesgo de pico de fatiga; prioriza dormir.")
    if sleep is not None and sleep < 6.5:
        notes.append("Menos de 6,5 h de sueño: baja la intensidad prevista.")
    if coros.get("stale"):
        notes.append("Datos de Coros de ayer o más viejos (el fetch falló esta madrugada).")
    for w in coros.get("warnings") or []:
        notes.append(f"Coros: {w}")

    notes_html = (
        "<ul style='margin:8px 0 0 18px;padding:0;font-size:13px;color:#555'>"
        + "".join(f"<li>{n}</li>" for n in notes)
        + "</ul>"
        if notes
        else ""
    )
    date_txt = f" · día {coros['date']}" if coros.get("date") else ""

    # Línea de sueño de la ventana larga (30 noches), compartida con el dashboard.
    # Si Coros no expone sleep_hours, sleep_verdict devuelve None y no se pinta:
    # mejor una línea menos que una media inventada.
    a30 = coros.get("sleep_30d") or {}
    frase_sueño = stats.sleep_verdict(a30)
    sleep_line = ""
    if frase_sueño:
        sleep_line = (
            f"<div style='font-size:13px;color:#555;margin-top:6px'>"
            f"<b>Sueño ({a30.get('days')} días):</b> {frase_sueño} "
            f"En objetivo el {a30.get('on_target_pct')}% de las noches.</div>"
        )

    return (
        "<h3 style='margin-bottom:4px'>Recuperación (Coros)</h3>"
        f"<table style='border-collapse:collapse'><tr>{cells}</tr></table>"
        f"{sleep_line}"
        f"<div style='font-size:11px;color:#888'>Fuente: Coros EvoLab vía @pinta365/coros (API no oficial){date_txt}"
        f"{' · sueño: MCP oficial de COROS' if coros.get('sleep_source') else ''}"
        f"{' · ' + 'datos de ayer o más viejos' if coros.get('stale') else ''}</div>"
        f"{notes_html}"
    )


def build_email_html(today, plan, strava, coros):
    diet = DIET[plan["session_type"]]
    coros_html = _coros_html(coros)

    # Zonas Karvonen: con la FC reposo de Coros si HR_REST_FROM_COROS y hay dato.
    hr_rest = HR_REST
    if HR_REST_FROM_COROS and coros and coros.get("resting_hr"):
        hr_rest = int(round(coros["resting_hr"]))
    zones = zones_for(hr_rest)
    zone_note = "" if hr_rest == HR_REST else f" (recalculadas con tu FC reposo de hoy: {hr_rest})"

    nota_html = f"<p><b>⚠️ {escape(plan['nota'])}</b></p>" if plan.get("nota") else ""
    adherence = assess_adherence(today, strava.get("activities"))

    return f"""
    <h2>🏃 Tu arranque del día — {today.strftime('%A %d de %B, %Y')}</h2>

    <p><b>Resumen de ayer (Strava):</b> {escape(str(strava['yesterday']))}<br>
    Volumen de los últimos 7 días: {strava['week_km']} km (carrera + caminatas)</p>

    {coros_html}

    <h3>Entrenamiento de hoy — Semana {plan['week_num']} · Bloque {plan['bloque']}</h3>
    <p>{escape(plan['session_desc'])}</p>
    <h3>Seguimiento del plan</h3>
    <p><b>Plan de ayer:</b> {escape(adherence['yesterday_plan'])}<br><b>Registrado ayer:</b> {escape(adherence['recorded'])}<br>{escape(adherence['yesterday'])}<br><b>Alternativa orientativa:</b> {escape(adherence['advice'])}</p>
    {nota_html}
    <p style="font-size:12px;color:#888;">
    Zonas de FC (Karvonen, reposo {hr_rest} / máx {HR_MAX}){zone_note}:
    Z1 {zones['Z1']} · Z2 {zones['Z2']} · Z3 {zones['Z3']} · Z4 {zones['Z4']} · Z5 {zones['Z5']}
    </p>

    <h3>Dieta de hoy ({plan['session_type']})</h3>
    <p>{diet['kcal']} · Proteína {diet['prot']} · Carbohidratos {diet['carbs']} · Grasas {diet['grasa']}</p>
    <p>Peso de referencia: {WEIGHT_KG} kg</p>
    """


def send_email(subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = os.environ["GMAIL_USER"]
    msg["To"] = os.environ["EMAIL_TO"]
    msg.attach(MIMEText(html_body, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"])
        server.sendmail(os.environ["GMAIL_USER"], [os.environ["EMAIL_TO"]], msg.as_string())


def main():
    today = date.today()
    plan = get_today_plan(today)
    if plan is None:
        print("El plan aún no empieza (PLAN_START en el futuro). Nada que enviar.")
        return

    strava = get_strava_summary(today)
    coros = get_coros_summary(today)
    print(f"Coros: {'OK — ' + str(coros.get('date')) if coros else 'sin datos (el correo sale solo con Strava)'}")

    html = build_email_html(today, plan, strava, coros)
    subject = f"🏃 Tu arranque del día — {today.strftime('%d %b %Y')}"

    # DRY_RUN=1 → imprime el correo en vez de enviarlo (para probar el pipeline
    # entero en local sin Spam ni Gmail). En GitHub Actions no se pone.
    if os.environ.get("DRY_RUN"):
        print("--- DRY RUN: correo NO enviado ---")
        print("Asunto:", subject)
        print(html)
        return

    send_email(subject, html)
    print("Correo enviado.")


if __name__ == "__main__":
    main()
