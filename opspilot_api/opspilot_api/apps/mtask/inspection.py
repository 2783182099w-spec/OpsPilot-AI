"""固定的只读健康巡检。补丁缓存和版本信息不是完整的 CVE 扫描。"""
import re
import shlex
from datetime import datetime, timezone

from rest_framework.exceptions import ValidationError
from opspilot_api.utils.redaction import redact


def validate_options(kind, raw):
    if not isinstance(raw, dict):
        raise ValidationError("任务选项必须为对象")
    profile = raw.get("profile", "health")
    if profile not in ("health", "baseline", "acceptance"):
        raise ValidationError("未知巡检类型")
    services = raw.get("services", [])
    certificates = raw.get("certificates", [])
    if not isinstance(services, list) or len(services) > 10 or any(not isinstance(x, str) or not re.fullmatch(r"[A-Za-z0-9_.@:-]{1,100}", x) for x in services):
        raise ValidationError("服务名最多 10 个，只允许常见 systemd 单元名称字符")
    if not isinstance(certificates, list) or len(certificates) > 5 or any(not isinstance(x, str) or len(x) > 300 or '..' in x.split('/') or not x.startswith(('/etc/ssl/', '/etc/pki/', '/etc/letsencrypt/')) for x in certificates):
        raise ValidationError("证书最多 5 个，只允许 /etc/ssl、/etc/pki、/etc/letsencrypt 内的绝对路径")
    context = {}
    for key, limit in [('report_title',100),('purpose',500),('recipient',100)]:
        value = raw.get(key, '')
        if not isinstance(value,str) or len(value)>limit:
            raise ValidationError('报告标题、用途或接收方长度无效')
        context[key] = value.strip()
    return {"profile": profile, "services": services, "certificates": certificates, **context}


def rules(options):
    items = [
        ("os", "系统及内核版本取证", 'cat /etc/os-release; uname -r', "evidence"),
        ("disk", "文件系统使用率（阈值 85%）", 'df -P -x tmpfs -x devtmpfs', "disk"),
        ("cpu", "CPU 短时使用率（阈值 90%）", "awk '/^cpu /{print $2,$3,$4,$5,$6,$7,$8,$9}' /proc/stat; sleep 0.2; awk '/^cpu /{print $2,$3,$4,$5,$6,$7,$8,$9}' /proc/stat", "cpu"),
        ("memory", "可用内存（阈值 10%）", "awk '/^(MemTotal|MemAvailable):/{print $1,$2}' /proc/meminfo", "memory"),
        ("load", "系统负载", 'getconf _NPROCESSORS_ONLN; cat /proc/loadavg', "load"),
    ]
    if options['profile'] in ("baseline", "acceptance"):
        items += [
            ("uid0", "UID 0 账号复核", "awk -F: '$3 == 0 {print $1}' /etc/passwd", "uid0"),
            ("shadow", "shadow 文件权限", 'stat -c %a /etc/shadow', "shadow"),
            ("ssh_root", "SSH root 登录策略", "sshd -T 2>/dev/null | grep '^permitrootlogin '", "ssh_root"),
            ("ssh_password", "SSH 密码登录策略", "sshd -T 2>/dev/null | grep '^passwordauthentication '", "ssh_password"),
            ("time_sync", "时间同步状态", 'timedatectl show -p NTPSynchronized --value', "yes"),
            ("firewall", "常见防火墙服务状态复核", 'systemctl is-active firewalld || systemctl is-active nftables || systemctl is-active ufw', "firewall"),
            ("packages", "关键软件版本取证", 'if command -v rpm >/dev/null; then rpm -q openssh-server openssl sudo; elif command -v dpkg-query >/dev/null; then dpkg-query -W openssh-server openssl sudo; else exit 2; fi', "evidence"),
            ("patches", "本地缓存安全公告复核（不刷新软件源）", 'if command -v dnf >/dev/null; then dnf -C -q updateinfo list --security 2>&1 | head -c 8192; else echo "未检测到 dnf，请人工核查对应发行版安全公告"; exit 2; fi', "patches"),
        ]
    for index, service in enumerate(options['services']):
        items.append((f"service_{index}", f"服务 {service}", 'systemctl is-active -- ' + shlex.quote(service), "service"))
    for index, certificate in enumerate(options['certificates']):
        items.append((f"cert_{index}", f"证书有效期 {certificate}", 'openssl x509 -noout -enddate -in ' + shlex.quote(certificate), "certificate"))
    return [item for item in items if item[0]==options["focus_check"]] if "focus_check" in options else items


def command(options):
    lines = ['set +e', 'export LC_ALL=C']
    for ident, title, cmd, evaluator in rules(options):
        lines += [f"printf '\\n__OPSPILOT_BEGIN_{ident}__\\n'", f"( {cmd} )", 'rc=$?', f"printf '\\n__OPSPILOT_END_{ident}__%s\\n' \"$rc\""]
    return '\n'.join(lines)


def assess(evaluator, text, code):
    if evaluator == 'service':
        return ('pass', '服务 active') if code == 0 and text.strip() == 'active' else ('warning', '服务未处于 active，请核查单元是否存在')
    if code != 0 or not text.strip():
        return 'unknown', '命令不可用、权限不足或未取得有效数据'
    if evaluator == 'evidence':
        return 'info', '仅采集版本证据；未据此判断是否存在漏洞'
    if evaluator == 'patches':
        return 'review', '仅供复核本地缓存，可能过期；需结合发行版公告确认漏洞与修复状态'
    if evaluator == 'disk':
        values = [int(value) for value in re.findall(r'\b(\d+)%', text)]
        return ('warning', '存在使用率达到 85% 的文件系统') if any(value >= 85 for value in values) else ('pass', '文件系统使用率低于阈值') if values else ('unknown', '无法解析文件系统数据')
    if evaluator == 'memory':
        values = dict(re.findall(r'(MemTotal|MemAvailable):\s*(\d+)', text))
        if int(values.get('MemTotal', 0)) <= 0 or 'MemAvailable' not in values:
            return 'unknown', '缺少可用内存数据'
        return ('warning', '可用内存不足 10%') if int(values['MemAvailable']) / int(values['MemTotal']) < .1 else ('pass', '可用内存正常')
    if evaluator == 'cpu':
        try:
            first, second = [[int(x) for x in line.split()] for line in text.splitlines()[:2]]
            total = sum(second) - sum(first)
            if total <= 0:
                raise ValueError()
            idle = second[3] - first[3] + second[4] - first[4]
            usage = max(0, min(100, (1-idle/total)*100))
            return ('warning' if usage >= 90 else 'pass'), f'短时采样约 {usage:.1f}%，不是长期趋势'
        except (ValueError, IndexError):
            return 'unknown', '无法解析 CPU 采样数据'
    if evaluator == 'load':
        try:
            cores, load = text.splitlines()[:2]
            return ('warning', '一分钟负载高于 CPU 核数的 1.5 倍') if float(load.split()[0]) > int(cores) * 1.5 else ('pass', '负载低于阈值')
        except (ValueError, IndexError):
            return 'unknown', '无法解析负载数据'
    if evaluator == 'uid0':
        return ('pass', '仅 root 为 UID 0') if text.strip() == 'root' else ('warning', '发现需复核的 UID 0 账号')
    if evaluator == 'shadow':
        return ('pass', '权限在预期范围') if text.strip() in ('0', '000', '400', '600', '640') else ('warning', '请复核 shadow 权限')
    if evaluator == 'ssh_root':
        return ('pass', '禁止 root 密码登录或禁止 root 登录') if text.strip().split()[-1] in ('no', 'prohibit-password', 'without-password') else ('warning', 'root 登录策略需要复核')
    if evaluator == 'ssh_password':
        return ('pass', '已禁用 SSH 密码登录') if text.strip() == 'passwordauthentication no' else ('warning', 'SSH 密码登录开启')
    if evaluator == 'yes':
        return ('pass', '系统报告已同步') if text.strip() == 'yes' else ('warning', '系统未报告时间已同步')
    if evaluator == 'firewall':
        return ('info', '检测到常见服务 active，不代表已验证规则有效性') if 'active' in text.splitlines() else ('review', '未检测到常见防火墙服务，请复核其他防火墙策略')
    if evaluator == 'certificate':
        try:
            expiry = datetime.strptime(text.strip().removeprefix('notAfter='), '%b %d %H:%M:%S %Y %Z').replace(tzinfo=timezone.utc)
            days = (expiry - datetime.now(timezone.utc)).days
            return ('warning' if days < 30 else 'pass'), f'证书剩余约 {days} 天'
        except ValueError:
            return 'unknown', '无法解析证书到期日期'
    return 'unknown', '需要人工复核'


def parse(output, options):
    checks = []
    for ident, title, cmd, evaluator in rules(options):
        match = re.search(r'__OPSPILOT_BEGIN_' + ident + r'__\n([\s\S]*?)\n__OPSPILOT_END_' + ident + r'__(\d+)', output)
        if match:
            evidence = redact(match.group(1).strip())[:4096]
            state, summary = assess(evaluator, evidence, int(match.group(2)))
        else:
            evidence, state, summary = '', 'unknown', '未返回该检查结果，可能超时或输出被截断'
        checks.append({'id': ident, 'title': title, 'state': state, 'summary': summary, 'evidence': evidence})
    return checks
