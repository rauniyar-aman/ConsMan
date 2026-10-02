from django.urls import path
from . import api

urlpatterns=[
    path('catalog/',api.catalog),path('catalog/<str:kind>/',api.catalog),path('catalog/<str:kind>/<int:pk>/',api.catalog),
    path('applications/',api.applications),path('applications/<uuid:pk>/',api.application_detail),
    path('applications/<uuid:pk>/offers/',api.offers),path('applications/<uuid:pk>/offers/<int:offer_id>/',api.offers),
    path('preferences/<uuid:person_id>/',api.preferences),
    path('documents/',api.document_create),path('documents/upload/',api.document_batch_upload),path('documents/<uuid:pk>/',api.document_detail),path('documents/<uuid:pk>/versions/<int:version>/',api.document_download),
    path('deadlines/',api.deadlines),path('deadlines/<int:pk>/',api.deadlines),
    path('blockers/',api.blocker_queue),path('blockers/<int:pk>/',api.blocker_queue),path('dashboard/',api.dashboard),
]
