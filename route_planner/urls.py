from django.urls import path
from . import views

urlpatterns = [
    path("route/", views.plan_route, name="plan_route"),
    path("health/", views.health, name="health"),
]
