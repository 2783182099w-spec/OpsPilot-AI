"""Bounded output-only text records; never record raw keyboard input."""
import re
import time
from datetime import timedelta

from django.utils import timezone
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from opspilot_api.utils.redaction import redact
from .models import SSHSession
from .permissions import accessible_hosts

LIMIT = 256 * 1024


def clean_text(value):
    # Remove terminal escape/OSC controls before storing plain text.
    value = re.sub(r'\x1b\][^\x07]*(?:\x07|\x1b\\)', '', value)
    value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', value)
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', value)
    value = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)', '[REDACTED PRIVATE KEY]', value)
    return redact(value)


def prune_text():
    """Keep metadata, expire only text: 30 days or latest 1000 sessions."""
    keep = list(SSHSession.objects.order_by('-id').values_list('id', flat=True)[:1000])
    SSHSession.objects.exclude(transcript='').filter(Q(started_at__lt=timezone.now()-timedelta(days=30)) | ~Q(pk__in=keep)).update(transcript='', recorded_bytes=0)


class TextRecorder:
    def __init__(self, session_id):
        self.session_id = session_id
        self.raw = ''
        self.size = 0
        self.truncated = False
        self.last_flush = time.monotonic()

    def append(self, text):
        if self.truncated:
            return
        remaining = LIMIT - self.size
        encoded = text.encode('utf-8')
        self.raw += encoded[:remaining].decode('utf-8', 'ignore')
        self.size += min(remaining, len(encoded))
        self.truncated = len(encoded) >= remaining
        if time.monotonic() - self.last_flush >= 5 or self.truncated:
            self.flush()

    def flush(self, final=False):
        # Only completed lines are visible during a session, avoiding secret
        # leakage when a password assignment arrives across network chunks.
        value = self.raw if final else self.raw[:self.raw.rfind('\n')+1]
        text = clean_text(value).encode("utf-8")[:LIMIT].decode("utf-8", "ignore")
        SSHSession.objects.filter(pk=self.session_id).update(transcript=text, recorded_bytes=len(text.encode('utf-8')), transcript_truncated=self.truncated)
        self.last_flush = time.monotonic()


class SessionRecordsView(APIView):
    def get(self, request, pk=None):
        rows = SSHSession.objects.select_related('host', 'owner')
        if not request.user.is_superuser:
            rows = rows.filter(owner=request.user, host__in=accessible_hosts(request.user))
        if pk is not None:
            row = get_object_or_404(rows, pk=pk)
            cutoffs = list(SSHSession.objects.order_by('-id').values_list('id', flat=True)[999:1000])
            cutoff = cutoffs[0] if cutoffs else None
            expired = row.started_at < timezone.now()-timedelta(days=30) or (cutoff is not None and row.id < cutoff)
            return Response({'id': row.id, 'text': '' if expired else row.transcript, 'truncated': row.transcript_truncated, 'bytes': row.recorded_bytes})
        search = request.query_params.get('search', '')[:100]
        if search:
            rows = rows.filter(Q(host_label__icontains=search) | Q(owner__username__icontains=search))
        if request.query_params.get('status'):
            rows = rows.filter(status=request.query_params['status'])
        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(rows.defer('transcript').order_by('-id'), request)
        return paginator.get_paginated_response([{'id': r.id, 'host_id': r.host_id, 'host_label': r.host_label or (r.host.name if r.host else '资产已删除'), 'owner': r.owner.username, 'status': r.status, 'started_at': r.started_at, 'ended_at': r.ended_at, 'bytes': r.recorded_bytes, 'truncated': r.transcript_truncated} for r in page])
