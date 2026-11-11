from django.urls import path
from . import views
from .releases import ReleaseDetailView, ReleaseRollbackView, ReleasesView
from .release_config import ApplicationView, ApplicationTokenView, ArtifactView, CIIntakeView, ReleasePrecheckView
from .diagnostic_workflows import PolicyView
from .diagnostics import DiagnosisView, RepairApprovalsView, TreesView, TrialView
from .managed_views import ManagedJobsView, ManagedRunView
from .script_views import ScriptLibraryView, ScriptPreviewView
from .reports import HealthReportView, ComparisonView
from .task_views import TasksView, TaskActionView, TaskReportView, WorkerStatusView
from .operations_views import SchedulesView, TemplateDetailView

urlpatterns = [
    path('release-applications', ApplicationView.as_view()),
    path('release-applications/<slug:application>/token', ApplicationTokenView.as_view()),
    path('release-applications/<slug:application>/artifacts', ArtifactView.as_view()),
    path('release-applications/<slug:application>/precheck', ReleasePrecheckView.as_view()),
    path('ci/<slug:application>/artifacts', CIIntakeView.as_view()),
    path('releases', ReleasesView.as_view()),
    path('releases/<int:pk>', ReleaseDetailView.as_view()),
    path('releases/<int:pk>/rollback-options', ReleaseRollbackView.as_view()),
    path('diagnostic-policies', PolicyView.as_view()),
    path('diagnostic-policies/<int:pk>', PolicyView.as_view()),
    path('diagnostic-trees', TreesView.as_view()),
    path('diagnostic-trees/<int:pk>', TreesView.as_view()),
    path('diagnostic-trees/<int:pk>/trial', TrialView.as_view()),
    path('repair-approvals', RepairApprovalsView.as_view()),
    path('incidents/<int:pk>/runs', DiagnosisView.as_view()),
    path('jobs',ManagedJobsView.as_view()),
    path('jobs/<int:pk>',ManagedJobsView.as_view()),
    path('jobs/<int:pk>/<str:action>',ManagedRunView.as_view()),
    path('scripts/preview',ScriptPreviewView.as_view()),
    path('scripts',ScriptLibraryView.as_view()),
    path('scripts/<int:pk>',ScriptLibraryView.as_view()),
    path('tasks/<int:pk>/health-report', HealthReportView.as_view()),
    path('tasks/<int:pk>/comparison', ComparisonView.as_view()),
    path('worker', WorkerStatusView.as_view()),
    path('schedules', SchedulesView.as_view()),
    path('schedules/<int:pk>', SchedulesView.as_view()),
    path('tasks/<int:pk>/action', TaskActionView.as_view()),
    path('tasks/<int:pk>/report', TaskReportView.as_view()),
    path('templates/<int:pk>', TemplateDetailView.as_view()),
    path('tasks', TasksView.as_view()),
    path('tasks/<int:pk>', TasksView.as_view()),
    path('cmd_exec', views.CmdExecView.as_view()),
    path('templates', views.TemplateView.as_view()),
    path('templates/categorys', views.TemplateCategoryView.as_view()),
]
