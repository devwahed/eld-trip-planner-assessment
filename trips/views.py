import time

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Stop
from .serializers import TripSerializer
from .services import (
    get_route_with_waypoints,
    calculate_stop_points, geocode_location_with_retry, estimate_distance, generate_eld_logs_with_stops,
)


class TripCreateView(APIView):
    def post(self, request):
        serializer = TripSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                'status': 'error',
                'errors': serializer.errors,
                'message': 'Invalid data provided'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            trip = serializer.save()
            max_retries = 3
            retry_count = 0
            route_data = None
            stops = []

            while retry_count < max_retries and not route_data:
                try:
                    cur_coords = geocode_location_with_retry(trip.current_location)
                    pickup_coords = geocode_location_with_retry(trip.pickup_location)
                    drop_coords = geocode_location_with_retry(trip.dropoff_location)

                    route_data = get_route_with_waypoints([cur_coords, pickup_coords, drop_coords])
                    total_miles = route_data['distance_miles']
                    stops = self.calculate_stops_with_fallback(trip, total_miles, route_data['geometry'])
                    Stop.objects.bulk_create(stops)

                except Exception as e:
                    retry_count += 1
                    if retry_count >= max_retries:
                        # Fallback to basic trip if all retries fail
                        stops = self.create_basic_trip_stops(trip)
                        total_miles = estimate_distance(
                            trip.current_location,
                            trip.pickup_location,
                            trip.dropoff_location
                        )
                    else:
                        time.sleep(2 ** retry_count)  # Exponential backoff
                    continue

            eld_logs = generate_eld_logs_with_stops(trip, total_miles)

            return Response({
                "status": "success",
                "trip": TripSerializer(trip).data,
                "total_miles": round(total_miles, 2),
                "eld_logs": eld_logs,
                "route_geometry": route_data['geometry'] if route_data else None,
                "stops": [{
                    "location": stop.location,
                    "stop_type": stop.stop_type,
                    "duration_minutes": stop.duration_minutes,
                    "sequence": stop.sequence,
                    "metadata": stop.metadata if hasattr(stop, 'metadata') else {}
                } for stop in stops],
                "warnings": ["Used fallback data"] if retry_count >= max_retries else []
            })

        except Exception as e:
            return Response({
                'status': 'error',
                'message': "Failed to create trip. Please try again.",
                'system_message': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def calculate_stops_with_fallback(self, trip, total_miles, route_geometry):
        """Calculate stops with multiple fallback layers"""
        try:
            stops = calculate_stop_points(trip, total_miles, route_geometry)
            if len(stops) < 2:  # At least pickup and dropoff
                raise ValueError("Insufficient stops calculated")
            return stops
        except Exception as e:
            return self.create_basic_trip_stops(trip)

    def create_basic_trip_stops(self, trip):
        """
        Create minimal trip with just pickup and dropoff
        """
        return [
            Stop(
                trip=trip,
                location=trip.pickup_location,
                stop_type="pickup",
                duration_minutes=60,
                sequence=1,
                metadata={
                    'purpose': 'Load pickup (fallback)',
                    'is_fallback': True
                }
            ),
            Stop(
                trip=trip,
                location=trip.dropoff_location,
                stop_type="dropoff",
                duration_minutes=60,
                sequence=2,
                metadata={
                    'purpose': 'Unload delivery (fallback)',
                    'is_fallback': True
                }
            )
        ]
