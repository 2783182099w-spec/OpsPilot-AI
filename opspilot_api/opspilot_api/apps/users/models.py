from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    platform_role = models.CharField(max_length=12, choices=[('operator','运维人员'),('observer','只读观察员')], default='operator')
    session_version = models.PositiveIntegerField(default=0)

    # 手机号允许为空，否则第二个不填手机号的用户会因唯一约束冲突而无法创建
    mobile = models.CharField(max_length=15, unique=True, null=True, blank=True, verbose_name='手机号码')
    # upload_to 表示上传文件的存储子路由，需要在settings配置中，配置上传文件的支持
    avatar = models.ImageField(upload_to='avatar', verbose_name='用户头像', null=True, blank=True)

    class Meta:
        db_table = 'uric_user'
        verbose_name = '用户信息'
        verbose_name_plural = verbose_name

# 命令行： python manage.py createsuperuser  yuan 123
# 代码：User.objects.create_user(username="yuan",password="123")
# 代码：User.objects.create_superuser(username="yuan",password="123")




class AccountAudit(models.Model):
    actor = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name='account_changes')
    target = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name='account_events')
    action = models.CharField(max_length=32)
    changes = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
