import re
import shlex
from rest_framework.exceptions import ValidationError


def validate_parameters(spec):
    if not isinstance(spec, list) or len(spec) > 10:
        raise ValidationError('参数最多 10 个')
    names = set()
    for item in spec:
        if not isinstance(item, dict) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,29}', str(item.get('name', ''))):
            raise ValidationError('参数名须由字母、数字、下划线组成')
        name = item['name']
        if name in names or re.search(r'password|token|secret|private_key', name, re.I):
            raise ValidationError('参数名重复或属于敏感凭据，请勿通过模板参数传递密码和密钥')
        names.add(name)
        if item.get('type', 'text') not in ('text', 'integer', 'enum'):
            raise ValidationError('参数类型只支持 text、integer、enum')
        if item.get('type') == 'enum' and (not isinstance(item.get('choices'), list) or not 1 <= len(item['choices']) <= 30 or any(not isinstance(x, str) or len(x) > 200 for x in item['choices'])):
            raise ValidationError('枚举需要 1 到 30 个文本选项')
    return spec


def render(template, supplied):
    spec = validate_parameters(template.parameters)
    if not isinstance(supplied, dict) or set(supplied) - {item['name'] for item in spec}:
        raise ValidationError('存在未定义参数')
    values = {}
    for item in spec:
        value = supplied.get(item['name'], item.get('default', ''))
        if isinstance(value, (dict, list, bool)) or value is None or len(str(value)) > 200 or '\n' in str(value) or '\x00' in str(value):
            raise ValidationError('参数值格式不合法')
        value = str(value)
        if not value and item.get('required', True):
            raise ValidationError(f"参数 {item['name']} 必填")
        if item.get('type') == 'integer' and not re.fullmatch(r'-?\d{1,12}', value):
            raise ValidationError('整数参数格式不合法')
        if item.get('type') == 'enum' and value not in item['choices']:
            raise ValidationError('参数值不在枚举范围内')
        values[item['name']] = shlex.quote(value)
    placeholders = re.findall(r'\{\{([A-Za-z][A-Za-z0-9_]*)\}\}', template.cmd)
    if set(placeholders) != set(values):
        raise ValidationError('模板占位符与参数定义不一致')
    # 模板必须把占位符作为独立 shell 参数，不允许在引号/变量展开中拼接。
    for match in re.finditer(r'\{\{[A-Za-z][A-Za-z0-9_]*\}\}', template.cmd):
        before = template.cmd[:match.start()]
        after = template.cmd[match.end():]
        if (before and before[-1] not in ' \t\n') or (after and after[0] not in ' \t\n;|&'):
            raise ValidationError('占位符必须作为独立参数，例如 systemctl status {{service}}')
        # 未闭合引号内的 shlex.quote 无法保证安全，保守拒绝所有含引号的参数模板。
    if placeholders and any(char in template.cmd for char in ('"', "'", '`', '$')):
        raise ValidationError('带参数的模板不允许引号、反引号或变量展开；请使用独立参数占位符')
    return re.sub(r'\{\{([A-Za-z][A-Za-z0-9_]*)\}\}', lambda match: values[match.group(1)], template.cmd)
