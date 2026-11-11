from rest_framework import serializers

from .models import CmdTemplate, CmdTemplateCategory
from .template_tools import validate_parameters, render
from types import SimpleNamespace


class CmdTemplateCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = CmdTemplateCategory
        fields = ["id", "name"]


class CmdTemplateSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = CmdTemplate
        fields = ["id", "name", "cmd", "description", "category", "category_name", "parameters", "version"]
        read_only_fields = ["version"]

    def validate_parameters(self, value):
        return validate_parameters(value)

    def validate_cmd(self, value):
        if not value.strip() or len(value) > 10000:
            raise serializers.ValidationError('模板命令须为 1 到 10000 字符')
        return value

    def validate(self, attrs):
        spec = attrs.get('parameters', self.instance.parameters if self.instance else [])
        command = attrs.get('cmd', self.instance.cmd if self.instance else '')
        example = {p['name']: p.get('default', (p['choices'][0] if p.get('type') == 'enum' else '0' if p.get('type') == 'integer' else 'example')) for p in spec}
        try:
            render(SimpleNamespace(parameters=spec, cmd=command), example)
        except Exception as exc:
            from rest_framework.exceptions import ValidationError
            if isinstance(exc, ValidationError):
                raise serializers.ValidationError({'cmd': exc.detail})
            raise
        return attrs
