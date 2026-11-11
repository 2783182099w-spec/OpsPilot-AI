import settings from '@/settings'

export function ciEndpoint(application) {
  const base = new URL(settings.host || '/', window.location.origin).href.replace(/\/$/, '')
  return base + '/mtask/ci/' + encodeURIComponent(application) + '/artifacts'
}

export function ciShell(application) {
  return [
    '#!/usr/bin/env bash',
    'set -euo pipefail',
    '# 在 Linux CI 中运行。制品须已上传，令牌从 CI 密钥库注入。',
    ': "${OPSPILOT_CI_TOKEN:?请在 CI 密钥库配置 OPSPILOT_CI_TOKEN}"',
    ': "${RELEASE_VERSION:?请设置制品版本，如 1.0.0}"',
    ': "${ARTIFACT_FILE:?请设置本地制品路径}"',
    ': "${ARTIFACT_URL:?请设置制品 HTTPS 下载地址}"',
    'OPSPILOT_CI_URL=${OPSPILOT_CI_URL:-'+ciEndpoint(application)+'}',
    'export OPSPILOT_CI_URL OPSPILOT_CI_TOKEN RELEASE_VERSION ARTIFACT_FILE ARTIFACT_URL',
    'python3 - <<\'PY\'',
    'import hashlib, json, os, urllib.request',
    'from urllib.parse import urlsplit',
    '',
    'endpoint = os.environ["OPSPILOT_CI_URL"]',
    'address = urlsplit(endpoint)',
    'if address.scheme != "https" and not (address.scheme == "http" and address.hostname in ("127.0.0.1", "localhost", "::1")):',
    '    raise SystemExit("远程 CI 上报必须通过 HTTPS")',
    'digest = hashlib.sha256()',
    'with open(os.environ["ARTIFACT_FILE"], "rb") as artifact:',
    '    for chunk in iter(lambda: artifact.read(65536), b""):',
    '        digest.update(chunk)',
    'manifest = {"version": os.environ["RELEASE_VERSION"],',
    '            "url": os.environ["ARTIFACT_URL"], "sha256": digest.hexdigest()}',
    'class NoRedirect(urllib.request.HTTPRedirectHandler):',
    '    def redirect_request(self, *args, **kwargs):',
    '        return None',
    'request = urllib.request.Request(endpoint, method="POST", data=json.dumps(manifest).encode(),',
    '    headers={"Content-Type": "application/json", "X-OpsPilot-CI-Token": os.environ["OPSPILOT_CI_TOKEN"]})',
    'with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:',
    '    print("OpsPilot 制品已登记，HTTP {}".format(response.status))',
    'PY',
  ].join('\n')
}

export async function copyText(value) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(value)
  throw new Error('Clipboard unavailable')
}
