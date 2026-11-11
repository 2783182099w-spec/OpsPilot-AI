from django.urls import path
from .service_checks import ServiceChecksView
from .dashboard import DashboardView
from .views import OverviewView, TargetView, HistoryView, RulesView, PlatformHealthView, WorkbenchView
urlpatterns = [
    path('service-checks', ServiceChecksView.as_view()),
    path('service-checks/<int:pk>', ServiceChecksView.as_view()),path('dashboard',DashboardView.as_view()),path('hosts/<int:pk>/workbench',WorkbenchView.as_view()),path('overview',OverviewView.as_view()),path('targets/<int:pk>',TargetView.as_view()),path('hosts/<int:pk>/history',HistoryView.as_view()),path('rules',RulesView.as_view()),path('rules/<int:pk>',RulesView.as_view()),path('health',PlatformHealthView.as_view())]
