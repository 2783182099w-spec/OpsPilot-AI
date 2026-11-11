from django.urls import path
from .profile import ProfileView
from .login import AccountLoginView, AccountRefreshView, AccountVerifyView
from .management import AccountListView,PasswordView,AccountAuditView

urlpatterns = [
    path('accounts/',AccountListView.as_view()),
    path('accounts/<int:pk>/',AccountListView.as_view()),
    path('accounts/<int:pk>/password/',PasswordView.as_view()),
    path('audit/',AccountAuditView.as_view()),
    path('me/password/',PasswordView.as_view()),
    path('me/', ProfileView.as_view()),
    path('login/', AccountLoginView.as_view(), name='token_obtain_pair'),
    path('api/', AccountVerifyView.as_view(), name='token_verify'),
    path('refresh/', AccountRefreshView.as_view(), name='token_refresh'),
]
