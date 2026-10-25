from django.contrib import admin

from planner.models import CalendarEvent, Company, ForecastRun, Product, Recommendation, Sale

admin.site.site_header = "Calendra admin"


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "owner")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "category", "on_hand", "lead_time_days", "company")
    search_fields = ("sku", "name")


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("product", "date", "quantity")
    list_filter = ("date",)


@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    list_display = ("name", "event_type", "start_date", "end_date", "company")


@admin.register(ForecastRun)
class ForecastRunAdmin(admin.ModelAdmin):
    list_display = ("company", "created_at", "plain_event_mape", "calendar_event_mape")


@admin.register(Recommendation)
class RecommendationAdmin(admin.ModelAdmin):
    list_display = ("product", "order_qty", "order_by", "urgent", "status")
