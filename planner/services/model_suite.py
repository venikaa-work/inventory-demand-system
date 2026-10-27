"""Four forecasts for one daily series. The caller keeps the one with the smallest hidden-month error."""

import warnings
from datetime import timedelta

import numpy as np

MODEL_NAMES = ("Weekday average", "Holt-Winters", "Ridge regression", "XGBoost")


def feature_row(day, history, events):
    from planner.services.forecast import day_features

    def lag(days_back):
        return float(history.get(day - timedelta(days=days_back), 0.0))

    features = day_features(day, events)
    row = [lag(1), lag(7), lag(14), float(np.mean([lag(step) for step in range(1, 8)]))]
    row.extend(1.0 if day.weekday() == index else 0.0 for index in range(6))
    row.extend(
        [
            features["major"],
            features["pre_major"],
            features["post_major"],
            features["holiday"],
            features["promo"],
            features["payday"],
        ]
    )
    return row


def _training_xy(series, events):
    history = {}
    rows = []
    target = []
    for day, qty in series:
        if len(history) >= 14:
            rows.append(feature_row(day, history, events))
            target.append(float(qty))
        history[day] = float(qty)
    if not rows:
        return None, None
    return np.asarray(rows, dtype=float), np.asarray(target, dtype=float)


def _cap(series):
    peak = max((qty for _, qty in series), default=1)
    return max(float(peak) * 6.0, 1.0)


def _recursive(predict_row, history_series, dates, events, cap):
    history = {day: float(qty) for day, qty in history_series}
    forecasts = []
    for day in dates:
        raw = predict_row(feature_row(day, history, events))
        if raw is None or not np.isfinite(raw):
            raw = 0.0
        qty = float(min(max(raw, 0.0), cap))
        forecasts.append(qty)
        history[day] = qty
    return forecasts


def _weekday(series, dates):
    from planner.services.forecast import fit_plain, predict_plain

    model = fit_plain(series)
    return [float(predict_plain(model, day)) for day in dates]


def _holt_winters(series, steps):
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    values = np.asarray([qty for _, qty in series], dtype=float)
    cap = _cap(series)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fitted = ExponentialSmoothing(
            values,
            trend="add",
            seasonal="add",
            seasonal_periods=7,
            initialization_method="estimated",
        ).fit(optimized=True, use_brute=False)
        raw = np.asarray(fitted.forecast(steps), dtype=float)
    return [float(min(max(value, 0.0), cap)) if np.isfinite(value) else 0.0 for value in raw]


def _ridge(series, dates, history_series, events):
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler

    features, target = _training_xy(series, events)
    if features is None or len(target) < 40:
        raise ValueError("Not enough days for ridge regression.")
    scaler = StandardScaler()
    model = Ridge(alpha=3.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model.fit(scaler.fit_transform(features), target)

    def predict_row(row):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return float(model.predict(scaler.transform(np.asarray([row])))[0])

    return _recursive(predict_row, history_series, dates, events, _cap(series))


def _xgboost(series, dates, history_series, events):
    from xgboost import XGBRegressor

    features, target = _training_xy(series, events)
    if features is None or len(target) < 40:
        raise ValueError("Not enough days for XGBoost.")
    model = XGBRegressor(
        n_estimators=120,
        max_depth=3,
        learning_rate=0.08,
        subsample=0.9,
        colsample_bytree=0.9,
        objective="reg:squarederror",
        random_state=7,
        n_jobs=1,
        verbosity=0,
    )
    model.fit(features, target)

    def predict_row(row):
        return float(model.predict(np.asarray([row]))[0])

    return _recursive(predict_row, history_series, dates, events, _cap(series))


def predict_window(series, dates, events):
    """Forecast `dates` using only `series` as history. One list of quantities per model."""
    forecasts = {}
    forecasts["Weekday average"] = _weekday(series, dates)
    try:
        forecasts["Holt-Winters"] = _holt_winters(series, len(dates))
    except Exception:
        forecasts["Holt-Winters"] = list(forecasts["Weekday average"])
    try:
        forecasts["Ridge regression"] = _ridge(series, dates, series, events)
    except Exception:
        forecasts["Ridge regression"] = list(forecasts["Weekday average"])
    try:
        forecasts["XGBoost"] = _xgboost(series, dates, series, events)
    except Exception:
        forecasts["XGBoost"] = list(forecasts["Weekday average"])
    return forecasts
