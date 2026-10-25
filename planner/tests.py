from django.contrib.auth.models import User
from django.test import TestCase

from planner.models import Company
from planner.services.demo import load_demo


class DemoForecastTests(TestCase):
    def test_calendar_model_beats_plain_model_on_event_days(self):
        user = User.objects.create_user(username="viva", password="test-pass-123")
        company = Company.objects.create(owner=user, name="Test Stores", city="Pune")
        run = load_demo(company)
        self.assertIsNotNone(run.calendar_event_mape)
        self.assertLess(run.calendar_event_mape, run.plain_event_mape)
        self.assertGreaterEqual(run.recommendations.filter(urgent=True).count(), 1)
