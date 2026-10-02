from django.urls import path
from . import api
urlpatterns=[path('entry/',api.entry),path('dashboard/',api.dashboard),path('messages/',api.messages),path('documents/<uuid:pk>/',api.document),path('staff/<uuid:pk>/',api.staff)]
