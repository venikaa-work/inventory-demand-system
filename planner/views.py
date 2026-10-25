from datetime import timedelta

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


@login_required
def dashboard(request):
    company = request.user.company
    run = company.forecast_runs.first()
    recommendations = []
    chart = {"labels": [], "plain": [], "calendar": []}
    upcoming = []
    order_value = 0
    urgent_count = 0
    if run:
        recommendations = list(run.recommendations.select_related("product"))
        urgent_count = sum(1 for item in recommendations if item.urgent and item.order_qty)
        order_value = sum((item.order_value for item in recommendations), start=0)
        today = timezone.localdate()
        horizon = today + timedelta(days=30)
        totals = {}
        for point in run.points.filter(segment=ForecastPoint.FUTURE, date__lt=horizon):
            bucket = totals.setdefault(point.date, [0.0, 0.0])
            bucket[0] += point.plain_qty
            bucket[1] += point.calendar_qty
        for day in sorted(totals):
            chart["labels"].append(day.strftime("%d %b"))
            chart["plain"].append(round(totals[day][0], 1))
            chart["calendar"].append(round(totals[day][1], 1))
        upcoming = list(
            company.events.filter(end_date__gte=today, start_date__lt=horizon)
            .exclude(event_type="payday")
            .order_by("start_date")[:6]
        )
    return render(
        request,
        "planner/dashboard.html",
        {
            "run": run,
            "recommendations": recommendations,
            "chart": chart,
            "upcoming": upcoming,
            "order_value": order_value,
            "urgent_count": urgent_count,
        },
    )


@login_required
def product_detail(request, pk):
    company = request.user.company
    product = get_object_or_404(Product, pk=pk, company=company)
    run = company.forecast_runs.first()
    recommendation = None
    chart = {"labels": [], "actual": [], "plain": [], "calendar": []}
    if run:
        recommendation = run.recommendations.filter(product=product).first()
        today = timezone.localdate()
        history_start = today - timedelta(days=70)
        actuals = {
            sale.date: sale.quantity
            for sale in product.sales.filter(date__gte=history_start, date__lt=today)
        }
        forecasts = {
            point.date: point
            for point in run.points.filter(product=product, date__gte=history_start)
        }
        end = today + timedelta(days=55)
        day = history_start
        while day <= end:
            chart["labels"].append(day.strftime("%d %b"))
            chart["actual"].append(actuals.get(day))
            point = forecasts.get(day)
            chart["plain"].append(round(point.plain_qty, 1) if point else None)
            chart["calendar"].append(round(point.calendar_qty, 1) if point else None)
            day += timedelta(days=1)
    return render(
        request,
        "planner/product.html",
        {
            "product": product,
            "run": run,
            "recommendation": recommendation,
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
    return redirect("product", pk=product.pk)


@login_required
def accept_recommendation(request, pk):
    recommendation = get_object_or_404(
        Recommendation, pk=pk, run__company=request.user.company
    )
    recommendation.status = Recommendation.ACCEPTED
    recommendation.save(update_fields=["status"])
    messages.success(request, f"Marked the {recommendation.product.name} order as accepted.")
    return redirect("product", pk=recommendation.product_id)


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
    return render(
        request,
        "planner/data.html",
        {
            "form": UploadForm(),
            "run": company.forecast_runs.first(),
            "product_count": company.products.count(),
            "sale_count": Sale.objects.filter(product__company=company).count(),
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
    company.name = "Kaveri General Stores"
    company.city = "Pune"
    company.save(update_fields=["name", "city"])
    load_demo(company)
    messages.success(request, "Demo store loaded for Kaveri General Stores, with a forecast ready.")
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
