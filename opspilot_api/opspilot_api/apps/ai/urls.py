from django.urls import path

from . import views

urlpatterns = [
    path('config', views.AIConfigView.as_view()),
    path('models', views.AIModelsView.as_view()),
    path('history', views.AIHistoryView.as_view()),
    path('chat', views.ChatView.as_view()),
]
