"""Fechas de las carreras y recuperación en el correo diario."""
import unittest
from datetime import date
from scripts import daily_brief as db


class RacePlanTests(unittest.TestCase):
    def test_race_days_and_diet(self):
        for day, name in [(date(2026, 11, 8), "Bombers"),
                          (date(2026, 11, 29), "Jean Bouin"),
                          (date(2027, 1, 24), "Granollers")]:
            with self.subTest(day=day):
                plan = db.get_today_plan(day)
                self.assertIn(name, plan['session_desc'])
                self.assertEqual(plan['session_type'], 'larga')
                html = db.build_email_html(day, plan, {'yesterday': '—', 'week_km': 0}, None)
                self.assertIn(name, html)
                self.assertIn(db.DIET['larga']['kcal'], html)

    def test_extended_plan(self):
        self.assertEqual(len(db.WEEKS), 20)
        self.assertEqual(db.get_today_plan(date(2027, 1, 10))["week_num"], 18)
        self.assertEqual(db.get_today_plan(date(2027, 1, 17))["week_num"], 19)
        self.assertEqual(db.get_today_plan(date(2027, 1, 24))["week_num"], 20)
        self.assertNotIn("Granollers", db.get_today_plan(date(2027, 1, 10))["session_desc"])
        self.assertIn("Plan completado", db.get_today_plan(date(2027, 1, 25))["session_desc"])

    def test_recovery_and_original_plan(self):
        for day in (date(2026, 11, 11), date(2026, 12, 2)):
            self.assertEqual(db.get_today_plan(day)['session_type'], 'descanso')
        self.assertEqual(db.get_today_plan(date(2026, 12, 13))['session_desc'], '11 km')
        self.assertEqual(db.get_today_plan(date(2026, 11, 5))['session_type'], 'facil')


if __name__ == '__main__':
    unittest.main()
