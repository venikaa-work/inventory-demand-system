from django.contrib.auth.models import User
from django.test import TestCase

from planner.models import Company
from planner.services.demo import load_demo


class DemoForecastTests(TestCase):
    def test_hidden_month_picks_the_closest_model(self):
        user = User.objects.create_user(username="viva", password="test-pass-123")
        company = Company.objects.create(owner=user, name="Test Stores", city="Pune")
        run = load_demo(company)
        self.assertEqual(len(run.model_scores), 4)
        closest = min(run.model_scores, key=lambda row: row["mae"])
        self.assertEqual(run.chosen_model, closest["name"])
        scores = {row["name"]: row["mae"] for row in run.model_scores}
        self.assertLess(scores[run.chosen_model], scores["Weekday average"])
        self.assertGreaterEqual(run.recommendations.filter(urgent=True).count(), 1)
