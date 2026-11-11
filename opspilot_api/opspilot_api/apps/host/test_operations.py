from unittest.mock import Mock, patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError
from host.models import Host, Finding
from host.files import checked_path
import io
import stat
import hashlib
from types import SimpleNamespace
from django.core.files.uploadedfile import SimpleUploadedFile


class HostOperationsTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user('asset-owner')
        self.other=get_user_model().objects.create_user('asset-other')
        self.host=Host.objects.create(name='test',ip_addr='127.0.0.1',port=22,username='test')
        self.host.users.add(self.owner)
        self.client=APIClient()
        self.client.force_authenticate(self.owner)

    def test_inventory_history_and_filter(self):
        self.client.force_authenticate(get_user_model().objects.create_superuser('asset-admin'))
        response=self.client.patch(f'/host/{self.host.id}',{'business':'acceptance','environment':'staging','purpose':'offline-test'},format='json')
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(self.client.get(f'/host/{self.host.id}/changes').data),1)
        self.assertEqual(self.client.get('/host/?page=1&environment=production').data['count'],0)

    def test_file_history_does_not_connect_and_unauthorized_transfer_rejected(self):
        with patch('host.files.SSHParamiko') as ssh:
            self.assertEqual(self.client.get(f'/host/{self.host.id}/files?history=1').status_code,200)
            ssh.assert_not_called()
            self.client.force_authenticate(self.other)
            self.assertEqual(self.client.get(f'/host/{self.host.id}/files').status_code,404)
            ssh.assert_not_called()

    def test_sftp_traversal_and_symlink_escape_are_rejected(self):
        sftp=Mock()
        with self.assertRaises(ValidationError): checked_path(sftp,'/tmp/../etc/shadow')
        sftp.normalize.return_value='/etc/shadow'
        with self.assertRaises(ValidationError): checked_path(sftp,'/tmp/link')
        sftp.normalize.return_value='/var/log/test.log'
        with self.assertRaises(ValidationError): checked_path(sftp,'/var/log/test.log',write=True)

    def test_finding_isolation_and_notes(self):
        finding=Finding.objects.create(host=self.host,owner=self.owner,dedupe_key='test',title='test')
        self.assertEqual(self.client.patch(f'/host/findings/{finding.id}',{'state':'processing','assignee':'operator','note':'checking'},format='json').status_code,200)
        self.assertEqual(finding.notes.count(),1)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f'/host/findings/{finding.id}').status_code,404)

    @patch('host.files.PkeyManager.get', return_value=('fake','fake'))
    @patch('host.files.SSHParamiko')
    def test_download_hash_and_slots_are_released_even_if_sftp_close_fails(self, ssh, key):
        content=b'fake log, no remote network'
        sftp=ssh.return_value.client.open_sftp.return_value
        sftp.normalize.return_value='/var/log/test.log'
        info=SimpleNamespace(st_mode=stat.S_IFREG|0o600,st_size=len(content),st_mtime=1)
        sftp.lstat.return_value=info
        sftp.stat.return_value=info
        sftp.open.side_effect=lambda *args: io.BytesIO(content)
        sftp.close.side_effect=RuntimeError('simulated close failure')
        for i in range(3):
            response=self.client.get(f'/host/{self.host.id}/files?path=/var/log/test.log&download=1')
            self.assertEqual(response.status_code,200)
            self.assertEqual(b''.join(response.streaming_content),content)
            self.assertEqual(response['X-Content-SHA256'],hashlib.sha256(content).hexdigest())
            response.close()

    @patch('host.files.PkeyManager.get', return_value=('fake','fake'))
    @patch('host.files.SSHParamiko')
    def test_upload_existing_file_requires_explicit_overwrite(self, ssh, key):
        sftp=ssh.return_value.client.open_sftp.return_value
        sftp.normalize.return_value='/tmp'
        sftp.lstat.return_value=SimpleNamespace(st_mode=stat.S_IFREG|0o600)
        response=self.client.post(f'/host/{self.host.id}/files',{'path':'/tmp','file':SimpleUploadedFile('test.txt',b'test')},format='multipart')
        self.assertEqual(response.status_code,400)
        sftp.open.assert_not_called()
        sftp.rename.assert_not_called()

    def test_external_findings_are_deduplicated_and_authorized(self):
        payload={'host_id':self.host.id,'event_id':'test-event','title':'service warning','evidence':'password=test-value'}
        first=self.client.post('/host/findings',payload,format='json')
        self.assertEqual(first.status_code,201)
        second=self.client.post('/host/findings',{**payload,'state':'resolved'},format='json')
        self.assertEqual(second.status_code,200)
        self.assertEqual(first.data['id'],second.data['id'])
        self.assertNotIn('test-value',Finding.objects.get(pk=first.data['id']).evidence)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post('/host/findings',payload,format='json').status_code,403)


class SessionTextTests(TestCase):
    def setUp(self):
        from host.models import SSHSession
        self.owner = get_user_model().objects.create_user('text-owner')
        self.other = get_user_model().objects.create_user('text-other')
        self.host = Host.objects.create(name='text-test', ip_addr='127.0.0.1', port=22222, username='test')
        self.host.users.add(self.owner)
        self.session = SSHSession.objects.create(owner=self.owner, host=self.host, host_label='text-test')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_output_is_redacted_across_chunks_and_bounded(self):
        from host.session_records import TextRecorder, LIMIT
        recorder = TextRecorder(self.session.id)
        recorder.append('\x1b[31mhello\x1b[0m\npass')
        recorder.flush()
        self.session.refresh_from_db()
        self.assertEqual(self.session.transcript, 'hello\n')
        recorder.append('word=very-secret\n')
        recorder.flush(final=True)
        self.session.refresh_from_db()
        self.assertNotIn('very-secret', self.session.transcript)
        self.assertIn('[REDACTED]', self.session.transcript)
        recorder.append('a' * (LIMIT + 100))
        recorder.flush(final=True)
        self.session.refresh_from_db()
        self.assertTrue(self.session.transcript_truncated)
        self.assertLessEqual(self.session.recorded_bytes, LIMIT)
        before = recorder.raw
        recorder.append('not stored')
        self.assertEqual(before, recorder.raw)

    def test_private_key_incomplete_block_and_escapes_removed(self):
        from host.session_records import clean_text
        text = clean_text('hello\x1b]0;title\x07\n-----BEGIN OPENSSH PRIVATE KEY-----\nnot-a-real-key\n')
        self.assertNotIn('not-a-real-key', text)
        self.assertNotIn('title', text)
        self.assertIn('[REDACTED PRIVATE KEY]', text)

    def test_records_paginated_without_text_and_access_revocation(self):
        self.session.transcript = 'output'
        self.session.save()
        response = self.client.get('/host/sessions?page=1')
        self.assertEqual(response.data['count'], 1)
        self.assertNotIn('transcript', response.data['results'][0])
        self.assertEqual(self.client.get(f'/host/sessions/{self.session.id}').data['text'], 'output')
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f'/host/sessions/{self.session.id}').status_code, 404)
        self.client.force_authenticate(self.owner)
        self.host.users.clear()
        self.assertEqual(self.client.get(f'/host/sessions/{self.session.id}').status_code, 404)

    def test_expired_text_hidden_and_pruned_without_metadata_deletion(self):
        from datetime import timedelta
        from django.utils import timezone
        from host.models import SSHSession
        from host.session_records import prune_text
        SSHSession.objects.filter(pk=self.session.id).update(started_at=timezone.now()-timedelta(days=31), transcript='old text', recorded_bytes=8)
        self.assertEqual(self.client.get(f'/host/sessions/{self.session.id}').data['text'], '')
        prune_text()
        self.session.refresh_from_db()
        self.assertEqual(self.session.transcript, '')
        self.assertEqual(SSHSession.objects.count(), 1)

    def test_raw_keyboard_input_is_not_recorded(self):
        from host.consumer import SSHCmdConsumer
        consumer = SSHCmdConsumer()
        consumer.ssh_chan = Mock()
        consumer.recorder = Mock()
        consumer.receive(text_data='{"type":"input","data":"password entered with echo off"}')
        consumer.ssh_chan.send.assert_called_once()
        consumer.recorder.append.assert_not_called()


    def test_only_latest_thousand_sessions_keep_text(self):
        from host.models import SSHSession
        from host.session_records import prune_text
        self.session.transcript = 'old text'
        self.session.save()
        SSHSession.objects.bulk_create([SSHSession(owner=self.owner, host=self.host) for _ in range(1000)])
        self.assertEqual(self.client.get(f'/host/sessions/{self.session.id}').data['text'], '')
        prune_text()
        self.session.refresh_from_db()
        self.assertEqual(self.session.transcript, '')
        self.assertEqual(SSHSession.objects.count(), 1001)
