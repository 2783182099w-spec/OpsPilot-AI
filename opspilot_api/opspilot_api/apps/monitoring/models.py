from django.db import models
from django.conf import settings


class MonitorTarget(models.Model):
    host = models.OneToOneField('host.Host', on_delete=models.CASCADE, related_name='monitor_target')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    endpoint = models.CharField(max_length=255)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class MonitorRule(models.Model):
    name = models.CharField(max_length=100)
    metric = models.CharField(max_length=30)
    threshold = models.FloatField(default=90)
    duration = models.PositiveIntegerField(default=300)
    recovery_seconds = models.PositiveIntegerField(default=120)
    severity = models.CharField(max_length=20, default='warning')
    enabled = models.BooleanField(default=True)


class MonitorHealth(models.Model):
    name = models.CharField(max_length=20, primary_key=True, default='default')
    heartbeat_at = models.DateTimeField(null=True)
    last_success_at = models.DateTimeField(null=True)
    error = models.CharField(max_length=500, blank=True)
    config_error = models.CharField(max_length=500, blank=True)


class ServiceCheck(models.Model):
    host = models.ForeignKey('host.Host', on_delete=models.CASCADE)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    tree = models.ForeignKey('mtask.DiagnosticTree', null=True, on_delete=models.PROTECT)
    name = models.CharField(max_length=100)
    unit = models.CharField(max_length=136, blank=True)
    port = models.PositiveIntegerField()
    path = models.CharField(max_length=151)
    mode = models.CharField(max_length=12, default='monitor')
    escalate_on_failure = models.BooleanField(default=True)
    severity = models.CharField(max_length=20, default='warning')
    enabled = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    state = models.CharField(max_length=12, default='unknown')
    last_checked_at = models.DateTimeField(null=True)
    last_evidence = models.CharField(max_length=120, blank=True)
    failures = models.PositiveSmallIntegerField(default=0)
    successes = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['host','port','path'], name='unique_service_check_endpoint')]
