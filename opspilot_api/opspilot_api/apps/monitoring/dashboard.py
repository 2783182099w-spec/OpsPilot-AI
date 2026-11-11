from collections import Counter
from datetime import timedelta
from django.db.models import Count
from django.db.models.functions import TruncHour
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from host.permissions import accessible_hosts
from host.operations import FindingsView
from mtask.task_views import visible_tasks
from .views import OverviewView, PlatformHealthView
from .prometheus import fleet_history,Unavailable


class DashboardView(APIView):
    def get(self,request):
        now=timezone.now()
        assets=accessible_hosts(request.user)
        overview=OverviewView().get(request).data
        rows=overview['rows']
        active=[r for r in rows if r['state'] in ('up','partial')]
        targets=[r['id'] for r in rows if r['target'] and r['target']['enabled']]
        trend={'cpu':[],'memory':[]}
        error=overview['error']
        if not error:
            try:
                trend=fleet_history(targets)
            except Unavailable as exc:
                error=str(exc)
        alerts=FindingsView().visible(request.user)
        tasks=visible_tasks(request.user)
        recent=tasks.filter(created_at__gte=now-timedelta(hours=24))
        outcomes=Counter(recent.values_list('status',flat=True))
        ended=sum(outcomes.get(k,0) for k in ('completed','failed','interrupted'))
        top={}
        for metric in ('cpu','memory','disk'):
            valid=[r for r in active if r['metrics'].get(metric) is not None]
            top[metric]=[{'id':r['id'],'name':r['name'],'value':r['metrics'][metric]} for r in sorted(valid,key=lambda r:r['metrics'][metric],reverse=True)[:5]]
        buckets=list(recent.annotate(hour=TruncHour('created_at')).values('hour').annotate(count=Count('id')).order_by('hour'))
        return Response({
            'generated_at':now,'scope':{'asset_total':assets.count(),'displayed':len(rows),'limit':300,'monitored':len(targets),'sampling_interval':60,'refresh_seconds':30},
            'collection':dict(Counter(r['state'] for r in rows)),
            'asset_groups':list(assets.values('environment').annotate(count=Count('id')).order_by('environment')),
            'alerts':{'active':alerts.filter(condition='active').count(),'unknown':alerts.filter(condition='unknown').count(),'critical_active':alerts.filter(condition='active',severity='critical').count(),'unhandled':alerts.exclude(condition='recovered').filter(state__in=['open','acknowledged']).count()},
            'recent_alerts':list(alerts.exclude(condition='recovered').order_by('-last_seen').values('id','host_id','host__name','title','severity','condition','last_seen')[:6]),
            'tasks':{'total_24h':recent.count(),'ended_24h':ended,'success_24h':outcomes.get('completed',0),'success_rate':round(outcomes.get('completed',0)/ended*100,1) if ended else None,'pending':tasks.filter(status='pending').count(),'running':tasks.filter(status='running').count(),'awaiting_approval':tasks.filter(status='awaiting_approval').count(),'outcomes':dict(outcomes),'hourly':[{'hour':r['hour'].strftime('%H:%M'),'count':r['count']} for r in buckets]},
            'recent_tasks':list(tasks.order_by('-id').values('id','kind','status','completed_hosts','created_at')[:6]),
            'top':top,'trend':trend,'error':error,'health':PlatformHealthView().get(request).data,
            'samples':{'cpu':sum(r['metrics'].get('cpu') is not None for r in active),'memory':sum(r['metrics'].get('memory') is not None for r in active)},
        })
