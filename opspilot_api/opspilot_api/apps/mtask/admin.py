from django.contrib import admin
from .models import CmdTemplate, CmdTemplateCategory, ExecutionTask

admin.site.register(CmdTemplate)
admin.site.register(CmdTemplateCategory)

# Register your models here.
