"""DeepSeek 对话服务。

API Key 只保存在服务端（settings.DEEPSEEK_API_KEY / 环境变量），
前端通过本项目后端转发，避免把密钥暴露到浏览器。
"""

import requests
from django.conf import settings


class DeepSeekError(Exception):
    """调用 DeepSeek 失败时抛出的业务异常。"""


def ask_deepseek(message, history=None, model=None, timeout=60):
    api_key = getattr(settings, "DEEPSEEK_API_KEY", "")
    if not api_key:
        raise DeepSeekError("后端未配置 DEEPSEEK_API_KEY，请设置环境变量或写入 settings/local.py")

    base_url = getattr(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = model or getattr(settings, "DEEPSEEK_MODEL", "deepseek-flash")

    messages = list(history or [])
    messages.append({"role": "user", "content": message})

    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"model": model, "messages": messages},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise DeepSeekError(f"请求 DeepSeek 失败：{exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise DeepSeekError(f"DeepSeek 返回了非 JSON 响应（HTTP {response.status_code}）") from exc

    if response.status_code >= 400 or "error" in data:
        detail = data.get("error") if isinstance(data, dict) else data
        raise DeepSeekError(f"DeepSeek 返回错误：{detail}")

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DeepSeekError(f"无法解析 DeepSeek 响应：{data}") from exc
