"""Weekday baseline plus multiplicative lifts learned from the business calendar."""

from datetime import timedelta

import numpy as np
from django.db import transaction
from django.utils import timezone

from planner.models import ForecastPoint, ForecastRun, Recommendation

HOLDOUT_DAYS = 28
HORIZON_DAYS = 56
REVIEW_DAYS = 7
LIFT_KEYS = ("major", "promo", "holiday", "payday", "post_major")


def day_features(day, events):
    major = holiday = promo = payday = False
    pre = 0.0
    post = 0.0
    for event in events:
        if event.start_date <= day <= event.end_date:
            if event.event_type == "major":
                major = True
            elif event.event_type == "holiday":
                holiday = True
            elif event.event_type == "promotion":
                promo = True
            elif event.event_type == "payday":
                payday = True
        if event.event_type == "major":
            until = (event.start_date - day).days
            if 1 <= until <= 7:
                pre = max(pre, (8 - until) / 7)
            after = (day - event.end_date).days
            if 1 <= after <= 5:
                post = 1.0
    if major:
        holiday = False
        promo = False
        payday = False
        pre = 0.0
        post = 0.0
    return {
        "major": 1.0 if major else 0.0,
        "pre_major": pre,
        "post_major": post,
        "holiday": 1.0 if holiday else 0.0,
        "promo": 1.0 if promo else 0.0,
        "payday": 1.0 if payday else 0.0,
    }


def is_event_day(features):
    return any(
        features[key] > 0
        for key in ("major", "pre_major", "post_major", "holiday", "promo")
    )


def fit_plain(series):
    buckets = {index: [] for index in range(7)}
    for day, qty in series:
        buckets[day.weekday()].append(qty)
    overall = float(np.mean([qty for _, qty in series])) if series else 0.0
    dow = {}
    for index in range(7):
        dow[index] = float(np.mean(buckets[index])) if buckets[index] else overall
    recent = series[-28:] if len(series) >= 28 else series
    recent_mean = float(np.mean([qty for _, qty in recent])) if recent else 0.0
    scale = recent_mean / overall if overall else 1.0
    scale = min(max(scale, 0.65), 1.5)
    return {"dow": dow, "scale": scale}


def predict_plain(model, day):
    return max(0.0, model["dow"][day.weekday()] * model["scale"])


def learn_lifts(series, events, plain_model):
    groups = {key: [] for key in LIFT_KEYS}
    pre_estimates = []
    for day, qty in series:
        baseline = predict_plain(plain_model, day)
        if baseline < 0.5:
            continue
        features = day_features(day, events)
        ratio = qty / baseline
        for key in LIFT_KEYS:
            if features[key] >= 1:
                groups[key].append(ratio)
        if features["pre_major"] > 0.2:
            strength = features["pre_major"]
            pre_estimates.append(1 + (ratio - 1) / strength)

    lifts = {}
    for key, values in groups.items():
        lifts[key] = float(np.median(values)) if len(values) >= 3 else 1.0
    lifts["pre_full"] = float(np.median(pre_estimates)) if len(pre_estimates) >= 3 else 1.0

    for key in ("major", "promo", "holiday", "payday", "pre_full"):
        lifts[key] = min(max(lifts[key], 1.0), 6.0)
    lifts["post_major"] = min(max(lifts["post_major"], 0.3), 1.05)
    return lifts


def predict_calendar(plain_model, lifts, day, events):
    features = day_features(day, events)
    qty = predict_plain(plain_model, day)
    if features["major"]:
        qty *= lifts["major"]
    else:
        if features["pre_major"] > 0:
            qty *= 1 + (lifts["pre_full"] - 1) * features["pre_major"]
        if features["post_major"]:
            qty *= lifts["post_major"]
        if features["holiday"]:
            qty *= lifts["holiday"]
        if features["promo"]:
            qty *= lifts["promo"]
        if features["payday"]:
            qty *= lifts["payday"]
    return max(0.0, qty)


def mape(pairs):
    usable = [(actual, predicted) for actual, predicted in pairs if actual >= 1]
    if not usable:
        return None
    total = sum(abs(actual - predicted) / actual for actual, predicted in usable)
    return 100 * total / len(usable)


def _upcoming_event(events, today, horizon_end):
    upcoming = [
        event
        for event in events
        if event.event_type in {"major", "promotion"} and event.end_date >= today and event.start_date <= horizon_end
    ]
    upcoming.sort(key=lambda event: (event.event_type != "major", event.start_date))
    return upcoming[0] if upcoming else None


def _recommendation(product, future_rows, errors, lifts, events, today):
    lead = max(int(product.lead_time_days), 1)
    lead_demand = sum(qty for _, qty in future_rows[:lead])
    review = sum(qty for _, qty in future_rows[lead : lead + REVIEW_DAYS])
    std = float(np.std(errors, ddof=1)) if len(errors) >= 2 else 0.0
    safety = 1.65 * std * np.sqrt(lead)
    target = lead_demand + review + safety
    order_qty = max(0, int(round(target - product.on_hand)))
    order_value = product.unit_cost * order_qty

    remaining = float(product.on_hand)
    stockout = None
    for day, qty in future_rows:
        remaining -= qty
        if remaining < 0:
            stockout = day
            break

    if order_qty == 0:
        order_by = today
        urgent = False
    elif stockout is None:
        order_by = today
        urgent = False
    else:
        order_by = stockout - timedelta(days=lead)
        if order_by < today:
            order_by = today
        urgent = stockout <= today + timedelta(days=lead)

    horizon_end = today + timedelta(days=HORIZON_DAYS)
    event = _upcoming_event(events, today, horizon_end)

    def written(day):
        return f"{day.day} {day.strftime('%b %Y')}"

    if order_qty == 0:
        reason = (
            f"On-hand stock covers the {lead}-day supplier lead time and the next review week."
        )
    else:
        parts = [f"Order {order_qty} units by {written(order_by)}."]
        if event is not None and event.event_type == "major":
            parts.append(
                f"{event.name} ({written(event.start_date)} to {written(event.end_date)}) "
                f"is on the calendar. Peak festivals sell about {lifts['major']:.1f}× a normal day for this product."
            )
        elif event is not None:
            parts.append(
                f"{event.name} ({written(event.start_date)} to {written(event.end_date)}) "
                f"is a promotion. Past promotions sold about {lifts['promo']:.1f}× a normal day."
            )
        parts.append(
            f"Demand over the {lead}-day lead time is about {lead_demand:.0f} units, "
            f"safety stock is {safety:.0f}, and {product.on_hand} are on hand."
        )
        if urgent and stockout is not None:
            parts.append(
                f"Stock runs out on {written(stockout)}, before a new delivery can arrive."
            )
        reason = " ".join(parts)

    return {
        "order_qty": order_qty,
        "order_value": order_value,
        "order_by": order_by,
        "projected_stockout": stockout,
        "safety_stock": safety,
        "demand_over_lead": lead_demand,
        "reason": reason,
        "urgent": urgent,
        "calendar_lifts": {key: round(float(lifts[key]), 3) for key in ("major", "promo", "holiday", "payday", "post_major", "pre_full")},
    }


def rebuild_forecast(company, today=None):
    today = today or timezone.localdate()
    yesterday = today - timedelta(days=1)
    products = list(company.products.all())
    events = list(company.events.all())
    if not products:
        raise ValueError("Add at least one product with sales before running a forecast.")

    pooled_plain = []
    pooled_calendar = []
    event_plain = []
    event_calendar = []
    point_rows = []
    recommendation_rows = []

    prepared = []
    for product in products:
        series = [
            (sale.date, sale.quantity)
            for sale in product.sales.filter(date__lte=yesterday).order_by("date")
        ]
        if len(series) <= HOLDOUT_DAYS + 30:
            continue
        train = series[:-HOLDOUT_DAYS]
        holdout = series[-HOLDOUT_DAYS:]
        plain_model = fit_plain(train)
        lifts = learn_lifts(train, events, plain_model)
        full_plain = fit_plain(series)
        full_lifts = learn_lifts(series, events, full_plain)
        prepared.append((product, holdout, plain_model, lifts, full_plain, full_lifts))

    if not prepared:
        raise ValueError("Each product needs more than two months of daily sales.")

    with transaction.atomic():
        company.forecast_runs.all().delete()
        run = ForecastRun.objects.create(
            company=company, horizon_days=HORIZON_DAYS, holdout_days=HOLDOUT_DAYS
        )
        for product, holdout, plain_model, lifts, full_plain, full_lifts in prepared:
            errors = []
            for day, actual in holdout:
                plain_qty = predict_plain(plain_model, day)
                calendar_qty = predict_calendar(plain_model, lifts, day, events)
                features = day_features(day, events)
                point_rows.append(
                    ForecastPoint(
                        run=run,
                        product=product,
                        date=day,
                        plain_qty=plain_qty,
                        calendar_qty=calendar_qty,
                        actual_qty=actual,
                        segment=ForecastPoint.HOLDOUT,
                    )
                )
                pooled_plain.append((actual, plain_qty))
                pooled_calendar.append((actual, calendar_qty))
                errors.append(calendar_qty - actual)
                if is_event_day(features):
                    event_plain.append((actual, plain_qty))
                    event_calendar.append((actual, calendar_qty))

            future_rows = []
            for offset in range(HORIZON_DAYS):
                day = today + timedelta(days=offset)
                plain_qty = predict_plain(full_plain, day)
                calendar_qty = predict_calendar(full_plain, full_lifts, day, events)
                future_rows.append((day, calendar_qty))
                point_rows.append(
                    ForecastPoint(
                        run=run,
                        product=product,
                        date=day,
                        plain_qty=plain_qty,
                        calendar_qty=calendar_qty,
                        actual_qty=None,
                        segment=ForecastPoint.FUTURE,
                    )
                )

            rec = _recommendation(product, future_rows, errors, full_lifts, events, today)
            recommendation_rows.append(Recommendation(run=run, product=product, status=Recommendation.SUGGESTED, **rec))

        ForecastPoint.objects.bulk_create(point_rows, batch_size=1000)
        Recommendation.objects.bulk_create(recommendation_rows)
        run.plain_mape = mape(pooled_plain)
        run.calendar_mape = mape(pooled_calendar)
        run.plain_event_mape = mape(event_plain)
        run.calendar_event_mape = mape(event_calendar)
        run.save()
    return run


def refresh_recommendation(product, run, today=None):
    today = today or timezone.localdate()
    events = list(product.company.events.all())
    points = list(run.points.filter(product=product).order_by("date"))
    holdout = [point for point in points if point.segment == ForecastPoint.HOLDOUT]
    future = [point for point in points if point.segment == ForecastPoint.FUTURE]
    if not future:
        return None
    errors = [
        point.calendar_qty - point.actual_qty
        for point in holdout
        if point.actual_qty is not None
    ]
    existing = Recommendation.objects.filter(run=run, product=product).first()
    lifts = existing.calendar_lifts if existing else learn_lifts([], events, fit_plain([]))
    # Stored lifts are rounded floats. Fill any key the formula expects.
    lifts = {
        "major": float(lifts.get("major", 1)),
        "promo": float(lifts.get("promo", 1)),
        "holiday": float(lifts.get("holiday", 1)),
        "payday": float(lifts.get("payday", 1)),
        "post_major": float(lifts.get("post_major", 1)),
        "pre_full": float(lifts.get("pre_full", 1)),
    }
    future_rows = [(point.date, point.calendar_qty) for point in future]
    rec = _recommendation(product, future_rows, errors, lifts, events, today)
    Recommendation.objects.update_or_create(
        run=run,
        product=product,
        defaults={**rec, "status": existing.status if existing else Recommendation.SUGGESTED},
    )
    return Recommendation.objects.get(run=run, product=product)
