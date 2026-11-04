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
        self.assertIn('no el ritmo', out['yesterday'])

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
