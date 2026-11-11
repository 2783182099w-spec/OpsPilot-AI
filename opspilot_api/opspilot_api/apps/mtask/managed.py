"""Versioned job definitions and frozen run manifests."""
import hashlib
import json
import re
from types import SimpleNamespace

from django.core import signing
from rest_framework.exceptions import ValidationError

from .scripts import validate_script
from .template_tools import render

ACTIVE = ['pending', 'running', 'awaiting_approval', 'awaiting_release']
ENVIRONMENTS = {'test', 'staging', 'production', 'other'}
UNIT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}\.service')


def bounded_int(value, lower, upper, label):
    if type(value) is not int or not lower <= value <= upper:
        raise ValidationError(f'{label}须为 {lower}～{upper} 的整数')
    return value


def clean_text(value, limit, label, required=False):
    if not isinstance(value, str) or len(value)>limit or (required and not value.strip()):
        raise ValidationError(f'{label}格式无效，最多 {limit} 字')
    return value.strip()


def param_value(field, value):
    label = field['label']
    if field['type'] == 'integer':
        if type(value) is not int:
            raise ValidationError(f'{label}须为整数')
        return bounded_int(value, field['min'], field['max'], label)
    if not isinstance(value, str) or len(value.encode('utf-8'))>200 or any(ord(c)<32 for c in value):
        raise ValidationError(f'{label}须为最多 200 字节的单行文本')
    if field['required'] and not value.strip():
        raise ValidationError(f'{label}不能为空')
    if field['type']=='enum' and value not in field['choices'] and not (not value and not field['required']):
        raise ValidationError(f'{label}不在允许选项中')
    return value


def schema(fields):
    if not isinstance(fields, list) or len(fields)>10:
        raise ValidationError('最多定义 10 个参数')
    result, names = [], set()
    for raw in fields:
        if not isinstance(raw, dict):
            raise ValidationError('参数定义须为对象')
        name = raw.get('name')
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,29}',name) or name in names or re.search('password|passwd|token|secret|key',name,re.I):
            raise ValidationError('参数名须唯一、使用字母数字下划线，不可用于传递凭据')
        kind = raw.get('type','text')
        required = raw.get('required',True)
        if kind not in ('text','integer','enum') or type(required) is not bool:
            raise ValidationError('参数类型或必填设置无效')
        field = {'name':name,'type':kind,'label':clean_text(raw.get('label') or name,50,'参数标签',True),'required':required}
        if kind=='integer':
            field['min']=bounded_int(raw.get('min',-1000000000),-1000000000,1000000000,'最小值')
            field['max']=bounded_int(raw.get('max',1000000000),field['min'],1000000000,'最大值')
        if kind=='enum':
            choices=raw.get('choices')
            if not isinstance(choices,list) or not 1<=len(choices)<=30 or any(not isinstance(v,str) or not v.strip() or len(v.encode('utf-8'))>200 or any(ord(c)<32 for c in v) for v in choices) or len(set(choices))!=len(choices):
                raise ValidationError('枚举须包含 1～30 个不同的非空单行选项')
            field['choices']=choices
        if 'default' in raw:
            field['default']=param_value(field,raw['default'])
        if kind=='integer' and not required and 'default' not in field:
            raise ValidationError('可选整数必须提供默认值')
        names.add(name)
        result.append(field)
    return result


def validate_definition(raw):
    if not isinstance(raw,dict):
        raise ValidationError('作业定义无效')
    name=clean_text(raw.get('name',''),100,'名称',True)
    kind=raw.get('kind')
    risk=raw.get('risk','change')
    if kind not in ('command','shell','python') or risk not in ('read_only','change'):
        raise ValidationError('作业类型或风险级别无效')
    environments=raw.get('environments',['test'])
    if not isinstance(environments,list) or not environments or any(not isinstance(x,str) or x not in ENVIRONMENTS for x in environments):
        raise ValidationError('请选择有效的目标环境')
    allowed=raw.get('allowed_host_ids',[])
    if not isinstance(allowed,list) or len(allowed)>300 or any(type(i) is not int or i<=0 for i in allowed):
        raise ValidationError('允许的主机名单无效')
    service=clean_text(raw.get('verify_service',''),136,'验证服务')
    if service and not UNIT.fullmatch(service):
        raise ValidationError('验证服务须是明确的 systemd .service 单元')
    if (risk=='change' or 'production' in environments) and not service:
        raise ValidationError('修改类或生产作业必须指定 systemd 服务验证')
    fields=schema(raw.get('parameters',[]))
    payload=validate_script({'name':name,'language':'python' if kind=='python' else 'shell','source':raw.get('source'),'timeout':raw.get('timeout',120),'concurrency':raw.get('concurrency',2)})
    if kind=='command':
        if any(x['type']=='enum' and not x['required'] for x in fields):
            raise ValidationError('命令模板的枚举参数须为必填')
        examples={x['name']:x['default'] if 'default' in x else x['min'] if x['type']=='integer' else x['choices'][0] if x['type']=='enum' else 'example' for x in fields}
        render(SimpleNamespace(parameters=fields,cmd=payload['source']),examples)
    batch=bounded_int(raw.get('batch_size',4),1,30,'后续批次大小')
    return {'name':name,'kind':kind,'risk':risk,'environments':sorted(set(environments)),
            'allowed_host_ids':sorted(set(allowed)),'verify_service':service,'parameters':fields,
            'source':payload['source'],'timeout':payload['timeout'],'concurrency':payload['concurrency'],
            'description':clean_text(raw.get('description',''),1000,'用途',True),
            'ssh_user':clean_text(raw.get('ssh_user',''),64,'SSH 账号限制'),
            'os_match':clean_text(raw.get('os_match',''),64,'系统名称限制'),
            'canary_count':bounded_int(raw.get('canary_count',1),1,4,'首批台数'),
            'batch_size':batch,'failure_limit':bounded_int(raw.get('failure_limit',1),1,300,'失败停止台数')}


def target_snapshot(host):
    return {'id':host.id,'name':host.name,'ip_addr':host.ip_addr,'port':host.port,
            'username':host.username,'environment':host.environment,'os_name':host.os_name}


def prepare(job, hosts, parameters, reason):
    if job.state!='published':
        raise ValidationError('仅已发布版本可以执行')
    definition=job.definition
    if not isinstance(parameters,dict) or set(parameters)-{f['name'] for f in definition['parameters']}:
        raise ValidationError('有未定义的参数')
    values={}
    for field in definition['parameters']:
        name=field['name']
        if name not in parameters and 'default' not in field and field['required']:
            raise ValidationError(f'{field["label"]}为必填项')
        values[name]=param_value(field,parameters.get(name,field.get('default','')))
    for host in hosts:
        if (host.environment not in definition['environments'] or
            definition['allowed_host_ids'] and host.id not in definition['allowed_host_ids'] or
            definition['ssh_user'] and host.username!=definition['ssh_user'] or
            definition['os_match'] and definition['os_match'].casefold() not in (host.os_name or '').casefold()):
            raise ValidationError(f'主机 #{host.id} 不符合该作业的目标限制')
    protected=definition['risk']=='change' or any(h.environment=='production' for h in hosts)
    reason=clean_text(reason,500,'操作原因',protected)
    command=definition['kind']=='command'
    source=render(SimpleNamespace(parameters=definition['parameters'],cmd=definition['source']),values) if command else definition['source']
    script=validate_script({**definition,'language':'python' if definition['kind']=='python' else 'shell','source':source,'args':[] if command else [str(v) for v in values.values()]})
    options={k:script[k] for k in script if k!='source'}
    options['managed']={'job_id':job.pk,'version':job.version,'kind':definition['kind'],'risk':definition['risk'],
        'protected':protected,'parameters':values,'reason':reason,'verify_service':definition['verify_service'],
        'canary_count':definition['canary_count'],'batch_size':definition['batch_size'],'failure_limit':definition['failure_limit'],
        'targets':[target_snapshot(h) for h in hosts]}
    return script['source'],options


def digest(user_id, source, options):
    data=json.dumps([user_id,source,options],ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(data.encode()).hexdigest()


def signed_plan(user_id, source, options):
    return signing.dumps(digest(user_id,source,options),salt='managed-job')


def check_plan(token,user_id,source,options):
    try:
        value=signing.loads(token,salt='managed-job',max_age=600)
    except (signing.BadSignature, TypeError, AttributeError):
        raise ValidationError('计划已失效，请重新预览')
    if value!=digest(user_id,source,options):
        raise ValidationError('作业版本、主机、顺序或参数已变化，请重新预览')


def verify_targets(task, hosts):
    snapshots={row['id']:row for row in task.options['managed']['targets']}
    for host in hosts:
        if snapshots.get(host.id)!=target_snapshot(host):
            raise ValidationError(f'主机 #{host.id} 的连接或环境信息已变化，须重新提交')
