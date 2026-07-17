from django.urls import path

from . import views

urlpatterns = [
    path("", views.admin_login, name="admin_login"),
    path("dashboard", views.admin_dashboard, name="admin_dashboard"),
    path("logout", views.admin_logout, name="admin_logout"),
    path("customers", views.admin_customerlist, name="admin_customerlist"),
    path("user-details/<int:user_id>/", views.admin_user_details, name="admin_user_details"),
    path(
        "user-details/<int:user_id>/toggle-status/",
        views.toggle_user_status,
        name="toggle_user_status",
    ),
    path("admin/analytics/", views.admin_analytics, name="admin_analytics"),
]