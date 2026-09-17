"""
Actualiza automáticamente, cada día, las partes del dashboard que se pueden
derivar de datos reales:

  De Strava (scripts/update_dashboard.py, siempre):
    - Fecha del encabezado
    - Tarjeta de la "semana en curso" en Historial (km + sesiones)
    - Última barra del gráfico de volumen mensual
    - Último punto del gráfico de tendencia semanal (volumen)

  De Coros (opcional, si existe coros_data.json — ver README paso 4):
    - Tarjetas "Estado actual": FC reposo 14 días, HRV base
    - Tarjetas del hero: FC reposo media y ratio de carga de hoy
    - Gráfico "Frecuencia cardíaca en reposo — últimos 30 días"
    - Gráfico "Carga de entrenamiento (Coros) — últimos 14 días"
    - Líneas Sueño / FC reposo del gráfico de tendencia semanal
    - Sueño y FC reposo de la tarjeta de la semana en curso

Lo de Coros es tolerante a fallos: si falta `coros_data.json` o le falta un
campo, esa parte se deja intacta (queda el valor manual que hubiera) y el
workflow no revienta. Coros usa una API NO oficial que puede caerse.

El sueño SOLO se actualiza si tu respuesta de Coros lo trae: la librería
@pinta365/coros 0.0.1 no expone el endpoint de sueño (sólo HRV nocturna), así
que en la práctica `sleep_hours` suele venir null y las líneas de sueño siguen
siendo manuales. Ver `SLEEP_KEYS` en scripts/coros_fetch.mjs y COROS_DEBUG=1.

Uso:
  python scripts/update_dashboard.py               # Strava + Coros (lo que hace el workflow)
  python scripts/update_dashboard.py --only-coros  # solo la parte Coros (sin red de Strava)
  python scripts/update_dashboard.py --dry-run     # no escribe el HTML

Variables de entorno requeridas (ya las tienes configuradas):
  STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_REFRESH_TOKEN
"""

import argparse
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:  # `requests` solo hace falta para la parte de Strava
    import requests
except ModuleNotFoundError:  # --only-coros funciona sin instalar dependencias
    requests = None

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # para importar coros_data

import coros_data

# Env DASHBOARD_PATH permite probar contra una copia sin tocar el real.
DASHBOARD_PATH = Path(os.environ.get("DASHBOARD_PATH") or Path(__file__).resolve().parent.parent / "dashboard" / "dashboard-kthuluh.html")
MAX_WEEKS_IN_CHART = 14  # cuántas semanas se mantienen en el gráfico de tendencia

MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES_LARGO = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
               "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


# ---------------------------------------------------------------------------
# Strava
# ---------------------------------------------------------------------------

def strava_access_token():
    if requests is None:
        raise SystemExit(
            "Falta `requests` para la parte de Strava:  pip install -r requirements.txt\n"
            "(o ejecuta solo la parte de Coros:  python scripts/update_dashboard.py --only-coros)"
        )
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


def has_marker(html, marker, marker_html=True):
    return get_marked(html, marker_html=marker_html, marker=marker) is not None


def js_list(values, quote=False):
    """[7.1, null] / ['a','b'] — null en vez de None, como espera Chart.js."""
    out = []
    for v in values:
        if v is None:
            out.append("null")
        elif quote:
            out.append("'" + str(v).replace("'", "") + "'")
        elif isinstance(v, float):
            out.append(f"{v:g}")
        else:
            out.append(str(v))
    return "[" + ",".join(out) + "]"


def parse_js_array(raw):
    """'['a','b']' → ['a','b'] ; '[1,2.5,null]' → [1, 2.5, None]."""
    if raw is None:
        return []
    body = raw.strip().strip("[]")
    if not body:
        return []
    items = re.findall(r"'([^']*)'|([-\d.]+|null)", body)
    out = []
    for quoted, bare in items:
        if quoted:
            out.append(quoted)
        elif bare == "null":
            out.append(None)
        else:
            try:
                out.append(float(bare) if "." in bare else int(bare))
            except ValueError:
                out.append(None)
    return out


def align_to_labels(values_by_monday, mondays):
    """Alinea {lunes: valor} con las semanas que ya pinta el gráfico."""
    return [values_by_monday.get(m) for m in mondays]


# ---------------------------------------------------------------------------
# Coros → HTML
# ---------------------------------------------------------------------------

def apply_coros(html, coros, mondays, today, manual_by_week=None):
    """Devuelve (html, [noticias]). Solo toca marcadores que existen y datos que hay.

    `manual_by_week` = { "YYYY-MM-DD(lunes)": {"WEEKLY_RHR": v, "WEEKLY_SLEEP": v} }
    con los valores que ya había en el HTML, indexados por SU lunes, para que al
    desplazar la ventana de 14 semanas no se corran los valores una posición.
    """
    notes = []
    manual_by_week = manual_by_week or {}
    if not coros:
        notes.append("sin coros_data.json → la parte de Coros queda como estaba (manual)")
        return html, notes

    latest = coros.get("latest") or {}
    last14 = coros.get("latest_14") or {}
    avail = coros.get("available") or {}
    days = coros_data.by_date(coros)
    weeks = coros_data.weekly(coros)

    # 1) Hero: FC reposo media (14 días) y ratio de carga de hoy
    rhr_avg = last14.get("resting_hr_avg")
    if rhr_avg is not None and has_marker(html, "HERO_RHR"):
        html = replace_marked_html(html, "HERO_RHR", f"{round(rhr_avg)}")
        notes.append(f"hero: FC reposo media {round(rhr_avg)} bpm")
    ratio = latest.get("load_ratio")
    if ratio is not None and has_marker(html, "HERO_RATIO"):
        html = replace_marked_html(html, "HERO_RATIO", f"{ratio:.2f}")
        notes.append(f"hero: ratio de carga {ratio:.2f}")

    # 2) Tarjetas "Estado actual"
    if rhr_avg is not None:
        lo, hi = last14.get("resting_hr_min"), last14.get("resting_hr_max")
        rango = f"Rango {lo:.0f}–{hi:.0f} bpm" if lo is not None and hi is not None else "Rango no disponible"
        if has_marker(html, "RHR_CARD"):
            html = replace_marked_html(html, "RHR_CARD", f"{round(rhr_avg)} bpm")
        if has_marker(html, "RHR_CARD_SUB"):
            html = replace_marked_html(
                html, "RHR_CARD_SUB",
                f"{rango}, últimos {last14.get('n_days', 14)} días desde el reloj Coros "
                f"(actualizado {coros.get('date', '—')}, API no oficial).",
            )
        notes.append(f"FC reposo 14 días: {round(rhr_avg)} bpm")

    hrv_base = latest.get("hrv_base")
    hrv_avg = last14.get("hrv_avg")
    if hrv_avg is not None or hrv_base is not None:
        val = hrv_avg if hrv_avg is not None else hrv_base
        if has_marker(html, "HRV_CARD"):
            html = replace_marked_html(html, "HRV_CARD", f"{round(val)} ms")
        if has_marker(html, "HRV_CARD_SUB"):
            txt = f"Media de las últimas 2 semanas desde EvoLab; baseline Coros {round(hrv_base)} ms" if hrv_base else \
                  "Media de las últimas 2 semanas desde EvoLab"
            html = replace_marked_html(html, "HRV_CARD_SUB", f"{txt} (la HRV nocturna sí la expone la librería; se refresca a diario).")
        notes.append(f"HRV: {round(val)} ms")

    # 3) Gráfico FC reposo — últimos 30 días
    last30 = [days[d] for d in sorted(days)[-30:]]
    rhr_series = [d.get("resting_hr") for d in last30]
    if any(v is not None for v in rhr_series):
        if has_marker(html, "RHR30_LABELS", marker_html=False):
            html = replace_marked(html, "RHR30_LABELS", js_list([d["date"][5:].replace("-", "/") for d in last30], quote=True))
        if has_marker(html, "RHR30_DATA", marker_html=False):
            html = replace_marked(html, "RHR30_DATA", js_list([round(v) if v is not None else None for v in rhr_series]))
        notes.append(f"gráfico FC reposo: {sum(1 for v in rhr_series if v is not None)}/30 días con dato")

    # 4) Gráfico de carga (corto = t7d, largo = t28d) — últimos 14 días
    last14_days = [days[d] for d in sorted(days)[-14:]]
    short = [d.get("load_short") for d in last14_days]
    long_ = [d.get("load_long") for d in last14_days]
    if any(v is not None for v in short) or any(v is not None for v in long_):
        if has_marker(html, "LOAD14_LABELS", marker_html=False):
            html = replace_marked(html, "LOAD14_LABELS", js_list([d["date"][5:].replace("-", "/") for d in last14_days], quote=True))
        if has_marker(html, "LOAD14_SHORT", marker_html=False):
            html = replace_marked(html, "LOAD14_SHORT", js_list([round(v) if v is not None else None for v in short]))
        if has_marker(html, "LOAD14_LONG", marker_html=False):
            html = replace_marked(html, "LOAD14_LONG", js_list([round(v) if v is not None else None for v in long_]))
        if has_marker(html, "LOAD_CAPTION"):
            cap = (
                f"Carga corto plazo (7 días) vs. largo plazo (28 días, Base Fitness). "
                f"Ratio actual {ratio:.2f}" if ratio is not None else "Carga corto plazo (7 días) vs. largo plazo (28 días, Base Fitness)"
            )
            tail = (
                " → por debajo de tu carga habitual: hay margen para meter más volumen."
                if ratio is not None and ratio < 0.85
                else " → dentro de la banda habitual de rendimiento."
                if ratio is not None and ratio <= 1.3
                else " → por encima de la banda habitual: prioriza recuperación y sueño."
                if ratio is not None
                else "."
            )
            html = replace_marked_html(html, "LOAD_CAPTION", f"{cap}{tail} Datos de EvoLab vía Coros, {coros.get('date', '—')}.")
        notes.append("gráfico de carga: actualizado")

    # 5) Sueño 14 días (solo si Coros lo trae)
    sleep_avg = last14.get("sleep_hours_avg")
    if sleep_avg is not None:
        if has_marker(html, "SLEEP_14D_AVG"):
            html = replace_marked_html(html, "SLEEP_14D_AVG", coros_data.fmt_hours(sleep_avg))
        if has_marker(html, "SLEEP_14D_SUB"):
            html = replace_marked_html(html, "SLEEP_14D_SUB", f"Media real de {last14.get('n_days', 14)} días leída de Coros.")
        notes.append(f"sueño 14 días: {coros_data.fmt_hours(sleep_avg)}")
    elif has_marker(html, "SLEEP_14D_SUB"):
        notes.append(
            "sueño: Coros no lo expone en analyse/query → sigue manual "
            "(ver SLEEP_KEYS en scripts/coros_fetch.mjs)"
        )

    # 6) Líneas semanales: ver reconcile_weekly_lines (se llama siempre, haya
    #    Coros o no) — ahí es donde se fusionan datos de Coros y valores manuales.

    return html, notes


def reconcile_weekly_lines(html, mondays, coros, manual_by_week=None):
    """Reescribe WEEKLY_RHR / WEEKLY_SLEEP alineados con los lunes del gráfico.

    Para cada semana: si Coros tiene dato → se usa; si no, se conserva el valor
    manual que hubiera en el HTML (indexado por su lunes, no por posición, para
    que no se corra al desplazar la ventana); si no hay de ninguno → null
    (Chart.js deja hueco) en vez de inventar un dato.
    """
    notes = []
    if not has_marker(html, "WEEKLY_RHR", marker_html=False) and not has_marker(html, "WEEKLY_SLEEP", marker_html=False):
        return html, ["gráfico semanal: sin marcadores WEEKLY_RHR/WEEKLY_SLEEP, no toco las líneas"]
    if not mondays:
        return html, ["gráfico semanal: no hay lista de lunes → no puedo alinear sueño/FC reposo"]

    weeks = coros_data.weekly(coros) if coros else {}
    manual_by_week = manual_by_week or {}
    for marker, key in (("WEEKLY_RHR", "resting_hr"), ("WEEKLY_SLEEP", "sleep_hours")):
        if not has_marker(html, marker, marker_html=False):
            continue
        # bpm enteros; horas de sueño con 2 decimales (7.46, no 7)
        as_int = key == "resting_hr"
        merged, filled, kept = [], 0, 0
        for monday in mondays:
            coros_val = (weeks.get(monday) or {}).get(key)
            manual = (manual_by_week.get(monday) or {}).get(marker)
            if coros_val is not None:
                merged.append(round(float(coros_val)) if as_int else round(float(coros_val), 2))
                filled += 1
            elif manual is not None:
                merged.append(manual)
                kept += 1
            else:
                merged.append(None)
        html = replace_marked(html, marker, js_list(merged))
        src = f"{filled} desde Coros, " if coros else ""
        notes.append(f"{marker}: {src}{kept} manuales conservadas, {merged.count(None)} en hueco")
    return html, notes


def snapshot_weekly_manual(html):
    """{ lunes: { 'WEEKLY_RHR': v, 'WEEKLY_SLEEP': v } } con lo que hay en el HTML."""
    old_mondays = [m for m in parse_js_array(get_marked(html, marker_html=False, marker="WEEKLY_MONDAYS")) if isinstance(m, str)]
    if not old_mondays:
        return {}
    out = {}
    for marker in ("WEEKLY_RHR", "WEEKLY_SLEEP"):
        vals = parse_js_array(get_marked(html, marker_html=False, marker=marker))
        for monday, val in zip(old_mondays, vals):
            if val is not None:
                out.setdefault(monday, {})[marker] = val
    return out


def build_current_week_card(this_week_monday, this_week_km, this_week_sessions, coros_week=None, has_coros=False):
    """Tarjeta de la semana en curso; con sueño/FC reposo reales si los hay."""
    label = week_label(this_week_monday) + " · en curso"
    coros_week = coros_week or {}
    sleep = coros_week.get("sleep_hours")
    rhr = coros_week.get("resting_hr")

    def metric(name, value, ok):
        if ok:
            return f'<div class="metric"><span>{name}</span><span class="v">{value}</span></div>'
        style = ' style="opacity:0.7;"' if has_coros else ""
        tag = " <i style=\"font-size:10px;\">(manual)</i>" if has_coros else ' <i style="font-size:10px;">(manual)</i>'
        return (
            f'<div class="metric"{style}><span>{name}{tag}</span><span class="v">—</span></div>'
        )

    return (
        f'''<div class="wcard partial"><div class="wk-lbl">{label}</div>
        <div class="metric"><span>Carrera</span><span class="v">{this_week_km} km</span></div>
        <div class="metric"><span>Sesiones</span><span class="v">{this_week_sessions}</span></div>
        {metric("Sueño", coros_data.fmt_hours(sleep), sleep is not None)}
        {metric("FC reposo", f"{round(rhr)} bpm", rhr is not None)}
      </div>'''
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Actualiza el dashboard desde Strava y (opcional) Coros")
    parser.add_argument("--only-coros", action="store_true", help="solo la parte de Coros; no toca Strava")
    parser.add_argument("--dry-run", action="store_true", help="no escribe el HTML")
    args = parser.parse_args()

    today = date.today()
    html = DASHBOARD_PATH.read_text(encoding="utf-8")
    coros = coros_data.load()
    mondays = []

    # Fotograma de los valores manuales ANTES de tocar nada, indexado por el
    # lunes al que le tocan, para poder conservarlos al desplazar la ventana.
    manual_by_week = snapshot_weekly_manual(html)

    if args.only_coros:
        # Alineación con las semanas que ya hay en el gráfico.
        mondays = [m for m in parse_js_array(get_marked(html, marker_html=False, marker="WEEKLY_MONDAYS")) if isinstance(m, str)]
        this_week_monday = week_start(today)
        html, notes = apply_coros(html, coros, mondays, today, manual_by_week)
        html, more = reconcile_weekly_lines(html, mondays, coros, manual_by_week)
        notes += more
        notes.append("modo --only-coros: Strava no se toca (fecha, km, meses)")
    else:
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
        mondays = [w.isoformat() for w in weeks_sorted]

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

        # 1) Fecha del encabezado
        fecha_txt = f"{today.day} {MESES_LARGO[today.month-1][:3]} {today.year}"
        html = replace_marked_html(html, "UPDATED_DATE", fecha_txt)

        # 2) Gráfico mensual — actualiza SOLO el índice del mes actual
        monthly_raw = get_marked(html, marker_html=False, marker="MONTHLY_VOL")
        monthly_vals = [float(x) for x in monthly_raw.strip("[]").split(",")]
        monthly_vals[today.month - 1] = round(month_km, 1)
        html = replace_marked(html, "MONTHLY_VOL", "[" + ",".join(str(v) for v in monthly_vals) + "]")

        # 3) Gráfico semanal — labels, volumen y los lunes reales (para Coros)
        html = replace_marked(html, "WEEKLY_LABELS", "[" + ",".join(f"'{l}'" for l in vol_labels) + "]")
        html = replace_marked(html, "WEEKLY_VOL", "[" + ",".join(str(v) for v in vol_values) + "]")
        if has_marker(html, "WEEKLY_MONDAYS", marker_html=False):
            html = replace_marked(html, "WEEKLY_MONDAYS", js_list(mondays, quote=True))

        # 3b) Resto de la parte Coros + fusión de las líneas semanales
        #     (FC reposo / sueño con Coros cuando lo haya; si no, se conserva el
        #     valor manual y las semanas nuevas van con null, nunca inventadas).
        html, notes = apply_coros(html, coros, mondays, today, manual_by_week)
        html, more = reconcile_weekly_lines(html, mondays, coros, manual_by_week)
        notes += more

        # 4) Tarjeta de la semana en curso
        coros_week = coros_data.weekly(coros).get(this_week_monday.isoformat()) if coros else None
        card = build_current_week_card(
            this_week_monday, this_week_km, this_week_sessions,
            coros_week=coros_week, has_coros=coros is not None,
        )
        html = replace_marked_html(html, "CURRENT_WEEK", card)

        print(
            f"Dashboard: semana en curso {this_week_km} km / {this_week_sessions} sesiones, "
            f"mes {month_km:.1f} km."
        )

    for n in notes:
        print(f"  · {n}")
    for w in (coros or {}).get("warnings", []):
        print(f"  ⚠ {w}")

    if args.dry_run:
        print("· --dry-run: no escribo el HTML.")
        return

    DASHBOARD_PATH.write_text(html, encoding="utf-8")
    print(f"Dashboard escrito en {DASHBOARD_PATH.relative_to(DASHBOARD_PATH.parents[1])}.")


if __name__ == "__main__":
    sys.exit(main())
