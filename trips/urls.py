from django.urls import path

from .views import TripCreateView

urlpatterns = [
    path('trip/', TripCreateView.as_view(), name='trip-create'),
]
