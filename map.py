"""
map.py — HOA Employee Portal · Server-side Geo-fence Validation
---------------------------------------------------------------
Validates employee clock-in/out coordinates against the office
geo-fence. Use this in your FastAPI / Flask / Supabase Edge Function
to prevent browser-side GPS spoofing.

Usage:
    from map import verify_clock, GeoPoint

    result = verify_clock(
        employee_id = "HOA-EMP-001",
        action      = "in",
        lat         = 28.6251,
        lng         = 77.3630,
        accuracy    = 18.0,        # metres, from browser GPS
    )
    print(result)
"""

import math
import json
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Literal, Optional


# ── Office Configuration ──────────────────────────────────────────────────────

OFFICE = {
    "name"    : "AltF Coworking, Joy Tower, Sector 62, Noida",
    "lat"     : 28.6248,
    "lng"     : 77.3633,
    "fence_m" : 200,          # geo-fence radius in metres
    "timezone": "Asia/Kolkata",
}

# Maximum browser-reported accuracy we will accept (metres).
# Rejects fixes from devices that couldn't get a proper GPS lock.
MAX_ACCEPTED_ACCURACY = 100


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class GeoPoint:
    latitude:  float
    longitude: float
    accuracy:  float = 0.0      # metres, reported by browser


@dataclass
class ClockEvent:
    employee_id : str
    action      : Literal["in", "out"]
    timestamp   : str           # ISO-8601 UTC
    lat         : float
    lng         : float
    accuracy    : float
    distance_m  : float         # metres from office
    inside_fence: bool
    status      : Literal["approved", "rejected"]
    reject_reason: Optional[str]


# ── Core Maths ────────────────────────────────────────────────────────────────

def haversine_metres(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Returns the great-circle distance in metres between two
    lat/lng coordinates using the Haversine formula.
    Accurate to within ~0.5% for distances up to a few kilometres.
    """
    R = 6_371_000  # Earth radius in metres

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi       = math.radians(lat2 - lat1)
    dlambda    = math.radians(lng2 - lng1)

    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)

    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def is_inside_fence(point: GeoPoint, fence_radius_m: float = None) -> tuple[bool, float]:
    """
    Returns (inside: bool, distance_metres: float).
    Adds half the reported accuracy as a buffer — if the device says
    it's at ±30m accuracy and 185m away, we treat it as potentially
    inside the 200m fence rather than rejecting unfairly.
    """
    radius = fence_radius_m or OFFICE["fence_m"]
    dist   = haversine_metres(
        point.latitude, point.longitude,
        OFFICE["lat"],  OFFICE["lng"]
    )
    # Accuracy buffer: give benefit of doubt equal to half reported accuracy
    effective_dist = max(0.0, dist - (point.accuracy / 2))
    return effective_dist <= radius, round(dist, 1)


# ── Validation ────────────────────────────────────────────────────────────────

def verify_clock(
    employee_id : str,
    action      : Literal["in", "out"],
    lat         : float,
    lng         : float,
    accuracy    : float = 0.0,
) -> ClockEvent:
    """
    Main entry point. Call this from your API endpoint when an
    employee submits a clock-in or clock-out request.

    Returns a ClockEvent with status "approved" or "rejected".
    Persist this object to your attendance table.
    """
    point = GeoPoint(latitude=lat, longitude=lng, accuracy=accuracy)
    now   = datetime.now(timezone.utc).isoformat()

    # ── Validation checks (in order of priority) ──────────────────────────────

    # 1. Coordinates must be plausible (not 0,0 or out of India bounds)
    if not _valid_coordinates(lat, lng):
        return ClockEvent(
            employee_id=employee_id, action=action, timestamp=now,
            lat=lat, lng=lng, accuracy=accuracy, distance_m=0,
            inside_fence=False, status="rejected",
            reject_reason="Invalid coordinates — possible GPS spoof detected.",
        )

    # 2. Accuracy must be within acceptable threshold
    if accuracy > MAX_ACCEPTED_ACCURACY:
        return ClockEvent(
            employee_id=employee_id, action=action, timestamp=now,
            lat=lat, lng=lng, accuracy=accuracy, distance_m=0,
            inside_fence=False, status="rejected",
            reject_reason=(
                f"GPS accuracy too low (±{accuracy:.0f}m). "
                f"Must be within ±{MAX_ACCEPTED_ACCURACY}m. "
                "Ask employee to move near a window or outdoors."
            ),
        )

    # 3. Geo-fence check
    inside, dist_m = is_inside_fence(point)
    if not inside:
        return ClockEvent(
            employee_id=employee_id, action=action, timestamp=now,
            lat=lat, lng=lng, accuracy=accuracy, distance_m=dist_m,
            inside_fence=False, status="rejected",
            reject_reason=(
                f"Outside geo-fence. Employee is {dist_m:.0f}m from office "
                f"(limit: {OFFICE['fence_m']}m)."
            ),
        )

    # ── All checks passed ─────────────────────────────────────────────────────
    return ClockEvent(
        employee_id=employee_id, action=action, timestamp=now,
        lat=lat, lng=lng, accuracy=accuracy, distance_m=dist_m,
        inside_fence=True, status="approved",
        reject_reason=None,
    )


def _valid_coordinates(lat: float, lng: float) -> bool:
    """
    Basic sanity check — rejects null island (0,0) and coordinates
    clearly outside India's bounding box.
    """
    if lat == 0.0 and lng == 0.0:
        return False
    # Approximate bounding box for India
    if not (6.5 <= lat <= 37.5):
        return False
    if not (68.0 <= lng <= 97.5):
        return False
    return True


# ── Helper: distance-only lookup ──────────────────────────────────────────────

def distance_from_office(lat: float, lng: float) -> float:
    """Returns metres from the HOA office. Useful for logging / dashboards."""
    return round(haversine_metres(lat, lng, OFFICE["lat"], OFFICE["lng"]), 1)


def fence_status(lat: float, lng: float, accuracy: float = 0.0) -> dict:
    """
    Quick summary dict — use this in a status endpoint if you want
    to show the employee their distance before they attempt to clock.
    """
    point = GeoPoint(lat, lng, accuracy)
    inside, dist_m = is_inside_fence(point)
    return {
        "distance_m"   : dist_m,
        "inside_fence" : inside,
        "fence_radius_m": OFFICE["fence_m"],
        "office"       : OFFICE["name"],
        "margin_m"     : round(OFFICE["fence_m"] - dist_m, 1),  # negative = outside
    }


# ── FastAPI integration example ───────────────────────────────────────────────
#
#   from fastapi import FastAPI, HTTPException
#   from pydantic import BaseModel
#   from map import verify_clock, fence_status
#
#   app = FastAPI()
#
#   class ClockPayload(BaseModel):
#       employee_id : str
#       action      : str          # "in" or "out"
#       lat         : float
#       lng         : float
#       accuracy    : float
#
#   @app.post("/api/clock")
#   def clock(payload: ClockPayload):
#       event = verify_clock(
#           employee_id = payload.employee_id,
#           action      = payload.action,
#           lat         = payload.lat,
#           lng         = payload.lng,
#           accuracy    = payload.accuracy,
#       )
#       if event.status == "rejected":
#           raise HTTPException(status_code=403, detail=event.reject_reason)
#       # TODO: save event to your DB here (Supabase / PostgreSQL)
#       return asdict(event)
#
#   @app.get("/api/fence-status")
#   def check_fence(lat: float, lng: float, accuracy: float = 0):
#       return fence_status(lat, lng, accuracy)


# ── CLI quick-test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("HOA Geo-fence Validator · Quick Test")
    print("=" * 60)

    tests = [
        # (label,            lat,      lng,      accuracy, action)
        ("Inside fence",     28.6251,  77.3630,  18.0,    "in"),
        ("On fence edge",    28.6248,  77.3651,  10.0,    "in"),
        ("Outside fence",    28.6300,  77.3700,  15.0,    "out"),
        ("Poor accuracy",    28.6248,  77.3633,  120.0,   "in"),
        ("Invalid coords",   0.0,      0.0,      5.0,     "in"),
        ("Outside India",    51.5074,  -0.1278,  10.0,    "in"),
    ]

    for label, lat, lng, acc, action in tests:
        result = verify_clock("HOA-EMP-001", action, lat, lng, acc)
        symbol = "✓" if result.status == "approved" else "✗"
        print(f"\n  {symbol}  {label}")
        print(f"     status   : {result.status.upper()}")
        print(f"     distance : {result.distance_m}m from office")
        if result.reject_reason:
            print(f"     reason   : {result.reject_reason}")

    print("\n" + "=" * 60)

    # fence_status example
    print("\nfence_status() example:")
    print(json.dumps(fence_status(28.6251, 77.3630, accuracy=22.0), indent=2))
