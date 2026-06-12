import httpx
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MWA_BASE = "https://mwa-api.int.api.egym.com/mwa/api"


def _mwa_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Accept-Language": "en-DE,en;q=0.5",
        "User-Agent": "Wellpass/3058 CFNetwork/3860.600.12 Darwin/25.5.0",
    }


def is_available(session: dict) -> bool:
    now = datetime.now(timezone.utc)
    booking_end = datetime.fromisoformat(session["bookingWindowEnd"].replace("Z", "+00:00"))
    free_spots = session["capacity"] - session["booked"]
    return free_spots > 0 and booking_end > now and session.get("status") == "ACTIVE"


async def get_schedule(gym_id: str, token: str, from_dt: datetime, to_dt: datetime) -> list:
    params = {
        "gymId": gym_id,
        "status": "ACTIVE",
        "from": from_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "to": to_dt.strftime("%Y-%m-%dT%H:%M:%S.999Z"),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{MWA_BASE}/class-booking/v1/class-session",
            params=params,
            headers=_mwa_headers(token),
        )
        resp.raise_for_status()
        return resp.json()


async def get_session(session_id: str, token: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{MWA_BASE}/class-booking/v1/class-session/{session_id}",
            headers=_mwa_headers(token),
        )
        resp.raise_for_status()
        return resp.json()


async def book_class(session_id: str, token: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{MWA_BASE}/class-booking/v1/booking",
            json={"classSessionId": session_id},
            headers=_mwa_headers(token),
        )
        resp.raise_for_status()
        return resp.json()


async def cancel_booking(booking_id: str, token: str) -> bool:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{MWA_BASE}/class-booking/v1/booking/{booking_id}/cancel",
            headers=_mwa_headers(token),
        )
        return resp.status_code in (200, 204)


async def get_user_bookings(token: str, from_dt: datetime) -> list:
    params = {
        "timeField": "END_TIME",
        "from": from_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "status": "BOOKED,CANCELLED_BY_GYM,WAITING_LIST,CHECKED_IN,NO_SHOW,CANCELLED_LATE,CANCELLED",
        "order": "ASC",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{MWA_BASE}/class-booking/v2/booking",
            params=params,
            headers=_mwa_headers(token),
        )
        resp.raise_for_status()
        return resp.json()


async def get_gym_by_slug(slug: str, token: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{MWA_BASE}/gym-finder/v1/gyms/{slug}",
            headers=_mwa_headers(token),
        )
        resp.raise_for_status()
        raw = resp.json()
        # Normalize: slug response uses "gymUUID" as the class-session gymId
        return {
            "serverGymsId": raw["gymUUID"],
            "name": raw.get("alias") or raw.get("gymChainName", slug),
            "slug": raw.get("slug", slug),
        }


async def _search_gyms_nearby(token: str, lat: float, lng: float) -> list:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{MWA_BASE}/gym-finder/v1/gyms/overview",
            params={
                "searchFilter": "wellpass",
                "limit": 1000,
                "latLong": f"{lat};{lng}",
                "radius": 30000,
            },
            headers=_mwa_headers(token),
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("gyms", [])


async def search_gyms(query: str, token: str, lat: float = 48.1351, lng: float = 11.5820) -> list:
    """3-step search: direct slug → muenchen-{slug} → coordinate search + client filter."""
    slug_attempt = query.strip().lower().replace(" ", "-")

    # Step 1: direct slug lookup
    try:
        gym = await get_gym_by_slug(slug_attempt, token)
        return [gym]
    except Exception:
        pass

    # Step 2: muenchen-{slug}
    try:
        gym = await get_gym_by_slug(f"muenchen-{slug_attempt}", token)
        return [gym]
    except Exception:
        pass

    # Step 3: coordinate search, filter client-side by alias field "a"
    raw_gyms = await _search_gyms_nearby(token, lat, lng)
    q = query.lower()
    matches = [g for g in raw_gyms if q in g.get("a", "").lower()]
    return [
        {
            "serverGymsId": g["g"],
            "name": g.get("a", g["g"]),
            "slug": g.get("slug", ""),
        }
        for g in matches[:10]
    ]


async def get_user_favourites(token: str) -> list:
    """Returns list of normalised gym dicts from user's Wellpass favourites."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{MWA_BASE}/gym-finder/v1/listings",
            params={"filters": "favourites"},
            headers=_mwa_headers(token),
        )
        resp.raise_for_status()
        data = resp.json()
        favs = data.get("favourites", [])
        result = []
        for fav in favs:
            gym = fav.get("gym", {})
            if gym.get("g"):
                result.append({
                    "serverGymsId": gym["g"],
                    "name": gym.get("a", gym["g"]),
                    "slug": gym.get("slug", ""),
                })
        return result
