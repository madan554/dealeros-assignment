from django.urls import path

from . import views

urlpatterns = [
    path("auth/login", views.login, name="login"),
    path("me", views.me, name="me"),
    path("reason-codes", views.reason_codes, name="reason-codes"),
    path("locations", views.locations, name="locations"),
    path("exceptions", views.exceptions, name="exceptions"),
    path("exceptions/<int:pk>", views.exception_detail, name="exception-detail"),
    path("summary", views.summary, name="summary"),
    path("ask", views.ask, name="ask"),
]
