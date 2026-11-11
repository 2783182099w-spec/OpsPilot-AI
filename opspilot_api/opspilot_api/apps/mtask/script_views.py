from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from host.permissions import require_hosts
from opspilot_api.utils.redaction import redact
from .models import JobScript, ScriptRevision
from .scripts import validate_script,preview_token


def script_data(row,source=False):
    data={'id':row.id,'name':row.name,'description':row.description,'language':row.language,'version':row.version,'updated_at':row.updated_at}
    if source:
        data['source']=row.source
    return data


class ScriptPreviewView(APIView):
    def post(self,request):
        ids=request.data.get('host_ids')
        require_hosts(request.user,ids)
        payload=validate_script(request.data.get('script'))
        return Response({'script':{**payload,'source':redact(payload['source']),'args':[redact(a) for a in payload['args']]},'host_count':len(set(ids)),'preview_token':preview_token(payload,ids),'message':'仅检查文本、参数与权限，尚未连接或执行；确认后生成不可变任务快照'})


class ScriptLibraryView(APIView):
    def get(self,request,pk=None):
        rows=JobScript.objects.filter(is_deleted=False)
        if pk is None:
            return Response([script_data(row) for row in rows.defer('source').order_by('-updated_at')[:200]])
        row=get_object_or_404(rows,pk=pk)
        return Response({'script':script_data(row,True),'revisions':[{'version':r.version,'language':r.language,'source':r.source,'description':r.description,'created_at':r.created_at} for r in row.revisions.order_by('-version')[:30]]})

    def post(self,request,pk=None):
        if not request.user.is_superuser:
            raise PermissionDenied('脚本库维护仅限管理员；普通用户可以提交脚本任务申请审批')
        with transaction.atomic():
            row=get_object_or_404(JobScript.objects.select_for_update(),pk=pk,is_deleted=False) if pk else None
            if row and request.data.get('expected_version')!=row.version:
                raise ValidationError('脚本版本已变化，请刷新后编辑')
            if not row and JobScript.objects.filter(is_deleted=False).count()>=200:
                raise ValidationError('脚本库最多 200 个有效脚本')
            payload=validate_script(request.data)
            description=request.data.get('description','')
            if not isinstance(description,str) or len(description)>1000:
                raise ValidationError('用途说明最多 1000 字')
            if row:
                ScriptRevision.objects.get_or_create(script=row,version=row.version,defaults={'language':row.language,'source':row.source,'description':row.description})
                row.version+=1
            else:
                row=JobScript(owner=request.user)
            row.name,row.language,row.source,row.description=payload['name'],payload['language'],payload['source'],description
            row.save()
        return Response(script_data(row),status=200 if pk else 201)

    patch=post

    def delete(self,request,pk):
        if not request.user.is_superuser:
            raise PermissionDenied('脚本归档仅限管理员')
        row=get_object_or_404(JobScript,pk=pk,is_deleted=False)
        row.is_deleted=True
        row.save(update_fields=['is_deleted'])
        return Response(status=204)
