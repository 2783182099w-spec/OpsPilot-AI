from rest_framework import serializers

from .models import CmdTemplate, CmdTemplateCategory


class CmdTemplateCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = CmdTemplateCategory
        fields = ["id", "name"]


class CmdTemplateSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = CmdTemplate
        fields = ["id", "name", "cmd", "description", "category", "category_name"]
