"""Fixed, privileged SSH baseline transaction. Also serves as the on-host rollback program."""

import fcntl
import glob
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

CONFIG = Path('/etc/ssh/sshd_config')
STATE = Path('/var/lib/opspilot-sshd')
ENV = {'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'}
SETTINGS = {
    'ssh_password':{'passwordauthentication':'no','kbdinteractiveauthentication':'no'},
    'ssh_root':{'permitrootlogin':'prohibit-password'},
}


def command(*args):
    result = subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                            universal_newlines=True,env=ENV,timeout=10)
    if result.returncode:
        raise RuntimeError(f'{args[0]} 退出码 {result.returncode}，需核对目标服务')
    return result.stdout.strip()


def read_config(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022 or info.st_nlink!=1:
        raise RuntimeError('配置文件权限或类型超出受控范围')
    if info.st_size>256*1024:
        raise RuntimeError('配置文件超出受控大小')
    return path.read_bytes()


def safe_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
        raise RuntimeError('SSH 配置目录不在受控范围')


def verify_layout(content, nested=False):
    for line in content.decode('utf-8').splitlines():
        match = re.match(r'^\s*([a-z][a-z0-9]*)(?:\s+|\s*=\s*)(.*)',line,re.I)
        if not match:
            continue
        key, value = match.groups()
        if key.lower()=='match':
            raise RuntimeError('存在 Match 条件，转人工核对每个登录范围')
        if key.lower()=='include':
            if nested or shlex.split(value,comments=True)!=['/etc/ssh/sshd_config.d/*.conf']:
                raise RuntimeError('存在非标准 Include，转人工核对配置来源')
            directory = Path('/etc/ssh/sshd_config.d')
            if directory.exists() or directory.is_symlink():
                safe_directory(directory)
            for name in sorted(glob.glob('/etc/ssh/sshd_config.d/*.conf')):
                verify_layout(read_config(Path(name)),nested=True)


def unit_name():
    for unit in ('sshd.service','ssh.service'):
        try:
            if command('systemctl','is-active',unit)!='active':
                continue
        except RuntimeError:
            continue
        details = command('systemctl','show','--property=MainPID,DropInPaths,ExecStart',unit)
        fields = dict(line.split('=',1) for line in details.splitlines() if '=' in line)
        if fields.get('DropInPaths') or re.search(r'\s-[fo]',fields.get('ExecStart','')):
            raise RuntimeError('SSH 服务存在自定义启动配置，转人工核对')
        pid = int(fields['MainPID'])
        argv = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\x00',b' ')
        if pid<1 or b'sshd' not in argv or re.search(rb'\s-[fo]',argv):
            raise RuntimeError('无法确认 SSH 主进程使用标准配置')
        return unit
    raise RuntimeError('未找到运行中的标准 SSH 服务单元')


def effective(path):
    command('/usr/sbin/sshd','-t','-f',str(path))
    output = command('/usr/sbin/sshd','-T','-f',str(path))
    return dict(line.split(None,1) for line in output.splitlines() if len(line.split(None,1))==2)


def matches(values, target):
    return all(values.get(key)==value or (key=='permitrootlogin' and values.get(key)=='without-password')
               for key,value in target.items())


def candidate(content, target):
    # Retain a single managed directive for each option and preserve the rest of the file.
    names = b'|'.join(re.escape(key.encode('ascii')) for key in target)
    owned = re.compile(rb'^# OpsPilot-AI baseline\r?\n(?:' + names + rb') [a-z-]+\r?\n',re.I|re.M)
    rest = owned.sub(b'',content)
    return b''.join(f'# OpsPilot-AI baseline\n{key} {value}\n'.encode('ascii') for key,value in target.items())+rest


def digest(content):
    return hashlib.sha256(content).hexdigest()


def replace_config(content, validate=None):
    fd, name = tempfile.mkstemp(prefix='.opspilot-sshd-',dir=str(CONFIG.parent))
    path = Path(name)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        original_info = CONFIG.lstat()
        os.chown(str(path),original_info.st_uid,original_info.st_gid)
        shutil.copystat(str(CONFIG),str(path),follow_symlinks=False)
        original_attrs = os.listxattr(str(CONFIG))
        for attr in original_attrs:
            if os.getxattr(str(path),attr)!=os.getxattr(str(CONFIG),attr):
                raise RuntimeError('无法保留 SSH 配置安全属性')
        if validate:
            validate(path)
        os.replace(str(path),str(CONFIG))
    finally:
        if path.exists():
            path.unlink()


def rollback(active, work):
    original = (work/'backup').read_bytes()
    applied = (work/'digest').read_text(encoding='ascii')
    current = read_config(CONFIG)
    if digest(current) not in (digest(original),applied):
        raise RuntimeError('配置已被外部更改，保留备份，须人工回退')
    if current!=original:
        replace_config(original,lambda path: effective(path))
    command('systemctl','reload',(work/'unit').read_text(encoding='ascii'))
    active.unlink()
    print('SSH 基线变更已回退',flush=True)


def apply(action, token, active, work):
    if active.exists():
        raise RuntimeError('此主机已有未完成 SSH 变更，请核对回滚状态')
    safe_directory(CONFIG.parent)
    unit = unit_name()
    original = read_config(CONFIG)
    verify_layout(original)
    target = SETTINGS[action]
    before = effective(CONFIG)
    if before.get('pubkeyauthentication')!='yes':
        raise RuntimeError('有效配置未确认启用密钥登录')
    if before.get('permitrootlogin') not in ('yes','without-password','prohibit-password'):
        raise RuntimeError('root 登录依赖特殊限制，转人工处理')
    observed = 'passwordauthentication' if action=='ssh_password' else 'permitrootlogin'
    if before.get(observed)!='yes':
        raise RuntimeError('当前 SSH 策略与审批证据不同，请重新诊断')
    staged = candidate(original,target)

    def verify(path):
        config = effective(path)
        if not matches(config,target) or config.get('pubkeyauthentication')!='yes':
            raise RuntimeError('候选配置未生效或影响密钥登录')

    work.mkdir(mode=0o700)
    (work/'backup').write_bytes(original)
    (work/'digest').write_text(digest(staged),encoding='ascii')
    (work/'unit').write_text(unit,encoding='ascii')
    worker = work/'change.py'
    shutil.copyfile(__file__,str(worker))
    active.write_text(token,encoding='ascii')
    timer = 'opspilot-sshd-'+token
    try:
        command('systemd-run','--unit='+timer,'--on-active=120s','--timer-property=AccuracySec=1s',sys.executable,str(worker),'rollback')
        if command('systemctl','is-active',timer+'.timer')!='active':
            raise RuntimeError('未确认 SSH 变更回滚计时器')
    except Exception:
        active.unlink()
        raise
    try:
        if read_config(CONFIG)!=original:
            raise RuntimeError('SSH 配置已变化，请重新诊断')
        replace_config(staged,verify)
        command('systemctl','reload',unit)
        verify(CONFIG)
    except Exception:
        rollback(active,work)
        raise
    print('已重载 SSH 服务，等待平台使用新的密钥会话复检',flush=True)


def main(action, token, phase='apply'):
    try:
        if (os.geteuid()!=0 or action not in SETTINGS or not re.fullmatch('[a-f0-9]{32}',token)
                or phase not in ('apply','commit','rollback')):
            raise RuntimeError('SSH 变更须使用受控参数和 root 密钥会话')
        os.umask(0o077)
        STATE.mkdir(mode=0o700,exist_ok=True)
        info = STATE.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o077:
            raise RuntimeError('SSH 回滚目录权限不安全')
        work = STATE/token
        active = STATE/'active'
        with (STATE/'lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if phase=='apply':
                apply(action,token,active,work)
            elif not active.exists() or active.read_text(encoding='ascii')!=token:
                if phase=='commit':
                    raise RuntimeError('变更已回退或无法确认，停止确认')
            elif phase=='rollback':
                rollback(active,work)
            else:
                if digest(read_config(CONFIG))!=(work/'digest').read_text(encoding='ascii'):
                    raise RuntimeError('配置已变化，停止确认')
                if not matches(effective(CONFIG),SETTINGS[action]):
                    raise RuntimeError('新密钥会话未验证目标策略')
                active.unlink()
                print('新密钥会话复检通过，已确认 SSH 基线变更',flush=True)
    except Exception as exc:
        print(f'SSH 基线变更停止：{exc}',flush=True)
        return 2
    return 0
