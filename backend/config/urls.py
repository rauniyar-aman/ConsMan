from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from intake import api
urlpatterns = [path('api/v1/',include('crm.urls')),path('api/public/qr/<str:code>/',api.public_qr),path('api/public/intake/submissions/',api.submit),path('api/public/intake/submissions/<uuid:pk>/resume/',api.verification),path('api/public/intake/submissions/<uuid:pk>/otp/<str:operation>/',api.verification),path('api/public/intake/submissions/<uuid:pk>/phone/',api.verification,{'operation':'phone'}),path('api/public/messaging/gateway-webhook/',api.gateway_webhook),path('api/schema/', SpectacularAPIView.as_view(), name='schema'), path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'))]
