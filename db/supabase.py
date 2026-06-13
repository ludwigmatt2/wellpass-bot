"""
All database operations. Uses sync supabase-py wrapped in asyncio.to_thread.

Required SQL schema (run in Supabase SQL editor):

CREATE TABLE users (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  telegram_id     BIGINT UNIQUE NOT NULL,
  telegram_name   TEXT,
  wellpass_email  TEXT NOT NULL,
  wellpass_pass   TEXT NOT NULL,
  exerciser_id    TEXT,
  jsessionid      TEXT,
  access_token    TEXT,
  token_expires   TIMESTAMPTZ,
  session_expires TIMESTAMPTZ,
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE user_studios (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID REFERENCES users(id) ON DELETE CASCADE,
  gym_id       TEXT NOT NULL,
  gym_name     TEXT NOT NULL,
  gym_slug     TEXT NOT NULL,
  active       BOOLEAN DEFAULT true,
  created_at   TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, gym_id)
);

CREATE TABLE watches (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID REFERENCES users(id) ON DELETE CASCADE,
  gym_id          TEXT NOT NULL,
  gym_name        TEXT NOT NULL,
  session_id      TEXT NOT NULL,
  class_name      TEXT NOT NULL,
  start_datetime  TIMESTAMPTZ NOT NULL,
  status          TEXT DEFAULT 'ACTIVE',
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE bookings (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID REFERENCES users(id) ON DELETE CASCADE,
  watch_id        UUID REFERENCES watches(id),
  booking_id      TEXT,
  gym_id          TEXT,
  gym_name        TEXT,
  class_name      TEXT,
  start_datetime  TIMESTAMPTZ,
  booked_at       TIMESTAMPTZ DEFAULT now(),
  status          TEXT DEFAULT 'BOOKED',
  cancel_warned   BOOLEAN DEFAULT false
);

CREATE TABLE class_filters (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID REFERENCES users(id) ON DELETE CASCADE,
  gym_id      TEXT NOT NULL,
  class_name  TEXT NOT NULL,
  created_at  TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, gym_id, class_name)
);
"""

import asyncio
import os
import logging
from supabase import create_client, Client

logger = logging.getLogger(__name__)
_client: Client | None = None


def _get() -> Client:
    global _client
    if _client is None:
        _client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _client


async def _run(fn):
    return await asyncio.to_thread(fn)


# ── Users ─────────────────────────────────────────────────────────────────────

async def get_user_by_telegram_id(telegram_id: int) -> dict | None:
    result = await _run(lambda: _get().table("users").select("*").eq("telegram_id", telegram_id).execute())
    return result.data[0] if result.data else None


async def create_user(
    telegram_id: int,
    telegram_name: str,
    wellpass_email: str,
    wellpass_pass_encrypted: str,
    exerciser_id: str,
    jsessionid: str,
    session_expires: str,
    access_token: str,
    token_expires: str,
) -> dict:
    result = await _run(lambda: _get().table("users").insert({
        "telegram_id": telegram_id,
        "telegram_name": telegram_name,
        "wellpass_email": wellpass_email,
        "wellpass_pass": wellpass_pass_encrypted,
        "exerciser_id": exerciser_id,
        "jsessionid": jsessionid,
        "session_expires": session_expires,
        "access_token": access_token,
        "token_expires": token_expires,
    }).execute())
    return result.data[0]


async def update_token(user_id: str, access_token: str, token_expires: str) -> None:
    await _run(lambda: _get().table("users").update({
        "access_token": access_token,
        "token_expires": token_expires,
    }).eq("id", user_id).execute())


async def update_user_session(
    user_id: str,
    exerciser_id: str,
    jsessionid: str,
    session_expires: str,
    access_token: str,
    token_expires: str,
) -> None:
    await _run(lambda: _get().table("users").update({
        "exerciser_id": exerciser_id,
        "jsessionid": jsessionid,
        "session_expires": session_expires,
        "access_token": access_token,
        "token_expires": token_expires,
    }).eq("id", user_id).execute())


async def get_user_by_id(user_id: str) -> dict | None:
    result = await _run(lambda: _get().table("users").select("*").eq("id", user_id).execute())
    return result.data[0] if result.data else None


async def get_all_users() -> list:
    result = await _run(lambda: _get().table("users").select("*").execute())
    return result.data or []


# ── Studios ───────────────────────────────────────────────────────────────────

async def get_user_studios(user_id: str) -> list:
    result = await _run(lambda: _get().table("user_studios").select("*").eq("user_id", user_id).eq("active", True).execute())
    return result.data or []


async def add_user_studio(user_id: str, gym_id: str, gym_name: str, gym_slug: str) -> dict:
    result = await _run(lambda: _get().table("user_studios").upsert({
        "user_id": user_id,
        "gym_id": gym_id,
        "gym_name": gym_name,
        "gym_slug": gym_slug,
        "active": True,
    }, on_conflict="user_id,gym_id").execute())
    return result.data[0]


async def remove_user_studio(user_id: str, gym_id: str) -> None:
    await _run(lambda: _get().table("user_studios").update({"active": False}).eq("user_id", user_id).eq("gym_id", gym_id).execute())


# ── Watches ───────────────────────────────────────────────────────────────────

async def get_active_watches() -> list:
    result = await _run(lambda: _get().table("watches").select("*").eq("status", "ACTIVE").execute())
    return result.data or []


async def get_watches_for_user(user_id: str) -> list:
    result = await _run(lambda: _get().table("watches").select("*").eq("user_id", user_id).eq("status", "ACTIVE").order("start_datetime").execute())
    return result.data or []


async def watch_exists(user_id: str, session_id: str) -> bool:
    result = await _run(lambda: _get().table("watches").select("id").eq("user_id", user_id).eq("session_id", session_id).eq("status", "ACTIVE").execute())
    return bool(result.data)


async def add_watch(
    user_id: str,
    gym_id: str,
    gym_name: str,
    session_id: str,
    class_name: str,
    start_datetime: str,
) -> dict:
    result = await _run(lambda: _get().table("watches").insert({
        "user_id": user_id,
        "gym_id": gym_id,
        "gym_name": gym_name,
        "session_id": session_id,
        "class_name": class_name,
        "start_datetime": start_datetime,
        "status": "ACTIVE",
    }).execute())
    return result.data[0]


async def cancel_watch(watch_id: str, user_id: str | None = None) -> int:
    def _do():
        q = _get().table("watches").update({"status": "CANCELLED"}).eq("id", watch_id)
        if user_id is not None:
            q = q.eq("user_id", user_id)
        return q.execute()
    result = await _run(_do)
    return len(result.data or [])


async def expire_watch(watch_id: str) -> None:
    await _run(lambda: _get().table("watches").update({"status": "EXPIRED"}).eq("id", watch_id).execute())


async def mark_watch_booked(watch_id: str) -> None:
    await _run(lambda: _get().table("watches").update({"status": "BOOKED"}).eq("id", watch_id).execute())


# ── Bookings ──────────────────────────────────────────────────────────────────

async def add_booking(
    user_id: str,
    watch_id: str | None,
    booking_id: str,
    gym_id: str,
    gym_name: str,
    class_name: str,
    start_datetime: str,
) -> dict:
    result = await _run(lambda: _get().table("bookings").insert({
        "user_id": user_id,
        "watch_id": watch_id,
        "booking_id": booking_id,
        "gym_id": gym_id,
        "gym_name": gym_name,
        "class_name": class_name,
        "start_datetime": start_datetime,
        "status": "BOOKED",
    }).execute())
    return result.data[0]


async def get_user_bookings_history(user_id: str, limit: int = 10) -> list:
    result = await _run(lambda: _get().table("bookings").select("*").eq("user_id", user_id).order("booked_at", desc=True).limit(limit).execute())
    return result.data or []


async def get_booking_by_wellpass_id(booking_id_wellpass: str, user_id: str) -> dict | None:
    result = await _run(lambda: _get().table("bookings")
        .select("*")
        .eq("booking_id", booking_id_wellpass)
        .eq("user_id", user_id)
        .execute())
    return result.data[0] if result.data else None


async def cancel_booking_record(booking_id_wellpass: str, user_id: str | None = None) -> int:
    def _do():
        q = _get().table("bookings").update({"status": "CANCELLED"}).eq("booking_id", booking_id_wellpass)
        if user_id is not None:
            q = q.eq("user_id", user_id)
        return q.execute()
    result = await _run(_do)
    return len(result.data or [])


async def has_booking_today(user_id: str, gym_id: str, date_str: str) -> bool:
    """date_str: YYYY-MM-DD interpreted as a Europe/Berlin calendar day.

    The day window is built in Europe/Berlin and converted to UTC so the
    TIMESTAMPTZ comparison matches the Postgres-normalized start_datetime
    regardless of UTC/Berlin offset boundaries.
    """
    from datetime import datetime, time, timezone
    from zoneinfo import ZoneInfo
    berlin = ZoneInfo("Europe/Berlin")
    day = datetime.strptime(date_str, "%Y-%m-%d").date()
    day_start_utc = datetime.combine(day, time(0, 0, 0), tzinfo=berlin).astimezone(timezone.utc)
    day_end_utc = datetime.combine(day, time(23, 59, 59), tzinfo=berlin).astimezone(timezone.utc)
    result = await _run(lambda: _get().table("bookings")
        .select("id")
        .eq("user_id", user_id)
        .eq("gym_id", gym_id)
        .eq("status", "BOOKED")
        .gte("start_datetime", day_start_utc.isoformat())
        .lte("start_datetime", day_end_utc.isoformat())
        .execute())
    return bool(result.data)


async def get_bookings_cancel_warning_due() -> list:
    """Bookings whose class starts within the next 13h and cancel_warned=false.

    The window is one-sided (now < start <= now+13h) so cancel_warned is the
    sole dedupe key — a booking can never be missed because its 12h mark fell
    in a sampling gap, and is warned exactly once regardless of poll timing.
    """
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    window_end = (now + timedelta(hours=13)).isoformat()
    result = await _run(lambda: _get().table("bookings")
        .select("*, users(telegram_id)")
        .eq("status", "BOOKED")
        .eq("cancel_warned", False)
        .gt("start_datetime", now.isoformat())
        .lte("start_datetime", window_end)
        .execute())
    return result.data or []


async def mark_cancel_warned(booking_db_id: str) -> None:
    await _run(lambda: _get().table("bookings").update({"cancel_warned": True}).eq("id", booking_db_id).execute())


# ── Filters ───────────────────────────────────────────────────────────────────

async def get_class_filters(user_id: str, gym_id: str) -> list:
    result = await _run(lambda: _get().table("class_filters").select("*").eq("user_id", user_id).eq("gym_id", gym_id).execute())
    return result.data or []


async def add_class_filter(user_id: str, gym_id: str, class_name: str) -> dict:
    result = await _run(lambda: _get().table("class_filters").upsert({
        "user_id": user_id,
        "gym_id": gym_id,
        "class_name": class_name,
    }, on_conflict="user_id,gym_id,class_name").execute())
    return result.data[0]


async def remove_class_filter(filter_id: str, user_id: str | None = None) -> int:
    def _do():
        q = _get().table("class_filters").delete().eq("id", filter_id)
        if user_id is not None:
            q = q.eq("user_id", user_id)
        return q.execute()
    result = await _run(_do)
    return len(result.data or [])


async def clear_class_filters(user_id: str, gym_id: str) -> None:
    await _run(lambda: _get().table("class_filters").delete().eq("user_id", user_id).eq("gym_id", gym_id).execute())
