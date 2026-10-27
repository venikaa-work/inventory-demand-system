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


def _fill_daily(series):
    if not series:
        return []
    lookup = {day: quantity for day, quantity in series}
    day = series[0][0]
    last = series[-1][0]
    filled = []
    while day <= last:
        filled.append((day, float(lookup.get(day, 0))))
        day += timedelta(days=1)
    return filled


def _error_row(actual_by_day, predicted_by_day, only_days=None):
    pairs = []
    absolute = []
    for day, actual in actual_by_day.items():
        if only_days is not None and day not in only_days:
            continue
        predicted = predicted_by_day[day]
        pairs.append((actual, predicted))
        absolute.append(abs(actual - predicted))
    if not absolute:
        return None, None, None
    mae = sum(absolute) / len(absolute)
    rmse = float(np.sqrt(sum(value ** 2 for value in absolute) / len(absolute)))
    return mae, rmse, mape(pairs)


def rebuild_forecast(company, today=None, catalogue="nara"):
    from planner.services.model_suite import MODEL_NAMES, predict_window

    today = today or timezone.localdate()
    yesterday = today - timedelta(days=1)
    products = list(company.products.filter(catalogue=catalogue))
    events = list(company.events.filter(catalogue=catalogue))
    if not products:
        raise ValueError("Add at least one product with sales before running a forecast.")

    future_dates = [today + timedelta(days=offset) for offset in range(HORIZON_DAYS)]
    bundles = []
    for product in products:
        series = _fill_daily(
            [
                (sale.date, sale.quantity)
                for sale in product.sales.filter(date__lte=yesterday).order_by("date")
            ]
        )
        if len(series) <= HOLDOUT_DAYS + 40:
            continue
        train = series[:-HOLDOUT_DAYS]
        holdout = series[-HOLDOUT_DAYS:]
        hold_dates = [day for day, _ in holdout]
        bundles.append(
            {
                "product": product,
                "holdout": holdout,
                "hold_preds": predict_window(train, hold_dates, events),
                "future_preds": predict_window(series, future_dates, events),
                "lifts": learn_lifts(series, events, fit_plain(series)),
            }
        )

    if not bundles:
        raise ValueError("Each product needs more than two months of daily sales.")

    actual_by_day = {}
    predicted_by_model = {name: {} for name in MODEL_NAMES}
    event_days = set()
    for bundle in bundles:
        for index, (day, actual) in enumerate(bundle["holdout"]):
            actual_by_day[day] = actual_by_day.get(day, 0.0) + actual
            if is_event_day(day_features(day, events)):
                event_days.add(day)
            for name in MODEL_NAMES:
                predicted_by_model[name][day] = predicted_by_model[name].get(day, 0.0) + bundle["hold_preds"][name][index]

    scores = []
    for name in MODEL_NAMES:
        mae, rmse, score_mape = _error_row(actual_by_day, predicted_by_model[name])
        scores.append({"name": name, "mae": round(mae, 2), "rmse": round(rmse, 2), "mape": round(score_mape, 2)})
    chosen = min(scores, key=lambda row: row["mae"])["name"]
    for row in scores:
        row["chosen"] = row["name"] == chosen
    weekday_event_mape = _error_row(actual_by_day, predicted_by_model["Weekday average"], event_days)[2]
    chosen_event_mape = _error_row(actual_by_day, predicted_by_model[chosen], event_days)[2]
    weekday_mape = next(row["mape"] for row in scores if row["name"] == "Weekday average")
    chosen_mape = next(row["mape"] for row in scores if row["name"] == chosen)

    point_rows = []
    recommendation_rows = []
    with transaction.atomic():
        company.forecast_runs.filter(catalogue=catalogue).delete()
        run = ForecastRun.objects.create(
            company=company,
            catalogue=catalogue,
            as_of=today,
            horizon_days=HORIZON_DAYS,
            holdout_days=HOLDOUT_DAYS,
            chosen_model=chosen,
            model_scores=scores,
            plain_mape=weekday_mape,
            calendar_mape=chosen_mape,
            plain_event_mape=weekday_event_mape,
            calendar_event_mape=chosen_event_mape,
        )
        for bundle in bundles:
            product = bundle["product"]
            errors = []
            for index, (day, actual) in enumerate(bundle["holdout"]):
                plain_qty = bundle["hold_preds"]["Weekday average"][index]
                chosen_qty = bundle["hold_preds"][chosen][index]
                errors.append(chosen_qty - actual)
                point_rows.append(
                    ForecastPoint(
                        run=run,
                        product=product,
                        date=day,
                        plain_qty=plain_qty,
                        calendar_qty=chosen_qty,
                        actual_qty=actual,
                        segment=ForecastPoint.HOLDOUT,
                    )
                )
            future_rows = []
            for index, day in enumerate(future_dates):
                plain_qty = bundle["future_preds"]["Weekday average"][index]
                chosen_qty = bundle["future_preds"][chosen][index]
                future_rows.append((day, chosen_qty))
                point_rows.append(
                    ForecastPoint(
                        run=run,
                        product=product,
                        date=day,
                        plain_qty=plain_qty,
                        calendar_qty=chosen_qty,
                        actual_qty=None,
                        segment=ForecastPoint.FUTURE,
                    )
                )
            recommendation = _recommendation(
                product, future_rows, errors, bundle["lifts"], events, today
            )
            recommendation_rows.append(
                Recommendation(run=run, product=product, status=Recommendation.SUGGESTED, **recommendation)
            )
        ForecastPoint.objects.bulk_create(point_rows, batch_size=1000)
        Recommendation.objects.bulk_create(recommendation_rows)
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
