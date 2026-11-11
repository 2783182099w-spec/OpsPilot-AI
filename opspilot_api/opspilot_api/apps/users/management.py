from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import MethodNotAllowed,PermissionDenied,ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from host.models import Host
from mtask.models import ExecutionTask
from .access import role_of
from .models import AccountAudit

User=get_user_model()


class AccountInput(serializers.ModelSerializer):
    password=serializers.CharField(max_length=128,min_length=12,write_only=True,trim_whitespace=False,required=False)
    role=serializers.ChoiceField(choices=['admin','operator','observer'],required=False)
    host_ids=serializers.ListField(child=serializers.IntegerField(min_value=1,max_value=2147483647),max_length=1000,required=False)

    class Meta:
        model=User
        fields=['username','first_name','email','is_active','password','role','host_ids']
        extra_kwargs={'first_name':{'max_length':100}}

    def validate(self,data):
        if self.instance and 'username' in data:
            raise ValidationError('已有账号不支持修改用户名')
        if self.instance and 'password' in data:
            raise ValidationError('请使用独立的重置密码操作')
        if not self.instance:
            if 'password' not in data:
                raise ValidationError({'password':'请输入初始密码'})
            verify_password(data['password'],User(username=data['username'],first_name=data.get('first_name',''),email=data.get('email','')))
        ids=data.get('host_ids')
        if ids is not None and Host.objects.filter(pk__in=set(ids),is_deleted=False).count()!=len(set(ids)):
            raise ValidationError({'host_ids':'含不存在或已下线资产'})
        return data


def verify_password(value,user):
    try:
        validate_password(value,user)
    except DjangoValidationError as exc:
        raise ValidationError({'password':exc.messages}) from exc


def admin(request):
    if not request.user.is_superuser:
        raise PermissionDenied('仅管理员可管理用户')


def locked_target(request, pk, require_admin=False):
    # Lock actor and target in ID order so simultaneous cross-admin changes cannot
    # disable both accounts or authorize a change from an already-revoked session.
    users = {user.pk: user for user in User.objects.select_for_update()
             .filter(pk__in={request.user.pk, pk}).order_by('pk')}
    actor = users.get(request.user.pk)
    if (actor is None or not actor.is_active or actor.session_version != request.user.session_version
            or (require_admin and not actor.is_superuser)):
        raise PermissionDenied('当前账号权限已变更，请重新登录')
    if pk not in users:
        raise Http404
    return users[pk]


def account(user,detail=False):
    data={'id':user.pk,'username':user.username,'first_name':user.first_name,'email':user.email,'role':role_of(user),
          'is_active':user.is_active,'last_login':user.last_login,'date_joined':user.date_joined}
    if detail:
        data['host_ids']=list(Host.objects.filter(users=user,is_deleted=False).values_list('id',flat=True))
    return data


def audit(actor,target,action,changes):
    AccountAudit.objects.create(actor=actor,target=target,action=action,changes=changes)


def revoke_jobs(user):
    ExecutionTask.objects.filter(owner=user,status='running').update(cancel_requested=True)


class AccountListView(APIView):
    def get(self,request,pk=None):
        admin(request)
        if pk is not None:
            return Response(account(get_object_or_404(User,pk=pk),True))
        rows=User.objects.order_by('username','id')
        term=request.query_params.get('search','').strip()[:100]
        if term:
            rows=rows.filter(Q(username__icontains=term)|Q(first_name__icontains=term)|Q(email__icontains=term))
        role=request.query_params.get('role')
        if role=='admin':
            rows=rows.filter(is_superuser=True)
        elif role in ('operator','observer'):
            rows=rows.filter(is_superuser=False,platform_role=role)
        state=request.query_params.get('active')
        if state in ('true','false'):
            rows=rows.filter(is_active=state=='true')
        pager=PageNumberPagination()
        pager.page_size=20
        return pager.get_paginated_response([account(u) for u in pager.paginate_queryset(rows,request)])

    @transaction.atomic
    def post(self,request,pk=None):
        admin(request)
        if pk is not None:
            raise MethodNotAllowed('POST')
        locked_target(request, request.user.pk, require_admin=True)
        serializer=AccountInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data=dict(serializer.validated_data)
        ids=data.pop('host_ids',[])
        role=data.pop('role','operator')
        password=data.pop('password')
        user=User.objects.create_user(password=password,is_staff=role=='admin',is_superuser=role=='admin',
                                      platform_role='operator' if role=='admin' else role,**data)
        for host in Host.objects.filter(pk__in=ids,is_deleted=False):
            host.users.add(user)
        audit(request.user,user,'created',{'role':role,'active':user.is_active,'host_ids':ids})
        return Response(account(user,True),status=201)

    @transaction.atomic
    def patch(self,request,pk=None):
        admin(request)
        if pk is None:
            raise MethodNotAllowed('PATCH')
        user=locked_target(request, pk, require_admin=True)
        serializer=AccountInput(user,data=request.data,partial=True)
        serializer.is_valid(raise_exception=True)
        data=dict(serializer.validated_data)
        old_role=role_of(user)
        role=data.pop('role',old_role)
        old_active=user.is_active
        enabled=data.get('is_active',old_active)
        if user.pk==request.user.pk and (not enabled or role!='admin'):
            raise ValidationError('不能停用或降级当前登录的管理员')
        ids=data.pop('host_ids',None)
        old_ids=set(Host.objects.filter(users=user,is_deleted=False).values_list('id',flat=True)) if ids is not None else None
        scope_changed=ids is not None and set(ids)!=old_ids
        critical=scope_changed or role!=old_role or enabled!=old_active
        before={k:getattr(user,k) for k in data}
        for key,value in data.items():
            setattr(user,key,value)
        user.is_superuser=user.is_staff=role=='admin'
        user.platform_role='operator' if role=='admin' else role
        if critical:
            user.session_version+=1
        user.save()
        if scope_changed:
            for host in Host.objects.filter(pk__in=old_ids-set(ids)):
                host.users.remove(user)
            for host in Host.objects.filter(pk__in=set(ids)-old_ids,is_deleted=False):
                host.users.add(user)
        changes={k:{'from':v,'to':data[k]} for k,v in before.items() if v!=data[k]}
        if role!=old_role:
            changes['role']={'from':old_role,'to':role}
        if scope_changed:
            changes['host_ids']={'from':sorted(old_ids),'to':sorted(set(ids))}
        if critical:
            revoke_jobs(user)
        if changes:
            audit(request.user,user,'updated',changes)
        return Response(account(user,True))


class PasswordInput(serializers.Serializer):
    password=serializers.CharField(min_length=12,max_length=128,trim_whitespace=False,write_only=True)
    current_password=serializers.CharField(max_length=128,trim_whitespace=False,write_only=True,required=False)


class PasswordView(APIView):
    permission_classes=[IsAuthenticated]

    @transaction.atomic
    def post(self,request,pk=None):
        if pk is not None:
            admin(request)
        user=locked_target(request, pk if pk is not None else request.user.pk, require_admin=pk is not None)
        serializer=PasswordInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        if user.pk==request.user.pk and not user.check_password(serializer.validated_data.get('current_password','')):
            raise ValidationError('当前密码不正确')
        password=serializer.validated_data['password']
        verify_password(password,user)
        user.set_password(password)
        user.session_version+=1
        user.save(update_fields=['password','session_version'])
        audit(request.user,user,'password_changed',{})
        return Response({'message':'密码已更新，旧会话已撤销'})


class AccountAuditView(APIView):
    def get(self,request):
        admin(request)
        rows=AccountAudit.objects.select_related('actor','target').order_by('-id')
        pager=PageNumberPagination()
        pager.page_size=20
        data=[{'id':e.pk,'actor':e.actor.username if e.actor else '—','target':e.target.username if e.target else '—',
               'action':e.action,'changes':e.changes,'created_at':e.created_at} for e in pager.paginate_queryset(rows,request)]
        return pager.get_paginated_response(data)
