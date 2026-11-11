import hashlib
import io
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import Event
from unittest import skipUnless
from unittest.mock import Mock,MagicMock,patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError
from host.models import Host
from .models import ExecutionTask,JobScript
from .scripts import validate_script,execute_script
from .tasks import run_task


class ScriptTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('script-user')
        self.admin=get_user_model().objects.create_superuser('script-admin',password='test-only')
        self.other=get_user_model().objects.create_user('script-other')
        self.host=Host.objects.create(name='local',ip_addr='127.0.0.1',port=22222,username='test')
        self.host.users.add(self.user)
        self.client=APIClient();self.client.force_authenticate(self.user)
        self.script={'language':'python','name':'query','source':'import sys\nprint(sys.argv[1:])\n','args':['hello; $(not-run)'],'timeout':5,'concurrency':1}

    def preview(self,script=None):
        return self.client.post('/mtask/scripts/preview',{'script':script or self.script,'host_ids':[self.host.id]},format='json')

    def test_preview_has_no_execution_and_snapshot_requires_approval(self):
        with patch('opspilot_api.utils.ssh.SSHParamiko') as ssh:
            preview=self.preview()
            self.assertEqual(preview.status_code,200)
            ssh.assert_not_called()
        response=self.client.post('/mtask/tasks',{'kind':'script','host_ids':[self.host.id],'script':self.script,'preview_token':preview.data['preview_token'],'confirmed':True},format='json')
        self.assertEqual(response.status_code,202)
        self.assertEqual(response.data['status'],'awaiting_approval')
        task=ExecutionTask.objects.get()
        self.assertEqual(task.command,self.script['source'])
        self.assertEqual(task.options['sha256'],hashlib.sha256(self.script['source'].encode()).hexdigest())
        self.assertEqual(self.client.post(f'/mtask/tasks/{task.id}/action',{'action':'approve'},format='json').status_code,403)

    def test_preview_cannot_be_reused_after_payload_change_or_revocation(self):
        preview=self.preview().data['preview_token']
        for field,value in [('source','print("changed")'),('args',['changed']),('timeout',10),('concurrency',2)]:
            changed={**self.script,field:value}
            response=self.client.post('/mtask/tasks',{'kind':'script','host_ids':[self.host.id],'script':changed,'preview_token':preview,'confirmed':True},format='json')
            self.assertEqual(response.status_code,400)
        self.host.users.clear()
        self.assertEqual(self.preview().status_code,403)
        self.assertEqual(ExecutionTask.objects.count(),0)

    def test_validation_limits_encoding_and_python_syntax(self):
        for override in ({'source':'a'*65537},{'source':'bad\x00text'},{'source':'print('},{'language':'powershell'},{'args':['x']*21},{'args':['x\ny']},{'timeout':601},{'concurrency':5}):
            with self.assertRaises(ValidationError):
                validate_script({**self.script,**override})
        payload=validate_script({**self.script,'source':'\ufeffprint(1)\r\n'})
        self.assertEqual(payload['source'],'print(1)\n')

    def test_library_version_edit_archive_and_task_isolation(self):
        self.assertEqual(self.client.post('/mtask/scripts',self.script,format='json').status_code,403)
        self.client.force_authenticate(self.admin)
        response=self.client.post('/mtask/scripts',self.script,format='json')
        pk=response.data['id']
        self.assertEqual(self.client.patch(f'/mtask/scripts/{pk}',{**self.script,'expected_version':0},format='json').status_code,400)
        self.assertEqual(self.client.patch(f'/mtask/scripts/{pk}',{**self.script,'source':'print(2)','expected_version':1},format='json').status_code,200)
        data=self.client.get(f'/mtask/scripts/{pk}').data
        self.assertEqual(data['script']['version'],2)
        self.assertEqual(data['revisions'][0]['source'],self.script['source'])
        self.client.delete(f'/mtask/scripts/{pk}')
        self.assertEqual(self.client.get(f'/mtask/scripts/{pk}').status_code,404)
        self.client.force_authenticate(self.user)
        task=ExecutionTask.objects.create(owner=self.other,kind='script',command='print(1)',host_ids=[self.host.id])
        self.assertEqual(self.client.get(f'/mtask/tasks/{task.id}').status_code,404)

    def test_remote_runner_delivers_source_not_shell_interpolation(self):
        payload=validate_script(self.script)
        ssh=Mock();ssh.client=Mock()
        sftp=MagicMock();ssh.client.open_sftp.return_value=sftp
        ssh.execute_cmd.side_effect=[(0,'/tmp/opspilot-job.ABCDEF1234\n'),(0,payload['sha256']+'  job.py\n'),(0,''),(0,'ok')]
        self.assertEqual(execute_script(ssh,payload,Event(),lambda chunk:None),(0,'ok'))
        command=ssh.execute_cmd.call_args_list[-1].args[0]
        self.assertNotIn(self.script['source'],command)
        self.assertIn(shlex.quote(self.script['args'][0]),command)
        self.assertIn('timeout --signal=TERM --kill-after=5s 5s',command)
        sftp.chmod.assert_called_once_with('/tmp/opspilot-job.ABCDEF1234/job.py',0o600)
        sftp.remove.assert_called_once()
        sftp.rmdir.assert_called_once()

    def test_timeout_cleanup_failure_does_not_mask_primary_error(self):
        payload=validate_script(self.script)
        ssh=Mock();sftp=MagicMock();ssh.client.open_sftp.return_value=sftp
        ssh.execute_cmd.side_effect=[(0,'/tmp/opspilot-job.ABCDEF1234'),(0,payload['sha256']+' job.py'),(0,''),TimeoutError('expected timeout')]
        sftp.remove.side_effect=OSError('connection lost')
        sftp.rmdir.side_effect=OSError('connection lost')
        sftp.close.side_effect=OSError('connection lost')
        with self.assertRaisesRegex(TimeoutError,'expected timeout'):
            execute_script(ssh,payload,Event(),lambda chunk:None)

    def test_failed_exclusive_create_never_deletes_an_existing_file(self):
        ssh=Mock();sftp=MagicMock();ssh.client.open_sftp.return_value=sftp
        ssh.execute_cmd.return_value=(0,'/tmp/opspilot-job.ABCDEF1234')
        sftp.open.side_effect=FileExistsError('existing source')
        with self.assertRaises(FileExistsError):
            execute_script(ssh,validate_script(self.script),Event(),lambda c:None)
        sftp.remove.assert_not_called()
        sftp.close.assert_called_once()

    def test_invalid_remote_directory_cannot_trigger_path_cleanup(self):
        ssh=Mock();ssh.execute_cmd.return_value=(0,'/tmp/other; bad')
        with self.assertRaises(RuntimeError):
            execute_script(ssh,validate_script(self.script),Event(),lambda chunk:None)
        ssh.client.open_sftp.assert_not_called()
        self.assertEqual(ssh.execute_cmd.call_count,1)

    @patch('mtask.tasks.PkeyManager.get',return_value=('fake','fake'))
    @patch('mtask.tasks.SSHParamiko')
    @patch('mtask.tasks.execute_script',return_value=(0,'ok'))
    def test_script_worker_dispatches_frozen_source(self,runner,ssh,key):
        payload=validate_script(self.script)
        task=ExecutionTask.objects.create(owner=self.admin,kind='script',command=payload.pop('source'),options=payload,host_ids=[self.host.id])
        run_task(task)
        task.refresh_from_db()
        self.assertEqual(task.status,'completed')
        self.assertEqual(runner.call_args.args[1]['source'],self.script['source'])
        self.assertEqual(task.host_results.get().output,'ok')

    @patch('mtask.tasks.PkeyManager.get',return_value=('fake','fake'))
    @patch('mtask.tasks.SSHParamiko')
    @patch('mtask.tasks.execute_script')
    def test_partial_stdout_survives_output_limit_failure(self,runner,ssh,key):
        def fail(client,payload,event,callback):
            callback(b'progress before failure\\n')
            raise ValueError('output exceeded limit')
        runner.side_effect=fail
        payload=validate_script(self.script)
        task=ExecutionTask.objects.create(owner=self.admin,kind='script',command=payload.pop('source'),options=payload,host_ids=[self.host.id])
        run_task(task)
        output=task.host_results.get().output
        self.assertIn('progress before failure',output)
        self.assertIn('output exceeded limit',output)

    def test_script_retry_keeps_original_snapshot_and_requires_approval_again(self):
        payload=validate_script(self.script)
        task=ExecutionTask.objects.create(owner=self.user,kind='script',command=payload.pop('source'),options=payload,host_ids=[self.host.id],status='failed')
        response=self.client.post(f'/mtask/tasks/{task.id}/action',{'action':'retry','confirmed':True},format='json')
        self.assertEqual(response.status_code,202)
        retried=ExecutionTask.objects.get(pk=response.data['id'])
        self.assertEqual(retried.command,self.script['source'])
        self.assertEqual(retried.status,'awaiting_approval')


GIT_BASH=Path('C:/Program Files/Git/bin/bash.exe')


@skipUnless(os.name=='nt' and GIT_BASH.is_file(),'Optional Windows Git Bash execution checks')
class LocalScriptIntegration(TestCase):
    def run_local(self,language,source,args):
        # Local adapter exercises actual bash/timeout/python programs, not SSH.
        with tempfile.TemporaryDirectory(prefix='opspilot-test-bin-') as bin_dir:
            python_path=str(Path(sys.executable)).replace('\\','/')
            drive=python_path[0].lower()
            posix_python='/'+drive+python_path[2:]
            wrapper=Path(bin_dir)/'python3'
            wrapper.write_text('#!/usr/bin/bash\nexec '+shlex.quote(posix_python)+' "$@"\n',encoding='utf-8')
            posix_bin='/'+bin_dir[0].lower()+bin_dir[2:].replace('\\','/')
            def local_path(path):
                if not path.startswith('/tmp/opspilot-job.'):
                    raise RuntimeError('Unexpected local test path')
                return Path(tempfile.gettempdir())/path.removeprefix('/tmp/')
            class FileAdapter:
                def get_channel(self):return Mock()
                def open(self,path,mode):return local_path(path).open('xb')
                def chmod(self,path,mode):pass
                def remove(self,path):local_path(path).unlink(missing_ok=True)
                def rmdir(self,path):local_path(path).rmdir()
                def close(self):pass
            class LocalSSH:
                def __init__(self):self.client=Mock();self.client.open_sftp.return_value=FileAdapter();self.directory=None
                def execute_cmd(self,command,**kwargs):
                    prefixed='export PATH='+shlex.quote(posix_bin)+':/usr/bin:/bin; set -e\n'+command
                    result=subprocess.run([str(GIT_BASH),'--noprofile','--norc','-c',prefixed],capture_output=True,timeout=kwargs.get('timeout',15),encoding='utf-8',errors='replace')
                    output=result.stdout+result.stderr
                    if command.startswith('umask') and result.returncode==0:self.directory=output.strip()
                    return result.returncode,output
            ssh=LocalSSH()
            result=execute_script(ssh,validate_script({'name':'local test','language':language,'source':source,'args':args,'timeout':5,'concurrency':1}),Event(),lambda c:None)
            self.assertFalse(local_path(ssh.directory).exists())
            return result

    def test_real_shell_argument_is_literal_and_file_is_removed(self):
        code,output=self.run_local('shell','printf "%s\\n" "$1"\n',['hello; $(echo SHOULD_NOT_EXECUTE)'])
        self.assertEqual(code,0,output)
        self.assertEqual(output.strip(),'hello; $(echo SHOULD_NOT_EXECUTE)')

    def test_real_python_argument_and_stdout(self):
        code,output=self.run_local('python','import sys\nprint(sys.argv[1])\n',['space ; $HOME'])
        self.assertEqual(code,0,output)
        self.assertEqual(output.strip(),'space ; $HOME')

    def test_real_shell_syntax_error_is_not_executed(self):
        code,output=self.run_local('shell','if then\n',[])
        self.assertNotEqual(code,0)
        self.assertIn('语法检查失败',output)

    def test_real_timeout_returns_124_and_cleans_file(self):
        code,output=self.run_local('shell','sleep 20\n',[])
        self.assertEqual(code,124,output)
