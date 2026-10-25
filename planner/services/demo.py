from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from planner.models import CalendarEvent, Company, Product, Sale
from planner.services.forecast import day_features, rebuild_forecast

DOW = (1.04, 0.98, 1.0, 1.06, 1.14, 1.2, 0.58)

PRODUCTS = [
    {"sku": "HAMP-01", "name": "Diwali gift hamper", "category": "Festive", "unit_cost": "499.00", "lead_time_days": 12, "on_hand": 36, "base": 11, "major": 3.3, "promo": 1.7, "payday": 1.05, "mild": 1.08, "post": 0.48, "jitter": 3},
    {"sku": "LIGHT-04", "name": "LED string lights", "category": "Festive", "unit_cost": "249.00", "lead_time_days": 14, "on_hand": 28, "base": 9, "major": 3.1, "promo": 1.65, "payday": 1.04, "mild": 1.05, "post": 0.5, "jitter": 5},
    {"sku": "NUT-12", "name": "Dry fruit mix 500g", "category": "Festive", "unit_cost": "380.00", "lead_time_days": 10, "on_hand": 42, "base": 8, "major": 2.6, "promo": 1.45, "payday": 1.08, "mild": 1.1, "post": 0.62, "jitter": 7},
    {"sku": "RICE-5", "name": "Basmati rice 5kg", "category": "Staple", "unit_cost": "420.00", "lead_time_days": 7, "on_hand": 260, "base": 16, "major": 1.35, "promo": 1.12, "payday": 1.22, "mild": 1.08, "post": 0.9, "jitter": 2},
    {"sku": "OIL-1", "name": "Sunflower oil 1L", "category": "Staple", "unit_cost": "145.00", "lead_time_days": 6, "on_hand": 240, "base": 18, "major": 1.28, "promo": 1.1, "payday": 1.2, "mild": 1.05, "post": 0.92, "jitter": 4},
    {"sku": "SUG-1", "name": "Sugar 1kg", "category": "Staple", "unit_cost": "48.00", "lead_time_days": 5, "on_hand": 280, "base": 22, "major": 1.55, "promo": 1.15, "payday": 1.28, "mild": 1.06, "post": 0.85, "jitter": 6},
    {"sku": "TEA-250", "name": "Assam tea 250g", "category": "Grocery", "unit_cost": "95.00", "lead_time_days": 6, "on_hand": 150, "base": 12, "major": 1.32, "promo": 1.22, "payday": 1.4, "mild": 1.08, "post": 0.88, "jitter": 8},
    {"sku": "DET-1", "name": "Detergent powder 1kg", "category": "Household", "unit_cost": "112.00", "lead_time_days": 8, "on_hand": 420, "base": 14, "major": 1.06, "promo": 1.12, "payday": 1.05, "mild": 1.02, "post": 0.96, "jitter": 1},
    {"sku": "BTL-9", "name": "Steel water bottle", "category": "General", "unit_cost": "299.00", "lead_time_days": 15, "on_hand": 18, "base": 5, "major": 1.3, "promo": 2.35, "payday": 1.06, "mild": 1.04, "post": 0.9, "jitter": 9},
    {"sku": "NOTE-4", "name": "Notebook 6-pack", "category": "Stationery", "unit_cost": "160.00", "lead_time_days": 9, "on_hand": 70, "base": 7, "major": 1.18, "promo": 1.45, "payday": 1.1, "mild": 1.12, "post": 0.9, "jitter": 11},
]


def _parse(value):
    year, month, day = (int(part) for part in value.split("-"))
    return date(year, month, day)


def demo_event_rows():
    named = [
        ("Holi", "2025-03-14", "2025-03-15", CalendarEvent.MAJOR, "Peak selling window"),
        ("Raksha Bandhan", "2025-08-09", "2025-08-09", CalendarEvent.MAJOR, ""),
        ("Dussehra", "2025-10-02", "2025-10-02", CalendarEvent.MAJOR, ""),
        ("Diwali week", "2025-10-14", "2025-10-21", CalendarEvent.MAJOR, "Gift, lights, and dry fruit peak"),
        ("Monsoon clearance", "2025-07-15", "2025-07-17", CalendarEvent.PROMOTION, ""),
        ("Republic Day", "2025-01-26", "2025-01-26", CalendarEvent.HOLIDAY, ""),
        ("Independence Day", "2025-08-15", "2025-08-15", CalendarEvent.HOLIDAY, ""),
        ("Christmas", "2025-12-25", "2025-12-25", CalendarEvent.HOLIDAY, ""),
        ("New Year", "2026-01-01", "2026-01-01", CalendarEvent.HOLIDAY, ""),
        ("Republic Day", "2026-01-26", "2026-01-26", CalendarEvent.HOLIDAY, ""),
        ("Holi", "2026-03-03", "2026-03-04", CalendarEvent.MAJOR, ""),
        ("Summer cooling sale", "2026-04-10", "2026-04-12", CalendarEvent.PROMOTION, ""),
        ("Independence Day", "2026-08-15", "2026-08-15", CalendarEvent.HOLIDAY, ""),
        ("Raksha Bandhan", "2026-08-28", "2026-08-28", CalendarEvent.MAJOR, ""),
        ("October festive display", "2026-10-03", "2026-10-08", CalendarEvent.PROMOTION, "In the recent check window"),
        ("Dussehra", "2026-10-20", "2026-10-20", CalendarEvent.MAJOR, ""),
        ("Pre-Diwali store sale", "2026-10-28", "2026-10-31", CalendarEvent.PROMOTION, "Coming up"),
        ("Diwali week", "2026-11-01", "2026-11-08", CalendarEvent.MAJOR, "Next peak. Orders must leave before this week."),
        ("Christmas", "2026-12-25", "2026-12-25", CalendarEvent.HOLIDAY, ""),
    ]
    rows = [
        {
            "name": name,
            "start_date": _parse(start),
            "end_date": _parse(end),
            "event_type": event_type,
            "notes": notes,
        }
        for name, start, end, event_type, notes in named
    ]
    cursor = date(2025, 1, 1)
    while cursor <= date(2026, 12, 1):
        rows.append(
            {
                "name": "Salary day",
                "start_date": cursor,
                "end_date": cursor,
                "event_type": CalendarEvent.PAYDAY,
                "notes": "",
            }
        )
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return rows


def synthesize_quantity(spec, day, events):
    features = day_features(day, events)
    qty = spec["base"] * DOW[day.weekday()]
    if features["major"]:
        qty *= spec["major"]
    else:
        if features["pre_major"] > 0:
            qty *= 1 + (spec["major"] - 1) * features["pre_major"] * 0.7
        if features["post_major"]:
            qty *= spec["post"]
        if features["holiday"]:
            qty *= spec["mild"]
        if features["promo"]:
            qty *= spec["promo"]
        if features["payday"]:
            qty *= spec["payday"]
    jitter = 0.94 + ((day.toordinal() + spec["jitter"]) % 13) / 100
    return max(0, int(round(qty * jitter)))


class _Event:
    def __init__(self, row):
        self.start_date = row["start_date"]
        self.end_date = row["end_date"]
        self.event_type = row["event_type"]


def load_demo(company, today=None):
    today = today or timezone.localdate()
    yesterday = today - timedelta(days=1)
    start = date(2025, 1, 1)
    event_rows = demo_event_rows()
    event_objects = [_Event(row) for row in event_rows]

    with transaction.atomic():
        company.forecast_runs.all().delete()
        company.products.all().delete()
        company.events.all().delete()
        CalendarEvent.objects.bulk_create(
            [CalendarEvent(company=company, **row) for row in event_rows]
        )
        products = [
            Product(
                company=company,
                sku=spec["sku"],
                name=spec["name"],
                category=spec["category"],
                unit_cost=spec["unit_cost"],
                lead_time_days=spec["lead_time_days"],
                on_hand=spec["on_hand"],
            )
            for spec in PRODUCTS
        ]
        Product.objects.bulk_create(products)
        saved = {product.sku: product for product in company.products.all()}
        sales = []
        day = start
        while day <= yesterday:
            for spec in PRODUCTS:
                sales.append(
                    Sale(
                        product=saved[spec["sku"]],
                        date=day,
                        quantity=synthesize_quantity(spec, day, event_objects),
                    )
                )
            day += timedelta(days=1)
        Sale.objects.bulk_create(sales, batch_size=2000)
    return rebuild_forecast(company, today=today)


def ensure_demo_user():
    User = get_user_model()
    user, created = User.objects.get_or_create(
        username="planner",
        defaults={"email": "planner@calendra.local"},
    )
    if created or not user.check_password("CalendraDemo2026"):
        user.email = "planner@calendra.local"
        user.set_password("CalendraDemo2026")
        user.save()
    company, _ = Company.objects.get_or_create(
        owner=user,
        defaults={"name": "Kaveri General Stores", "city": "Pune"},
    )
    if company.name != "Kaveri General Stores":
        company.name = "Kaveri General Stores"
        company.city = "Pune"
        company.save(update_fields=["name", "city"])
    load_demo(company)
    return user
