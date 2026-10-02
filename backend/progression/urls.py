from django.urls import path
from . import api
urlpatterns=[path('workflows/',api.workflows),path('applications/<uuid:pk>/',api.journey)]
