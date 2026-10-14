from django.conf import settings
from rest_framework import serializers

from opspilot_api.utils.key import PkeyManager
from opspilot_api.utils.ssh import SSHParamiko

from . import models


class HostCategoryModelSerializer(serializers.ModelSerializer):
    """主机类别序列化器"""

    class Meta:
        model = models.HostCategory
        fields = ["id", "name"]


class HostModelSerializer(serializers.ModelSerializer):
    """主机序列化器"""

    category_name = serializers.CharField(source="category.name", read_only=True)
    password = serializers.CharField(max_length=256, required=False, allow_blank=True, write_only=True, label="登录密码")
    tags = serializers.ListField(child=serializers.CharField(max_length=30), max_length=10, required=False)

    class Meta:
        model = models.Host
        fields = [
            "id",
            "category",
            "category_name",
            "name",
            "ip_addr",
            "port",
            "description",
            "username",
            "password",
            "tags",
            "asset_status",
        ]

    @staticmethod
    def ensure_global_key():
        """确保全局 SSH 密钥对存在，返回 (private_key, public_key)。"""
        try:
            return PkeyManager.get(settings.DEFAULT_KEY_NAME)
        except KeyError:
            private_key, public_key = SSHParamiko.gen_key()
            PkeyManager.set(settings.DEFAULT_KEY_NAME, private_key, public_key, "ssh全局秘钥对")
            return private_key, public_key

    def validate(self, attrs):
        if not getattr(settings, "HOST_SSH_VALIDATE", True):
            return attrs
        # 批量导入时可以跳过逐台的连通性校验，避免每行都发起一次 SSH 连接
        if self.context.get("skip_ssh_validate"):
            self.ensure_global_key()
            return attrs

        strict = getattr(settings, "HOST_SSH_VALIDATE", True)
        if not attrs.get("password"):
            if self.instance:
                return attrs
            raise serializers.ValidationError("首次验证需要主机密码")
        def field(name):
            return attrs.get(name, getattr(self.instance, name, None))
        ssh = SSHParamiko(
            field("ip_addr"),
            field("port"),
            field("username"),
            password=str(attrs.get("password")),
        )
        client = ssh.get_connected_client()

        if client is None:
            if strict:
                raise serializers.ValidationError(
                    "主机认证信息错误：无法使用该账号密码连接到目标主机。"
                )
            # 本地开发：允许先录入暂时连不上的主机，但仍保证全局密钥对存在
            self.ensure_global_key()
            return attrs

        try:
            _, public_key = self.ensure_global_key()
            ssh.upload_key(public_key)
        except Exception as exc:  # noqa: BLE001
            if strict:
                raise serializers.ValidationError(f"公钥下发失败：{exc}")
        finally:
            ssh.close()
        return attrs

    def create(self, validated_data):
        # 密码只用于首次校验，不落库
        validated_data.pop("password", None)
        host = super().create(validated_data)
        request = self.context.get("request")
        if request:
            host.users.add(request.user)
        return host

    def validate_port(self, value):
        if not 1 <= value <= 65535:
            raise serializers.ValidationError("端口必须在 1 到 65535 之间")
        return value

    def update(self, instance, validated_data):
        validated_data.pop("password", None)
        return super().update(instance, validated_data)
