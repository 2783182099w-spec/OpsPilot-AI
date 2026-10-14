from django.contrib import admin
from .models import Host, HostCategory, SSHSession


@admin.register(Host)
class HostAdmin(admin.ModelAdmin):
    list_display = ('name', 'ip_addr', 'port', 'username')
    search_fields = ('name', 'ip_addr')
    filter_horizontal = ('users',)


admin.site.register(HostCategory)
admin.site.register(SSHSession)

# Register your models here.
