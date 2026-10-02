from django.urls import path
from . import api

urlpatterns = [path('templates/', api.templates), path('people/<uuid:pk>/', api.person_messages)]
