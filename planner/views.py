import math
from collections import defaultdict
from datetime import timedelta

from django.db.models import Max, Min, Sum

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from planner.forms import EventForm, SignUpForm, StockForm, UploadForm
from planner.models import ForecastPoint, Product, Recommendation, Sale
from planner.services.demo import load_demo
from planner.services.forecast import rebuild_forecast, refresh_recommendation
from planner.services.upload import import_sales


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Account created. Load the demo store or upload a sales file.")
        return redirect("data")
    return render(request, "planner/signup.html", {"form": form})


def _mean_error(rows, predicted_index):
    if not rows:
        return None, None, None
    absolute = [abs(row[0] - row[predicted_index]) for row in rows]
    mae = sum(absolute) / len(rows)
    rmse = math.sqrt(sum(value ** 2 for value in absolute) / len(rows))
    usable = [row for row in rows if row[0] >= 1]
    mape = None
    if usable:
        mape = 100 * sum(abs(row[0] - row[predicted_index]) / row[0] for row in usable) / len(usable)
    return mae, rmse, mape


def _plan_for(product, run, today):
    horizon = today + timedelta(days=30)
    expected = 0
    if run:
        expected = round(
            sum(
                run.points.filter(
                    product=product,
                    segment=ForecastPoint.FUTURE,
                    date__gte=today,
                    date__lt=horizon,
                ).values_list("calendar_qty", flat=True)
            )
        )
    last_30 = sum(
        product.sales.filter(date__gte=today - timedelta(days=30), date__lt=today).values_list(
            "quantity", flat=True
        )
    )
    change = round(100 * (expected - last_30) / last_30) if last_30 else None
    recommendation = run.recommendations.filter(product=product).first() if run else None
    if recommendation and recommendation.projected_stockout:
        days_left = (recommendation.projected_stockout - today).days
    elif expected:
        days_left = int(product.on_hand / (expected / 30))
    else:
        days_left = None
    price = product.selling_price or (product.unit_cost * 2)
    order_qty = recommendation.order_qty if recommendation else 0
    story = _order_story(product, run, today, order_qty)
    planned = product.on_hand + order_qty
    scale = max(planned, expected, 1)
    incoming = (
        order_qty
        if recommendation and recommendation.status == Recommendation.ACCEPTED
        else 0
    )
    return {
        "product": product,
        "expected": expected,
        "last_30": last_30,
        "change": change,
        "days_left": days_left,
        "recommendation": recommendation,
        "price": price,
        "potential": expected * price,
        "leftover": planned - expected,
        "incoming": incoming,
        "stockout": recommendation.projected_stockout if recommendation else None,
        "planned": planned,
        "bar_stock": round(100 * product.on_hand / scale, 1),
        "bar_order": round(100 * order_qty / scale, 1),
        "bar_demand": min(100, round(100 * expected / scale, 1)),
        **story,
    }


def _order_story(product, run, today, order_qty):
    empty = {
        "arrival": None,
        "gap_missed": 0,
        "later_missed": 0,
        "covers_until": None,
        "next_order_by": None,
        "order_in_time": None,
    }
    if not run or not order_qty:
        return empty
    horizon = today + timedelta(days=30)
    points = run.points.filter(
        product=product,
        segment=ForecastPoint.FUTURE,
        date__gte=today,
        date__lt=horizon,
    ).order_by("date")
    lead = max(int(product.lead_time_days), 1)
    arrival = today + timedelta(days=lead)
    stock = float(product.on_hand)
    gap_missed = 0.0
    later_missed = 0.0
    covers_until = None
    added = False
    for point in points:
        if not added and point.date >= arrival:
            stock += order_qty
            added = True
        qty = float(point.calendar_qty)
        if stock >= qty:
            stock -= qty
            continue
        missed = qty - max(stock, 0.0)
        stock = 0.0
        if point.date < arrival:
            gap_missed += missed
        else:
            later_missed += missed
            if covers_until is None:
                covers_until = point.date
    stockout = None
    recommendation = run.recommendations.filter(product=product).first()
    if recommendation:
        stockout = recommendation.projected_stockout
    return {
        "arrival": arrival,
        "gap_missed": round(gap_missed),
        "later_missed": round(later_missed),
        "covers_until": covers_until,
        "next_order_by": covers_until - timedelta(days=lead) if covers_until else None,
        "order_in_time": stockout - timedelta(days=lead) if stockout else None,
    }


def _sorted_plans(products, run, today):
    rows = [_plan_for(product, run, today) for product in products]
    rows.sort(
        key=lambda row: (
            not (row["recommendation"] and row["recommendation"].urgent and row["recommendation"].order_qty),
            row["days_left"] if row["days_left"] is not None else 9999,
        )
    )
    return rows


def _story_chart(product, run, today):
    chart = {"labels": [], "actual": [], "forecast": [], "mode": "story", "todayIndex": None}
    if not run:
        return chart
    history_start = today - timedelta(days=90)
    end = today + timedelta(days=29)
    actuals = {
        sale.date: sale.quantity
        for sale in product.sales.filter(date__gte=history_start, date__lt=today)
    }
    forecasts = {
        point.date: point.calendar_qty
        for point in run.points.filter(
            product=product, segment=ForecastPoint.FUTURE, date__gte=today, date__lte=end
        )
    }
    index = 0
    day = history_start
    while day <= end:
        chart["labels"].append(day.strftime("%d %b"))
        if day == today:
            chart["todayIndex"] = index
        if day < today:
            chart["actual"].append(actuals.get(day))
            chart["forecast"].append(actuals.get(day) if day == today - timedelta(days=1) else None)
        else:
            chart["actual"].append(None)
            chart["forecast"].append(round(forecasts[day]) if day in forecasts else None)
        day += timedelta(days=1)
        index += 1
    return chart


@login_required
def dashboard(request):
    company = request.user.company
    run = company.forecast_runs.first()
    today = timezone.localdate()
    horizon = today + timedelta(days=30)
    chart = {
        "labels": [],
        "actual": [],
        "plain": [],
        "calendar": [],
        "plainLabel": "Weekday average",
        "calendarLabel": (run.chosen_model if run and run.chosen_model else "Chosen model"),
    }
    context = {
        "run": run,
        "today": today,
        "chart": chart,
        "upcoming": [],
        "items": [],
        "major": None,
        "first_sale": None,
        "last_sale": None,
        "next_plain": 0,
        "next_calendar": 0,
        "plain_mae": None,
        "calendar_mae": None,
        "plain_rmse": None,
        "calendar_rmse": None,
        "plain_mape": None,
        "calendar_mape": None,
        "month_end": horizon - timedelta(days=1),
    }
    if not run:
        return render(request, "planner/dashboard.html", context)

    future = defaultdict(lambda: [0.0, 0.0])
    by_item = defaultdict(float)
    for point in run.points.filter(segment=ForecastPoint.FUTURE, date__lt=horizon):
        future[point.date][0] += point.plain_qty
        future[point.date][1] += point.calendar_qty
        by_item[point.product_id] += point.calendar_qty

    holdout = defaultdict(lambda: [0.0, 0.0, 0.0])
    for point in run.points.filter(segment=ForecastPoint.HOLDOUT):
        row = holdout[point.date]
        row[0] += point.actual_qty or 0
        row[1] += point.plain_qty
        row[2] += point.calendar_qty
    hold_rows = list(holdout.values())
    plain_mae, plain_rmse, plain_mape = _mean_error(hold_rows, 1)
    calendar_mae, calendar_rmse, calendar_mape = _mean_error(hold_rows, 2)

    history_start = today - timedelta(days=90)
    actuals = defaultdict(float)
    for sold_on, quantity in Sale.objects.filter(
        product__company=company, date__gte=history_start, date__lt=today
    ).values_list("date", "quantity"):
        actuals[sold_on] += quantity

    day = history_start
    while day < horizon:
        chart["labels"].append(day.strftime("%d %b"))
        chart["actual"].append(round(actuals[day]) if day < today and day in actuals else None)
        if day in future:
            chart["plain"].append(round(future[day][0]))
            chart["calendar"].append(round(future[day][1]))
        else:
            chart["plain"].append(None)
            chart["calendar"].append(None)
        day += timedelta(days=1)

    items = []
    for product in company.products.all():
        items.append({"product": product, "units": round(by_item[product.id])})
    items.sort(key=lambda item: item["units"], reverse=True)
    first_sale = Sale.objects.filter(product__company=company).order_by("date").values_list("date", flat=True).first()
    last_sale = Sale.objects.filter(product__company=company).order_by("-date").values_list("date", flat=True).first()

    context.update(
        {
            "upcoming": list(
                company.events.filter(end_date__gte=today, start_date__lt=horizon)
                .exclude(event_type="payday")
                .order_by("start_date")[:6]
            ),
            "items": items,
            "major": company.events.filter(
                event_type="major", end_date__gte=today, start_date__lt=horizon
            ).order_by("start_date").first(),
            "first_sale": first_sale,
            "last_sale": last_sale,
            "next_plain": round(sum(row[0] for row in future.values())),
            "next_calendar": round(sum(row[1] for row in future.values())),
            "plain_mae": plain_mae,
            "calendar_mae": calendar_mae,
            "plain_rmse": plain_rmse,
            "calendar_rmse": calendar_rmse,
            "plain_mape": plain_mape,
            "calendar_mape": calendar_mape,
        }
    )
    rows = _sorted_plans(company.products.all(), run, today) if run else []
    context["rows"] = rows
    context["expanded"] = rows[0] if rows else None
    context["tab"] = "forecast"
    if rows:
        context["chart"] = _story_chart(rows[0]["product"], run, today)
    return render(request, "planner/dashboard.html", context)


@login_required
def product_detail(request, pk):
    company = request.user.company
    product = get_object_or_404(Product, pk=pk, company=company)
    run = company.forecast_runs.first()
    today = timezone.localdate()
    plan = _plan_for(product, run, today) if run else None
    chart = _story_chart(product, run, today)
    rows = _sorted_plans(company.products.all(), run, today) if run else []
    return render(
        request,
        "planner/product.html",
        {
            "product": product,
            "run": run,
            "plan": plan,
            "expanded": plan,
            "rows": rows,
            "tab": request.GET.get("tab", "forecast"),
            "chart": chart,
            "stock_form": StockForm(
                initial={"on_hand": product.on_hand, "lead_time_days": product.lead_time_days}
            ),
        },
    )


@login_required
def update_stock(request, pk):
    product = get_object_or_404(Product, pk=pk, company=request.user.company)
    form = StockForm(request.POST)
    if form.is_valid():
        product.on_hand = form.cleaned_data["on_hand"]
        product.lead_time_days = form.cleaned_data["lead_time_days"]
        product.save(update_fields=["on_hand", "lead_time_days"])
        run = request.user.company.forecast_runs.first()
        if run:
            refresh_recommendation(product, run)
        messages.success(request, "Stock and lead time updated. The order suggestion uses the new numbers.")
    return redirect(f"/products/{product.pk}/?tab=details#item-{product.pk}")


@login_required
def accept_recommendation(request, pk):
    recommendation = get_object_or_404(
        Recommendation, pk=pk, run__company=request.user.company
    )
    recommendation.status = Recommendation.ACCEPTED
    recommendation.save(update_fields=["status"])
    messages.success(request, f"Marked the {recommendation.product.name} order as accepted.")
    return redirect(f"/products/{recommendation.product_id}/#item-{recommendation.product_id}")


@login_required
def roadmap(request):
    return render(request, "planner/roadmap.html")


@login_required
def calendar_page(request):
    company = request.user.company
    form = EventForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        event = form.save(commit=False)
        event.company = company
        event.save()
        messages.success(request, "Event added. Rebuild the forecast so the new date is included.")
        return redirect("calendar")
    today = timezone.localdate()
    events = company.events.filter(end_date__gte=today - timedelta(days=45)).exclude(
        event_type="payday"
    )
    paydays = company.events.filter(event_type="payday", end_date__gte=today).count()
    return render(
        request,
        "planner/calendar.html",
        {"form": form, "events": events, "payday_count": paydays},
    )


@login_required
def data_page(request):
    company = request.user.company
    sales = Sale.objects.filter(product__company=company)
    span = sales.aggregate(first=Min("date"), last=Max("date"), units=Sum("quantity"))
    catalogue = company.products.annotate(
        sold=Sum("sales__quantity"),
        first_sale=Min("sales__date"),
        last_sale=Max("sales__date"),
    ).order_by("name")
    return render(
        request,
        "planner/data.html",
        {
            "form": UploadForm(),
            "run": company.forecast_runs.first(),
            "product_count": company.products.count(),
            "sale_count": sales.count(),
            "span": span,
            "catalogue": catalogue,
        },
    )


@login_required
def upload_sales(request):
    form = UploadForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            result = import_sales(request.user.company, form.cleaned_data["sales_file"])
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("data")
        messages.success(
            request,
            f"Imported {result['sales']} sales rows and {result['products']} new products. Rebuild the forecast.",
        )
    else:
        messages.error(request, "Choose a CSV or Excel file.")
    return redirect("data")


@login_required
def load_demo_view(request):
    if request.method != "POST":
        return redirect("data")
    company = request.user.company
    company.name = "Nara Studio"
    company.city = "Mumbai"
    company.save(update_fields=["name", "city"])
    load_demo(company)
    messages.success(request, "Nara Studio is loaded from Shopify sales, with a forecast ready.")
    return redirect("dashboard")


@login_required
def run_forecast_view(request):
    if request.method != "POST":
        return redirect("dashboard")
    try:
        rebuild_forecast(request.user.company)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("data")
    messages.success(request, "Forecast rebuilt from the latest sales and calendar.")
    return redirect("dashboard")
