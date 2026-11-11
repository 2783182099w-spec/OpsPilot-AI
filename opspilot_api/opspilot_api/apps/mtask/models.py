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
    version = models.PositiveIntegerField(default=1)
    parameters = models.JSONField(default=list, blank=True)
    category = models.ForeignKey('CmdTemplateCategory', on_delete=models.CASCADE,verbose_name='模板类别')
    cmd = models.TextField(verbose_name='模板内容')

    class Meta:
        db_table = "cmd_template"
        verbose_name = "指令模板"
        verbose_name_plural = verbose_name


class ExecutionTask(models.Model):
    kind = models.CharField(max_length=20, default="command")
    options = models.JSONField(default=dict, blank=True)
    rollout_phase = models.CharField(max_length=20, default="initial")
    stop_reason = models.CharField(max_length=300, blank=True)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='released_jobs')
    released_at = models.DateTimeField(null=True)
    cancel_requested = models.BooleanField(default=False)
    worker_token = models.CharField(max_length=64, blank=True)
    started_at = models.DateTimeField(null=True)
    completed_hosts = models.PositiveIntegerField(default=0)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='approved_tasks')
    approved_at = models.DateTimeField(null=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    command = models.TextField()
    host_ids = models.JSONField()
    status = models.CharField(max_length=20, default="pending", db_index=True)
    results = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)


class HostResult(models.Model):
    task = models.ForeignKey(ExecutionTask, on_delete=models.CASCADE, related_name="host_results")
    host_id_snapshot = models.PositiveIntegerField()
    host_info = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default="queued")
    stages = models.JSONField(default=list)
    failure_code = models.CharField(max_length=40, blank=True)
    may_have_run = models.BooleanField(null=True)
    batch = models.PositiveSmallIntegerField(default=0)
    exit_code = models.IntegerField(null=True)
    output = models.TextField(blank=True)
    checks = models.JSONField(default=list)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["task", "host_id_snapshot"], name="unique_task_host_result")]


class WorkerLease(models.Model):
    name = models.CharField(max_length=30, primary_key=True)
    token = models.CharField(max_length=64, blank=True)
    expires_at = models.DateTimeField()
    heartbeat_at = models.DateTimeField()


class TaskSchedule(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    name = models.CharField(max_length=100)
    host_ids = models.JSONField()
    kind = models.CharField(max_length=20, default="inspection")
    options = models.JSONField(default=dict)
    interval_minutes = models.PositiveIntegerField(default=60)
    enabled = models.BooleanField(default=False)
    next_run = models.DateTimeField()
    last_task = models.ForeignKey(ExecutionTask, null=True, on_delete=models.SET_NULL)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        indexes = [models.Index(fields=['enabled','next_run'], name='schedule_due_idx')]


class TemplateRevision(models.Model):
    template = models.ForeignKey(CmdTemplate, on_delete=models.CASCADE, related_name="revisions")
    version = models.PositiveIntegerField()
    command = models.TextField()
    parameters = models.JSONField(default=list)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["template", "version"], name="unique_template_revision")]


class JobScript(models.Model):
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=1000, blank=True)
    language = models.CharField(max_length=10)
    source = models.TextField()
    version = models.PositiveIntegerField(default=1)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    is_deleted = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


class ScriptRevision(models.Model):
    script = models.ForeignKey(JobScript,on_delete=models.CASCADE,related_name='revisions')
    version = models.PositiveIntegerField()
    language = models.CharField(max_length=10)
    source = models.TextField()
    description = models.CharField(max_length=1000,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['script','version'],name='unique_script_revision')]

class ManagedJob(models.Model):
    definition = models.JSONField()
    version = models.PositiveIntegerField(default=1)
    state = models.CharField(max_length=16, default='draft', db_index=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)


class ManagedJobRevision(models.Model):
    job = models.ForeignKey(ManagedJob, on_delete=models.CASCADE, related_name='revisions')
    version = models.PositiveIntegerField()
    definition = models.JSONField()
    state = models.CharField(max_length=16)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['job','version'], name='unique_managed_job_revision')]


class ReleaseRun(models.Model):
    task = models.OneToOneField(ExecutionTask, on_delete=models.PROTECT, related_name='release_run')
    application = models.CharField(max_length=40, db_index=True)
    artifact = models.JSONField()
    rollback_of = models.ForeignKey('self', null=True, on_delete=models.PROTECT)


class DiagnosticTree(models.Model):
    name = models.CharField(max_length=100)
    version = models.PositiveIntegerField(default=1)
    definition = models.JSONField()


class DiagnosticRun(models.Model):
    task = models.OneToOneField(ExecutionTask, on_delete=models.PROTECT, related_name='diagnostic_run')
    finding = models.ForeignKey('host.Finding', on_delete=models.PROTECT, related_name='diagnostic_runs')
    occurrence = models.PositiveIntegerField()
    mode = models.CharField(max_length=12)
    escalated_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=['finding', 'mode', 'occurrence'])]


class DiagnosticTreeRevision(models.Model):
    tree = models.ForeignKey(DiagnosticTree, on_delete=models.CASCADE, related_name='revisions')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    version = models.PositiveIntegerField()
    name = models.CharField(max_length=100)
    definition = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['tree','version'], name='unique_diagnostic_tree_version')]


class DiagnosticPolicy(models.Model):
    name = models.CharField(max_length=100)
    enabled = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    config = models.JSONField()
    next_scan_at = models.DateTimeField(null=True)


class DiagnosticWorkflowRun(models.Model):
    policy = models.ForeignKey(DiagnosticPolicy, on_delete=models.PROTECT)
    finding = models.ForeignKey('host.Finding', on_delete=models.PROTECT, related_name='diagnostic_workflows')
    occurrence = models.PositiveIntegerField()
    snapshot = models.JSONField()
    diagnosis = models.OneToOneField(DiagnosticRun, null=True, on_delete=models.PROTECT, related_name='+')
    repair = models.OneToOneField(DiagnosticRun, null=True, on_delete=models.PROTECT, related_name='+')
    status = models.CharField(max_length=16, default='diagnosing', db_index=True)
    branch = models.CharField(max_length=30, blank=True)
    reason = models.CharField(max_length=250, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['finding','occurrence'], name='unique_finding_workflow_occurrence')]


class ReleaseApplication(models.Model):
    application = models.SlugField(max_length=40, unique=True)
    target = models.JSONField()
    enabled = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    ci_digest = models.CharField(max_length=64, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)


class ReleaseArtifact(models.Model):
    application = models.ForeignKey(ReleaseApplication, on_delete=models.PROTECT, related_name='artifacts')
    version = models.CharField(max_length=80)
    url = models.URLField(max_length=220)
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['application','version'],name='unique_release_artifact_version')]
