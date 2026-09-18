"""
Tests del dashboard automático (sin red, sin credenciales).

    python -m unittest discover -s tests -t .
    # o simplemente:  python -m unittest tests.test_dashboard

Cubren las tres cosas que pueden romperse en silencio:

1. Que los marcadores `AUTO:` que el script intenta escribir existan (y una sola
   vez) en `dashboard/dashboard-kuthuluh.html`.
2. Que con datos los números salgan bien, y sin datos **no se invente nada**
   (el HTML se queda con los valores manuales).
3. Que dos ejecuciones seguidas den el mismo HTML (idempotencia) y que las
   constantes compartidas con `daily_brief.py` sigan cuadrando.
"""

import json
import os
import re
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import coros_data  # noqa: E402
import dashboard_stats as stats  # noqa: E402
import update_dashboard as ud  # noqa: E402

DASHBOARD = ROOT / "dashboard" / "dashboard-kthuluh.html"
TODAY = date(2026, 9, 18)  # viernes: fijo, para que las aserciones sean estables

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def raw_activity(day, km, minutes, type_="Run", hr=None, name="Rodaje", workout_type=None, elev=0.0):
    return {
        "id": f"{day.isoformat()}-{name[:6]}-{km}",
        "name": name,
        "type": type_,
        "sport_type": type_,
        "start_date_local": f"{day.isoformat()}T07:30:00Z",
        "distance": km * 1000,
        "moving_time": int(minutes * 60),
        "elapsed_time": int(minutes * 60) + 40,
        "total_elevation_gain": elev,
        "average_heartrate": hr,
        "workout_type": workout_type,
    }


# Volumen de las últimas 12 semanas completas (de más antigua a más reciente):
# incluye una rampa fuerte (10 → 31 km en 3 semanas) para disparar los avisos.
WEEKLY_KM = [22, 18, 16, 12, 10, 12, 26, 31, 28, 24, 22, 20]
CURRENT_WEEK_KM = [8.0, 4.2]


def build_activities():
    acts = []
    this_monday = stats.week_start(TODAY)

    # Semanas completas (martes, jueves, sábado y domingo según el volumen)
    for i, km in enumerate(WEEKLY_KM):
        monday = this_monday - timedelta(weeks=len(WEEKLY_KM) - i)
        if km == 0:
            continue
        easy, quality, long_run = km * 0.25, km * 0.3, km * 0.45
        acts.append(raw_activity(monday + timedelta(days=1), round(easy, 1), round(easy * 6.0), hr=136))
        acts.append(raw_activity(monday + timedelta(days=3), round(quality, 1), round(quality * 4.45), hr=158, name="Series"))
        acts.append(raw_activity(monday + timedelta(days=6), round(long_run, 1), round(long_run * 5.6), hr=141, elev=180))

    # Semana en curso (sin terminar) → tarjeta "en curso" y última barra con *
    for j, km in enumerate(CURRENT_WEEK_KM):
        acts.append(raw_activity(this_monday + timedelta(days=j + 1), km, round(km * 6.1), hr=138))

    # Carreras del año (workout_type=1)
    acts.append(raw_activity(date(2026, 1, 18), 21.1, 117.0, name="La Mitja de Granollers", workout_type=1, hr=165))
    acts.append(raw_activity(date(2026, 2, 15), 42.2, 264.1, name="Marató Sevilla", workout_type=1, hr=158))
    acts.append(raw_activity(date(2026, 5, 10), 10.0, 49.5, name="Cursa El Corte Inglés", workout_type=1, hr=172))
    acts.append(raw_activity(date(2026, 5, 24), 10.0, 49.2, name="Cursa Diagonal", workout_type=1, hr=171))

    # Fuerza: 2x/semana hasta el 20 de julio, después nada
    for i in range(18):
        day = date(2026, 4, 6) + timedelta(days=7 * i)
        if day > date(2026, 7, 20):
            break
        acts.append(raw_activity(day, 0.0, 30, type_="WeightTraining", name="Fuerza"))

    # Alguna caminata suelta
    acts.append(raw_activity(this_monday - timedelta(days=9), 4.5, 55, type_="Walk", name="Caminata"))
    return acts


def build_coros(today=TODAY, days=120):
    """Un coros_data.json con la misma forma que escribe scripts/coros_fetch.mjs."""
    day_list = []
    for i in range(days):
        d = today - timedelta(days=i)
        rhr = 46 + (i % 7) % 5
        day_list.append({
            "date": d.isoformat(),
            "resting_hr": rhr,
            "hrv": 44 + (i % 5) - 2,
            "hrv_base": 42,
            "sleep_hours": round(6.1 + (i % 5) * 0.35, 2),  # 6.10 - 7.50
            "steps": 12000 + (i * 613) % 9000,
            "load_short": 200 + i,
            "load_long": 1300 - i,
            "load_ratio": 0.52 + (i % 10) * 0.01,
        })
    day_list[0]["sleep_hours"] = 5.75  # la noche más corta, para el hábito 02

    def mean(key, upto):
        vals = [d[key] for d in day_list[:upto] if d.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    weeks = {}
    for d in day_list:
        monday = stats.week_start(date.fromisoformat(d["date"])).isoformat()
        bucket = weeks.setdefault(monday, {"resting_hr": [], "sleep_hours": [], "hrv": []})
        bucket["resting_hr"].append(d["resting_hr"])
        bucket["sleep_hours"].append(d["sleep_hours"])
        bucket["hrv"].append(d["hrv"])

    return {
        "schema_version": 1,
        "generated_at": today.isoformat() + "T05:10:00+00:00",
        "source": "@pinta365/coros (no oficial)",
        "region": "eu",
        "date": day_list[0]["date"],
        "latest": day_list[0],
        "latest_14": {
            "resting_hr_avg": mean("resting_hr", 14),
            "resting_hr_min": min(d["resting_hr"] for d in day_list[:14]),
            "resting_hr_max": max(d["resting_hr"] for d in day_list[:14]),
            "sleep_hours_avg": mean("sleep_hours", 14),
            "steps_avg": mean("steps", 14),
            "hrv_avg": mean("hrv", 14),
            "n_days": 14,
        },
        "days": day_list,
        "weeks": [
            {"week_start": k, "resting_hr": round(sum(v["resting_hr"]) / len(v["resting_hr"])),
             "sleep_hours": round(sum(v["sleep_hours"]) / len(v["sleep_hours"]), 2),
             "hrv": round(sum(v["hrv"]) / len(v["hrv"])), "load_ratio": 0.7,
             "distance_km": 20.0, "n_days": len(v["resting_hr"])}
            for k, v in sorted(weeks.items())
        ],
        "available": {"resting_hr": True, "hrv": True, "sleep_hours": True,
                      "steps": True, "load_ratio": True},
        "warnings": [],
        "resting_hr": day_list[0]["resting_hr"],
        "sleep_hours": day_list[0]["sleep_hours"],
        "hrv": day_list[0]["hrv"],
    }


def new_html():
    return DASHBOARD.read_text(encoding="utf-8")


def run_all_sections(html, acts, coros, today=TODAY):
    """Pasa el HTML por todas las secciones, como hace main(), sin red."""
    notes = []
    html = ud.apply_header_footer(html, acts, coros, today, notes)
    html = ud.apply_coros(html, coros, today, notes)
    html = ud.apply_entreno(html, acts, coros, today, notes)
    html = ud.apply_dieta(html, acts, coros, today, notes)
    html = ud.apply_habitos(html, acts, coros, today, notes)
    html, mondays = ud.apply_historial(html, acts, coros, today, notes)
    html = ud.reconcile_weekly_lines(html, mondays, coros, ud.snapshot_weekly_manual(html), notes)
    html = ud.apply_recos(html, acts, coros, today, notes)
    # Tarjeta de la semana en curso (en main() se pinta al final, tras Historial)
    this_monday = stats.week_start(today)
    week = stats.weekly_sessions(acts, this_monday) if acts else {"runs": 0, "km": 0.0}
    coros_week = stats.coros_weekly_map(coros).get(this_monday.isoformat()) if coros else None
    if acts and ud.has_marker(html, "CURRENT_WEEK"):
        html = ud.replace_marked_html(
            html, "CURRENT_WEEK",
            ud.build_current_week_card(this_monday, week["km"], week["runs"],
                                       coros_week=coros_week, has_coros=coros is not None),
        )
    return html, notes


def marked(html, marker):
    return ud.get_marked(html, marker_html=True, marker=marker)


def marked_js(html, marker):
    return ud.get_marked(html, marker_html=False, marker=marker)


# ---------------------------------------------------------------------------
# 1. Marcadores
# ---------------------------------------------------------------------------

class TestMarkers(unittest.TestCase):
    """Todo marcador que el script puede escribir tiene que existir (una vez)."""

    # Marcadores de texto/HTML de esta versión
    HTML_MARKERS = [
        "UPDATED_DATE", "FOOTER_INFO", "HERO_RHR", "HERO_RATIO", "RHR_CARD", "RHR_CARD_SUB",
        "HRV_CARD", "HRV_CARD_SUB", "SLEEP_14D_AVG", "SLEEP_14D_SUB", "STEPS_CARD", "STEPS_CARD_SUB",
        "LOAD_CAPTION", "PLAN_STATUS", "ENTRENO_PB", "ENTRENO_LAST4W", "ENTRENO_VOL_NOTE",
        "ZONES_META", "ZONES_ROWS", "ENTRENO_STRENGTH_NOTE", "DIETA_ACTIVITY", "DIETA_CONTEXT",
        "HABIT_01_BODY", "HABIT_02_BODY", "HABIT_03_BODY", "HABIT_05_BODY", "HIST_COVERAGE_NOTE",
        "TREND_VOL_CARD", "TREND_RHR_CARD", "TREND_SLEEP_CARD", "TREND_VERDICT", "RACES",
        "MONTHLY_VOL_YEAR", "MONTHLY_RHR_YEAR", "MONTHLY_RANGE_TITLE", "MONTHLY_CARDS",
        "WEEKLY_CARDS", "CURRENT_WEEK", "RECOS",
    ]
    JS_MARKERS = [
        "RHR30_LABELS", "RHR30_DATA", "LOAD14_LABELS", "LOAD14_SHORT", "LOAD14_LONG",
        "CHARTVOL_LABELS", "CHARTVOL_DATA", "MONTHLY_YEAR", "MONTHLY_LABELS", "MONTHLY_VOL",
        "MONTHLY_RHR", "WEEKLY_MONDAYS", "WEEKLY_LABELS", "WEEKLY_VOL", "WEEKLY_SLEEP", "WEEKLY_RHR",
    ]

    def test_marcadores_de_texto_existen_y_no_se_repiten(self):
        html = new_html()
        for marker in self.HTML_MARKERS:
            with self.subTest(marker=marker):
                self.assertEqual(html.count(f"<!--AUTO:{marker}-->"), 1, f"apertura de {marker}")
                self.assertEqual(html.count(f"<!--/AUTO:{marker}-->"), 1, f"cierre de {marker}")

    def test_marcadores_de_js_existen_y_no_se_repiten(self):
        html = new_html()
        for marker in self.JS_MARKERS:
            with self.subTest(marker=marker):
                self.assertEqual(html.count(f"/*AUTO:{marker}*/"), 1, f"apertura de {marker}")
                self.assertEqual(html.count(f"/*/AUTO:{marker}*/"), 1, f"cierre de {marker}")


# ---------------------------------------------------------------------------
# 2. Con datos
# ---------------------------------------------------------------------------

class TestConDatos(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.acts = stats.normalize(build_activities())
        cls.coros = build_coros()
        cls.html, cls.notes = run_all_sections(new_html(), cls.acts, cls.coros)

    def test_entreno(self):
        # Zonas: con FC reposo 50 la tabla tiene que ser idéntica a la manual
        rows = marked(self.html, "ZONES_ROWS")
        self.assertIn("116–129", rows)
        self.assertIn("170–182", rows)
        self.assertIn("Zonas de FC (Karvonen: FC reposo 50 · FC máx 182)", marked(self.html, "ZONES_META"))
        # Mejor 10K = la carrera más rápida de las que cubren 10 km (49:12)
        self.assertIn("49:12", marked(self.html, "ENTRENO_PB"))
        self.assertIn("1:57:00", marked(self.html, "ENTRENO_PB"))
        # Gráfico de volumen: 8 barras, la última es la semana en curso y va con *
        data = marked_js(self.html, "CHARTVOL_DATA")
        self.assertEqual(len(ud.parse_js_array(data)), ud.CHARTVOL_WEEKS)
        self.assertTrue(marked_js(self.html, "CHARTVOL_LABELS").rstrip("]").endswith("*'"))
        # Estado del plan: 18 sep 2026 = semana 2 (arrancó el 7 sep)
        self.assertIn("semana 2 de 18", marked(self.html, "PLAN_STATUS"))

    def test_dieta(self):
        contexto = marked(self.html, "DIETA_CONTEXT")
        self.assertIn("Contexto real", contexto)
        self.assertIn("km", contexto)
        self.assertIn("ratio de carga", contexto)
        self.assertIn("pasos", marked(self.html, "DIETA_ACTIVITY"))

    def test_habitos(self):
        self.assertIn("20 jul", marked(self.html, "HABIT_01_BODY").replace("julio", "jul"))
        self.assertIn("noches más cortas", marked(self.html, "HABIT_02_BODY"))
        self.assertIn("techo de Z2", marked(self.html, "HABIT_03_BODY"))
        self.assertIn("semana", marked(self.html, "HABIT_05_BODY"))

    def test_historial(self):
        # Carreras del año: las 4 del fixture, con su tiempo
        races = marked(self.html, "RACES")
        self.assertEqual(races.count('class="race-item"'), 4)
        self.assertIn("Cursa El Corte Inglés", races)
        self.assertIn("1:57:00", races)
        # Barras mensuales: de enero al mes en curso
        self.assertEqual(len(ud.parse_js_array(marked_js(self.html, "MONTHLY_VOL"))), TODAY.month)
        self.assertEqual(marked(self.html, "MONTHLY_VOL_YEAR"), "2026")
        # 12 tarjetas semanales + la de la semana en curso aparte
        self.assertEqual(marked(self.html, "WEEKLY_CARDS").count('class="wcard'), ud.WEEKLY_CARDS)
        self.assertIn("en curso", marked(self.html, "CURRENT_WEEK"))
        # La rampa de volumen del fixture (10 → 31 km) tiene que salir arriba
        self.assertRegex(marked(self.html, "ENTRENO_VOL_NOTE"), r"31,\d km \(semana del")
        # Tendencia: las 3 tarjetas con datos
        self.assertIn("km/sem", marked(self.html, "TREND_VOL_CARD"))
        self.assertIn("bpm", marked(self.html, "TREND_RHR_CARD"))
        self.assertIn("h", marked(self.html, "TREND_SLEEP_CARD"))
        self.assertIn("En conjunto", marked(self.html, "TREND_VERDICT"))

    def test_recomendaciones(self):
        recos = marked(self.html, "RECOS")
        self.assertIn("Salto fuerte de volumen reciente", recos)
        self.assertIn("Fuerza parada", recos)
        self.assertGreaterEqual(recos.count('class="rec'), 5)

    def test_sueño_y_pasos_de_coros(self):
        self.assertIn("~", marked(self.html, "STEPS_CARD"))
        self.assertIn("Rango", marked(self.html, "STEPS_CARD_SUB"))
        self.assertNotEqual(marked(self.html, "SLEEP_14D_AVG"), "7h 03m")

    def test_ninguna_nota_de_error(self):
        errores = [n for n in self.notes if n.startswith("⚠")]
        self.assertEqual([], errores)

    def test_idempotente(self):
        """Reescribir un HTML ya generado no cambia nada."""
        segundo, _ = run_all_sections(self.html, self.acts, self.coros)
        self.assertEqual(self.html, segundo)

    def test_marcadores_equilibrados(self):
        """Los marcadores siguen ahí a propósito (son el contrato), pero cerrados."""
        self.assertEqual(self.html.count("<!--AUTO:"), self.html.count("<!--/AUTO:"))
        self.assertEqual(self.html.count("/*AUTO:"), self.html.count("/*/AUTO:"))


# ---------------------------------------------------------------------------
# 3. Sin datos: nunca inventar
# ---------------------------------------------------------------------------

class TestSinDatos(unittest.TestCase):
    def test_sin_strava_ni_coros_solo_cambia_fecha_y_pie(self):
        html, notes = run_all_sections(new_html(), [], None)
        original = new_html()
        # Normalizamos los dos bloques que sí se actualizan siempre
        for marker in ("UPDATED_DATE", "FOOTER_INFO"):
            original = ud.replace_marked_html(original, marker, "X")
            html = ud.replace_marked_html(html, marker, "X")
        self.assertEqual(original, html)
        self.assertTrue(any("sin coros_data.json" in n for n in notes))

    def test_sin_coros_se_conservan_los_valores_manuales(self):
        acts = stats.normalize(build_activities())
        html, _ = run_all_sections(new_html(), acts, None)
        # Sin Coros, las tarjetas del Resumen se quedan como estaban…
        self.assertEqual(marked(html, "RHR_CARD"), "47 bpm")
        self.assertEqual(marked(html, "HERO_RHR"), "47")
        self.assertEqual(marked(html, "STEPS_CARD"), "~13.790")
        # …y las semanas de las que no hay dato conservan el valor manual leído
        # del HTML (no un 0 ni un guion inventado)
        self.assertIn("49", marked_js(html, "WEEKLY_RHR"))
        # La tendencia de FC reposo / sueño no se toca
        self.assertIn("52", marked(html, "TREND_RHR_CARD"))

    def test_meses_sin_datos_conservan_la_tarjeta_manual(self):
        """Marzo y abril no están en el fixture: se conserva la tarjeta manual."""
        acts = stats.normalize(build_activities())
        html, _ = run_all_sections(new_html(), acts, build_coros())
        meses = marked(html, "MONTHLY_CARDS")
        self.assertIn("Marzo", meses)          # del HTML manual, sin datos en Strava
        self.assertIn("recuperación post-maratón", meses)
        self.assertIn("Agosto", meses)         # regenerada con datos reales
        self.assertNotIn("enero a mayo", marked(html, "MONTHLY_RANGE_TITLE"))

    def test_coros_con_valores_basura_no_revienta(self):
        """Un coros_data.json editado a mano no debe tumbar la actualización."""
        coros = build_coros()
        coros["days"] = [{"date": "no-fecha", "resting_hr": "x", "sleep_hours": "y", "steps": "z"}] + coros["days"]
        coros["latest"] = {"load_ratio": None, "hrv_base": "muchas"}
        coros["latest_14"] = {"resting_hr_avg": "?", "steps_avg": "muchos", "sleep_hours_avg": "7"}
        coros["weeks"] = [{"week_start": "2026-09-07", "resting_hr": "x", "sleep_hours": "y"}]
        html, notes = run_all_sections(new_html(), stats.normalize(build_activities()), coros)
        self.assertEqual([n for n in notes if n.startswith("⚠")], [])
        # Y lo que no se puede leer, no se escribe
        self.assertEqual(html.count("<!--AUTO:"), html.count("<!--/AUTO:"))

    def test_sin_actividades_no_se_tocan_los_graficos_de_strava(self):
        coros = build_coros()
        html, _ = run_all_sections(new_html(), [], coros)
        self.assertEqual(
            ud.parse_js_array(marked_js(html, "CHARTVOL_DATA")),
            [18.8, 13.3, 0, 8.0, 27.3, 38.1, 38.1, 8.3],
        )
        self.assertEqual(ud.parse_js_array(marked_js(html, "MONTHLY_VOL")),
                         [93.9, 111.0, 44.1, 29.7, 92.4, 61.6, 67.3, 111.4, 39.2])


# ---------------------------------------------------------------------------
# 4. Piezas sueltas
# ---------------------------------------------------------------------------

class TestHelpers(unittest.TestCase):
    def test_zonas_karvonen_igual_que_la_tabla_manual(self):
        self.assertEqual(
            stats.karvonen(50, 182),
            {"Z1": (116, 129), "Z2": (130, 142), "Z3": (143, 156), "Z4": (157, 169), "Z5": (170, 182)},
        )

    def test_lee_las_tarjetas_manuales_del_html(self):
        cards = ud.snapshot_weekly_cards(new_html(), TODAY)
        # "31 ago–6 sep" del HTML manual → lunes 31 ago 2026, con sueño y pulso
        self.assertEqual(cards.get("2026-08-31"), {"sleep_hours": 7 + 34 / 60, "resting_hr": 49})
        # El HTML manual trae 13 semanas (8 jun → 31 ago); la semana en curso va aparte
        self.assertEqual(len(cards), 13)
        self.assertEqual(cards.get("2026-06-08"), {"sleep_hours": 7 + 9 / 60, "resting_hr": 49})
        self.assertNotIn("2026-09-07", cards)

    def test_best_effort_se_queda_con_la_mejor(self):
        acts = stats.normalize([
            raw_activity(date(2026, 5, 10), 10.0, 49.5, workout_type=1),
            raw_activity(date(2026, 6, 1), 10.4, 55.0),   # más lenta
            raw_activity(date(2026, 7, 1), 4.0, 18.0),    # no llega a 10K
        ])
        best = stats.best_effort(acts, 10.0)
        self.assertAlmostEqual(best["time_s"], 49.5 * 60, delta=1)

    def test_rampa_de_volumen(self):
        acts = stats.normalize(build_activities())
        ramp = stats.volume_ramp(acts, TODAY)
        self.assertAlmostEqual(ramp["peak_km"], 31.0, delta=0.3)
        self.assertAlmostEqual(ramp["trough_km"], 10.0, delta=0.3)
        self.assertEqual(ramp["span_weeks"], 3)
        self.assertGreater(ramp["pct"], 1.0)

    def test_trend_4_mas_4(self):
        acts = stats.normalize(build_activities())
        t = stats.trend(acts, build_coros(), TODAY, weeks=4)
        self.assertAlmostEqual(t["vol"]["now"], sum([28, 24, 22, 20]) / 4, delta=0.6)
        self.assertAlmostEqual(t["vol"]["prev"], sum([10, 12, 26, 31]) / 4, delta=0.6)
        self.assertIsNotNone(t["rhr"]["now"])
        self.assertEqual(t["now"]["end"], stats.week_start(TODAY) - timedelta(days=1))

    def test_no_hay_solapes_entre_zonas(self):
        zones = stats.karvonen(47, 182)
        self.assertEqual(zones["Z2"][0], zones["Z1"][1] + 1)
        self.assertEqual(zones["Z5"][1], 182)


# ---------------------------------------------------------------------------
# 5. Constantes compartidas y flujo completo (main)
# ---------------------------------------------------------------------------

class TestConstantesCompartidas(unittest.TestCase):
    """El plan y las zonas tienen que decir lo mismo en el correo y el dashboard."""

    def _const(self, module_path, name):
        text = (ROOT / module_path).read_text(encoding="utf-8")
        m = re.search(rf"{name}\s*=\s*(.+)", text)
        self.assertIsNotNone(m, f"{name} no encontrado en {module_path}")
        return m.group(1).strip().split("#")[0].strip()

    def test_plan_y_zonas(self):
        for name in ("PLAN_START", "HR_REST", "HR_MAX"):
            with self.subTest(name=name):
                self.assertEqual(self._const("scripts/daily_brief.py", name),
                                 self._const("scripts/update_dashboard.py", name))


class TestMainEndToEnd(unittest.TestCase):
    """main() completo con fixtures locales: escribe el HTML y es reproducible."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)

        self.html_path = tmp / "dashboard.html"
        self.html_path.write_text(new_html(), encoding="utf-8")

        fixture = tmp / "strava.json"
        fixture.write_text(json.dumps(build_activities()), encoding="utf-8")

        coros_path = tmp / "coros_data.json"
        coros_path.write_text(json.dumps(build_coros()), encoding="utf-8")

        self._env = {
            "STRAVA_FIXTURE": str(fixture),
            "DASHBOARD_PATH": str(self.html_path),
            "COROS_DATA_PATH": str(coros_path),
        }
        self._old_env = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)
        self._old_path = ud.DASHBOARD_PATH
        self._old_coros_path = coros_data.COROS_DATA_PATH
        ud.DASHBOARD_PATH = self.html_path
        coros_data.COROS_DATA_PATH = coros_path
        self._argv = sys.argv

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        ud.DASHBOARD_PATH = self._old_path
        coros_data.COROS_DATA_PATH = self._old_coros_path
        sys.argv = self._argv

    def _run_main(self):
        sys.argv = ["update_dashboard.py"]
        rc = ud.main()
        self.assertEqual(rc, 0)
        return self.html_path.read_text(encoding="utf-8")

    def test_flujo_completo(self):
        out = self._run_main()
        self.assertNotEqual(out, new_html())
        self.assertIn("49:12", marked(out, "ENTRENO_PB"))
        self.assertEqual(out.count("<!--AUTO:"), out.count("<!--/AUTO:"))

        # Segunda pasada: mismo resultado (no se desplaza ni se degrada nada)
        out2 = self._run_main()
        self.assertEqual(out, out2)

    def test_dry_run_no_escribe(self):
        antes = self.html_path.read_text(encoding="utf-8")
        sys.argv = ["update_dashboard.py", "--dry-run"]
        self.assertEqual(ud.main(), 0)
        self.assertEqual(self.html_path.read_text(encoding="utf-8"), antes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
