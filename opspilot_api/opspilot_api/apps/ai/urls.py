from django.urls import path

from . import views

urlpatterns = [
    path("history", views.AIHistoryView.as_view()),
    path("chat", views.ChatView.as_view()),
]
