from .access import role_of
from rest_framework.views import APIView
from rest_framework.response import Response


class ProfileView(APIView):
    def get(self, request):
        return Response({'id': request.user.id, 'username': request.user.username, 'is_superuser': request.user.is_superuser, 'first_name':request.user.first_name, 'role':role_of(request.user)})
