"""
Correo diario de entrenamiento + dieta + resumen (Kthuluh).

Fuentes:
  - Strava API oficial (actividad de ayer + volumen de la semana).
  - Coros: OPCIONAL, vía scripts/coros_fetch.mjs (no oficial). Si el
    archivo coros_data.json no existe, el correo se manda igual sin
    esos datos.

Variables de entorno requeridas (se configuran como GitHub Secrets):
  STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_REFRESH_TOKEN
  GMAIL_USER, GMAIL_APP_PASSWORD, EMAIL_TO
"""

import json
import os
import smtplib
import ssl
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# 1. CONFIG — ajusta aquí si cambia algo de tu plan o tus datos
# ---------------------------------------------------------------------------

PLAN_START = date(2026, 9, 7)  # lunes = semana 1, día 1 del bloque 10K
WEIGHT_KG = 73
HR_REST = 50
HR_MAX = 182
COROS_DATA_PATH = Path(__file__).parent.parent / "coros_data.json"

# Zonas de FC (Karvonen)
HRR = HR_MAX - HR_REST
def hr_zone(pct_lo, pct_hi):
    lo = round(HR_REST + HRR * pct_lo)
    hi = round(HR_REST + HRR * pct_hi)
    return f"{lo}-{hi} bpm"

ZONES = {
    "Z1": hr_zone(0.50, 0.60),
    "Z2": hr_zone(0.60, 0.70),
    "Z3": hr_zone(0.70, 0.80),
    "Z4": hr_zone(0.80, 0.90),
    "Z5": hr_zone(0.90, 1.00),
}

# Plan semana a semana (18 semanas: 8 de bloque 10K + 10 de bloque 21K).
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
    # Bloque 2 — 21K (semanas 9-18)
    {"facil": "recuperación activa, suave", "calidad": None, "larga": "11-13 km Z2"},
    {"facil": "recuperación activa, suave", "calidad": None, "larga": "11-13 km Z2"},
    {"facil": "8-9 km Z2", "calidad": "tempo semanal @4:50-5:00/km", "larga": "14 km progresiva"},
    {"facil": "8-9 km Z2", "calidad": "tempo semanal @4:50-5:00/km", "larga": "15-16 km progresiva"},
    {"facil": "8-9 km Z2", "calidad": "tempo semanal @4:50-5:00/km", "larga": "17 km progresiva"},
    {"facil": "6 km Z2 suave (descarga -30%)", "calidad": None, "larga": "11 km"},
    {"facil": "8 km Z2", "calidad": "ritmo 21K específico", "larga": "17-19 km, con 10-12 km a 4:44-4:48/km"},
    {"facil": "8 km Z2", "calidad": "ritmo 21K específico", "larga": "17-19 km, con 10-12 km a 4:44-4:48/km"},
    {"facil": "6 km Z2 suave (descarga)", "calidad": None, "larga": "13 km suave"},
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
    resp = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"after": after_epoch, "before": before_epoch, "per_page": 50},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fmt_pace(distance_m, moving_time_s):
    if not distance_m:
        return "-"
    sec_per_km = moving_time_s / (distance_m / 1000)
    m, s = divmod(int(sec_per_km), 60)
    return f"{m}:{s:02d}/km"


def get_strava_summary(today):
    token = strava_access_token()
    yesterday = today - timedelta(days=1)

    y_start = int(datetime.combine(yesterday, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    y_end = int(datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    yesterday_acts = strava_activities(token, y_start, y_end)

    week_start = int(datetime.combine(today - timedelta(days=7), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    week_acts = strava_activities(token, week_start, y_end)
    week_km = sum(a["distance"] for a in week_acts if a["type"] == "Run") / 1000

    if not yesterday_acts:
        y_text = "Descanso — no se registró actividad."
    else:
        parts = []
        for a in yesterday_acts:
            dist_km = a["distance"] / 1000
            parts.append(
                f"{a['name']} ({a['type']}): {dist_km:.1f} km, {fmt_pace(a['distance'], a['moving_time'])}, "
                f"{a['total_elevation_gain']:.0f} m desnivel"
            )
        y_text = " | ".join(parts)

    return {"yesterday": y_text, "week_km": round(week_km, 1)}


# ---------------------------------------------------------------------------
# 3. COROS (opcional) — lee coros_data.json si existe
# ---------------------------------------------------------------------------

def get_coros_summary():
    if not COROS_DATA_PATH.exists():
        return None
    try:
        return json.loads(COROS_DATA_PATH.read_text())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 4. Armar el plan y la dieta de hoy
# ---------------------------------------------------------------------------

def get_today_plan(today):
    days_in = (today - PLAN_START).days
    if days_in < 0:
        return None  # el plan todavía no empieza
    week_idx = min(days_in // 7, len(WEEKS) - 1)
    week = WEEKS[week_idx]
    session_type = DAY_TO_SESSION.get(today.weekday(), "descanso")
    session_desc = week.get(session_type)
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


# ---------------------------------------------------------------------------
# 5. Componer y enviar el correo
# ---------------------------------------------------------------------------

def build_email_html(today, plan, strava, coros):
    diet = DIET[plan["session_type"]]

    coros_html = ""
    if coros:
        coros_html = f"""
        <p><b>FC reposo hoy:</b> {coros.get('resting_hr', '-')} bpm ·
        <b>Sueño anoche:</b> {coros.get('sleep_hours', '-')} h ·
        <b>HRV:</b> {coros.get('hrv', '-')} ms</p>
        """
    else:
        coros_html = "<p><i>(Sin datos de Coros configurados en este correo.)</i></p>"

    nota_html = f"<p><b>⚠️ {plan['nota']}</b></p>" if plan.get("nota") else ""

    return f"""
    <h2>🏃 Tu arranque del día — {today.strftime('%A %d de %B, %Y')}</h2>

    <p><b>Resumen de ayer (Strava):</b> {strava['yesterday']}<br>
    Volumen de los últimos 7 días: {strava['week_km']} km</p>

    {coros_html}

    <h3>Entrenamiento de hoy — Semana {plan['week_num']} · Bloque {plan['bloque']}</h3>
    <p>{plan['session_desc']}</p>
    {nota_html}
    <p style="font-size:12px;color:#888;">
    Zonas de FC (Karvonen, reposo {HR_REST} / máx {HR_MAX}):
    Z1 {ZONES['Z1']} · Z2 {ZONES['Z2']} · Z3 {ZONES['Z3']} · Z4 {ZONES['Z4']} · Z5 {ZONES['Z5']}
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
    coros = get_coros_summary()

    html = build_email_html(today, plan, strava, coros)
    subject = f"🏃 Tu arranque del día — {today.strftime('%d %b %Y')}"
    send_email(subject, html)
    print("Correo enviado.")


if __name__ == "__main__":
    main()
