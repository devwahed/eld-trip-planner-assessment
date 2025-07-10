from rest_framework import serializers

from constants import MAX_CYCLE_HOURS
from .models import Trip, Stop


class StopSerializer(serializers.ModelSerializer):
    class Meta:
        model = Stop
        fields = ['trip', 'location', 'stop_type', 'duration_minutes', 'sequence', 'metadata']


class TripSerializer(serializers.ModelSerializer):
    class Meta:
        model = Trip
        fields = ['id', 'current_location', 'pickup_location', 'dropoff_location', 'current_cycle_used']
        extra_kwargs = {
            'current_cycle_used': {'required': True}
        }

    def validate_current_cycle_used(self, value):
        if value < 0:
            raise serializers.ValidationError("Cycle hours cannot be negative")
        if value > MAX_CYCLE_HOURS:
            raise serializers.ValidationError(f"Cycle hours cannot exceed {MAX_CYCLE_HOURS}")
        return value
