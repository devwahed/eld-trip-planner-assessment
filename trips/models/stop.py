from django.db import models

from trips.models.trip import Trip


class Stop(models.Model):
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="stops")
    location = models.CharField(max_length=255)
    stop_type = models.CharField(max_length=50, choices=(
        ('rest', 'Rest'), ('fuel', 'Fuel'), ('pickup', 'Pickup'), ('dropoff', 'Dropoff')))
    duration_minutes = models.IntegerField()
    sequence = models.IntegerField()
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['sequence']
