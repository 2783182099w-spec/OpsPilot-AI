"""Script validation, immutable previews and bounded remote delivery."""
import ast
import hashlib
import json
import re
import shlex
from django.core import signing
from rest_framework.exceptions import ValidationError
from opspilot_api.utils.redaction import redact

LIMIT = 65536


def validate_script(data):
    if not isinstance(data,dict):
        raise ValidationError('脚本设置须为对象')
    language,source = data.get('language'),data.get('source')
    name,args = data.get('name','临时脚本'),data.get('args',[])
    timeout,concurrency = data.get('timeout',120),data.get('concurrency',2)
    if language not in ('shell','python'):
        raise ValidationError('请选择 Shell 或 Python 3')
    if not isinstance(source,str):
        raise ValidationError('脚本正文须为 UTF-8 文本')
    source = source.removeprefix('\ufeff').replace('\r\n','\n').replace('\r','\n')
    if not source.strip() or len(source.encode('utf-8'))>LIMIT or any(ord(c)<32 and c not in '\n\t' for c in source):
        raise ValidationError('脚本须为 1～64 KiB UTF-8 文本，不包含 NUL 或控制字符')
    if not isinstance(name,str) or not 1<=len(name.strip())<=100:
        raise ValidationError('脚本名称须为 1～100 字')
    if not isinstance(args,list) or len(args)>20 or any(not isinstance(a,str) or len(a.encode('utf-8'))>500 or any(ord(c)<32 for c in a) for a in args):
        raise ValidationError('参数最多 20 个，每个最多 500 字节，不能包含控制字符')
    if sum(len(a.encode('utf-8')) for a in args)>4000:
        raise ValidationError('参数总长度不能超过 4000 字节')
    if type(timeout) is not int or not 5<=timeout<=600 or type(concurrency) is not int or not 1<=concurrency<=4:
        raise ValidationError('单机超时为 5～600 秒，并发为 1～4 台')
    if language=='python':
        try:
            ast.parse(source,filename='job.py')
        except (SyntaxError,ValueError,RecursionError) as exc:
            raise ValidationError(f'Python 语法检查失败：{str(exc)[:250]}')
    return {'language':language,'source':source,'name':name.strip(),'args':args,'timeout':timeout,'concurrency':concurrency,'sha256':hashlib.sha256(source.encode('utf-8')).hexdigest()}


def signature(payload, host_ids):
    canonical = json.dumps({'script':payload,'hosts':sorted(set(host_ids))},sort_keys=True,ensure_ascii=False,separators=(',',':'))
    return hashlib.sha256(canonical.encode()).hexdigest()


def preview_token(payload, host_ids):
    return signing.dumps(signature(payload,host_ids),salt='opspilot-script-preview')


def verify_preview(token,payload,host_ids):
    try:
        value = signing.loads(token,salt='opspilot-script-preview',max_age=600)
    except (signing.BadSignature,TypeError):
        raise ValidationError('预览已过期或无效，请重新预览脚本、参数和目标主机')
    if value!=signature(payload,host_ids):
        raise ValidationError('脚本、参数或目标已变化，请重新预览后确认')


def execute_script(ssh, payload, stop_event, on_output, stage=None):
    """Never interpolate script bodies into a shell command or heredoc."""
    if stop_event.is_set():
        raise InterruptedError('尚未创建脚本，任务已取消')
    emit = stage or (lambda name, state, detail='': None)
    phase = 'upload'
    emit(phase,'running')
    directory = None
    sftp = None
    created_file = False
    try:
        code,output = ssh.execute_cmd('umask 077; mktemp -d /tmp/opspilot-job.XXXXXXXXXX',timeout=10,max_output=1024)
        if code!=0 or not re.fullmatch(r'/tmp/opspilot-job\.[A-Za-z0-9]{6,32}',output.strip()):
            raise RuntimeError('无法创建受限脚本目录；请检查 /tmp 可写性和 mktemp')
        directory = output.strip()
        path = directory+('/job.sh' if payload['language']=='shell' else '/job.py')
        sftp = ssh.client.open_sftp()
        sftp.get_channel().settimeout(10)
        remote_file = sftp.open(path,'wx')
        created_file = True
        with remote_file as target:
            target.write(payload['source'].encode('utf-8'))
        sftp.chmod(path,0o600)
        emit(phase,'success')
        phase='preflight'
        emit(phase,'running')
        if stop_event.is_set():
            raise InterruptedError('脚本尚未执行，任务已取消')
        interpreter = 'bash' if payload['language']=='shell' else 'python3'
        preflight = 'command -v '+interpreter+' >/dev/null || { echo "目标主机缺少 '+interpreter+'，平台不会自动安装依赖"; exit 127; }; command -v timeout >/dev/null || { echo "目标主机缺少 coreutils timeout"; exit 127; }; sha256sum -- '+shlex.quote(path)
        code,output = ssh.execute_cmd(preflight,timeout=10,max_output=2048)
        if code:
            emit(phase,'failed','解释器或必要工具不可用')
            return code,redact(output)
        if not output.split() or output.split()[0]!=payload['sha256']:
            raise RuntimeError('远端脚本摘要不一致，拒绝执行')
        if interpreter=='bash':
            check='bash -n -- '+shlex.quote(path)
        else:
            check='python3 -c '+shlex.quote('import ast,sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())')+' '+shlex.quote(path)
        code,output = ssh.execute_cmd(check,timeout=10,max_output=4096)
        if code:
            emit(phase,'failed','远端语法检查失败')
            return code,'远端解释器语法检查失败：\n'+redact(output)
        emit(phase,'success')
        if stop_event.is_set():
            raise InterruptedError('脚本尚未执行，任务已取消')
        phase='execute'
        emit(phase,'running')
        cleanup = 'rm -f -- '+shlex.quote(path)+' 2>/dev/null || true; rmdir -- '+shlex.quote(directory)+' 2>/dev/null || true'
        invocation = ('bash -- ' if interpreter=='bash' else 'python3 -u -- ')+shlex.quote(path)
        if payload['args']:
            invocation += ' '+' '.join(shlex.quote(arg) for arg in payload['args'])
        command = 'trap '+shlex.quote(cleanup)+' EXIT\ncd -- '+shlex.quote(directory)+'\nexport PYTHONIOENCODING=utf-8\ntimeout --signal=TERM --kill-after=5s '+str(payload['timeout'])+'s '+invocation
        code,output = ssh.execute_cmd(command,timeout=payload['timeout']+15,stop_event=stop_event,on_output=on_output,max_output=65536)
        try:
            remaining = sftp.listdir(directory)
            if isinstance(remaining,list) and remaining:
                output += '\n脚本生成文件保留在受限工作目录 '+directory+'，平台只清理投递的源码，不递归删除脚本产生的数据。'
        except Exception:
            pass
        emit(phase,'success' if code==0 else 'failed',f'退出码 {code}')
        return code,output
    except Exception:
        emit(phase,'failed')
        raise
    finally:
        # Only our validated unique path is removed. No recursive deletion.
        if directory:
            emit('cleanup','running')
        if sftp is None and directory:
            try:
                ssh.execute_cmd('rmdir -- '+shlex.quote(directory),timeout=5,max_output=1024)
            except Exception:
                pass
        if sftp is not None:
            if directory:
                if created_file:
                    try:
                        sftp.remove(directory+('/job.sh' if payload['language']=='shell' else '/job.py'))
                    except Exception:
                        pass
                try:
                    sftp.rmdir(directory)
                except Exception:
                    pass
            try:
                sftp.close()
            except Exception:
                pass
        if directory:
            emit('cleanup','attempted','已尝试清理投递文件；脚本自建文件不递归删除')
