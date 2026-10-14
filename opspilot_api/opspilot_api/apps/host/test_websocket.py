from django.test import TransactionTestCase
from django.contrib.auth import get_user_model
from channels.testing import WebsocketCommunicator
from asgiref.sync import async_to_sync
from rest_framework_simplejwt.tokens import RefreshToken
from opspilot_api.routing import application


class WebSocketPermissionTests(TransactionTestCase):
    def test_no_token_is_rejected(self):
        async def check():
            communicator = WebsocketCommunicator(application, '/ws/ssh/1/', headers=[(b'origin', b'http://127.0.0.1:9000')])
            connected, code = await communicator.connect()
            self.assertFalse(connected)
            self.assertEqual(code, 4401)
        async_to_sync(check)()

    def test_valid_user_without_host_permission_is_rejected(self):
        user = get_user_model().objects.create_user('ws-test')
        token = str(RefreshToken.for_user(user).access_token)
        async def check():
            communicator = WebsocketCommunicator(application, f'/ws/ssh/999/?token={token}', headers=[(b'origin', b'http://127.0.0.1:9000')])
            connected, code = await communicator.connect()
            self.assertFalse(connected)
            self.assertEqual(code, 4403)
        async_to_sync(check)()
