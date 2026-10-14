import re


def redact(text):
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----", "[REDACTED PRIVATE KEY]", str(text))
    text = re.sub(r"(?i)(password|passwd|token|api[_-]?key|secret|authorization)(\s*[:=]\s*)([^\s,;]+)", r"\1\2[REDACTED]", text)
    return re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED KEY]", text)
