from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from host.permissions import require_hosts
from opspilot_api.utils.redaction import redact
from .managed import prepare, signed_plan, check_plan, validate_definition
from .models import ManagedJob, ManagedJobRevision
from .task_views import enqueue, serialize


def public_definition(definition):
    return {key:value for key,value in definition.items() if key!='source'}


def job_data(job, editor=False):
    return {'id':job.id,'version':job.version,'state':job.state,'owner':job.owner.username,
            'updated_at':job.updated_at,'definition':job.definition if editor else public_definition(job.definition)}


class ManagedJobsView(APIView):
    def get(self,request,pk=None):
        jobs=ManagedJob.objects.select_related('owner').order_by('-updated_at')
        if not request.user.is_superuser:
            jobs=jobs.filter(state='published')
        if pk is None:
            return Response([job_data(job) for job in jobs[:200]])
        job=get_object_or_404(jobs,pk=pk)
        data=job_data(job,request.user.is_superuser)
        if request.user.is_superuser:
            data['revisions']=[{'version':r.version,'state':r.state,'actor':r.actor.username,'created_at':r.created_at,'definition':r.definition}
                for r in job.revisions.select_related('actor').order_by('-version')[:30]]
        return Response(data)

    @transaction.atomic
    def post(self,request,pk=None):
        if not request.user.is_superuser:
            raise PermissionDenied('仅管理员维护发布作业')
        if pk is None:
            if ManagedJob.objects.count()>=200:
                raise ValidationError('作业库最多保留 200 个作业')
            definition=validate_definition(request.data.get('definition'))
            job=ManagedJob.objects.create(owner=request.user,definition=definition)
        else:
            job=get_object_or_404(ManagedJob.objects.select_for_update(),pk=pk)
            version=request.data.get('version')
            if type(version) is not int or version!=job.version:
                raise ValidationError('版本已变化，请刷新后重试')
            action=request.data.get('action')
            if action=='save':
                job.definition=validate_definition(request.data.get('definition'))
                job.state='draft'
            elif action=='publish' and job.state!='published':
                job.definition=validate_definition(job.definition)
                job.state='published'
            elif action=='disable' and job.state=='published':
                job.state='disabled'
            else:
                raise ValidationError('作业状态不支持此操作')
            job.version+=1
            job.save()
        ManagedJobRevision.objects.create(job=job,version=job.version,state=job.state,
                                          definition=job.definition,actor=request.user)
        return Response(job_data(job,True),status=201 if pk is None else 200)


class ManagedRunView(APIView):
    def post(self,request,pk,action):
        if action not in ('preview','run'):
            raise ValidationError('未知作业操作')
        with transaction.atomic():
            jobs=ManagedJob.objects.select_for_update() if action=='run' else ManagedJob.objects
            job=get_object_or_404(jobs,pk=pk,state='published')
            ids=request.data.get('host_ids')
            hosts_by_id={h.id:h for h in require_hosts(request.user,ids)}
            if len(ids)!=len(hosts_by_id):
                raise ValidationError('目标主机不可重复')
            source,options=prepare(job,[hosts_by_id[i] for i in ids],request.data.get('parameters',{}),request.data.get('reason',''))
            approval=options['managed']['protected'] or not request.user.is_superuser
            if action=='preview':
                public={**options, 'args':[redact(a) for a in options['args']],
                        'managed':{**options['managed'],'reason':redact(options['managed']['reason']),
                                   'parameters':{k:redact(v) for k,v in options['managed']['parameters'].items()}}}
                return Response({'token':signed_plan(request.user.id,source,options),'source':redact(source),
                                 'options':public,'approval_required':approval})
            if request.data.get('confirmed') is not True:
                raise ValidationError('请确认作业计划')
            check_plan(request.data.get('token'),request.user.id,source,options)
            return Response(serialize(enqueue(request.user,ids,'script',source,options,approval)),status=202)
