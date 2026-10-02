from django.urls import path
from . import api
urlpatterns = [path('workspace/', api.workspace), path('people/<uuid:pk>/', api.person), path('people/<uuid:pk>/generate/', api.generate)]
