from django.urls import path, re_path

from host import views
from .trust import HostTrustView
from .operations import AssetChangesView, FindingsView
from .files import FilesView
from .session_records import SessionRecordsView

urlpatterns = [
    path('sessions', SessionRecordsView.as_view()),
    path('sessions/<int:pk>', SessionRecordsView.as_view()),
    path('findings', FindingsView.as_view()),
    path('findings/<int:pk>', FindingsView.as_view()),
    path('<int:pk>/changes', AssetChangesView.as_view()),
    path('<int:pk>/files', FilesView.as_view()),
    path('<int:pk>/trust', HostTrustView.as_view()),
    path('export', views.HostExportView.as_view()),
    # host/
    path('', views.HostModelViewSet.as_view({"get": "list", "post": "create"})),
    path('category', views.HostCategoryListAPIView.as_view()),
    path('excel_host', views.HostExcelView.as_view()),
    path('<int:pk>/upload_key', views.HostKeyUploadView.as_view()),
    re_path(r'(?P<pk>\d+)', views.HostModelViewSet.as_view({
        "get": "retrieve",
        "put": "update",
        "patch": "partial_update",
        "delete": "destroy",
    })),
]
