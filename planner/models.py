from django.conf import settings
from django.db import models


class Company(models.Model):
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="company"
    )
    name = models.CharField(max_length=120)
    city = models.CharField(max_length=80, default="Pune")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Product(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="products")
    sku = models.CharField(max_length=32)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=80)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)
    lead_time_days = models.PositiveIntegerField(default=7)
    on_hand = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "sku"], name="unique_sku_per_company")
        ]
        ordering = ["name"]

    def __str__(self):
        return f"{self.sku} {self.name}"


class Sale(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="sales")
    date = models.DateField()
    quantity = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["product", "date"], name="unique_sale_per_day")
        ]
        indexes = [models.Index(fields=["product", "date"])]
        ordering = ["date"]


class CalendarEvent(models.Model):
    MAJOR = "major"
    HOLIDAY = "holiday"
    PROMOTION = "promotion"
    PAYDAY = "payday"
    TYPE_CHOICES = [
        (MAJOR, "Peak festival"),
        (HOLIDAY, "Public holiday"),
        (PROMOTION, "Promotion"),
        (PAYDAY, "Payday"),
    ]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="events")
    name = models.CharField(max_length=120)
    start_date = models.DateField()
    end_date = models.DateField()
    event_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    notes = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["start_date", "name"]

    def __str__(self):
        return self.name


class ForecastRun(models.Model):
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="forecast_runs"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    horizon_days = models.PositiveIntegerField(default=56)
    holdout_days = models.PositiveIntegerField(default=28)
    plain_mape = models.FloatField(null=True, blank=True)
    calendar_mape = models.FloatField(null=True, blank=True)
    plain_event_mape = models.FloatField(null=True, blank=True)
    calendar_event_mape = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ForecastPoint(models.Model):
    HOLDOUT = "holdout"
    FUTURE = "future"
    SEGMENT_CHOICES = [(HOLDOUT, "Holdout"), (FUTURE, "Future")]

    run = models.ForeignKey(ForecastRun, on_delete=models.CASCADE, related_name="points")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="forecast_points")
    date = models.DateField()
    plain_qty = models.FloatField()
    calendar_qty = models.FloatField()
    actual_qty = models.FloatField(null=True, blank=True)
    segment = models.CharField(max_length=16, choices=SEGMENT_CHOICES)

    class Meta:
        indexes = [models.Index(fields=["run", "product", "date"])]
        ordering = ["date"]


class Recommendation(models.Model):
    SUGGESTED = "suggested"
    ACCEPTED = "accepted"
    STATUS_CHOICES = [(SUGGESTED, "Suggested"), (ACCEPTED, "Accepted")]

    run = models.ForeignKey(
        ForecastRun, on_delete=models.CASCADE, related_name="recommendations"
    )
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="recommendations")
    order_qty = models.PositiveIntegerField(default=0)
    order_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    order_by = models.DateField()
    projected_stockout = models.DateField(null=True, blank=True)
    safety_stock = models.FloatField(default=0)
    demand_over_lead = models.FloatField(default=0)
    reason = models.TextField(blank=True)
    urgent = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=SUGGESTED)
    calendar_lifts = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "product"], name="unique_reco_per_run")
        ]
        ordering = ["-urgent", "-order_value"]
