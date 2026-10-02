from django.urls import path
from . import api
from . import admin_api,webhooks

urlpatterns = [path('templates/', api.templates), path('people/<uuid:pk>/', api.person_messages),path('workspace/',admin_api.workspace),path('webhooks/<str:provider>/',webhooks.receive)]
urlpatterns += [path('opt-out/<str:token>/',webhooks.opt_out)]
