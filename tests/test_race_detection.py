"""Detección por nombre limitada al historial; no modifica Strava ni PB."""
import unittest
from datetime import date
from scripts import dashboard_stats as stats
from scripts import update_dashboard as ud
from tests.test_dashboard import raw_activity, new_html, marked


class TestRacesByName(unittest.TestCase):
    def test_cursa_y_tast_coexisten_con_carreras_marcadas(self):
        today = date(2026, 11, 4)
        raw = [
            raw_activity(date(2026, 10, 31), 10.1, 48, name='El Tast de la Mitja'),
            raw_activity(date(2026, 10, 30), 10, 50, name='CURSA Bombers'),
            raw_activity(date(2026, 10, 29), 10, 50, name='Cursa Diagonal', workout_type=1),
            raw_activity(date(2026, 10, 28), 10, 50, name='cursa popular'),
            raw_activity(date(2026, 10, 27), 10, 50, name='Rodaje tranquilo'),
            raw_activity(date(2026, 10, 26), 10, 50, type_='Ride', name='Cursa bici'),
            raw_activity(date(2025, 12, 31), 10, 50, name='Cursa antigua'),
            raw_activity(date(2026, 11, 5), 10, 50, name='Cursa futura'),
            raw_activity(date(2026, 10, 25), 0, 50, name='Cursa sin distancia'),
        ]
        acts = stats.normalize(raw)
        html, _ = ud.apply_historial(new_html(), acts, None, today, [])
        races = marked(html, 'RACES')
        self.assertIn('El Tast de la Mitja (identificada por nombre)', races)
        self.assertIn('CURSA Bombers (identificada por nombre)', races)
        self.assertIn('cursa popular (identificada por nombre)', races)
        self.assertEqual(races.count('Cursa Diagonal'), 1)
        self.assertNotIn('Cursa Diagonal (identificada por nombre)', races)
        for name in ('Rodaje tranquilo', 'Cursa bici', 'Cursa antigua', 'Cursa futura', 'Cursa sin distancia'):
            self.assertNotIn(name, races)
        self.assertEqual([r['name'] for r in stats.races(acts, year=2026)], ['Cursa Diagonal'])
        self.assertLess(races.index('El Tast'), races.index('CURSA Bombers'))

    def test_actividad_con_nombre_peligroso_se_escapa(self):
        raw = [raw_activity(date(2026, 11, 1), 10, 50, name='Cursa <script>alert(1)</script>')]
        html, _ = ud.apply_historial(new_html(), stats.normalize(raw), None, date(2026, 11, 4), [])
        races = marked(html, 'RACES')
        self.assertIn('Cursa &lt;script&gt;', races)
        self.assertNotIn('<script>', races)


if __name__ == '__main__':
    unittest.main()
