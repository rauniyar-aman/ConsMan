from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import api,operations,settings_api,authentication,reports,duplicate_api
from qr import api as qr_api
from intake import api as intake_api
from data_import import api as import_api
router = DefaultRouter()
router.register('people', api.PersonViewSet, basename='person')
urlpatterns = [
    path('reports/',reports.reports),
    path('duplicate-check/',duplicate_api.duplicate_check),
    path('health/',api.health),path('auth/session/',api.session),path('auth/login/',api.LoginView.as_view()),path('auth/logout/',api.logout_view),path('auth/mfa/setup/',authentication.mfa_setup),path('auth/mfa/verify/',authentication.mfa_verify),
    path('masters/',api.masters),path('dashboard/',api.dashboard),path('followups/',api.followup_queue),path('followups/<int:pk>/complete/',api.complete_followup),path('followups/<int:pk>/update/',operations.followup_update),
    path('people/<uuid:pk>/profile/',operations.edit_person),path('people/<uuid:pk>/lifecycle/',operations.lifecycle),path('people/<uuid:pk>/reassign/',operations.reassign),path('people/<uuid:pk>/archive/',operations.archive),
    path('people/<uuid:pk>/contacts/',operations.contacts),path('people/<uuid:pk>/contacts/<int:contact_id>/',operations.contacts),path('people/<uuid:pk>/education/',operations.education,{'kind':'education'}),path('people/<uuid:pk>/education/<int:item_id>/',operations.education,{'kind':'education'}),path('people/<uuid:pk>/test-scores/',operations.education,{'kind':'test_scores'}),path('people/<uuid:pk>/test-scores/<int:item_id>/',operations.education,{'kind':'test_scores'}),path('people/<uuid:pk>/activities/<int:item_id>/correct/',operations.correct_activity),path('people/<uuid:pk>/team/',settings_api.team_member),
    path('discovery/',operations.discovery),path('access-requests/',operations.access_requests),path('access-requests/<int:pk>/decide/',operations.decide_access),path('tasks/',operations.tasks),path('tasks/<int:pk>/update/',operations.task_update),path('notifications/',operations.notifications),path('duplicate-review/',operations.duplicates_review),path('duplicate-review/<int:pk>/dismiss/',operations.duplicates_review),path('merges/',operations.merge_people),path('merges/<int:pk>/reverse/',operations.reverse_merge),path('bulk/',operations.bulk),path('export/',operations.export_people),path('audit/',operations.audit_log),
    path('settings/',settings_api.settings_home),path('settings/proposals/',settings_api.propose_master),path('qr/',qr_api.qr_list),path('qr/<uuid:pk>/',qr_api.qr_update),path('qr/<uuid:pk>/download/',qr_api.qr_download),
    path('intake/submissions/',intake_api.staff_queue),path('intake/submissions/<uuid:pk>/<str:operation>/',intake_api.staff_queue),path('intake/assisted/',intake_api.staff_assisted),
    path('imports/',import_api.batches),path('imports/<uuid:pk>/',import_api.batch_action),path('imports/<uuid:pk>/commit/',import_api.commit),path('imports/<uuid:pk>/<str:operation>/',import_api.batch_action),
    path('',include(router.urls)),
]
