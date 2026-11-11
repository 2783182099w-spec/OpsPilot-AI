from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from rest_framework.throttling import UserRateThrottle
from opspilot_api.utils.redaction import redact
from .models import Finding, FindingNote
from datetime import timedelta
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.utils import timezone
from .permissions import accessible_hosts


class FindingsThrottle(UserRateThrottle):
    rate = '60/minute'


class AssetChangesView(APIView):
    def get(self, request, pk):
        host = get_object_or_404(accessible_hosts(request.user), pk=pk)
        return Response(list(host.changes.order_by('-id').values('id','actor__username','changes','created_at')[:50]))


class FindingsView(APIView):
    throttle_classes = [FindingsThrottle]
    def visible(self, user):
        queryset = Finding.objects.select_related('host')
        return queryset if user.is_superuser else queryset.filter(Q(owner=user) | Q(source__in=['monitor','service']), host__in=accessible_hosts(user))

    def get(self, request, pk=None):
        queryset = self.visible(request.user)
        if pk is not None:
            row = get_object_or_404(queryset, pk=pk)
            return Response({'id': row.id, 'title': row.title, 'evidence': row.evidence, 'signal':row.signal, 'manual_required':row.manual_required, 'environment':row.host.environment if row.host else '', 'business':row.host.business if row.host else '', 'state': row.state, 'assignee': row.assignee, 'host_id': row.host_id, 'host_name': row.host.name if row.host else '资产已删除', 'severity': row.severity, 'source':row.source, 'condition':row.condition, 'occurrences':row.occurrences, 'recovered_at':row.recovered_at, 'silenced_until':row.silenced_until, 'source_task_id': row.source_task_id, 'first_seen': row.first_seen, 'last_seen': row.last_seen, 'notes': list(row.notes.order_by('id').values('actor__username','text','created_at')[:100])})
        for field in ('source','condition'):
            if request.query_params.get(field):
                queryset = queryset.filter(**{field:request.query_params[field]})
        if request.query_params.get('host_id','').isdigit():
            queryset = queryset.filter(host_id=int(request.query_params['host_id']))
        for field in ('environment','business'):
            value = request.query_params.get(field,'').strip()[:100]
            if value:
                queryset = queryset.filter(**{f'host__{field}' + ('__icontains' if field=='business' else ''):value})
        state = request.query_params.get('state')
        if state:
            queryset = queryset.filter(state=state)
        severity = request.query_params.get('severity')
        search = request.query_params.get('search', '')[:100]
        if search:
            queryset = queryset.filter(Q(title__icontains=search) | Q(host__name__icontains=search) | Q(assignee__icontains=search))
        if request.query_params.get('manual') == '1':
            queryset = queryset.filter(manual_required=True)
        summary_rows = queryset
        now = timezone.now()
        inbox = Q(manual_required=True) | (~Q(state='resolved') & ~Q(condition='recovered'))
        recovered = Q(condition='recovered', recovered_at__gte=now-timedelta(hours=24))
        bucket = request.query_params.get('bucket')
        if bucket == 'inbox':
            queryset = queryset.filter(inbox)
        elif bucket == 'active':
            queryset = queryset.filter(condition='active')
        elif bucket == 'recovered':
            queryset = queryset.filter(recovered)
        if severity:
            queryset = queryset.filter(severity=severity)
        fields = ('id','host_id','host__name','host__environment','host__business','title','severity','state','assignee','last_seen','source_task_id','source','condition','occurrences','recovered_at','silenced_until','manual_required')
        if request.query_params.get('sort') == 'priority':
            queryset = queryset.annotate(priority=Case(
                When(severity='critical', then=Value(0)), When(severity='warning', then=Value(1)),
                default=Value(2), output_field=IntegerField(),
            )).order_by('-manual_required', 'priority', '-last_seen', '-pk')
        else:
            queryset = queryset.order_by('-last_seen', '-pk')
        rows = queryset.values(*fields)
        if 'page' in request.query_params:
            from rest_framework.pagination import PageNumberPagination
            paginator = PageNumberPagination()
            paginator.page_size = 20
            result = paginator.get_paginated_response(paginator.paginate_queryset(rows,request))
            if request.query_params.get('summary') == '1':
                result.data['summary'] = list(summary_rows.order_by().values('severity').annotate(total=Count('pk')))
            if request.query_params.get('overview') == '1':
                result.data['overview'] = summary_rows.aggregate(
                    inbox=Count('pk', filter=inbox),
                    critical=Count('pk', filter=Q(condition='active', severity='critical')),
                    active=Count('pk', filter=Q(condition='active')),
                    recovered=Count('pk', filter=recovered),
                )
                result.data['updated_at'] = now
            return result
        return Response(list(rows[:200]))

    @transaction.atomic
    def patch(self, request, pk):
        row = get_object_or_404(self.visible(request.user).select_related(None).select_for_update(), pk=pk)
        state = request.data.get('state', row.state)
        assignee = request.data.get('assignee', row.assignee)
        note = request.data.get('note', '')
        if state not in ('open','acknowledged','processing','resolved') or not isinstance(assignee, str) or len(assignee) > 100 or not isinstance(note, str) or len(note) > 2000:
            raise ValidationError('状态、负责人或处理记录格式不合法')
        silence = request.data.get('silence_minutes')
        if silence is not None and (type(silence) is not int or not 0 <= silence <= 10080):
            raise ValidationError('静默时间为 0～10080 分钟')
        with transaction.atomic():
            previous = row.state
            handoff = request.data.get('ack_follow_up') is True and row.manual_required
            if handoff:
                row.manual_required = False
                if state == 'open':
                    state = 'processing'
                assignee = assignee or request.user.username
                note = '人工接手。' + note
            row.state, row.assignee = state, assignee
            if silence is not None:
                row.silenced_until = timezone.now()+timedelta(minutes=silence) if silence else None
            row.save(update_fields=['state','assignee','silenced_until','manual_required'])
            FindingNote.objects.create(finding=row, actor=request.user, text=redact(f'{previous} → {state}；负责人：{assignee}；静默分钟：{silence}；{note}')[:2000])
        return Response({'message': '处理记录已保存'})

    def post(self, request):
        from .permissions import require_hosts
        host_id = request.data.get('host_id')
        host = require_hosts(request.user, [host_id])[0]
        event_id, title, evidence = request.data.get('event_id'), request.data.get('title'), request.data.get('evidence', '')
        import re
        if not isinstance(event_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', event_id) or not isinstance(title, str) or not 1 <= len(title) <= 200 or not isinstance(evidence, str) or len(evidence) > 6000:
            raise ValidationError('请提供有效的 event_id、title、evidence')
        state = request.data.get('state', 'open')
        if state not in ('open','resolved'):
            raise ValidationError('外部告警状态仅支持 open、resolved')
        key = f'external:{request.user.id}:{host.id}:{event_id}'
        if not Finding.objects.filter(dedupe_key=key).exists() and Finding.objects.filter(owner=request.user).count() >= 2000:
            raise ValidationError('异常记录达到上限，请先归档清理')
        row, created = Finding.objects.update_or_create(dedupe_key=key, defaults={'owner':request.user,'host':host,'title':title,'evidence':redact(evidence),'state':state,'source':'external','condition':'recovered' if state=='resolved' else 'active'})
        return Response({'id':row.id,'state':row.state},status=201 if created else 200)
