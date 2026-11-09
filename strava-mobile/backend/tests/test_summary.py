import unittest
from datetime import date, timedelta
from unittest.mock import patch
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class SummaryTest(unittest.TestCase):
    def test_yesterday_and_races(self):
        today = date(2026, 11, 5)
        acts = [dict(start_date_local='2026-11-04T09:00:00', sport_type='Run', name='Cursa', distance=10000, moving_time=3000),
                dict(start_date_local='2026-11-04T18:00:00', sport_type='WeightTraining', name='Fuerza', distance=0, moving_time=1800),
                dict(start_date_local='2026-11-03T09:00:00', sport_type='Run', name='Race', workout_type=1, distance=5000, moving_time=1500)]
        result = main.summarize(acts, today)
        self.assertEqual(result['week_km'], 15)
        self.assertEqual(len(result['yesterday']), 2)
        self.assertEqual(result['yesterday'][0]['pace_sec_km'], 300)
        self.assertTrue(result['races'][0]['inferred'])
        self.assertFalse(result['races'][1]['inferred'])

    def test_no_activities_is_not_proof_of_rest(self):
        result = main.summarize([], date(2026, 11, 5))
        self.assertEqual(result['yesterday'], [])
        self.assertIn('no demuestra descanso', result['note'])

    def test_approval_is_enforced(self):
        with patch.object(main, 'profile_for', return_value={'approved': False}):
            with self.assertRaises(main.HTTPException) as e:
                main.approved('id')
            self.assertEqual(e.exception.status_code, 403)

if __name__ == '__main__':
    unittest.main()
