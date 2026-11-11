from collections import Counter
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from .task_views import visible_tasks


LABELS = {'pass':'通过','warning':'异常','unknown':'待确认','review':'需人工复核','info':'信息取证','success':'执行成功','failed':'执行失败','timeout':'超时','interrupted':'中断','cancelled':'取消','queued':'排队','running':'执行中'}


def conclusion(row):
    states = {c.get('state') for c in row.checks}
    if row.status != 'success' or not states:
        return '未完成有效检查'
    if 'warning' in states:
        return '发现异常'
    if states & {'unknown','review'}:
        return '存在待确认或待复核项'
    if 'pass' not in states:
        return '仅采集到信息，不能判断健康'
    return '本次检查范围内未发现异常'


def comparison(task, user):
    previous = visible_tasks(user).filter(id__lt=task.id,kind=task.kind,options__profile=task.options.get('profile','health'),status__in=['completed','failed']).order_by('-id')
    # Compare exact asset sets only. Do not compare unrelated batches.
    previous = next((t for t in previous[:100] if sorted(t.host_ids)==sorted(task.host_ids)),None)
    if not previous:
        return {'previous_id':None,'changes':[],'message':'尚无相同主机集合与方案的历史任务'}
    old = {(r.host_id_snapshot,c.get('id'),c.get('title')):c for r in previous.host_results.all() for c in r.checks}
    changes = []
    current_keys = set()
    for row in task.host_results.all():
        for check in row.checks:
            key = (row.host_id_snapshot,check.get('id'),check.get('title'))
            current_keys.add(key)
            before = old.get(key)
            if before is None or before.get('state') != check.get('state'):
                changes.append({'host':row.host_info.get('name',''),'title':check.get('title',''),'before':LABELS.get(before.get('state'),'未知') if before else '新增检查项','after':LABELS.get(check.get('state'),check.get('state'))})
    for key,check in old.items():
        if key not in current_keys:
            changes.append({'host':str(key[0]),'title':check.get('title',''),'before':LABELS.get(check.get('state'),check.get('state')),'after':'本次未返回，不能判断恢复'})
    return {'previous_id':previous.id,'changes':changes[:1000],'message':'仅比较检查结论变化，不将证据数值波动等同于配置变更'}


class HealthReportView(APIView):
    def get(self, request, pk):
        task = get_object_or_404(visible_tasks(request.user).select_related('owner'),pk=pk,kind='inspection')
        rows = list(task.host_results.order_by('id'))
        counts = Counter(c.get('state','unknown') for r in rows for c in r.checks)
        masked = request.query_params.get('mask') == '1'
        hosts = []
        for index,row in enumerate(rows,1):
            hosts.append({'name':f'主机 {index}' if masked else row.host_info.get('name','未知'),'address':'已隐藏' if masked else row.host_info.get('ip_addr','未知'),'status':LABELS.get(row.status,row.status),'conclusion':conclusion(row),'started_at':row.started_at,'finished_at':row.finished_at,'output':row.output,'checks':[{**c,'label':LABELS.get(c.get('state'),c.get('state'))} for c in row.checks]})
        complete = task.status in ('completed','failed') and len(rows)==len(task.host_ids) and all(r.status=='success' and r.checks for r in rows)
        clean = complete and all(conclusion(r)=='本次检查范围内未发现异常' for r in rows)
        summary = '本次所列检查范围内未发现异常' if clean else '存在异常、未完成或待复核项目，请查看明细后判断'
        diff = comparison(task,request.user)
        if masked:
            for item in diff['changes']:
                item['host'] = '已隐藏'
        html = render_to_string('mtask/health_report.html', {'task':task,'hosts':hosts,'summary':summary,'counts':[{'label':LABELS.get(k,k),'value':v} for k,v in counts.items()],'comparison':diff,'masked':masked})
        response = HttpResponse(html,content_type='text/html; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="health-report-{task.id}.html"'
        response['X-Content-Type-Options']='nosniff'
        response['Content-Security-Policy']="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
        return response


class ComparisonView(APIView):
    def get(self, request, pk):
        task = get_object_or_404(visible_tasks(request.user),pk=pk,kind='inspection')
        return Response(comparison(task,request.user))
