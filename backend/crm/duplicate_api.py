from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from .models import Person
from .permissions import scope,scoped_people
from .services import duplicate_signals,normalize_phone

@extend_schema(request=OpenApiTypes.OBJECT,responses=OpenApiTypes.OBJECT)
@api_view(['POST'])
def duplicate_check(request):
    scope(request.user,'create')
    name=serializers.CharField(max_length=160,allow_blank=True).run_validation(request.data.get('full_name',''))
    phone=normalize_phone(request.data['phone']) if request.data.get('phone') else ''
    email=serializers.EmailField(allow_blank=True).run_validation(request.data.get('email','')).lower()
    matches=duplicate_signals(phone,email,name,request.data.get('dob'),country=request.data.get('preferred_country',''))
    visible=set(scoped_people(request.user,Person.objects.filter(pk__in=[m['person'].pk for m in matches])).values_list('pk',flat=True))
    return Response([{'id':str(m['person'].pk),'confidence':m['confidence'],'owner':m['person'].owner.get_full_name() if m['person'].owner else 'Unassigned','branch':m['person'].branch.name,**({'name':m['person'].full_name,'ref':m['person'].ref,'signals':m['signals']} if m['person'].pk in visible else {'masked':True})} for m in matches[:20]])
