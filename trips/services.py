import re
import time
from datetime import datetime, timedelta

import openrouteservice
import requests
from django.core.cache import cache
from geopy.distance import great_circle
from geopy.geocoders import Nominatim
from requests import RequestException

from constants import FUEL_INTERVAL, REST_BREAK_INTERVAL, AVERAGE_SPEED, MAX_CYCLE_HOURS
from eld_trip_planner.settings import ORS_API_KEY

ors_client = openrouteservice.Client(key=ORS_API_KEY)
geolocator = Nominatim(user_agent="eld_trip_planner")


def geocode_location(address):
    """
    Returns (lat, lng) tuple from address string with caching
    """
    cache_key = make_cache_key(address)
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        geocode = ors_client.pelias_search(text=address)
        coords = geocode['features'][0]['geometry']['coordinates']
        result = (coords[1], coords[0])
        cache.set(cache_key, result, timeout=86400)
        return result
    except Exception as e:
        raise ValueError(f"Geocoding failed for {address}: {str(e)}")


def get_route_and_distance(origin, destination):
    """
    Returns route geometry and total distance in miles between two points.
    """
    try:
        coords = [origin[::-1], destination[::-1]]
        route = ors_client.directions(coords, profile='driving-car', format='geojson')
        distance_m = route['features'][0]['properties']['summary']['distance']
        return {
            'distance_miles': round(distance_m / 1609.34, 2),
            'geometry': route['features'][0]['geometry']
        }
    except openrouteservice.exceptions.ApiError as e:
        raise ValueError(f"Routing failed: {str(e)}")


def make_cache_key(address):
    # Replace all non-alphanumeric characters with underscores
    cleaned = re.sub(r'[^a-zA-Z0-9]', '_', address)
    return f"geocode_{cleaned.lower()}"


def get_route_with_waypoints(waypoints):
    """
    Get route geometry passing through multiple waypoints, safely handling optimization.
    """
    try:
        coords = [[lon, lat] for lat, lon in waypoints]
        params = {
            'coordinates': coords,
            'profile': 'driving-car',
            'format': 'geojson'
        }
        if len(coords) > 3:
            params['optimize_waypoints'] = True
        route = ors_client.directions(**params)
        distance_m = route['features'][0]['properties']['summary']['distance']
        return {
            'distance_miles': round(distance_m / 1609.34, 2),
            'geometry': route['features'][0]['geometry'],
            'waypoints': waypoints
        }
    except openrouteservice.exceptions.ApiError as e:
        raise ValueError(f"OpenRouteService API error: {str(e)}")
    except Exception as e:
        raise ValueError(f"Routing failed: {str(e)}")


def find_poi_near_location(coords, keywords, radius_km=10, max_retries=3):
    """
    Find a POI near coordinates using Nominatim. Accepts a list of keywords to try.
    """
    if isinstance(keywords, str):
        keywords = [keywords]

    for keyword in keywords:
        cache_key = f"poi_{keyword.replace(' ', '_')}_{coords[0]}_{coords[1]}_{radius_km}"
        cached = cache.get(cache_key)
        if cached:
            return cached
        for attempt in range(max_retries):
            try:
                time.sleep(1.5)
                reverse_cache_key = f"rev_{coords[0]}_{coords[1]}"
                location = cache.get(reverse_cache_key)
                if not location:
                    location = geolocator.reverse(
                        f"{coords[0]},{coords[1]}",
                        exactly_one=True,
                        timeout=10
                    )
                    if location:
                        cache.set(reverse_cache_key, location, timeout=86400)
                if not location:
                    continue
                bbox = get_viewbox(coords, radius_km)
                params = {
                    'q': keyword,
                    'format': 'jsonv2',
                    'limit': 1,
                    'dedupe': 1,
                    'countrycodes': 'us,ca',
                    'viewbox': f"{bbox[0][0]},{bbox[0][1]},{bbox[1][0]},{bbox[1][1]}",
                    'bounded': 1
                }
                response = requests.get(
                    'https://nominatim.openstreetmap.org/search',
                    params=params,
                    headers={'User-Agent': 'eld_trip_planner'},
                    timeout=10
                )
                if response.status_code == 200:
                    results = response.json()
                    if results:
                        result = {
                            'name': results[0].get('display_name', '').split(',')[0],
                            'address': results[0].get('display_name', ''),
                            'coordinates': (float(results[0]['lat']), float(results[0]['lon'])),
                            'distance_km': great_circle(
                                coords,
                                (float(results[0]['lat']), float(results[0]['lon']))
                            ).km
                        }
                        cache.set(cache_key, result, timeout=86400)
                        return result
                elif response.status_code == 429:
                    time.sleep(5 * (attempt + 1))
                    continue
            except (RequestException, KeyError, ValueError) as e:
                print(f"[find_poi_near_location] Keyword `{keyword}` attempt {attempt + 1} failed: {str(e)}")
                time.sleep(3)
                continue
    return None


def get_viewbox(coords, radius_km):
    """
    Create a bounding box around coordinates
    """
    lat, lng = coords
    delta = 0.009 * radius_km  # Km to degrees
    return [
        [lng - delta, lat - delta],
        [lng + delta, lat + delta]
    ]


def geocode_location_with_retry(address, max_retries=3):
    """
    Geocode with retry logic
    """
    for attempt in range(max_retries):
        try:
            return geocode_location(address)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)
    raise ValueError("Geocoding failed after retries")


def estimate_distance(current_loc, pickup_loc, dropoff_loc):
    """
    Fallback distance estimation when routing fails
    """
    try:
        cur_coords = geocode_location_with_retry(current_loc)
        pickup_coords = geocode_location_with_retry(pickup_loc)
        drop_coords = geocode_location_with_retry(dropoff_loc)

        leg1 = great_circle(cur_coords, pickup_coords).miles
        leg2 = great_circle(pickup_coords, drop_coords).miles
        return round(leg1 + leg2, 2)
    except:
        return 500


def calculate_stop_points(trip, total_miles, route_geometry):
    """
    Calculate pickup, fuel, rest, and dropoff stops along the route with fallbacks and correct sequence.
    """
    from trips.models import Stop

    stops = []
    sequence = 1
    estimated_drive_hours = total_miles / AVERAGE_SPEED

    try:
        if not route_geometry or 'coordinates' not in route_geometry:
            raise ValueError("Invalid route geometry provided")
        route_coords = route_geometry['coordinates']
        if len(route_coords) < 2:
            raise ValueError("Route geometry has insufficient points")

        # 1. Add pickup stop
        pickup_coords = geocode_location(trip.pickup_location)
        stops.append(Stop(
            trip=trip,
            location=trip.pickup_location,
            stop_type="pickup",
            duration_minutes=60,
            sequence=sequence,
            metadata={
                'coordinates': pickup_coords,
                'purpose': 'Load pickup'
            }
        ))
        sequence += 1

        # 2. Insert Fuel Stops every ~1000 miles
        fuel_keywords = ["fuel", "gas station", "truck stop", "petrol station"]
        fuel_needed = int(total_miles // FUEL_INTERVAL)
        for i in range(1, fuel_needed + 1):
            mile_marker = i * FUEL_INTERVAL
            if mile_marker >= total_miles * 0.95:
                break
            progress = mile_marker / total_miles
            idx = max(1, min(int(progress * len(route_coords)), len(route_coords) - 2))
            coords = route_coords[idx][::-1]

            poi = None
            for radius in [10, 20, 40]:
                poi = find_poi_near_location(coords, keywords=fuel_keywords, radius_km=radius)
                if poi:
                    break

            stops.append(Stop(
                trip=trip,
                location=poi['name'] if poi else f"Fuel stop #{i}",
                stop_type="fuel",
                duration_minutes=30,
                sequence=sequence,
                metadata={
                    'coordinates': poi['coordinates'] if poi else coords,
                    'address': poi['address'] if poi else '',
                    'estimated_mileage': mile_marker,
                    'purpose': f"Fuel stop #{i}",
                    'is_fallback': not bool(poi),
                    'search_radius_km': radius if poi else None
                }
            ))
            sequence += 1

        # 3. Insert Rest Stops every ~8 driving hours (~440 miles)
        rest_keywords = ["rest area", "truck rest area", "highway rest stop", "rest station"]
        for i in range(1, int(estimated_drive_hours // REST_BREAK_INTERVAL) + 1):
            break_miles = i * REST_BREAK_INTERVAL * AVERAGE_SPEED
            if break_miles >= total_miles * 0.9:
                break
            progress = break_miles / total_miles
            idx = max(1, min(int(progress * len(route_coords)), len(route_coords) - 2))
            coords = route_coords[idx][::-1]

            poi = None
            for radius in [10, 20, 40]:
                poi = find_poi_near_location(coords, keywords=rest_keywords, radius_km=radius)
                if poi:
                    break

            stops.append(Stop(
                trip=trip,
                location=poi['name'] if poi else f"Rest stop #{i}",
                stop_type="rest",
                duration_minutes=30,
                sequence=sequence,
                metadata={
                    'coordinates': poi['coordinates'] if poi else coords,
                    'address': poi['address'] if poi else '',
                    'purpose': f"Mandatory rest break #{i}",
                    'is_fallback': not bool(poi),
                    'search_radius_km': radius if poi else None
                }
            ))
            sequence += 1

        # 4. Add dropoff stop
        dropoff_coords = geocode_location(trip.dropoff_location)
        stops.append(Stop(
            trip=trip,
            location=trip.dropoff_location,
            stop_type="dropoff",
            duration_minutes=60,
            sequence=sequence,
            metadata={
                'coordinates': dropoff_coords,
                'purpose': 'Unload delivery'
            }
        ))

        return sorted(stops, key=lambda x: x.sequence)
    except Exception as e:
        print(f"[calculate_stop_points] Error: {e}")
        return [
            Stop(
                trip=trip,
                location=trip.pickup_location,
                stop_type="pickup",
                duration_minutes=60,
                sequence=1,
                metadata={'coordinates': geocode_location(trip.pickup_location)}
            ),
            Stop(
                trip=trip,
                location=trip.dropoff_location,
                stop_type="dropoff",
                duration_minutes=60,
                sequence=2,
                metadata={'coordinates': geocode_location(trip.dropoff_location)}
            )
        ]


def generate_eld_logs_with_stops(trip, total_miles):
    from geopy.distance import great_circle

    logs = []
    stops = list(trip.stops.order_by('sequence'))
    current_cycle_used = trip.current_cycle_used
    remaining_cycle_hours = MAX_CYCLE_HOURS - current_cycle_used

    if remaining_cycle_hours <= 0:
        raise ValueError("Driver has already exceeded 70-hr cycle.")

    day = 1
    current_hour = 0.0
    odometer = 0.0
    daily_drive_hours = 0.0

    for i, stop in enumerate(stops):
        # Distance from previous stop
        if i > 0:
            prev = stops[i - 1]
            prev_coords = prev.metadata.get("coordinates")
            stop_coords = stop.metadata.get("coordinates")
            if prev_coords and stop_coords:
                segment_miles = great_circle(prev_coords, stop_coords).miles
            else:
                segment_miles = total_miles / (len(stops) - 1)

            drive_hours = round(segment_miles / AVERAGE_SPEED, 2)

            while drive_hours > 0:
                # New day if over daily limit
                if daily_drive_hours >= 11 or current_hour >= 14:
                    logs.append({
                        "day": day,
                        "date": (datetime.now() + timedelta(days=day - 1)).strftime('%Y-%m-%d'),
                        "events": [],
                        "odometer": round(odometer, 2)
                    })
                    day += 1
                    current_hour = 0.0
                    daily_drive_hours = 0.0

                max_drive_today = min(11 - daily_drive_hours, drive_hours)
                event_start = current_hour
                event_end = current_hour + max_drive_today
                current_hour = event_end
                odometer += max_drive_today * AVERAGE_SPEED
                drive_hours -= max_drive_today
                daily_drive_hours += max_drive_today

                logs.append({
                    "day": day,
                    "date": (datetime.now() + timedelta(days=day - 1)).strftime('%Y-%m-%d'),
                    "events": [{
                        "type": "driving",
                        "start": round(event_start, 2),
                        "end": round(event_end, 2),
                        "location": f"Driving to {stop.location}",
                        "metadata": {
                            "from": prev.location,
                            "to": stop.location,
                            "estimated_miles": round(max_drive_today * AVERAGE_SPEED, 2)
                        }
                    }],
                    "odometer": round(odometer, 2)
                })

                if daily_drive_hours >= 8:
                    # Insert mandatory rest break
                    logs[-1]["events"].append({
                        "type": "on_duty",
                        "start": round(current_hour, 2),
                        "end": round(current_hour + 0.5, 2),
                        "location": "Mandatory 30-minute rest"
                    })
                    current_hour += 0.5
                    daily_drive_hours = 0.0

        # Add stop event
        stop_duration = stop.duration_minutes / 60
        if current_hour + stop_duration > 24:
            day += 1
            current_hour = 0.0
            daily_drive_hours = 0.0

        logs.append({
            "day": day,
            "date": (datetime.now() + timedelta(days=day - 1)).strftime('%Y-%m-%d'),
            "events": [{
                "type": stop.stop_type,
                "start": round(current_hour, 2),
                "end": round(current_hour + stop_duration, 2),
                "location": stop.location,
                "metadata": stop.metadata
            }],
            "odometer": round(odometer, 2)
        })
        current_hour += stop_duration

    return logs
