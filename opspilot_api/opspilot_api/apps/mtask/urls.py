from django.urls import path
from . import views
from .task_views import TasksView

urlpatterns = [
    path('tasks', TasksView.as_view()),
    path('tasks/<int:pk>', TasksView.as_view()),
    path('cmd_exec', views.CmdExecView.as_view()),
    path('templates', views.TemplateView.as_view()),
    path('templates/categorys', views.TemplateCategoryView.as_view()),
]
