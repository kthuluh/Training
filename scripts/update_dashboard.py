"""
Actualiza automáticamente, cada día, SOLO las partes del dashboard que
vienen de Strava:
  - Fecha del encabezado
  - Tarjeta de la "semana en curso" en Historial (km + sesiones)
  - Última barra del gráfico de volumen mensual
  - Último punto del gráfico de tendencia semanal (volumen)

Lo que viene de Coros (FC reposo, sueño, HRV, ratio de carga) NO se toca
aquí — tu integración de GitHub no tiene Coros conectado (fue una
decisión a propósito, ver README). Esos campos quedan marcados como
"(manual)" en el propio HTML y solo cambian si tú los editas a mano, o
si en el futuro decides activar el bloque opcional de Coros.

Variables de entorno requeridas (ya las tienes configuradas):
  STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_REFRESH_TOKEN
"""

import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

DASHBOARD_PATH = Path(__file__).parent.parent / "dashboard" / "dashboard-kthuluh.html"
MAX_WEEKS_IN_CHART = 14  # cuántas semanas se mantienen en el gráfico de tendencia

MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES_LARGO = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
               "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


# ---------------------------------------------------------------------------
# Strava
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


def strava_runs_since(token, since_date):
    after = int(datetime.combine(since_date, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    resp = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={"Authorization": f"Bearer {token}"},
        params={"after": after, "per_page": 100},
        timeout=30,
    )
    resp.raise_for_status()
    return [a for a in resp.json() if a["type"] == "Run"]


def week_start(d):
    return d - timedelta(days=d.weekday())  # lunes de esa semana


def week_label(monday):
    sunday = monday + timedelta(days=6)
    if monday.month == sunday.month:
        return f"{monday.day}-{sunday.day} {MESES[monday.month-1].lower()}"
    return f"{monday.day}{MESES[monday.month-1].lower()}-{sunday.day}{MESES[sunday.month-1].lower()}"


# ---------------------------------------------------------------------------
# Reemplazos marcados en el HTML
# ---------------------------------------------------------------------------

def replace_marked(html, marker, new_value):
    pattern = re.compile(
        re.escape(f"/*AUTO:{marker}*/") + r".*?" + re.escape(f"/*/AUTO:{marker}*/"),
        re.DOTALL,
    )
    replacement = f"/*AUTO:{marker}*/{new_value}/*/AUTO:{marker}*/"
    new_html, n = pattern.subn(replacement, html)
    if n != 1:
        raise RuntimeError(f"Marcador {marker} no encontrado (o encontrado más de una vez) en el HTML.")
    return new_html


def replace_marked_html(html, marker, new_value):
    pattern = re.compile(
        re.escape(f"<!--AUTO:{marker}-->") + r".*?" + re.escape(f"<!--/AUTO:{marker}-->"),
        re.DOTALL,
    )
    replacement = f"<!--AUTO:{marker}-->{new_value}<!--/AUTO:{marker}-->"
    new_html, n = pattern.subn(replacement, html)
    if n != 1:
        raise RuntimeError(f"Marcador {marker} no encontrado (o encontrado más de una vez) en el HTML.")
    return new_html


def get_marked(html, marker_html=True, marker=""):
    if marker_html:
        pattern = re.compile(
            re.escape(f"<!--AUTO:{marker}-->") + r"(.*?)" + re.escape(f"<!--/AUTO:{marker}-->"),
            re.DOTALL,
        )
    else:
        pattern = re.compile(
            re.escape(f"/*AUTO:{marker}*/") + r"(.*?)" + re.escape(f"/*/AUTO:{marker}*/"),
            re.DOTALL,
        )
    m = pattern.search(html)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    today = date.today()
    token = strava_access_token()

    # Traemos actividades desde hace MAX_WEEKS_IN_CHART semanas para reconstruir
    # el gráfico de tendencia semanal completo (rolling window).
    since = week_start(today) - timedelta(weeks=MAX_WEEKS_IN_CHART - 1)
    runs = strava_runs_since(token, since)

    # --- Agrupar por semana (lunes-domingo) ---
    weekly_km = {}
    for r in runs:
        start = datetime.fromisoformat(r["start_date_local"].replace("Z", "")).date()
        wk = week_start(start)
        weekly_km.setdefault(wk, 0.0)
        weekly_km[wk] += r["distance"] / 1000

    this_week_monday = week_start(today)
    weeks_sorted = sorted(weekly_km.keys())
    # aseguramos que la semana actual aparezca aunque tenga 0 km
    if this_week_monday not in weekly_km:
        weekly_km[this_week_monday] = 0.0
        weeks_sorted = sorted(weekly_km.keys())

    weeks_sorted = weeks_sorted[-MAX_WEEKS_IN_CHART:]
    vol_values = [round(weekly_km[w], 1) for w in weeks_sorted]
    vol_labels = [week_label(w) + ("*" if w == this_week_monday else "") for w in weeks_sorted]

    # --- Mes actual ---
    month_start = today.replace(day=1)
    month_km = sum(
        a["distance"] / 1000
        for a in runs
        if datetime.fromisoformat(a["start_date_local"].replace("Z", "")).date() >= month_start
    )

    # --- Semana actual: sesiones + km ---
    this_week_runs = [
        a for a in runs
        if week_start(datetime.fromisoformat(a["start_date_local"].replace("Z", "")).date()) == this_week_monday
    ]
    this_week_km = round(sum(a["distance"] for a in this_week_runs) / 1000, 1)
    this_week_sessions = len(this_week_runs)

    # --- Cargar HTML ---
    html = DASHBOARD_PATH.read_text(encoding="utf-8")

    # 1) Fecha del encabezado
    fecha_txt = f"{today.day} {MESES_LARGO[today.month-1][:3]} {today.year}"
    html = replace_marked_html(html, "UPDATED_DATE", fecha_txt)

    # 2) Gráfico mensual — actualiza SOLO el índice del mes actual
    monthly_raw = get_marked(html, marker_html=False, marker="MONTHLY_VOL")
    monthly_vals = [float(x) for x in monthly_raw.strip("[]").split(",")]
    monthly_vals[today.month - 1] = round(month_km, 1)
    html = replace_marked(html, "MONTHLY_VOL", "[" + ",".join(str(v) for v in monthly_vals) + "]")

    # 3) Gráfico semanal — labels y volumen (rolling window completo)
    html = replace_marked(html, "WEEKLY_LABELS", "[" + ",".join(f"'{l}'" for l in vol_labels) + "]")
    html = replace_marked(html, "WEEKLY_VOL", "[" + ",".join(str(v) for v in vol_values) + "]")

    # Sueño y FC reposo del gráfico semanal: como no hay Coros, si aparece
    # una semana nueva se rellena con null (Chart.js la deja como hueco)
    # en vez de inventar un dato. Las semanas que ya tenían valor manual
    # se conservan.
    for marker in ["WEEKLY_SLEEP", "WEEKLY_RHR"]:
        raw = get_marked(html, marker_html=False, marker=marker)
        existing = [v.strip() for v in raw.strip("[]").split(",")]
        if len(existing) < len(vol_values):
            existing = existing + ["null"] * (len(vol_values) - len(existing))
        elif len(existing) > len(vol_values):
            existing = existing[-len(vol_values):]
        html = replace_marked(html, marker, "[" + ",".join(existing) + "]")

    # 4) Tarjeta de la semana en curso
    monday = this_week_monday
    sunday = monday + timedelta(days=6)
    label = week_label(monday) + " · en curso"
    card = f'''<div class="wcard partial"><div class="wk-lbl">{label}</div>
        <div class="metric"><span>Carrera</span><span class="v">{this_week_km} km</span></div>
        <div class="metric"><span>Sesiones</span><span class="v">{this_week_sessions}</span></div>
        <div class="metric" style="opacity:0.7;"><span>Sueño <i style="font-size:10px;">(manual)</i></span><span class="v">—</span></div>
        <div class="metric" style="opacity:0.7;"><span>FC reposo <i style="font-size:10px;">(manual)</i></span><span class="v">—</span></div>
      </div>'''
    html = replace_marked_html(html, "CURRENT_WEEK", card)

    DASHBOARD_PATH.write_text(html, encoding="utf-8")
    print(f"Dashboard actualizado: semana en curso {this_week_km} km / {this_week_sessions} sesiones, mes {month_km:.1f} km.")


if __name__ == "__main__":
    main()
