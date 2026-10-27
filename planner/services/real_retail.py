import csv
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum

from planner.models import CalendarEvent, ForecastPoint, Product, Sale
from planner.services.forecast import _fill_daily, mape, rebuild_forecast
from planner.services.model_suite import MODEL_NAMES, predict_window

TRAIN_END = date(2024, 1, 2)
AS_OF = date(2024, 2, 1)
TEST_END = date(2024, 2, 29)
SOURCE = settings.BASE_DIR / "sample_data" / "iowa_daily.csv"

# The Iowa file left bottle price blank. These are approximate shelf prices
# so the screen can show a dollar figure. The test itself is bottle counts.
PRICES = {
    "64870": Decimal("16.99"),
    "11788": Decimal("11.99"),
    "36904": Decimal("7.99"),
    "38177": Decimal("19.99"),
    "43337": Decimal("17.99"),
    "36304": Decimal("8.99"),
    "1799": Decimal("21.99"),
    "65013": Decimal("14.99"),
}

EVENTS = [
    ("New Year", "2023-01-01", "2023-01-01", CalendarEvent.HOLIDAY),
    ("Independence Day", "2023-07-04", "2023-07-04", CalendarEvent.HOLIDAY),
    ("Thanksgiving", "2023-11-23", "2023-11-23", CalendarEvent.HOLIDAY),
    ("Christmas", "2023-12-24", "2023-12-26", CalendarEvent.MAJOR),
    ("New Year", "2024-01-01", "2024-01-01", CalendarEvent.HOLIDAY),
]


def _parse(value):
    year, month, day = (int(part) for part in value.split("-"))
    return date(year, month, day)


def _gap(guessed, actual):
    difference = guessed - actual
    if difference > 0:
        return f"{difference:,} over"
    if difference < 0:
        return f"{abs(difference):,} short"
    return "exact"


def _february_check(company, run):
    events = list(company.events.filter(catalogue="real"))
    products = list(company.products.filter(catalogue="real"))
    # Walk every day after training so February keeps the right weekday.
    # January 3–31 is forecast, then discarded. Only February is scored.
    horizon = [TRAIN_END + timedelta(days=offset + 1) for offset in range((TEST_END - TRAIN_END).days)]
    test_dates = [day for day in horizon if AS_OF <= day <= TEST_END]
    shop_actual = {day: 0.0 for day in test_dates}
    shop_pred = {name: {day: 0.0 for day in test_dates} for name in MODEL_NAMES}
    product_rows = []
    for product in products:
        history = _fill_daily(
            [
                (sold.date, sold.quantity)
                for sold in product.sales.filter(date__lte=TRAIN_END).order_by("date")
            ]
        )
        full_forecast = predict_window(history, horizon, events)
        offset = horizon.index(test_dates[0])
        forecasts = {name: values[offset:offset + len(test_dates)] for name, values in full_forecast.items()}
        actuals = {
            sold.date: sold.quantity
            for sold in product.sales.filter(date__gte=AS_OF, date__lte=TEST_END)
        }
        totals = {name: 0.0 for name in MODEL_NAMES}
        sold_total = 0
        for index, day in enumerate(test_dates):
            actual = actuals.get(day, 0)
            sold_total += actual
            shop_actual[day] += actual
            for name in MODEL_NAMES:
                guessed = max(0.0, forecasts[name][index])
                totals[name] += guessed
                shop_pred[name][day] += guessed
        saved_points = {
            point.date: point
            for point in run.points.filter(
                product=product,
                segment=ForecastPoint.FUTURE,
                date__gte=AS_OF,
                date__lte=TEST_END,
            )
        }
        chosen = run.chosen_model or "Holt-Winters"
        for index, day in enumerate(test_dates):
            point = saved_points.get(day)
            if point is None:
                continue
            point.plain_qty = max(0.0, forecasts["Weekday average"][index])
            point.calendar_qty = max(0.0, forecasts[chosen][index])
            point.save(update_fields=["plain_qty", "calendar_qty"])
        product_rows.append(
            {
                "name": product.name,
                "category": product.category,
                "sold": sold_total,
                "weekday": round(totals["Weekday average"]),
                "holt": round(totals["Holt-Winters"]),
                "ridge": round(totals["Ridge regression"]),
                "xgb": round(totals["XGBoost"]),
            }
        )
    actual_total = round(sum(shop_actual.values()))
    model_rows = []
    for name in MODEL_NAMES:
        guessed = round(sum(shop_pred[name].values()))
        pairs = [(shop_actual[day], shop_pred[name][day]) for day in test_dates]
        model_rows.append(
            {
                "name": name,
                "guessed": guessed,
                "gap": _gap(guessed, actual_total),
                "mape": round(mape(pairs) or 0, 1),
            }
        )
    closest = min(model_rows, key=lambda row: abs(row["guessed"] - actual_total))
    for row in model_rows:
        row["closest"] = row["name"] == closest["name"]
    days = [
        {
            "date": f"{day.day} {day.strftime('%b')}",
            "sold": round(shop_actual[day]),
            "weekday": round(shop_pred["Weekday average"][day]),
            "holt": round(shop_pred["Holt-Winters"][day]),
            "ridge": round(shop_pred["Ridge regression"][day]),
            "xgb": round(shop_pred["XGBoost"][day]),
        }
        for day in test_dates
    ]
    product_rows.sort(key=lambda row: row["sold"], reverse=True)
    return {
        "start": "1 Feb 2024",
        "end": "29 Feb 2024",
        "trained_through": "2 Jan 2024",
        "actual": actual_total,
        "models": model_rows,
        "products": product_rows,
        "days": days,
    }


def load_real(company):
    """Train on 2 Jan 2023–2 Jan 2024. February 2024 is the unseen month."""
    if not SOURCE.exists():
        raise ValueError("The Iowa sales file is missing from sample_data.")
    with SOURCE.open(newline="") as handle:
        raw_rows = list(csv.DictReader(handle))
    if not raw_rows:
        raise ValueError("The Iowa sales file is empty.")

    specs = {}
    for row in raw_rows:
        specs.setdefault(
            row["sku"],
            {"name": row["name"], "category": row["category"]},
        )

    with transaction.atomic():
        company.forecast_runs.filter(catalogue="real").delete()
        company.products.filter(catalogue="real").delete()
        company.events.filter(catalogue="real").delete()
        CalendarEvent.objects.bulk_create(
            [
                CalendarEvent(
                    company=company,
                    catalogue="real",
                    name=name,
                    start_date=_parse(start),
                    end_date=_parse(end),
                    event_type=event_type,
                )
                for name, start, end, event_type in EVENTS
            ]
        )
        products = Product.objects.bulk_create(
            [
                Product(
                    company=company,
                    catalogue="real",
                    sku=sku,
                    name=spec["name"],
                    category=spec["category"],
                    selling_price=PRICES.get(sku, Decimal("12.00")),
                    unit_cost=(PRICES.get(sku, Decimal("12.00")) * Decimal("0.70")).quantize(Decimal("0.01")),
                    lead_time_days=7,
                    on_hand=0,
                )
                for sku, spec in specs.items()
            ]
        )
        saved = {product.sku: product for product in products}
        Sale.objects.bulk_create(
            [
                Sale(
                    product=saved[row["sku"]],
                    date=_parse(row["date"]),
                    quantity=int(row["quantity"]),
                )
                for row in raw_rows
                if _parse(row["date"]) <= TRAIN_END or AS_OF <= _parse(row["date"]) <= TEST_END
            ],
            batch_size=2000,
        )
        recent_start = TRAIN_END - timedelta(days=13)
        for product in products:
            recent = (
                product.sales.filter(date__gte=recent_start, date__lte=TRAIN_END).aggregate(total=Sum("quantity"))["total"]
                or 0
            )
            product.on_hand = max(8, round(recent / 14 * product.lead_time_days))
            product.save(update_fields=["on_hand"])

    run = rebuild_forecast(company, today=AS_OF, catalogue="real")
    run.comparison = _february_check(company, run)
    run.save(update_fields=["comparison"])
    return run
