"""El seguimiento nunca inventa sesiones ni cambia el plan."""
import unittest
from datetime import date, timedelta
from unittest import mock
from scripts import daily_brief as db
from scripts import dashboard_stats as stats
from scripts import update_dashboard as ud
from tests.test_dashboard import raw_activity, new_html, marked


class AdherenceTests(unittest.TestCase):
    def test_opcional_y_reposo_no_se_marcan_como_incumplimiento(self):
        d = date(2026, 11, 4)  # martes: descanso o fuerza opcional
        out = db.assess_adherence(d, [])
        self.assertIn('opcional', out['yesterday'])
        self.assertIn('opcional', out['advice'])
        self.assertEqual(out['today'], db.get_today_plan(d)['session_desc'])

    def test_sin_conexion_no_afirma_incumplimiento(self):
        out = db.assess_adherence(date(2026, 11, 5), None)
        self.assertIn('no se puede determinar', out['yesterday'])
        self.assertNotIn('no aparece la carrera', out['yesterday'])

    def test_run_ausente_y_registrado_solo_valida_tipo(self):
        d = date(2026, 11, 5)
        self.assertIn('no aparece', db.assess_adherence(d, [])['yesterday'])
        acts = stats.normalize([raw_activity(d - timedelta(days=1), 3, 20)])
        out = db.assess_adherence(d, acts)
        self.assertIn('no confirma por sí sola', out['yesterday'])

    def test_rodaje_o_descanso_no_se_marca_como_faltado(self):
        out = db.assess_adherence(date(2026, 11, 6), [])
        self.assertIn("rodaje opcional", out["yesterday"])
        self.assertNotIn("sesión perdida", out["advice"])

    def test_coros_desactualizado_no_produce_consejo_de_carga(self):
        old = {"date": "2026-08-01", "latest": {"load_ratio": 2.0}, "latest_14": {"resting_hr_avg": 55}}
        titles = [x[1] for x in ud.build_recos([], old, date(2026, 11, 4))]
        self.assertIn("Datos de Coros no actualizados", titles)
        self.assertNotIn("Ratio de carga alto (por encima de 1.3)", titles)

    def test_carrera_protegida_y_salida_larga(self):
        d = date(2026, 11, 8)
        self.assertIn('Día de carrera', db.assess_adherence(d, [])['advice'])
        d = date(2026, 11, 2)
        acts = stats.normalize([raw_activity(d - timedelta(days=1), 15, 80)])
        self.assertIn('rodaje Z2 suave', db.assess_adherence(d, acts)['advice'])

    def test_dashboard_correo_usan_misma_recomendacion_y_escapan_html(self):
        d = date(2026, 11, 4)
        result = db.assess_adherence(d, [])
        html = ud.apply_adherence(new_html(), [], d, [])
        self.assertIn(result['advice'], marked(html, 'ADHERENCE'))
        email = db.build_email_html(d, db.get_today_plan(d),
                                    {'yesterday': '<script>x</script>', 'week_km': 0, 'activities': []}, None)
        self.assertIn(result['advice'], email)
        self.assertNotIn('<script>x</script>', email)
        self.assertIn('&lt;script&gt;', email)

    def test_periodo_sin_plan(self):
        self.assertIn('no había', db.assess_adherence(date(2026, 8, 1), [])['yesterday'])
        self.assertIn('no había', db.assess_adherence(date(2027, 1, 26), [])['yesterday'])

    def test_strava_tast_no_etiquetado_en_historial(self):
        d = date(2026, 11, 4)
        raw = [raw_activity(date(2026, 11, 1), 10.1, 48, name='El Tast de la Mitja')]
        acts = stats.normalize(raw)
        html, _ = ud.apply_historial(new_html(), acts, None, d, [])
        self.assertIn('El Tast de la Mitja', marked(html, 'RACES'))
        self.assertIn('identificada por nombre', marked(html, 'RACES'))
        self.assertEqual(stats.races(acts, year=2026), [])  # no altera PB

    def test_rampa_antigua_no_se_califica_de_reciente(self):
        d = date(2026, 11, 4)
        raw = [raw_activity(date(2026, 8, 3), 5, 30), raw_activity(date(2026, 8, 10), 40, 200),
               raw_activity(date(2026, 10, 12), 20, 110), raw_activity(date(2026, 10, 19), 20, 110),
               raw_activity(date(2026, 10, 26), 20, 110)]
        items = ud.build_recos(stats.normalize(raw), None, d)
        self.assertNotIn('Salto fuerte de volumen reciente', [item[1] for item in items])

    def test_resumen_strava_error_y_limites_horarios(self):
        d = date(2026, 11, 4)
        with mock.patch.object(db, 'strava_access_token', side_effect=KeyError('secret')):
            summary = db.get_strava_summary(d)
        self.assertIsNone(summary['activities'])
        with mock.patch.object(db, 'strava_access_token', return_value='token'), \
             mock.patch.object(db, 'strava_activities', return_value=[]) as fetch:
            db.get_strava_summary(d)
        self.assertEqual(fetch.call_args.args[2] - fetch.call_args.args[1], 7 * 86400)


if __name__ == '__main__':
    unittest.main()

class FullDayAdherenceTests(unittest.TestCase):
    def test_sin_entreno_conforme_a_descanso_opcional(self):
        result = db.assess_adherence(date(2026, 11, 4), [])
        self.assertIn('coincide', result['yesterday'])
        self.assertIn('fuerza era opcional', result['yesterday'])
        self.assertIn('No hay actividades', result['recorded'])

    def test_fuerza_y_carrera_exigente_en_dia_flexible(self):
        d = date(2026, 11, 4)
        acts = stats.normalize([
            raw_activity(d - timedelta(days=1), 0, 25, type_='WeightTraining', name='Fuerza'),
            raw_activity(d - timedelta(days=1), 12, 65, name='Carrera larga', hr=160),
        ])
        result = db.assess_adherence(d, acts)
        self.assertIn('se aparta del plan', result['yesterday'])
        self.assertIn('esfuerzo elevado', result['yesterday'])
        self.assertIn('Fuerza', result['recorded'])
        self.assertIn('Carrera larga', result['recorded'])
        self.assertIn('descanso o rodaje Z2 suave', result['advice'])
        self.assertNotEqual(result['advice'], 'Mantén la sesión prevista si te encuentras bien; ante dolor o fatiga, prioriza descanso.')

    def test_fuerza_en_dia_flexible_esta_prevista(self):
        d = date(2026, 11, 4)
        acts = stats.normalize([raw_activity(d - timedelta(days=1), 0, 30, type_='WeightTraining', name='Fuerza')])
        result = db.assess_adherence(d, acts)
        self.assertIn('conforme', result['yesterday'])
        self.assertIn('Fuerza', result['recorded'])
        self.assertNotIn('se aparta', result['yesterday'])

    def test_ciclismo_no_se_omite_y_carga_elevada_sin_carrera(self):
        d = date(2026, 11, 4)
        acts = stats.normalize([raw_activity(d - timedelta(days=1), 45, 70, type_='Ride', name='Bicicleta', hr=165)])
        result = db.assess_adherence(d, acts)
        self.assertIn('Bicicleta', result['recorded'])
        self.assertIn('otras actividades', result['yesterday'])
        self.assertIn('indicadores de carga', result['advice'])

    def test_actividad_adicional_en_dia_de_carrera_prevista(self):
        d = date(2026, 11, 5)
        acts = stats.normalize([raw_activity(d - timedelta(days=1), 5, 30),
                                raw_activity(d - timedelta(days=1), 0, 20, type_='Yoga', name='Yoga')])
        result = db.assess_adherence(d, acts)
        self.assertIn('no confirma por sí sola', result['yesterday'])
        self.assertIn('Yoga', result['recorded'])

    def test_alta_intensidad_no_invalida_la_carrera_de_hoy(self):
        d = date(2026, 11, 8)
        acts = stats.normalize([raw_activity(d - timedelta(days=1), 15, 70, hr=168)])
        advice = db.assess_adherence(d, acts)['advice']
        self.assertIn('Día de carrera', advice)
        self.assertIn('valora no competir', advice)

    def test_dashboard_y_correo_muestran_registros_escapados(self):
        d = date(2026, 11, 4)
        acts = stats.normalize([raw_activity(d - timedelta(days=1), 12, 60, name='<script>')])
        html = ud.apply_adherence(new_html(), acts, d, [])
        email = db.build_email_html(d, db.get_today_plan(d),
            {'yesterday': 'actividad', 'week_km': 12, 'activities': acts}, None)
        for value in (marked(html, 'ADHERENCE'), email):
            self.assertIn('&lt;script&gt;', value)
            self.assertIn('se aparta del plan', value)
            self.assertNotIn('<script>', value)

    def test_datos_no_disponibles_no_equivalen_a_descanso(self):
        result = db.assess_adherence(date(2026, 11, 4), None)
        self.assertIn('no se puede determinar', result['yesterday'])
        self.assertIn('no está disponible', result['recorded'])
        self.assertNotIn('coincide', result['yesterday'])

    def test_sesion_facil_compara_distancia_y_pulso_sin_dar_cumplida_intensidad(self):
        today = date(2026, 11, 3)
        acts = stats.normalize([raw_activity(today - timedelta(days=1), 12, 65, hr=165)])
        result = db.assess_adherence(today, acts)
        self.assertIn('Distancia registrada: 12.0 km', result['yesterday'])
        self.assertIn('difiere de la referencia', result['yesterday'])
        self.assertIn('supera el techo de Z2', result['yesterday'])
        self.assertIn('no confirma por sí sola', result['yesterday'])

    def test_actividad_fuera_del_plan_sigue_visible(self):
        today = date(2027, 1, 26)
        acts = stats.normalize([raw_activity(today - timedelta(days=1), 5, 32, name='Correr')])
        result = db.assess_adherence(today, acts)
        self.assertIn('no había', result['yesterday'])
        self.assertIn('Correr', result['recorded'])
