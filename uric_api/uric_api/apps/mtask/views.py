from django.conf import settings
from rest_framework import status
from rest_framework.generics import ListCreateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from host.models import Host
from uric_api.utils.key import PkeyManager
from uric_api.utils.ssh import SSHParamiko

from .models import CmdTemplate, CmdTemplateCategory
from .serializers import CmdTemplateCategorySerializer, CmdTemplateSerializer


class CmdExecView(APIView):
    """批量执行命令：逐台主机执行，单台失败不影响其他主机。"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        host_ids = request.data.get("host_ids") or []
        cmd = (request.data.get("cmd") or "").strip()
        if not host_ids or not cmd:
            return Response(
                {"error": "请选择主机并输入要执行的命令"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            private_key, _ = PkeyManager.get(settings.DEFAULT_KEY_NAME)
        except KeyError:
            return Response(
                {"error": "尚未生成全局 SSH 密钥对，请先在「资产管理」中成功添加一台主机"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = []
        for host in Host.objects.filter(id__in=host_ids):
            item = {
                "host_info": {
                    "id": host.id,
                    "name": host.name,
                    "ip_addr": host.ip_addr,
                    "port": host.port,
                },
                "res_code": None,
                "res_data": "",
            }
            try:
                ssh = SSHParamiko(host.ip_addr, host.port, host.username, private_key)
                if ssh.get_connected_client() is None:
                    item["res_data"] = "无法连接到该主机"
                else:
                    code, output = ssh.execute_cmd(cmd)
                    item["res_code"] = code
                    item["res_data"] = output
            except Exception as exc:  # noqa: BLE001 - 单台失败不应中断整批任务
                item["res_data"] = f"执行失败：{exc}"
            results.append(item)

        return Response(results)


class TemplateView(ListCreateAPIView):
    """指令模板：列表 / 新建"""

    queryset = CmdTemplate.objects.all().order_by("-id")
    serializer_class = CmdTemplateSerializer
    permission_classes = [IsAuthenticated]


class TemplateCategoryView(ListCreateAPIView):
    """指令模板类别：列表 / 新建"""

    queryset = CmdTemplateCategory.objects.all().order_by("-id")
    serializer_class = CmdTemplateCategorySerializer
    permission_classes = [IsAuthenticated]
