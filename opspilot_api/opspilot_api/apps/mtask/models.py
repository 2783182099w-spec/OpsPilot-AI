from django.db import models

from opspilot_api.utils.models import BaseModel
from django.conf import settings

class CmdTemplateCategory(BaseModel):
    class Meta:
        db_table = "cmd_template_category"
        verbose_name = "模板分类"
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.name

class CmdTemplate(BaseModel):
    category = models.ForeignKey('CmdTemplateCategory', on_delete=models.CASCADE,verbose_name='模板类别')
    cmd = models.TextField(verbose_name='模板内容')

    class Meta:
        db_table = "cmd_template"
        verbose_name = "指令模板"
        verbose_name_plural = verbose_name


class ExecutionTask(models.Model):
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='approved_tasks')
    approved_at = models.DateTimeField(null=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    command = models.TextField()
    host_ids = models.JSONField()
    status = models.CharField(max_length=20, default="pending")
    results = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)
