from django.core.management.base import BaseCommand

from planner.services.demo import ensure_demo_user


class Command(BaseCommand):
    help = "Create the demo planner login and load Kaveri General Stores."

    def handle(self, *args, **options):
        user = ensure_demo_user()
        run = user.company.forecast_runs.first()
        self.stdout.write(self.style.SUCCESS("Demo ready."))
        self.stdout.write("Login: planner")
        self.stdout.write("Password: CalendraDemo2026")
        if run:
            self.stdout.write(
                f"Event-day MAPE  plain {run.plain_event_mape:.1f}%  calendar {run.calendar_event_mape:.1f}%"
            )
