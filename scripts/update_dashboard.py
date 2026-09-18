"""
Actualiza automáticamente, cada día, las partes del dashboard que se pueden
derivar de datos reales. **Todas las pestañas**, no solo la de Resumen general:

  De Strava (`scripts/update_dashboard.py`):
    - Fecha del encabezado y pie con la cobertura real de datos
    - Resumen: nada propio (sus datos vienen de Coros)
    - Entrenamiento: mejores marcas 10K/21K, últimas 4 semanas, gráfico de
      volumen, tabla de zonas Karvonen y estado del plan (semana en curso)
    - Dieta: contexto real de la semana (km, sesiones, pasos, ratio de carga)
    - Hábitos: fuerza, sueño/pasos, FC en Z2 y rampa de volumen
    - Historial: tendencia 4+4 semanas, carreras del año, barras y FC reposo
      mensuales, tarjetas mensuales, 12 semanas de detalle y semana en curso
    - Recomendaciones: avisos generados a partir de los datos (volumen, carga,
      recuperación, fuerza, sueño)

  De Coros (opcional, si existe coros_data.json — ver README paso 4):
    - Tarjetas "Estado actual": FC reposo 14 días, HRV base, pasos
    - Tarjetas del hero: FC reposo media y ratio de carga de hoy
    - Gráficos: FC reposo 30 días, carga 14 días, FC reposo mensual
    - Líneas Sueño / FC reposo del gráfico semanal y de las tarjetas semanales
    - Recomendaciones de recuperación

Lo de Coros es tolerante a fallos: si falta `coros_data.json` o le falta un
campo, esa parte se deja intacta (queda el valor manual que hubiera) y el
workflow no revienta. Coros usa una API NO oficial que puede caerse.

Igual con Strava: si una sección no tiene datos, se conserva lo que ya había en
el HTML (nunca se inventa un valor ni se escribe un 0 donde no hay dato). Y cada
sección va aislada: si una falla, las demás se actualizan igual.

Uso:
  python scripts/update_dashboard.py               # Strava + Coros (lo que hace el workflow)
  python scripts/update_dashboard.py --only-coros  # solo la parte Coros (sin red de Strava)
  python scripts/update_dashboard.py --dry-run     # no escribe el HTML

Variables de entorno requeridas (ya las tienes configuradas):
  STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_REFRESH_TOKEN

Extras útiles:
  STRAVA_FIXTURE     path a un JSON con actividades crudas de Strava → no usa red
  DASHBOARD_PATH     path del HTML a reescribir (para probar contra una copia)
  HR_REST_FROM_COROS "1" → recalcula las zonas Karvonen con tu FC reposo real
"""

import argparse
import html as html_lib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:  # `requests` solo hace falta para la parte de Strava
    import requests
except ModuleNotFoundError:  # --only-coros funciona sin instalar dependencias
    requests = None

sys.path.insert(0, str(Path(__file__).resolve().parent))  # para importar los módulos de al lado

import coros_data
import dashboard_stats as stats

# Env DASHBOARD_PATH permite probar contra una copia sin tocar el real.
DASHBOARD_PATH = Path(os.environ.get("DASHBOARD_PATH") or Path(__file__).resolve().parent.parent / "dashboard" / "dashboard-kthuluh.html")

MAX_WEEKS_IN_CHART = 14   # semanas del gráfico de tendencia (Historial)
CHARTVOL_WEEKS = 8        # barras del gráfico de volumen (Entrenamiento)
WEEKLY_CARDS = 12         # tarjetas "semana a semana" (Historial)
STRAVA_HISTORY_DAYS = 365 # ventana de actividades que se descarga

# Espejo de las constantes de scripts/daily_brief.py (el plan y las zonas tienen
# que decir lo mismo en el correo y en el dashboard). Hay un test que lo compara.
PLAN_START = date(2026, 9, 7)  # lunes = semana 1 del bloque 10K
HR_REST = 50
HR_MAX = 182
HR_REST_FROM_COROS = os.environ.get("HR_REST_FROM_COROS", "").strip().lower() in {"1", "true", "yes", "si", "sí"}

MESES = stats.MESES
MESES_LARGO = stats.MESES_LARGO
DIAS = stats.DIAS

FLAT_DELTA = '<span class="delta flat">＝</span>'

# Estilos de las zonas (los mismos colores que ya tenía la tabla del HTML)
ZONE_STYLE = {
    "Z1": ("#8FA98F", "Recuperación", "Rodajes de recuperación, calentamiento"),
    "Z2": ("#3F5C46", "Aeróbico/fácil", "Base, tiradas largas, la mayoría del volumen"),
    "Z3": ("#35586B", "Tempo", "Ritmo controlado, umbral aeróbico"),
    "Z4": ("#A9552E", "Umbral", "Series largas, ritmo de 21K exigido"),
    "Z5": ("#B8402B", "VO2max", "Series cortas, ritmo de 10K"),
}


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


def strava_activities(token, after_epoch, per_page=200, max_pages=6):
    """Todas las actividades desde `after` (paginado), no solo carreras.

    Strava devuelve de la más reciente a la más antigua; con per_page=200 un año
    normal de entrenamiento cabe en 1-2 páginas.
    """
    out = []
    for page in range(1, max_pages + 1):
        resp = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {token}"},
            params={"after": after_epoch, "per_page": per_page, "page": page},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        out.extend(batch)
        if len(batch) < per_page:
            break
    return out


def load_activities(today):
    """Actividades normalizadas del último año (o del fixture de pruebas)."""
    fixture = os.environ.get("STRAVA_FIXTURE")
    if fixture:
        raw = json.loads(Path(fixture).read_text(encoding="utf-8"))
        print(f"· Usando fixture local de Strava: {fixture}")
        return stats.normalize(raw)
    token = strava_access_token()
    since = today - timedelta(days=STRAVA_HISTORY_DAYS)
    after = int(datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    return stats.normalize(strava_activities(token, after))


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


def set_html(html, marker, value, notes=None, note=None):
    """Reemplaza un marcador solo si existe; deja constancia en `notes`."""
    if not has_marker(html, marker):
        if notes is not None:
            notes.append(f"{marker}: marcador ausente, no toco nada")
        return html
    if notes is not None and note:
        notes.append(note)
    return replace_marked_html(html, marker, value)


def set_js(html, marker, value, notes=None, note=None):
    if not has_marker(html, marker, marker_html=False):
        if notes is not None:
            notes.append(f"{marker}: marcador ausente, no toco nada")
        return html
    if notes is not None and note:
        notes.append(note)
    return replace_marked(html, marker, value)


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


def esc(text):
    return html_lib.escape(str(text), quote=False)


# ---------------------------------------------------------------------------
# Helpers de pintado
# ---------------------------------------------------------------------------

def delta_span(cur, prev, good_up=True, kind="num", unit=""):
    """Flechita de cambio con el mismo criterio de color que el HTML original.

    `good_up=False` para cosas donde subir es malo (FC reposo).
    """
    if cur is None or prev is None:
        return ""
    d = float(cur) - float(prev)
    if kind == "num":
        if abs(d) < 0.05:
            return FLAT_DELTA
        txt = stats.fmt_es(abs(d), 1)
    elif kind == "bpm":
        if abs(d) < 0.5:
            return FLAT_DELTA
        txt = f"{abs(d):.0f}"
    elif kind == "hours":
        if abs(d) < 1 / 120:
            return FLAT_DELTA
        txt = stats.fmt_minutes(abs(d))
    elif kind == "pct":
        if abs(d) < 1:
            return FLAT_DELTA
        txt = f"{abs(d):.0f}%"
    else:
        txt = f"{abs(d):g}"
    cls = "up" if (d > 0) == good_up else "down"
    arrow = "▲" if d > 0 else "▼"
    return f'<span class="delta {cls}">{arrow}{txt}{unit}</span>'


def fmt_miles(v):
    """15984 → '15.984' (separador de miles como en el resto del dashboard)."""
    if v is None:
        return "—"
    return f"{float(v):,.0f}".replace(",", ".")


def fmt_hours(v):
    return coros_data.fmt_hours(v)          # '7 h 03 m' (tarjetas grandes y prosa)


def fmt_hours_compact(v):
    """'7h 03m' — para las tarjetas pequeñas (semanal y tendencia)."""
    if v is None:
        return "—"
    try:
        total = round(float(v) * 60)
    except (TypeError, ValueError):
        return "—"
    return f"{total // 60}h {total % 60:02d}m"


def coros_bits_line(coros):
    """'FC reposo, HRV, carga' — lo que realmente trae el JSON de Coros."""
    labels = {"resting_hr": "FC reposo", "hrv": "HRV", "sleep_hours": "sueño",
              "steps": "pasos", "load_ratio": "carga"}
    avail = (coros or {}).get("available") or {}
    bits = [label for key, label in labels.items() if avail.get(key)]
    if not bits and coros:
        bits = ["FC reposo"]
    return ", ".join(bits)


def plan_progress(today):
    """(nº de semana del plan, bloque) o (None, None) si aún no ha empezado."""
    days_in = (today - PLAN_START).days
    if days_in < 0:
        return None, None
    week = days_in // 7 + 1
    return week, ("10K" if week <= 8 else "21K")


def hr_rest_for_zones(coros):
    """FC reposo para las zonas: la de Coros si HR_REST_FROM_COROS y hay dato."""
    if HR_REST_FROM_COROS and coros:
        rhr = (coros.get("latest_14") or {}).get("resting_hr_avg")
        if rhr:
            return int(round(float(rhr))), True
    return HR_REST, False


# ---------------------------------------------------------------------------
# Coros → HTML (Resumen general)
# ---------------------------------------------------------------------------

def apply_coros(html, coros, today, notes=None):
    """Tarjetas y gráficos del Resumen general (hero, estado actual y gráficos).

    Solo toca datos que existen: si falta un campo, se queda lo que hubiera.
    Las líneas semanales del gráfico de tendencia las lleva
    `reconcile_weekly_lines`, que también necesita los valores manuales.
    """
    notes = notes if notes is not None else []
    if not coros:
        notes.append("sin coros_data.json → la parte de Coros queda como estaba (manual)")
        return html

    latest = coros.get("latest") or {}
    last14 = coros.get("latest_14") or {}
    days = coros_data.by_date(coros)
    num = stats.as_number  # red de seguridad: un JSON editado a mano no debe tumbar el cron

    # 1) Hero: FC reposo media (14 días) y ratio de carga de hoy
    rhr_avg = num(last14.get("resting_hr_avg"))
    if rhr_avg is not None and has_marker(html, "HERO_RHR"):
        html = replace_marked_html(html, "HERO_RHR", f"{round(rhr_avg)}")
        notes.append(f"hero: FC reposo media {round(rhr_avg)} bpm")
    ratio = num(latest.get("load_ratio"))
    if ratio is not None and has_marker(html, "HERO_RATIO"):
        html = replace_marked_html(html, "HERO_RATIO", f"{ratio:.2f}")
        notes.append(f"hero: ratio de carga {ratio:.2f}")

    # 2) Tarjetas "Estado actual"
    if rhr_avg is not None:
        lo, hi = num(last14.get("resting_hr_min")), num(last14.get("resting_hr_max"))
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

    hrv_base = num(latest.get("hrv_base"))
    hrv_avg = num(last14.get("hrv_avg"))
    if hrv_avg is not None or hrv_base is not None:
        val = hrv_avg if hrv_avg is not None else hrv_base
        if has_marker(html, "HRV_CARD"):
            html = replace_marked_html(html, "HRV_CARD", f"{round(val)} ms")
        if has_marker(html, "HRV_CARD_SUB"):
            txt = f"Media de las últimas 2 semanas desde EvoLab; baseline Coros {round(hrv_base)} ms" if hrv_base else \
                  "Media de las últimas 2 semanas desde EvoLab"
            html = replace_marked_html(html, "HRV_CARD_SUB", f"{txt} (la HRV nocturna sí la expone la librería; se refresca a diario).")
        notes.append(f"HRV: {round(val)} ms")

    # 2b) Tarjeta de pasos (solo si Coros los trae; si no, se queda manual)
    steps_avg = num(last14.get("steps_avg"))
    if steps_avg is not None:
        steps_days = [v for v in (num(d.get("steps")) for d in (coros.get("days") or [])[-14:]) if v is not None]
        if has_marker(html, "STEPS_CARD"):
            html = replace_marked_html(html, "STEPS_CARD", f"~{fmt_miles(steps_avg)}")
        if has_marker(html, "STEPS_CARD_SUB"):
            rango = ""
            if steps_days:
                rango = f"Rango {fmt_miles(min(steps_days))}–{fmt_miles(max(steps_days))} pasos. "
            html = replace_marked_html(
                html, "STEPS_CARD_SUB",
                f"{rango}Media de los últimos {len(steps_days) or 14} días desde Coros "
                f"(actualizado {coros.get('date', '—')}, API no oficial).",
            )
        notes.append(f"pasos 14 días: {round(steps_avg)}")

    # 3) Gráfico FC reposo — últimos 30 días
    last30 = [days[d] for d in sorted(days)[-30:]]
    rhr_series = [num(d.get("resting_hr")) for d in last30]
    if any(v is not None for v in rhr_series):
        if has_marker(html, "RHR30_LABELS", marker_html=False):
            html = replace_marked(html, "RHR30_LABELS", js_list([d["date"][5:].replace("-", "/") for d in last30], quote=True))
        if has_marker(html, "RHR30_DATA", marker_html=False):
            html = replace_marked(html, "RHR30_DATA", js_list([round(v) if v is not None else None for v in rhr_series]))
        notes.append(f"gráfico FC reposo: {sum(1 for v in rhr_series if v is not None)}/30 días con dato")

    # 4) Gráfico de carga (corto = t7d, largo = t28d) — últimos 14 días
    last14_days = [days[d] for d in sorted(days)[-14:]]
    short = [num(d.get("load_short")) for d in last14_days]
    long_ = [num(d.get("load_long")) for d in last14_days]
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
    sleep_avg = num(last14.get("sleep_hours_avg"))
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

    return html


def reconcile_weekly_lines(html, mondays, coros, manual_by_week=None, notes=None):
    """Reescribe WEEKLY_RHR / WEEKLY_SLEEP alineados con los lunes del gráfico.

    Para cada semana: si Coros tiene dato → se usa; si no, se conserva el valor
    manual que hubiera en el HTML (indexado por su lunes, no por posición, para
    que no se corra al desplazar la ventana); si no hay de ninguno → null
    (Chart.js deja hueco) en vez de inventar un dato.
    """
    notes = notes if notes is not None else []
    if not has_marker(html, "WEEKLY_RHR", marker_html=False) and not has_marker(html, "WEEKLY_SLEEP", marker_html=False):
        notes.append("gráfico semanal: sin marcadores WEEKLY_RHR/WEEKLY_SLEEP, no toco las líneas")
        return html
    if not mondays:
        notes.append("gráfico semanal: no hay lista de lunes → no puedo alinear sueño/FC reposo")
        return html

    weeks = coros_data.weekly(coros) if coros else {}
    manual_by_week = manual_by_week or {}
    for marker, key in (("WEEKLY_RHR", "resting_hr"), ("WEEKLY_SLEEP", "sleep_hours")):
        if not has_marker(html, marker, marker_html=False):
            continue
        as_int = key == "resting_hr"  # bpm enteros; horas de sueño con 2 decimales
        merged, filled, kept = [], 0, 0
        for monday in mondays:
            coros_val = stats.as_number((weeks.get(monday) or {}).get(key))
            manual = (manual_by_week.get(monday) or {}).get(marker)
            if coros_val is not None:
                merged.append(round(coros_val) if as_int else round(coros_val, 2))
                filled += 1
            elif manual is not None:
                merged.append(manual)
                kept += 1
            else:
                merged.append(None)
        html = replace_marked(html, marker, js_list(merged))
        src = f"{filled} desde Coros, " if coros else ""
        notes.append(f"{marker}: {src}{kept} manuales conservadas, {merged.count(None)} en hueco")
    return html


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


# ---------------------------------------------------------------------------
# Encabezado y pie
# ---------------------------------------------------------------------------

def apply_header_footer(html, acts, coros, today, notes):
    fecha_txt = f"{today.day} {MESES_LARGO[today.month-1][:3]} {today.year}"
    html = set_html(html, "UPDATED_DATE", fecha_txt, notes, f"fecha del encabezado: {fecha_txt}")

    if has_marker(html, "FOOTER_INFO"):
        if acts:
            strava_txt = f"actividades del {stats.fmt_range(acts[0]['date'], acts[-1]['date'])}"
        else:
            strava_txt = "actividades de los últimos 12 meses"
        if coros and coros_bits_line(coros):
            coros_txt = f"Coros ({coros_bits_line(coros)}) hasta el {coros.get('date', '—')}"
        else:
            coros_txt = "Coros (sin datos estos días)"
        f = f"Datos de Strava ({strava_txt}) y {coros_txt}, actualizado el {today.day} de {MESES_LARGO[today.month-1]} de {today.year}. "
        html = set_html(html, "FOOTER_INFO", f, notes)
    return html


# ---------------------------------------------------------------------------
# Entrenamiento 10K / 21K
# ---------------------------------------------------------------------------

def apply_entreno(html, acts, coros, today, notes):
    if not acts and not coros:
        notes.append("entreno: sin datos de Strava ni Coros, no toco la pestaña")
        return html

    # 0) Estado del plan
    week, bloque = plan_progress(today)
    if has_marker(html, "PLAN_STATUS"):
        if week is None:
            txt = f"<b>Plan:</b> arranca el {PLAN_START.day} de {MESES_LARGO[PLAN_START.month-1]} de {PLAN_START.year} (semana 1 del bloque 10K)."
        elif week <= 18:
            txt = (f"<b>Plan:</b> hoy {DIAS[today.weekday()]} {today.day} de {MESES_LARGO[today.month-1]} de {today.year} — "
                   f"vas por la <b>semana {week} de 18</b> (bloque {bloque}). Arrancó el "
                   f"{PLAN_START.day} de {MESES_LARGO[PLAN_START.month-1]} de {PLAN_START.year}.")
        else:
            txt = f"<b>Plan:</b> las 18 semanas del plan ya terminaron (arrancó el {PLAN_START.day} de {MESES_LARGO[PLAN_START.month-1]} de {PLAN_START.year})."
        html = set_html(html, "PLAN_STATUS", txt, notes, f"plan: semana {week} de 18")

    # 1) Mejores marcas 10K / 21K (últimos 12 meses, calculadas de Strava)
    if acts and has_marker(html, "ENTRENO_PB"):
        pb10 = stats.best_effort(acts, 10.0)
        pb21 = stats.best_effort(acts, 21.1)
        pieces = []
        if pb10:
            pieces.append(f"tu mejor 10K es <b>{stats.fmt_time(pb10['time_s'])}</b> ({stats.fmt_pace(pb10['pace_s'])})")
        if pb21:
            pieces.append(f"tu mejor 21K es <b>{stats.fmt_time(pb21['time_s'])}</b> ({stats.fmt_pace(pb21['pace_s'])})")
        if pieces:
            html = set_html(html, "ENTRENO_PB", " y ".join(pieces) + " (de los últimos 12 meses en Strava)", notes,
                            "mejores marcas 10K/21K recalculadas")

    # 2) Últimas 4 semanas: sesiones/semana y tirada larga más larga
    if acts and has_marker(html, "ENTRENO_LAST4W"):
        mondays = stats.recent_mondays(today, 4)
        sessions = [stats.weekly_sessions(acts, m)["runs"] for m in mondays]
        if any(sessions):
            mean_runs = sum(sessions) / len(sessions)
            in_window = [a for a in stats.runs(acts) if a["date"] >= mondays[0]]
            longest = max(in_window, key=lambda a: a["km"], default=None)
            txt = f"Últimas 4 semanas registradas: ~{stats.fmt_es(mean_runs, 1)} salidas/semana"
            if longest:
                txt += f", tirada larga hasta {stats.fmt_es(longest['km'], 1)} km"
                if longest["elevation_m"]:
                    txt += f" con desnivel (hasta {longest['elevation_m']:.0f} m)"
            html = set_html(html, "ENTRENO_LAST4W", txt + ".", notes, "últimas 4 semanas: resumen actualizado")

    # 3) Nota de rampa de volumen
    ramp = stats.volume_ramp(acts, today) if acts else None
    if ramp and has_marker(html, "ENTRENO_VOL_NOTE"):
        if ramp["trough_km"] is not None and ramp["peak_km"] > 0:
            pct = f" (+{ramp['pct']*100:.0f}%)" if ramp["pct"] else ""
            txt = (f"El volumen semanal ha subido rápido: de {stats.fmt_es(ramp['trough_km'], 1)} km "
                   f"({stats.fmt_week(ramp['trough_monday'])}) a {stats.fmt_es(ramp['peak_km'], 1)} km "
                   f"({stats.fmt_week(ramp['peak_monday'])}) en {ramp['span_weeks']} semanas{pct}.")
            html = set_html(html, "ENTRENO_VOL_NOTE", txt, notes, "nota de volumen: actualizada")

    # 4) Gráfico de volumen semanal (barras) de la pestaña Entrenamiento
    if acts and has_marker(html, "CHARTVOL_LABELS", marker_html=False):
        mondays = stats.recent_mondays(today, CHARTVOL_WEEKS, include_current=True)
        this_monday = stats.week_start(today)
        labels = [stats.week_label(m) + ("*" if m == this_monday else "") for m in mondays]
        data = [stats.weekly_km(acts, m) for m in mondays]
        html = set_js(html, "CHARTVOL_LABELS", js_list(labels, quote=True))
        html = set_js(html, "CHARTVOL_DATA", js_list(data), notes, f"gráfico de volumen: {CHARTVOL_WEEKS} semanas")

    # 5) Zonas Karvonen (opcionalmente con la FC reposo real de Coros)
    hr_rest, from_coros = hr_rest_for_zones(coros)
    if has_marker(html, "ZONES_META"):
        extra = f" — recalculadas con tu FC reposo real de Coros" if from_coros else ""
        html = set_html(html, "ZONES_META", f"Zonas de FC (Karvonen: FC reposo {hr_rest} · FC máx {HR_MAX}){extra}",
                        notes, f"zonas: FC reposo {hr_rest}")
    if has_marker(html, "ZONES_ROWS"):
        rows = []
        for z in stats.zone_table(hr_rest, HR_MAX):
            color, label, use = ZONE_STYLE[z["name"]]
            rows.append(
                f'<tr><td><span class="zone-chip" style="background:{color}"></span>{z["name"]} — {label}</td>'
                f'<td class="num">{z["lo"]}–{z["hi"]}</td><td>{use}</td></tr>'
            )
        html = set_html(html, "ZONES_ROWS", "\n        " + "\n        ".join(rows))

    # 6) Nota de fuerza (se solapa con la pestaña de Hábitos, a propósito)
    st = stats.strength_stats(acts, today) if acts else None
    if st and has_marker(html, "ENTRENO_STRENGTH_NOTE"):
        if st["last_date"] is None:
            txt = ("No hay ninguna sesión de fuerza registrada en Strava en los últimos 12 meses. "
                   "Con el volumen subiendo, 2 sesiones cortas de 25–30 min/semana (sentadilla, zancada, "
                   "puente de glúteo, core) bajan bastante el riesgo de lesión — mira la pestaña de Hábitos.")
        elif st["days_ago"] > 21:
            recientes = f"{st['n_now']} sesiones" if st["n_now"] else "ninguna"
            txt = (f"Tu última sesión de fuerza fue el {stats.fmt_day(st['last_date'])} "
                   f"(hace {st['days_ago']} días) y en los últimos 28 días: {recientes}. "
                   f"Con el volumen subiendo, 2 sesiones cortas de 25–30 min/semana (sentadilla, zancada, "
                   f"puente de glúteo, core) bajan bastante el riesgo de lesión — mira la pestaña de Hábitos.")
        else:
            txt = (f"Fuerza en marcha: {st['n_now']} sesiones en los últimos 28 días "
                   f"(última el {stats.fmt_day(st['last_date'])}). Mantén las 2 sesiones cortas de 25–30 min/semana.")
        html = set_html(html, "ENTRENO_STRENGTH_NOTE", txt, notes, "nota de fuerza: actualizada")

    return html


# ---------------------------------------------------------------------------
# Dieta
# ---------------------------------------------------------------------------

def apply_dieta(html, acts, coros, today, notes):
    last14 = (coros or {}).get("latest_14") or {}
    steps_avg = stats.as_number(last14.get("steps_avg"))

    # Pasos reales en el texto de introducción (si Coros los trae)
    if steps_avg is not None and has_marker(html, "DIETA_ACTIVITY"):
        html = set_html(html, "DIETA_ACTIVITY",
                        f"(carreras + ~{fmt_miles(steps_avg)} pasos/día de media en 14 días, vía Coros)",
                        notes, f"dieta: pasos {round(steps_avg)}")

    # Contexto real de las últimas semanas
    if (acts or coros) and has_marker(html, "DIETA_CONTEXT"):
        mondays = stats.recent_mondays(today, 4)
        km4 = [stats.weekly_km(acts, m) for m in mondays] if acts else []
        bits = []
        if acts:
            this_week = stats.weekly_sessions(acts, stats.week_start(today))
            week_km = stats.weekly_km(acts, stats.week_start(today))
            bits.append(f"esta semana: {stats.fmt_es(week_km, 1)} km en {this_week['runs']} salidas")
            if any(km4):
                bits.append(f"media de las 4 semanas anteriores: {stats.fmt_es(sum(km4)/len(km4), 1)} km/semana")
        ratio_coros = stats.as_number((coros or {}).get("latest", {}).get("load_ratio")) if coros else None
        if ratio_coros is not None:
            bits.append(f"ratio de carga {ratio_coros:.2f} (Coros)")
        if steps_avg is not None:
            bits.append(f"~{fmt_miles(steps_avg)} pasos/día en 14 días")
        if bits:
            html = set_html(html, "DIETA_CONTEXT",
                            "<b>Contexto real (auto):</b> " + " · ".join(bits) +
                            ". Ajusta las calorías de los días de calidad/tirada larga según lo que toque cada día.",
                            notes, "dieta: contexto actualizado")
    return html


# ---------------------------------------------------------------------------
# Hábitos
# ---------------------------------------------------------------------------

def apply_habitos(html, acts, coros, today, notes):
    # 01 · Fuerza
    if acts and has_marker(html, "HABIT_01_BODY"):
        st = stats.strength_stats(acts, today)
        if st["last_date"] is not None:
            prev = f" (las 4 semanas anteriores: {st['n_prev']})" if st["n_prev"] else ""
            recientes = f"{st['n_now']} sesiones" if st["n_now"] else "ninguna"
            txt = (f"Última sesión registrada: {stats.fmt_day(st['last_date'])} (hace {st['days_ago']} días). "
                   f"En los últimos 28 días: {recientes}{prev}. Con el volumen de carrera subiendo, "
                   f"es lo que más baja el riesgo de lesión de aquí al 21K. 25–30 min: sentadilla, zancada, "
                   f"puente de glúteo, plancha/core, gemelo.")
            html = set_html(html, "HABIT_01_BODY", txt, notes, f"hábitos 01: última fuerza {st['last_date']}")

    # 02 · Sueño / pasos
    nights = stats.sleep_short_nights(coros, today, days=90, n=3)
    if nights and has_marker(html, "HABIT_02_BODY"):
        def night_txt(n):
            base = f"{fmt_hours(n['hours'])} el {n['date'].day}/{n['date'].month}"
            if n["steps"]:
                base += f" ({fmt_miles(n['steps'])} pasos)"
            return base
        listed = "; ".join(night_txt(n) for n in nights)
        tail = ""
        if any(n["steps"] and n["steps"] >= 15000 for n in nights):
            tail = " Y sí: coinciden con los días de más pasos y más carga."
        txt = (f"Tus noches más cortas de los últimos 90 días: {listed}.{tail} "
               f"Son justo las noches en las que más necesitas recuperar — intenta adelantar la hora de acostarte esos días.")
        html = set_html(html, "HABIT_02_BODY", txt, notes, "hábitos 02: noches más cortas actualizadas")
    elif has_marker(html, "HABIT_02_BODY") and coros:
        notes.append("hábitos 02: Coros no expone horas de sueño → se queda el texto manual")

    # 03 · Correr Z2 realmente en Z2
    hr_rest, _ = hr_rest_for_zones(coros)
    ceiling = stats.karvonen(hr_rest, HR_MAX)["Z2"][1]
    hard = stats.hard_runs(acts, ceiling, today, days=28) if acts else None
    if hard and hard["n_with_hr"] and has_marker(html, "HABIT_03_BODY"):
        if hard["n_hard"] == 0:
            txt = (f"Ninguna de tus {hard['n_with_hr']} salidas de las últimas 4 semanas con pulso pasó del techo de Z2 "
                   f"({ceiling} bpm): el rodaje fácil está controlado. Sigue usando la FC como límite duro, no la sensación.")
        else:
            worst = hard["worst"]
            extra = ""
            if worst:
                extra = (f" La más alta: {worst['avg_hr']:.0f} bpm de media el {worst['date'].day}/{worst['date'].month}"
                         f" ({esc(worst['name'])}).") if worst.get("name") else ""
            txt = (f"De tus últimas {hard['n_runs']} salidas ({hard['n_with_hr']} con pulso), {hard['n_hard']} acabaron con "
                   f"FC media por encima del techo de Z2 ({ceiling} bpm).{extra} Usa la FC como límite duro, no la sensación — "
                   f"si te pasas de Z2, camina un poco.")
        html = set_html(html, "HABIT_03_BODY", txt, notes, f"hábitos 03: {hard['n_hard']}/{hard['n_with_hr']} salidas sobre Z2")

    # 05 · Subir el volumen con calma
    ramp = stats.volume_ramp(acts, today) if acts else None
    if ramp and ramp["trough_km"] is not None and has_marker(html, "HABIT_05_BODY"):
        pct = f" (+{ramp['pct']*100:.0f}%)" if ramp["pct"] else ""
        txt = (f"Pasaste de {stats.fmt_es(ramp['trough_km'], 1)} km ({stats.fmt_week(ramp['trough_monday'])}) a "
               f"{stats.fmt_es(ramp['peak_km'], 1)} km ({stats.fmt_week(ramp['peak_monday'])}) en {ramp['span_weeks']} semanas{pct}. "
               f"A partir de aquí, no subas más de un 10% de una semana a otra salvo semanas de descarga.")
        html = set_html(html, "HABIT_05_BODY", txt, notes, "hábitos 05: rampa de volumen actualizada")

    return html


# ---------------------------------------------------------------------------
# Historial
# ---------------------------------------------------------------------------

def snapshot_weekly_cards(html, today=None):
    """Lee las tarjetas de semana que ya hay en el HTML → { lunes: {...} }.

    Sirve para no perder tus valores manuales de Sueño / FC reposo cuando Strava
    regenera esa rejilla (Coros casi nunca trae sueño, así que sin esto se
    quedaría en '—'). El año se deduce buscando la fecha más cercana a hoy.
    """
    block = get_marked(html, marker="WEEKLY_CARDS")
    if not block:
        return {}
    out = {}
    today = today or date.today()
    for card in re.findall(r'<div class="wk-lbl">(.*?)</div>(.*?)(?=<div class="wcard|<div class="wk-lbl">|$)', block, re.DOTALL):
        label, body = card
        monday = _monday_from_label(label, today)
        if monday is None:
            continue
        vals = {}
        sleep = re.search(r'<span>Sueño[^<]*(?:<i[^>]*>.*?</i>)?</span>\s*<span class="v">(\d+)\s*h\s*(\d+)\s*m', body)
        if sleep:
            vals["sleep_hours"] = int(sleep.group(1)) + int(sleep.group(2)) / 60
        rhr = re.search(r'<span>FC reposo</span>\s*<span class="v">(\d+)\s*bpm', body)
        if rhr:
            vals["resting_hr"] = int(rhr.group(1))
        if vals:
            out[monday.isoformat()] = vals
    return out


def _monday_from_label(label, today):
    """'8–14 jun' / '29 jun–5 jul' / '14-20 sep · en curso' → el lunes de esa semana."""
    txt = label.split("·")[0].strip()
    parts = re.split(r"[–-]", txt.replace("\u2013", "-"))
    if len(parts) < 2:
        return None

    def parse(part, fallback_month=None):
        m = re.match(r"^\s*(\d{1,2})\s*([a-záéíóú]{3,})?\s*$", part, re.IGNORECASE)
        if not m:
            return None
        day = int(m.group(1))
        month_txt = (m.group(2) or fallback_month or "").lower()[:3]
        month = next((i + 1 for i, name in enumerate(MESES_LARGO) if name.startswith(month_txt)), None) if month_txt else None
        return (day, month)

    end = parse(parts[-1])
    start = parse(parts[0], fallback_month=(MESES_LARGO[end[1] - 1] if end and end[1] else None))
    if not (start and end and start[1] and end[1]):
        return None
    best = None
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            d = date(year, start[1], start[0])
        except ValueError:
            continue
        monday = stats.week_start(d)
        dist = abs((monday - today).days)
        if best is None or dist < best[0]:
            best = (dist, monday)
    return best[1] if best else None


def snapshot_monthly_cards(html):
    """{ 1..12: '<div class="mcard">…</div>' } con las tarjetas mensuales manuales."""
    block = get_marked(html, marker="MONTHLY_CARDS")
    if not block:
        return {}
    out = {}
    for snippet in re.findall(r'<div class="mcard">.*?</div></div>', block, re.DOTALL):
        m = re.search(r"<h4>([^<]+)</h4>", snippet)
        if not m:
            continue
        name = m.group(1).strip().lower()
        month = next((i + 1 for i, mes in enumerate(MESES_LARGO) if mes.startswith(name[:3])), None)
        if month:
            out[month] = snippet.strip()
    return out


def apply_historial(html, acts, coros, today, notes):
    coros_weeks = stats.coros_weekly_map(coros)
    manual_cards = snapshot_weekly_cards(html)

    # 0) Nota de cobertura (solo si hay datos: sin ellos, el texto manual
    #    explica más que cualquier cosa que podamos generar)
    if (acts or coros) and has_marker(html, "HIST_COVERAGE_NOTE"):
        bits = []
        if acts:
            bits.append(f"actividades de Strava del {stats.fmt_range(acts[0]['date'], acts[-1]['date'])}")
        if coros:
            try:
                coros_day = date.fromisoformat(str(coros.get("date"))[:10])
            except (TypeError, ValueError):
                coros_day = None
            cuando = stats.fmt_day(coros_day) if coros_day else str(coros.get("date", "—"))
            bits.append(f"Coros hasta el {cuando} ({coros_bits_line(coros)})")
        week, bloque = plan_progress(today)
        plan_txt = (f"Hoy es {DIAS[today.weekday()]} {today.day} de {MESES_LARGO[today.month-1]} y vas por la semana {week} de 18 "
                    f"del plan ({bloque})." if week and week <= 18 else
                    f"Hoy es {DIAS[today.weekday()]} {today.day} de {MESES_LARGO[today.month-1]}.")
        txt = (f"<b>Cobertura de datos:</b> {'; '.join(bits) if bits else 'sin datos nuevos'}. "
               f"El detalle semana a semana cubre las últimas {WEEKLY_CARDS} semanas completas. {plan_txt}")
        html = set_html(html, "HIST_COVERAGE_NOTE", txt, notes)

    # 1) Tarjetas de tendencia (4 + 4 semanas) y veredicto
    t = stats.trend(acts, coros, today, weeks=4)
    rng_now = stats.fmt_range(t["now"]["start"], t["now"]["end"])
    rng_prev = stats.fmt_range(t["prev"]["start"], t["prev"]["end"])

    if t["vol"]["now"] is not None and has_marker(html, "TREND_VOL_CARD"):
        pct = None
        if t["vol"]["prev"]:
            pct = (t["vol"]["now"] - t["vol"]["prev"]) / t["vol"]["prev"] * 100
        card = (f'<div class="card">\n        <h3>Volumen de carrera</h3>\n'
                f'        <div class="big">{stats.fmt_es(t["vol"]["now"], 1)} <span style="font-size:16px;">km/sem</span> '
                f'{delta_span(pct, 0, kind="pct")}</div>\n'
                f'        <div class="sub">Media de las últimas 4 semanas ({rng_now})')
        if t["vol"]["prev"] is not None:
            card += f' frente a las 4 anteriores ({rng_prev}, {stats.fmt_es(t["vol"]["prev"], 1)} km/sem).'
        else:
            card += "."
        card += '</div>\n      </div>'
        html = set_html(html, "TREND_VOL_CARD", card, notes, "historial: tarjeta de volumen")

    if t["rhr"]["now"] is not None and has_marker(html, "TREND_RHR_CARD"):
        d = (t["rhr"]["now"] - t["rhr"]["prev"]) if t["rhr"]["prev"] is not None else None
        card = (f'<div class="card">\n        <h3>FC reposo</h3>\n'
                f'        <div class="big">{t["rhr"]["now"]:.0f} <span style="font-size:16px;">bpm</span> '
                f'{delta_span(t["rhr"]["now"], t["rhr"]["prev"], good_up=False, kind="bpm")}</div>\n'
                f'        <div class="sub">Media de {t["rhr"]["n_now"]} días con dato de Coros en las últimas 4 semanas ({rng_now})')
        if d is not None and abs(d) >= 0.5:
            card += (f' → {stats.fmt_es(d, 1)} bpm respecto a las 4 anteriores '
                     f'({stats.fmt_es(t["rhr"]["prev"], 1)} bpm, {t["rhr"]["n_prev"]} días con dato).')
        elif d is not None:
            card += f' → prácticamente igual que las 4 anteriores ({stats.fmt_es(t["rhr"]["prev"], 1)} bpm).'
        else:
            card += ' (aún no hay 4 semanas anteriores con dato de Coros para comparar).'
        card += '</div>\n      </div>'
        html = set_html(html, "TREND_RHR_CARD", card, notes, "historial: tarjeta de FC reposo")

    if t["sleep"]["now"] is not None and has_marker(html, "TREND_SLEEP_CARD"):
        card = (f'<div class="card">\n        <h3>Sueño</h3>\n'
                f'        <div class="big">{fmt_hours_compact(t["sleep"]["now"])} '
                f'{delta_span(t["sleep"]["now"], t["sleep"]["prev"], kind="hours")}</div>\n'
                f'        <div class="sub">Media de {t["sleep"]["n_now"]} noches con dato de Coros ({rng_now}).</div>\n      </div>')
        html = set_html(html, "TREND_SLEEP_CARD", card, notes, "historial: tarjeta de sueño")

    if has_marker(html, "TREND_VERDICT"):
        sent = []
        if t["vol"]["now"] is not None and t["vol"]["prev"]:
            pct = (t["vol"]["now"] - t["vol"]["prev"]) / t["vol"]["prev"] * 100
            if pct >= 20:
                sent.append(f"el volumen sube un {pct:.0f}% respecto a las 4 semanas anteriores")
            elif pct <= -20:
                sent.append(f"el volumen ha bajado un {abs(pct):.0f}%")
            else:
                sent.append("el volumen se mantiene estable")
        warn = False
        if t["rhr"]["now"] is not None and t["rhr"]["prev"] is not None:
            d = t["rhr"]["now"] - t["rhr"]["prev"]
            if d >= 2:
                sent.append(f"y la FC reposo lo está notando ({d:+.0f} bpm)")
                warn = True
            elif d <= -2:
                sent.append(f"y la FC reposo mejora ({d:+.0f} bpm)")
        if t["sleep"]["now"] is not None and t["sleep"]["prev"] is not None:
            mins = (t["sleep"]["now"] - t["sleep"]["prev"]) * 60
            if mins <= -15:
                sent.append(f"el sueño baja {abs(mins):.0f} min")
                warn = True
            elif mins >= 15:
                sent.append(f"el sueño mejora {mins:.0f} min")
        if sent:
            conclusion = ("No es alarmante, pero es el momento de reforzar el descanso en los días de carga alta "
                          "(pestaña Hábitos) antes de seguir subiendo volumen.") if warn else \
                         ("Toleras bien la carga: hay margen para seguir subiendo volumen con control.")
            html = set_html(html, "TREND_VERDICT", "En conjunto: " + ", ".join(sent) + ". " + conclusion, notes,
                            "historial: veredicto actualizado")

    # 2) Carreras del año (Strava, workout_type = Race)
    if acts and has_marker(html, "RACES"):
        year_races = stats.races(acts, year=today.year)
        if year_races:
            items = []
            for r in year_races:
                cuando = f"{r['date'].day} {MESES_LARGO[r['date'].month-1]}"
                items.append(
                    f'<div class="race-item"><div><div class="rname">{esc(r["name"])}</div>'
                    f'<div class="rmeta">{cuando} · {stats.fmt_es(r["km"], 1)} km</div></div>'
                    f'<div class="num">{stats.fmt_time(r["time_s"])}</div></div>'
                )
            html = set_html(html, "RACES", "\n      ".join(items), notes, f"historial: {len(year_races)} carreras del año")

    # 3) Gráficos mensuales (barras + FC reposo) y tarjetas mensuales
    manual_months = snapshot_monthly_cards(html)
    if acts:
        year = today.year
        months = list(range(1, today.month + 1))
        vol = stats.month_volume(acts, year)
        manual_year = parse_js_array(get_marked(html, marker_html=False, marker="MONTHLY_YEAR"))
        manual_rhr = [v for v in parse_js_array(get_marked(html, marker_html=False, marker="MONTHLY_RHR"))]
        coros_rhr = stats.coros_month_rhr(coros, year) if coros else {}

        if any(m in vol for m in months):
            html = set_js(html, "MONTHLY_LABELS",
                          js_list([f"{MESES[m-1]}{'*' if m == today.month else ''}" for m in months], quote=True))
            html = set_js(html, "MONTHLY_VOL", js_list([vol.get(m) or 0.0 for m in months]), notes, "historial: barras mensuales")
            html = set_js(html, "MONTHLY_YEAR", str(year))
            html = set_html(html, "MONTHLY_VOL_YEAR", str(year))
            html = set_html(html, "MONTHLY_RHR_YEAR", str(year))

            # FC reposo mensual: dato de Coros si lo hay; si no, se conserva el
            # valor manual (solo si el array manual es del mismo año).
            same_year = bool(manual_year) and int(manual_year[0]) == year
            rhr_vals = []
            for i, m in enumerate(months):
                if m in coros_rhr:
                    rhr_vals.append(coros_rhr[m]["avg"])
                elif same_year and i < len(manual_rhr):
                    rhr_vals.append(manual_rhr[i])
                else:
                    rhr_vals.append(None)
            html = set_js(html, "MONTHLY_RHR", js_list(rhr_vals), notes, "historial: FC reposo mensual")

        if has_marker(html, "MONTHLY_RANGE_TITLE"):
            title = (f"Resumen mensual — {MESES_LARGO[0]}" if today.month == 1
                     else f"Resumen mensual — {MESES_LARGO[0]} a {MESES_LARGO[today.month-1]}")
            html = set_html(html, "MONTHLY_RANGE_TITLE", title)

        if has_marker(html, "MONTHLY_CARDS"):
            cards = []
            for m in months:
                km = vol.get(m)
                sess = stats.month_sessions(acts, year).get(m, {})
                race = next((r for r in stats.races(acts, year=year) if r["date"].month == m), None)
                coros_m = coros_rhr.get(m)
                if km is None and not sess.get("runs"):
                    # Sin dato en Strava: se conserva la tarjeta manual de ese mes
                    if m in manual_months:
                        cards.append(manual_months[m])
                    continue
                parts = []
                if sess.get("runs"):
                    parts.append(f"{sess['runs']} salida" + ("s" if sess["runs"] > 1 else ""))
                if sess.get("strength"):
                    parts.append(f"{sess['strength']} de fuerza")
                if coros_m:
                    parts.append(f"FC reposo {coros_m['avg']:.0f} bpm")
                if race:
                    parts.append(esc(race["name"]))
                cards.append(
                    f'<div class="mcard"><h4>{MESES_LARGO[m-1].capitalize()}</h4>'
                    f'<div class="v">{stats.fmt_es(km or 0, 1)} km</div>'
                    f'<div class="m2">{" · ".join(parts) if parts else "—"}</div></div>'
                )
            if cards:
                html = set_html(html, "MONTHLY_CARDS", "\n      ".join(cards), notes, f"historial: {len(cards)} meses resumidos")

    # 4) Gráfico de tendencia semanal (rolling window de semanas consecutivas)
    if acts:
        mondays = stats.recent_mondays(today, MAX_WEEKS_IN_CHART, include_current=True)
        this_monday = stats.week_start(today)
        vol_values = [stats.weekly_km(acts, m) for m in mondays]
        labels = [stats.week_label(m) + ("*" if m == this_monday else "") for m in mondays]
        html = set_js(html, "WEEKLY_LABELS", js_list(labels, quote=True))
        html = set_js(html, "WEEKLY_VOL", js_list(vol_values))
        html = set_js(html, "WEEKLY_MONDAYS", js_list([m.isoformat() for m in mondays], quote=True))
        notes.append(f"gráfico semanal: {MAX_WEEKS_IN_CHART} semanas")
        mondays_iso = [m.isoformat() for m in mondays]
    else:
        mondays_iso = [m for m in parse_js_array(get_marked(html, marker_html=False, marker="WEEKLY_MONDAYS")) if isinstance(m, str)]

    # 5) Tarjetas "semana a semana" (12 semanas completas)
    if acts and has_marker(html, "WEEKLY_CARDS"):
        mondays = stats.recent_mondays(today, WEEKLY_CARDS)
        max_km = max((stats.weekly_km(acts, m) for m in mondays), default=0)
        cards = []
        for i, m in enumerate(mondays):
            sess = stats.weekly_sessions(acts, m)
            km = sess["km"]
            prev_km = stats.weekly_km(acts, m - timedelta(days=7))
            iso = m.isoformat()
            coros_w = coros_weeks.get(iso) or {}
            sleep = stats.as_number(coros_w.get("sleep_hours"))
            if sleep is None:
                sleep = (manual_cards.get(iso) or {}).get("sleep_hours")
            rhr = stats.as_number(coros_w.get("resting_hr"))
            if rhr is None:
                rhr = (manual_cards.get(iso) or {}).get("resting_hr")
            prev_iso = (m - timedelta(days=7)).isoformat()
            prev_sleep = stats.as_number((coros_weeks.get(prev_iso) or {}).get("sleep_hours"))
            if prev_sleep is None:
                prev_sleep = (manual_cards.get(prev_iso) or {}).get("sleep_hours")
            prev_rhr = stats.as_number((coros_weeks.get(prev_iso) or {}).get("resting_hr"))
            if prev_rhr is None:
                prev_rhr = (manual_cards.get(prev_iso) or {}).get("resting_hr")

            if not sess["runs"] and not sess["strength"] and sleep is None and rhr is None:
                continue  # semana sin nada que contar

            session_bits = []
            if sess["runs"]:
                session_bits.append(str(sess["runs"]))
            elif km == 0:
                session_bits.append("0")
            if sess["strength"]:
                session_bits.append(f"{sess['strength']} fuerza" if not sess["runs"] else f"+ {sess['strength']} fuerza")
            if sess["walks"]:
                session_bits.append(f"+ {sess['walks']} caminata" + ("s" if sess["walks"] > 1 else ""))
            sessions_txt = " ".join(session_bits) or "0"
            if km and km == max_km:
                sessions_txt += " — pico del periodo"

            sleep_txt = (f"{fmt_hours_compact(sleep)} {delta_span(sleep, prev_sleep, kind='hours')}".strip()
                         if sleep is not None else "—")
            rhr_txt = (f"{rhr:.0f} bpm {delta_span(rhr, prev_rhr, good_up=False, kind='bpm')}".strip()
                       if rhr is not None else "—")
            partial = ' partial' if m == stats.week_start(today) else ''
            km_txt = f"{stats.fmt_es(km, 1)} km"
            km_delta = delta_span(km, prev_km, kind="num") if i else ""
            cards.append(
                f'<div class="wcard{partial}"><div class="wk-lbl">{stats.week_label(m)}</div>\n'
                f'        <div class="metric"><span>Carrera</span><span class="v">'
                f'{km_txt + (" " + km_delta if km_delta else "")}</span></div>\n'
                f'        <div class="metric"><span>Sesiones</span><span class="v">{sessions_txt}</span></div>\n'
                f'        <div class="metric"><span>Sueño</span><span class="v">{sleep_txt}</span></div>\n'
                f'        <div class="metric"><span>FC reposo</span><span class="v">{rhr_txt}</span></div>\n'
                f'      </div>'
            )
        if cards:
            html = set_html(html, "WEEKLY_CARDS", "\n      ".join(cards), notes, f"historial: {len(cards)} tarjetas semanales")
        else:
            notes.append("historial: no hay semanas con datos para las tarjetas")

    return html, mondays_iso


def build_current_week_card(this_week_monday, this_week_km, this_week_sessions, coros_week=None, has_coros=False):
    """Tarjeta de la semana en curso; con sueño/FC reposo reales si los hay."""
    label = stats.week_label(this_week_monday) + " · en curso"
    coros_week = coros_week or {}
    sleep = stats.as_number(coros_week.get("sleep_hours"))
    rhr = stats.as_number(coros_week.get("resting_hr"))

    def metric(name, value, ok):
        if ok:
            return f'<div class="metric"><span>{name}</span><span class="v">{value}</span></div>'
        style = ' style="opacity:0.7;"' if has_coros else ""
        tag = " <i style=\"font-size:10px;\">(manual)</i>"
        return (
            f'<div class="metric"{style}><span>{name}{tag}</span><span class="v">—</span></div>'
        )

    # Ojo: los dos argumentos se evalúan siempre, aunque `ok` sea False. Se
    # calculan aquí para que un None (Coros sin esa semana) no reviente el
    # `round()` como pasaba antes.
    sleep_val = fmt_hours_compact(sleep) if sleep is not None else "—"
    rhr_val = f"{round(rhr)} bpm" if rhr is not None else "—"

    return (
        f'''<div class="wcard partial"><div class="wk-lbl">{label}</div>
        <div class="metric"><span>Carrera</span><span class="v">{stats.fmt_es(this_week_km, 1)} km</span></div>
        <div class="metric"><span>Sesiones</span><span class="v">{this_week_sessions}</span></div>
        {metric("Sueño", sleep_val, sleep is not None)}
        {metric("FC reposo", rhr_val, rhr is not None)}
      </div>'''
    )


# ---------------------------------------------------------------------------
# Recomendaciones
# ---------------------------------------------------------------------------

def build_recos(acts, coros, today):
    """[(warn, título, cuerpo)] generados a partir de los datos disponibles."""
    items = []
    latest = (coros or {}).get("latest") or {}
    last14 = (coros or {}).get("latest_14") or {}

    ramp = stats.volume_ramp(acts, today) if acts else None
    if ramp and ramp["trough_km"] is not None and ramp["pct"] and ramp["pct"] >= 0.6 and ramp["span_weeks"] <= 4:
        items.append((True, "Salto fuerte de volumen reciente",
                      f"De {stats.fmt_es(ramp['trough_km'], 1)} km ({stats.fmt_week(ramp['trough_monday'])}) a "
                      f"{stats.fmt_es(ramp['peak_km'], 1)} km ({stats.fmt_week(ramp['peak_monday'])}) en "
                      f"{ramp['span_weeks']} semanas (+{ramp['pct']*100:.0f}%). La regla del 10% semanal se supera de largo: "
                      f"estabiliza 1–2 semanas antes de volver a subir el volumen."))

    ratio = stats.as_number(latest.get("load_ratio"))
    if ratio is not None:
        if ratio > 1.3:
            items.append((True, "Ratio de carga alto (por encima de 1.3)",
                          f"Ratio de carga {ratio:.2f} en Coros: estás metiendo más carga de la que tu base digiere sin coste. "
                          f"Baja la intensidad de las próximas 2–3 sesiones y prioriza dormir."))
        elif ratio < 0.85:
            items.append((False, "Margen para más volumen",
                          f"Ratio de carga {ratio:.2f} en Coros: por debajo de tu carga habitual, así que hay margen para meter "
                          f"más volumen (con la regla del 10% semanal)."))
        else:
            items.append((False, "Carga en banda óptima",
                          f"Ratio de carga {ratio:.2f} en Coros: dentro de la banda habitual de rendimiento."))

    rhr14 = stats.as_number(last14.get("resting_hr_avg"))
    if rhr14 is not None:
        lo = stats.as_number(last14.get("resting_hr_min"))
        hi = stats.as_number(last14.get("resting_hr_max"))
        rango = f"rango {lo:.0f}–{hi:.0f}" if lo is not None and hi is not None else "sin rango"
        hrv, hrv_base = last14.get("hrv_avg"), latest.get("hrv_base")
        hrv_txt = ""
        if hrv is not None:
            hrv_txt = f" y HRV {hrv:.0f} ms"
            if hrv_base:
                hrv_txt += f" (baseline {hrv_base:.0f})"
        if rhr14 >= HR_REST + 3:
            items.append((True, "FC reposo por encima de tu referencia",
                          f"{rhr14:.0f} bpm de media en 14 días ({rango}) frente a tu referencia de {HR_REST} bpm{hrv_txt}. "
                          f"Suele ser fatiga acumulada o falta de sueño: baja intensidad hasta que vuelva a su sitio."))
        else:
            items.append((False, "Buena base de recuperación",
                          f"FC reposo {rhr14:.0f} bpm de media en 14 días ({rango}){hrv_txt}: tu cuerpo tolera bien la carga "
                          f"actual, así que el margen está en meter más entrenamiento, no en preocuparte por sobreentrenar."))

    sleep14 = stats.as_number(last14.get("sleep_hours_avg"))
    if sleep14 is not None:
        if sleep14 < 7:
            items.append((True, "Sueño por debajo de 7 h de media",
                          f"{fmt_hours(sleep14)} de media en 14 días. Con el volumen actual, dormir es la palanca más barata "
                          f"que tienes: intenta adelantar la hora de acostarte los días de carga alta."))
        else:
            items.append((False, "Sueño en rango",
                          f"{fmt_hours(sleep14)} de media en 14 días: suficiente para sostener la carga actual."))

    if acts:
        st = stats.strength_stats(acts, today)
        if st["last_date"] is None:
            items.append((True, "Sin sesiones de fuerza registradas",
                          "No aparece ninguna sesión de WeightTraining en el último año de Strava. Retomar 2x/semana es la "
                          "palanca más directa para llegar sano al 21K con el volumen que viene."))
        elif st["days_ago"] > 21:
            items.append((True, f"Fuerza parada desde el {stats.fmt_day(st['last_date'])}",
                          f"Última sesión hace {st['days_ago']} días. Retomarla 2x/semana es la palanca más directa para llegar "
                          f"sano al 21K con el volumen que viene."))
        else:
            items.append((False, "Fuerza en marcha",
                          f"{st['n_now']} sesiones en los últimos 28 días (última el {stats.fmt_day(st['last_date'])}). "
                          f"Mantén el ritmo de 2x/semana."))

    # Recomendaciones fijas (no dependen de datos automáticos)
    items.append((False, "Peso confirmado: 73 kg",
                  "Ya recalculado en dieta y en el resumen con este dato. Solo falta que actualices el perfil de Coros, "
                  "que sigue marcando 70 kg, para que ambas plataformas coincidan."))
    items.append((False, "Zonas de FC de Strava desactualizadas",
                  "Tus zonas manuales en Strava no coinciden exactamente con el cálculo Karvonen que vienes usando en tus "
                  "análisis. Actualízalas ahí para que Strava, Coros y estas tablas usen el mismo criterio."))
    items.append((False, "No tengo datos de Apple Salud / MiSalud",
                  "Este panel se basa solo en Strava y Coros. Si quieres que las cifras de peso, pasos o FC se crucen también "
                  "con Apple Salud, no hay conector directo: o pasas los datos a mano o revisas si Apple Salud sincroniza ya "
                  "con Coros/Strava."))
    return items


def apply_recos(html, acts, coros, today, notes):
    if not acts and not coros:
        notes.append("recomendaciones: sin datos, no toco la pestaña")
        return html
    if not has_marker(html, "RECOS"):
        notes.append("recomendaciones: marcador RECOS ausente")
        return html
    items = build_recos(acts, coros, today)
    out = []
    for warn, title, body in items:
        cls = "rec warn" if warn else "rec"
        out.append(f'<div class="{cls}">\n      <div class="dot"></div>\n      '
                   f'<div><h3>{esc(title)}</h3><p>{esc(body)}</p></div>\n    </div>')
    html = replace_marked_html(html, "RECOS", "\n    ".join(out))
    notes.append(f"recomendaciones: {sum(1 for i in items if i[0])} avisos de {len(items)} puntos")
    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def safe(html, label, fn, notes):
    """Ejecuta una sección sin tumbar el resto: un fallo se anota y se sigue."""
    try:
        result = fn(html)
    except Exception as exc:  # noqa: BLE001 — es un cron: mejor parcial que nada
        notes.append(f"⚠ {label} falló: {exc}")
        print(f"  ⚠ {label} falló: {exc}")
        return html
    return result


def main():
    parser = argparse.ArgumentParser(description="Actualiza el dashboard desde Strava y (opcional) Coros")
    parser.add_argument("--only-coros", action="store_true", help="solo la parte de Coros; no toca Strava")
    parser.add_argument("--dry-run", action="store_true", help="no escribe el HTML")
    args = parser.parse_args()

    today = date.today()
    html = DASHBOARD_PATH.read_text(encoding="utf-8")
    coros = coros_data.load()
    notes = []

    # Fotograma de los valores manuales ANTES de tocar nada, indexado por el
    # lunes al que le tocan, para poder conservarlos al desplazar la ventana.
    manual_by_week = snapshot_weekly_manual(html)

    if args.only_coros:
        acts = []
        mondays = [m for m in parse_js_array(get_marked(html, marker_html=False, marker="WEEKLY_MONDAYS")) if isinstance(m, str)]
        html = safe(html, "Resumen (Coros)", lambda h: apply_coros(h, coros, today, notes), notes)
        html = safe(html, "líneas semanales", lambda h: reconcile_weekly_lines(h, mondays, coros, manual_by_week, notes), notes)
        html = safe(html, "encabezado/pie", lambda h: apply_header_footer(h, acts, coros, today, notes), notes)
        notes.append("modo --only-coros: Strava no se toca (entreno, dieta, hábitos, historial, recos)")
    else:
        acts = load_activities(today)
        print(f"· Strava: {len(acts)} actividades (últimos {STRAVA_HISTORY_DAYS} días), "
              f"{len(stats.runs(acts))} de carrera.")

        this_monday = stats.week_start(today)
        this_week = stats.weekly_sessions(acts, this_monday)
        month_km = sum(v for m, v in stats.month_volume(acts, today.year).items() if m == today.month)
        print(f"· Semana en curso: {this_week['km']} km / {this_week['runs']} salidas · mes {month_km:.1f} km.")

        html = safe(html, "encabezado/pie", lambda h: apply_header_footer(h, acts, coros, today, notes), notes)
        html = safe(html, "Resumen (Coros)", lambda h: apply_coros(h, coros, today, notes), notes)
        html = safe(html, "Entrenamiento", lambda h: apply_entreno(h, acts, coros, today, notes), notes)
        html = safe(html, "Dieta", lambda h: apply_dieta(h, acts, coros, today, notes), notes)
        html = safe(html, "Hábitos", lambda h: apply_habitos(h, acts, coros, today, notes), notes)
        result = safe_result(html, "Historial", lambda h: apply_historial(h, acts, coros, today, notes), notes)
        html, mondays_iso = result
        html = safe(html, "líneas semanales", lambda h: reconcile_weekly_lines(h, mondays_iso, coros, manual_by_week, notes), notes)
        html = safe(html, "Recomendaciones", lambda h: apply_recos(h, acts, coros, today, notes), notes)

        # Tarjeta de la semana en curso (solo si Strava devolvió algo: sin
        # actividades no sabemos si es descanso o un fallo de la API)
        if acts and has_marker(html, "CURRENT_WEEK"):
            coros_week = stats.coros_weekly_map(coros).get(this_monday.isoformat()) if coros else None
            card = build_current_week_card(this_monday, this_week["km"], this_week["runs"],
                                           coros_week=coros_week, has_coros=coros is not None)
            html = set_html(html, "CURRENT_WEEK", card, notes, "historial: tarjeta de la semana en curso")

    for n in notes:
        print(f"  · {n}")
    for w in (coros or {}).get("warnings", []):
        print(f"  ⚠ {w}")

    if args.dry_run:
        print("· --dry-run: no escribo el HTML.")
        return 0

    DASHBOARD_PATH.write_text(html, encoding="utf-8")
    try:
        donde = DASHBOARD_PATH.relative_to(DASHBOARD_PATH.parents[1])
    except ValueError:  # ruta rara (tests, /tmp…)
        donde = DASHBOARD_PATH
    print(f"Dashboard escrito en {donde}.")
    return 0


def safe_result(html, label, fn, notes):
    """Como `safe`, pero para secciones que devuelven (html, extra)."""
    try:
        return fn(html)
    except Exception as exc:  # noqa: BLE001
        notes.append(f"⚠ {label} falló: {exc}")
        print(f"  ⚠ {label} falló: {exc}")
        return html, []


if __name__ == "__main__":
    sys.exit(main())
