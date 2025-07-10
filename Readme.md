# ELD Trip Planner API

This repository provides a backend API for planning electronic logging device (ELD)-compliant trips. Given a starting point, pickup, and drop-off locations, the API calculates the route, estimates distance, inserts appropriate rest and fuel stops, and generates ELD-compliant log entries based on U.S. cycle rules.

---

## 🚀 Features

- Geocoding with retries and caching
- Route generation using OpenRouteService
- Intelligent stop calculation (pickup, fuel, rest, drop-off)
- ELD-compliant log sheet generation based on FMCSA cycle hours
- Fallback logic when API calls fail or insufficient data is returned

---

## 📋 Pre-requisites

Before running the project, ensure you have:

- Python 3.9+
- SQLite
- Django 3.2+ and Django REST Framework
- An [OpenRouteService](https://openrouteservice.org/dev/#/signup) API key

---

## 🔧 Local Setup

1. **Clone the repository**

   ```bash
   git clone https://github.com/your-username/eld-trip-planner.git
   cd eld-trip-planner

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Apply migrations**

   ```bash
   python manage.py migrate
   ```

4. **(Optional) Create a superuser for admin access**

   ```bash
   python manage.py createsuperuser
   ```

---

## ▶️ Running the Project

Start the development server:

```bash
python manage.py runserver
```

Test the trip creation API with a `POST` request to:

```
POST /api/trips/
```

### Example payload:

```json
{
  "current_location": "Chicago, IL",
  "pickup_location": "Indianapolis, IN",
  "dropoff_location": "Cleveland, OH",
  "current_cycle_used": 10
}
```

---

## 📦 API Output

* Total route distance
* GeoJSON route geometry
* Generated stop points (pickup, fuel, rest, dropoff)
* Detailed daily ELD logs with timestamps, driving/rest durations, and locations

---

## 📁 Project Structure (Relevant Parts)

```
trips/
├── views.py              # TripCreateView - main API entrypoint
├── services.py           # All route/stops/geocoding logic
├── models.py             # Trip & Stop models (not shown here)
├── serializers.py        # Input validation for Trip creation
```

---

## 📝 Notes

* ELD log logic simulates U.S. Hours of Service (HOS) regulations (70-hour/8-day cycle, 11-hour daily limit, 30-min rest after 8 hours).
* Route and POI resolution falls back gracefully to ensure the system remains usable even if APIs fail.

```
