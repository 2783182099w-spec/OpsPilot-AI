from django.urls import path, re_path

from host import views

urlpatterns = [
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
