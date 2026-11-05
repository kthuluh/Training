"""Menús y evaluación de ritmos con datos simulados, nunca con credenciales."""
import copy
import unittest
from datetime import date, timedelta
from unittest import mock
from scripts import meal_plan
from scripts import daily_brief as db
from scripts import dashboard_stats as stats
from scripts import update_dashboard as ud
from tests.test_dashboard import new_html, raw_activity, marked


class DailyMenus(unittest.TestCase):
    def test_daily_menus_within_approximate_existing_ranges(self):
        bounds = {'descanso': (2350, 2500, 220, 365), 'facil': (2600, 2800, 220, 365),
                  'calidad': (2900, 3200, 440, 585), 'larga': (2900, 3200, 440, 585)}
        for i in range(14):
            today = date(2026, 11, 2) + timedelta(days=i)
            for kind, (cmin, cmax, carbmin, carbmax) in bounds.items():
                menus = meal_plan.menus_for(today, kind)
                self.assertEqual(len(menus), 2)
                for menu in menus:
                    with self.subTest(day=today, kind=kind, kcal=menu['kcal']):
                        self.assertEqual(len(menu['meals']), 5)
                        self.assertTrue(cmin <= menu['kcal'] <= cmax)
                        self.assertTrue(carbmin <= menu['carbs'] <= carbmax)
                        self.assertTrue(117 <= menu['protein'] <= 131)
                        self.assertTrue(75 <= menu['fat'] <= 95)

    def test_varies_by_day_without_mutating_templates(self):
        base = copy.deepcopy(meal_plan.BASE)
        first = meal_plan.menus_for(date(2026, 11, 2), 'facil')
        later = meal_plan.menus_for(date(2026, 11, 4), 'facil')
        self.assertNotEqual(first, later)
        self.assertEqual(meal_plan.BASE, base)

    def test_dashboard_two_menus_and_no_apple_recommendation(self):
        today = date(2026, 11, 4)
        html = ud.apply_dieta(new_html(), [], None, today, [])
        menu = marked(html, 'DIETA_MENU')
        self.assertIn(today.isoformat(), menu)
        self.assertIn('Opción 1', menu)
        self.assertIn('Opción 2', menu)
        self.assertIn('alergias', menu)
        self.assertNotIn('Apple Salud', str(ud.build_recos([], {}, today)))
        self.assertNotIn('Apple Salud', marked(new_html(), 'RECOS'))


class PaceAnalysis(unittest.TestCase):
    def test_rodaje_pace_and_hr(self):
        yesterday = date(2026, 9, 7)
        acts = stats.normalize([raw_activity(yesterday, 8, 48, hr=138)])
        result = db.assess_adherence(yesterday + timedelta(days=1), acts)
        self.assertIn('6:00/km', result['analysis'])
        self.assertIn('dentro del rango', result['analysis'])
        self.assertIn('138 ppm', result['analysis'])
        self.assertIn('no indica el tiempo real', result['analysis'])

    def test_intervals_do_not_use_average_for_repetitions(self):
        previous = {'session_type': 'calidad', 'session_desc': '6x800m @4:20-4:25/km'}
        running = [{'name': 'Series', 'km': 8, 'moving_time': 2800, 'laps': None}]
        self.assertIn('Sin vueltas', db.analyse_training(previous, running))
        laps = [{'distance': 800, 'moving_time': 209}] * 5 + [{'distance': 800, 'moving_time': 230}]
        running[0]['laps'] = laps
        result = db.analyse_training(previous, running)
        self.assertIn('6 de 6', result)
        self.assertIn('5 de 6', result)
        self.assertIn('no corresponder', result)

    def test_larga_partial_segments_not_claimed_consecutive(self):
        previous = {'session_type': 'larga', 'session_desc': '17-19 km, con 10-12 km a 4:44-4:48/km'}
        running = [{'name': 'Larga', 'km': 17, 'moving_time': 5200,
                    'laps': [{'distance': 1000, 'moving_time': 285}] * 3}]
        result = db.analyse_training(previous, running)
        self.assertIn('3.0 km', result)
        self.assertIn('No se comprueba si son consecutivas', result)

    def test_laps_fetch_failure_does_not_block_yesterday(self):
        today = date(2026, 9, 10)  # ayer calidad
        raw = [raw_activity(today - timedelta(days=1), 8, 48)]
        raw[0]['id'] = 123
        with mock.patch.object(db.requests, 'get', side_effect=db.requests.ConnectionError):
            db.enrich_yesterday_laps(raw, 'secret', today)
        self.assertNotIn('laps', raw[0])
        result = db.assess_adherence(today, stats.normalize(raw))
        self.assertIn('Sin vueltas', result['analysis'])

    def test_mail_and_dashboard_same_analysis(self):
        today = date(2026, 9, 8)
        acts = stats.normalize([raw_activity(today - timedelta(days=1), 8, 48, hr=135)])
        result = db.assess_adherence(today, acts)
        html = ud.apply_adherence(new_html(), acts, today, [])
        email = db.build_email_html(today, db.get_today_plan(today),
                                    {'yesterday': 'entreno', 'week_km': 8, 'activities': acts}, None)
        self.assertIn(result['analysis'], marked(html, 'ADHERENCE'))
        self.assertIn(result['analysis'], email)


class StravaLapsIntegration(unittest.TestCase):
    def test_fetch_laps_for_yesterday_only_and_normalize(self):
        today = date(2026, 9, 10)
        old = raw_activity(today - timedelta(days=2), 5, 30)
        old['id'] = 123
        quality = raw_activity(today - timedelta(days=1), 8, 42)
        quality['id'] = 456
        values = [old, quality]
        response = mock.Mock()
        response.json.return_value = [{'distance': 800, 'moving_time': 210}] * 6
        with mock.patch.object(db.requests, 'get', return_value=response) as get:
            db.enrich_yesterday_laps(values, 'token', today)
        get.assert_called_once()
        self.assertIn('/456/laps', get.call_args.args[0])
        self.assertNotIn('laps', old)
        acts = stats.normalize(values)
        result = db.assess_adherence(today, acts)
        self.assertIn('6 de 6', result['analysis'])
        self.assertIn('6 de 6 vueltas compatibles', result['analysis'])

    def test_missing_hr_and_laps_are_not_interpreted_as_success(self):
        previous = {'session_type': 'facil', 'session_desc': '8 km Z2'}
        result = db.analyse_training(previous, [{'name': 'Run', 'km': 8, 'moving_time': 2880, 'avg_hr': None}])
        self.assertIn('Sin FC registrada', result)
        previous = {'session_type': 'calidad', 'session_desc': '6x800m @4:20-4:25/km'}
        result = db.analyse_training(previous, [{'name': 'Run', 'km': 8, 'moving_time': 2880, 'laps': []}])
        self.assertIn('Sin vueltas', result)
        self.assertNotIn('cumplida', result)

    def test_daily_menus_refresh_for_each_date(self):
        d1, d2 = date(2026, 11, 4), date(2026, 11, 5)
        html = ud.apply_dieta(new_html(), [], None, d1, [])
        self.assertIn(d1.isoformat(), marked(html, 'DIETA_MENU'))
        html = ud.apply_dieta(html, [], None, d2, [])
        self.assertIn(d2.isoformat(), marked(html, 'DIETA_MENU'))
        self.assertNotIn(d1.isoformat(), marked(html, 'DIETA_MENU'))


if __name__ == '__main__':
    unittest.main()
