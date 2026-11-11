from django.db import models

from opspilot_api.utils.models import BaseModel
from users.models import User


class HostCategory(BaseModel):
    """主机类别"""

    class Meta:
        db_table = "host_category"
        verbose_name = "主机类别"
        verbose_name_plural = verbose_name  # 取消提示文字中关于英文复数+s的情况


class Host(BaseModel):
    environment = models.CharField(max_length=20, default="test", choices=[("test", "测试"), ("staging", "验收"), ("production", "生产"), ("other", "其他")])
    business = models.CharField(max_length=100, blank=True)
    owner_label = models.CharField(max_length=100, blank=True)
    os_name = models.CharField(max_length=150, blank=True)
    purpose = models.CharField(max_length=300, blank=True)
    connection_state = models.CharField(max_length=20, default="unknown")
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_check_note = models.CharField(max_length=300, blank=True)
    tags = models.JSONField(default=list, blank=True)
    asset_status = models.CharField(max_length=20, default="unknown", choices=[("unknown", "未知"), ("active", "使用中"), ("maintenance", "维护中"), ("retired", "已下线")])
    # 真正在数据库中的字段实际上叫 category_id，而category则代表了关联的哪个分类模型对象
    category = models.ForeignKey('HostCategory', on_delete=models.DO_NOTHING, verbose_name='主机类别', related_name='hc',
                                 null=True, blank=True)
    ip_addr = models.CharField(blank=True, null=True, max_length=500, verbose_name='连接地址')
    port = models.IntegerField(verbose_name='端口')
    username = models.CharField(max_length=50, verbose_name='登录用户')
    users = models.ManyToManyField(User)

    class Meta:
        db_table = "host"
        verbose_name = "主机信息"
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.name + ':' + self.ip_addr


# 全局密钥和共钥，所有用户都使用这个一对
class PkeyModel(BaseModel):
    name = models.CharField(max_length=32, unique=True)  # 名称
    private = models.TextField(verbose_name="私钥")
    public = models.TextField(verbose_name="公钥")

    def __repr__(self):
        return f'<Pkey {self.name}>'


class SSHSession(models.Model):
    transcript = models.TextField(blank=True)
    transcript_truncated = models.BooleanField(default=False)
    recorded_bytes = models.PositiveIntegerField(default=0)
    host_label = models.CharField(max_length=200, blank=True)

    owner = models.ForeignKey(User, on_delete=models.PROTECT)
    host = models.ForeignKey(Host, null=True, on_delete=models.SET_NULL)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=20, default="connecting")


class AssetChange(models.Model):
    host = models.ForeignKey(Host, on_delete=models.CASCADE, related_name="changes")
    actor = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    changes = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class Finding(models.Model):
    host = models.ForeignKey(Host, null=True, on_delete=models.SET_NULL)
    owner = models.ForeignKey(User, on_delete=models.PROTECT)
    dedupe_key = models.CharField(max_length=200, unique=True)
    title = models.CharField(max_length=200)
    source = models.CharField(max_length=20, default='inspection')
    condition = models.CharField(max_length=20, default='unknown')
    occurrences = models.PositiveIntegerField(default=1)
    recovered_at = models.DateTimeField(null=True)
    silenced_until = models.DateTimeField(null=True)
    source_task = models.ForeignKey("mtask.ExecutionTask", null=True, on_delete=models.SET_NULL)
    severity = models.CharField(max_length=20, default="warning")
    signal = models.JSONField(default=dict, blank=True)
    manual_required = models.BooleanField(default=False)
    evidence = models.TextField(blank=True)
    state = models.CharField(max_length=20, default="open")
    assignee = models.CharField(max_length=100, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)


class FindingNote(models.Model):
    finding = models.ForeignKey(Finding, on_delete=models.CASCADE, related_name="notes")
    actor = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    text = models.CharField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)


class FileTransfer(models.Model):
    owner = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    host = models.ForeignKey(Host, null=True, on_delete=models.SET_NULL)
    direction = models.CharField(max_length=20)
    path = models.CharField(max_length=1000)
    size = models.PositiveIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=20, default="failed")
    created_at = models.DateTimeField(auto_now_add=True)
