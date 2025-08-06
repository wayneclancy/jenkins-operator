from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('logs/<str:pod_name>/', views.pod_logs, name='pod_logs'),
]
