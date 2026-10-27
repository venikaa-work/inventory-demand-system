from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from planner.models import CalendarEvent, Company, Product, Sale
from planner.services.forecast import day_features, rebuild_forecast

DOW = (1.04, 0.98, 1.0, 1.06, 1.14, 1.2, 0.58)

PRODUCTS = [
    {"sku": "TEE-CREAM-S", "name": "Cotton crewneck T-shirt", "category": "Small · Cream", "unit_cost": "620.00", "selling_price": "1490.00", "lead_time_days": 7, "on_hand": 24, "base": 3.4, "major": 2.6, "promo": 1.85, "payday": 1.08, "mild": 1.12, "post": 0.55, "jitter": 3},
    {"sku": "TOTE-TERRA", "name": "Canvas tote bag", "category": "Terracotta", "unit_cost": "480.00", "selling_price": "1290.00", "lead_time_days": 10, "on_hand": 30, "base": 2.1, "major": 1.7, "promo": 1.55, "payday": 1.06, "mild": 1.08, "post": 0.7, "jitter": 5},
    {"sku": "DRESS-IVORY-S", "name": "Silk slip dress", "category": "Small · Ivory", "unit_cost": "2400.00", "selling_price": "5490.00", "lead_time_days": 14, "on_hand": 36, "base": 1.3, "major": 3.1, "promo": 1.7, "payday": 1.04, "mild": 1.1, "post": 0.5, "jitter": 7},
    {"sku": "SHIRT-WHITE-M", "name": "Oversized oxford shirt", "category": "Medium · White", "unit_cost": "1250.00", "selling_price": "2890.00", "lead_time_days": 10, "on_hand": 42, "base": 2.2, "major": 1.8, "promo": 1.45, "payday": 1.1, "mild": 1.06, "post": 0.72, "jitter": 2},
    {"sku": "DRESS-BLACK-M", "name": "Linen shirt dress", "category": "Medium · Black", "unit_cost": "1680.00", "selling_price": "4290.00", "lead_time_days": 12, "on_hand": 34, "base": 1.5, "major": 2.4, "promo": 1.6, "payday": 1.05, "mild": 1.08, "post": 0.58, "jitter": 4},
    {"sku": "TROUSER-STONE-M", "name": "Wide-leg trousers", "category": "Medium · Stone", "unit_cost": "1400.00", "selling_price": "3190.00", "lead_time_days": 12, "on_hand": 38, "base": 1.7, "major": 1.65, "promo": 1.4, "payday": 1.12, "mild": 1.05, "post": 0.75, "jitter": 6},
    {"sku": "POLO-NAVY-M", "name": "Cotton polo", "category": "Medium · Navy", "unit_cost": "720.00", "selling_price": "1690.00", "lead_time_days": 8, "on_hand": 46, "base": 2.4, "major": 1.55, "promo": 1.5, "payday": 1.15, "mild": 1.04, "post": 0.8, "jitter": 8},
    {"sku": "SHORT-OLIVE-M", "name": "Weekend shorts", "category": "Medium · Olive", "unit_cost": "780.00", "selling_price": "1890.00", "lead_time_days": 9, "on_hand": 40, "base": 1.6, "major": 1.25, "promo": 1.7, "payday": 1.08, "mild": 1.02, "post": 0.85, "jitter": 9},
    {"sku": "CARDI-OAT-M", "name": "Knit cardigan", "category": "Medium · Oat", "unit_cost": "1550.00", "selling_price": "3490.00", "lead_time_days": 14, "on_hand": 48, "base": 1.1, "major": 1.45, "promo": 1.25, "payday": 1.06, "mild": 1.04, "post": 0.82, "jitter": 11},
    {"sku": "COAT-CAMEL-M", "name": "Wool overcoat", "category": "Medium · Camel", "unit_cost": "4200.00", "selling_price": "8990.00", "lead_time_days": 21, "on_hand": 26, "base": 0.55, "major": 2.2, "promo": 1.3, "payday": 1.02, "mild": 1.05, "post": 0.7, "jitter": 1},
]


def _parse(value):
    year, month, day = (int(part) for part in value.split("-"))
    return date(year, month, day)


def demo_event_rows():
    named = [
        ("Holi", "2025-03-14", "2025-03-15", CalendarEvent.MAJOR, "Peak selling window"),
        ("Raksha Bandhan", "2025-08-09", "2025-08-09", CalendarEvent.MAJOR, ""),
        ("Dussehra", "2025-10-02", "2025-10-02", CalendarEvent.MAJOR, ""),
        ("Diwali week", "2025-10-14", "2025-10-21", CalendarEvent.MAJOR, "Festive clothing peak"),
        ("Monsoon clearance", "2025-07-15", "2025-07-17", CalendarEvent.PROMOTION, "End-of-season clothing sale"),
        ("Republic Day", "2025-01-26", "2025-01-26", CalendarEvent.HOLIDAY, ""),
        ("Independence Day", "2025-08-15", "2025-08-15", CalendarEvent.HOLIDAY, ""),
        ("Christmas", "2025-12-25", "2025-12-25", CalendarEvent.HOLIDAY, ""),
        ("New Year", "2026-01-01", "2026-01-01", CalendarEvent.HOLIDAY, ""),
        ("Republic Day", "2026-01-26", "2026-01-26", CalendarEvent.HOLIDAY, ""),
        ("Holi", "2026-03-03", "2026-03-04", CalendarEvent.MAJOR, ""),
        ("Summer drop", "2026-04-10", "2026-04-12", CalendarEvent.PROMOTION, "New shirts and shorts"),
        ("Independence Day", "2026-08-15", "2026-08-15", CalendarEvent.HOLIDAY, ""),
        ("Raksha Bandhan", "2026-08-28", "2026-08-28", CalendarEvent.MAJOR, ""),
        ("October festive drop", "2026-10-03", "2026-10-08", CalendarEvent.PROMOTION, "In the recent check window"),
        ("Dussehra", "2026-10-20", "2026-10-20", CalendarEvent.MAJOR, ""),
        ("Pre-Diwali edit", "2026-10-28", "2026-10-31", CalendarEvent.PROMOTION, "Coming up on the site"),
        ("Diwali week", "2026-11-01", "2026-11-08", CalendarEvent.MAJOR, "Dresses and tees peak. Orders must ship before this week."),
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
                selling_price=spec["selling_price"],
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
        defaults={"name": "Nara Studio", "city": "Mumbai"},
    )
    if company.name != "Nara Studio":
        company.name = "Nara Studio"
        company.city = "Mumbai"
        company.save(update_fields=["name", "city"])
    load_demo(company)
    return user
