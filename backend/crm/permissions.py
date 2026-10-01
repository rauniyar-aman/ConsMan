import json
from pathlib import Path
from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

MATRIX = json.loads(Path(__file__).with_name('permission_matrix.json').read_text())

def profile(user):
    if not user.is_authenticated or not user.is_active or not hasattr(user, 'staff'):
        raise PermissionDenied('An active staff profile is required.')
    return user.staff

def scope(user, action):
    result = MATRIX.get(profile(user).role, {}).get(action)
    if not result:
        raise PermissionDenied('Your role cannot perform this action.')
    return result

def scoped_people(user, queryset, action='view'):
    access = scope(user, action)
    if access == 'branch':
        return queryset.filter(branch=profile(user).branch)
    if access == 'own':
        from .models import AccessGrant
        grants=AccessGrant.objects.filter(user=user,active=True).values('person_id')
        return queryset.filter(Q(owner=user) | Q(pk__in=grants))
    if access in ['assigned','documents']:
        from .models import TeamMember
        return queryset.filter(pk__in=TeamMember.objects.filter(user=user,active=True).values('person_id'))
    return queryset

def branch_scope(user, queryset, action, field='branch'):
    access = scope(user, action)
    return queryset if access == 'full' else queryset.filter(**{field:profile(user).branch})
