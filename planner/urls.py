from django.contrib.auth import views as auth_views
from django.urls import path

from planner import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="planner/login.html", redirect_authenticated_user=True),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("signup/", views.signup, name="signup"),
    path("products/<int:pk>/", views.product_detail, name="product"),
    path("products/<int:pk>/stock/", views.update_stock, name="update_stock"),
    path("recommendations/<int:pk>/accept/", views.accept_recommendation, name="accept"),
    path("calendar/", views.calendar_page, name="calendar"),
    path("next/", views.roadmap, name="roadmap"),
    path("data/", views.data_page, name="data"),
    path("data/upload/", views.upload_sales, name="upload"),
    path("data/demo/", views.load_demo_view, name="load_demo"),
    path("forecast/run/", views.run_forecast_view, name="run_forecast"),
]
