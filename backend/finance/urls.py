from django.urls import path
from . import api
urlpatterns=[path('',api.workspace),path('files/<int:pk>/',api.file_download),path('<str:kind>/<uuid:pk>/proof/',api.upload),path('<str:kind>/<str:pk>/events/',api.events),path('<str:kind>/<uuid:pk>/pdf/',api.pdf),path('export/<str:kind>/',api.export)]
