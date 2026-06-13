import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from telegram import Bot
from telegram.constants import ParseMode

import db.supabase as db
from wellpass import auth, api
from bot.messages import format_booking_confirmation, format_cancel_warning
from bot.keyboards import cancel_keyboard

logger = logging.getLogger(__name__)
_BERLIN = ZoneInfo("Europe/Berlin")

# watch_id -> last known availability (True = had free spot last check).
# Keyed per-watch (not per-session) so concurrent watchers of the same
# session get independent spot-open edge detection.
_availability_state: dict[str, bool] = {}

# watch_id -> last book-failure timestamp (UTC). Used to back off repeated
# booking attempts so a persistent failure does not hammer the endpoint.
_book_failed_at: dict[str, datetime] = {}
_BOOK_RETRY_COOLDOWN_S = 30

# user_id -> consecutive token-refresh failure count.
_token_fail_count: dict[str, int] = {}
_TOKEN_FAIL_NOTIFY_THRESHOLD = 3

# watch_id -> last (booked, capacity, waitlistBooked, status) seen. Used to emit
# a change-only AVAIL log line so a test run shows whether `booked` ever drops or
# the spot is absorbed by the waitlist — without spamming a line every 5s.
_last_counts: dict[str, tuple] = {}

# Timestamp (UTC) of the last fully-successful poll iteration. Exposed for /status.
_last_poll_ok: datetime | None = None


def last_successful_poll() -> "datetime | None":
    return _last_poll_ok


async def _send(bot: Bot, telegram_id: int, text: str, reply_markup=None) -> None:
    try:
        await bot.send_message(telegram_id, text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Failed to send message to {telegram_id}: {e}")


async def _notify_booked(bot: Bot, telegram_id: int, booking: dict, session: dict, gym_name: str) -> None:
    await _send(bot, telegram_id, format_booking_confirmation(booking, session, gym_name),
                reply_markup=cancel_keyboard(booking["id"]))


async def _notify_cancel_warning(bot: Bot, telegram_id: int, booking: dict) -> None:
    await _send(bot, telegram_id, format_cancel_warning(booking),
                reply_markup=cancel_keyboard(booking["booking_id"]))


async def _check_watches(bot: Bot) -> None:
    watches = await db.get_active_watches()
    if not watches:
        _availability_state.clear()
        return

    # Prune state for watches that no longer exist (keyed by watch id)
    active_ids = {w["id"] for w in watches}
    for wid in list(_availability_state):
        if wid not in active_ids:
            del _availability_state[wid]
    for wid in list(_book_failed_at):
        if wid not in active_ids:
            del _book_failed_at[wid]
    for wid in list(_last_counts):
        if wid not in active_ids:
            del _last_counts[wid]

    by_user: dict[str, list] = {}
    for w in watches:
        by_user.setdefault(w["user_id"], []).append(w)

    # Batch user DB reads in parallel
    user_ids = list(by_user.keys())
    users_list = await asyncio.gather(*[db.get_user_by_id(uid) for uid in user_ids])

    user_cache: dict[str, dict] = {}
    token_cache: dict[str, str] = {}
    for user_id, user in zip(user_ids, users_list):
        if not user:
            continue
        try:
            token = await auth.get_valid_token(user, db)
            user_cache[user_id] = user
            token_cache[user_id] = token
            _token_fail_count.pop(user_id, None)
        except Exception as e:
            # Escalate: a refresh failure means every watch for this user is dead
            # until they re-auth. Notify the user once after repeated failures.
            fails = _token_fail_count.get(user_id, 0) + 1
            _token_fail_count[user_id] = fails
            logger.error(f"Token refresh failed for user {user_id} (#{fails}): {e}")
            if fails == _TOKEN_FAIL_NOTIFY_THRESHOLD and user:
                await _send(bot, user["telegram_id"],
                            "⚠️ *Anmeldung fehlgeschlagen* — deine Überwachungen sind "
                            "pausiert. Bitte verbinde dich neu mit /start.")

    now = datetime.now(timezone.utc)

    for watch in watches:
        wid = watch["id"]
        try:
            user_id = watch["user_id"]
            token = token_cache.get(user_id)
            user = user_cache.get(user_id)
            if not token or not user:
                logger.warning(f"Skipping watch {wid[:8]} for user {user_id[:8]}: no valid token")
                continue

            try:
                session = await api.get_session(watch["session_id"], token)
            except Exception as e:
                logger.warning(f"Session fetch failed for watch {wid[:8]} [{watch['class_name']}]: {e}")
                continue

            end_raw = session.get("bookingWindowEnd")
            start_raw = session.get("startDateTime")
            cap = session.get("capacity")
            booked = session.get("booked")
            if not end_raw or not start_raw or cap is None or booked is None:
                logger.warning(f"Watch {wid[:8]} [{watch['class_name']}]: incomplete session payload — skipping")
                continue

            booking_end = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
            start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
            start_local = start_dt.astimezone(_BERLIN)
            free = cap - booked
            label = f"{watch['class_name']} {start_local.strftime('%a %d.%m %H:%M')}"

            # Change-only diagnostic: reveals whether `booked` ever drops or the
            # freed spot is swallowed by the Wellpass waitlist (booked stays full).
            wl = session.get("waitlistBooked")
            sess_status = session.get("status")
            counts = (booked, cap, wl, sess_status)
            if _last_counts.get(wid) != counts:
                _last_counts[wid] = counts
                logger.info(
                    f"AVAIL [{label}] free={free}/{cap} booked={booked} "
                    f"waitlistBooked={wl} status={sess_status}"
                )

            if booking_end <= now or start_dt <= now:
                logger.info(f"Expiring watch [{label}] — booking window closed")
                _availability_state.pop(wid, None)
                await db.expire_watch(wid)
                continue

            if session.get("status") != "ACTIVE":
                logger.info(f"Expiring watch [{label}] — session status={session.get('status')}")
                _availability_state.pop(wid, None)
                await db.expire_watch(wid)
                await _send(bot, user["telegram_id"],
                            f"ℹ️ Die Klasse *{watch['class_name']}* ({start_local.strftime('%a %d.%m %H:%M')}) "
                            f"wurde vom Studio abgesagt.")
                continue

            available = api.is_available(session)
            was_available = _availability_state.get(wid, False)

            if available != was_available:
                _availability_state[wid] = available
                if available:
                    logger.info(f"SPOT OPEN: [{label}] {free}/{cap} frei — attempting book")
                    await _send(bot, user["telegram_id"],
                                f"🔔 *Platz gesehen!*\n_{label}_\n{free}/{cap} frei — versuche zu buchen…")
                else:
                    logger.info(f"SPOT GONE: [{label}] wieder voll ({booked}/{cap})")

            if not available:
                continue

            # Back off if we recently failed booking this watch — require a fresh
            # availability edge plus a cooldown before re-POSTing.
            last_fail = _book_failed_at.get(wid)
            if last_fail and (now - last_fail).total_seconds() < _BOOK_RETRY_COOLDOWN_S:
                continue

            date_str = start_raw[:10]
            gym_id = session.get("gym", {}).get("serverGymsId", watch["gym_id"])
            if await db.has_booking_today(user_id, gym_id, date_str):
                logger.info(f"Daily limit reached for [{label}] — skipping")
                await _send(bot, user["telegram_id"],
                            f"⚠️ *Tägliches Limit erreicht*\nPlatz für _{label}_ gefunden, aber du hast heute bei diesem Studio bereits gebucht.")
                continue

            # Block 1: the booking itself. On failure the watch stays ACTIVE; we
            # drop the availability edge + set a cooldown so we don't hammer.
            try:
                booking = await api.book_class(watch["session_id"], token)
            except Exception as e:
                logger.exception(f"Auto-book FAILED [{label}]: {e}")
                _book_failed_at[wid] = now
                _availability_state.pop(wid, None)
                await _send(bot, user["telegram_id"],
                            f"❌ *Buchung fehlgeschlagen*\n_{label}_\nBitte versuche es erneut.")
                continue

            # Booking succeeded — commit terminal state immediately so we never
            # re-book on the next tick, regardless of bookkeeping outcome.
            _availability_state.pop(wid, None)
            _book_failed_at.pop(wid, None)
            await db.mark_watch_booked(wid)
            gym_name = session.get("gym", {}).get("name", watch["gym_name"])

            # The booking happened on Wellpass. If the response has no id we
            # cannot reconcile it in our DB — alert loudly rather than crash.
            bid = booking.get("id")
            if not bid:
                logger.error(
                    f"Auto-booked [{label}] for user {user_id[:8]} but response had no id — "
                    f"booking exists on Wellpass, DB untracked. Response: {booking}"
                )

            # Block 2: bookkeeping + notify. If this fails the user IS booked —
            # never tell them otherwise; just log for reconciliation.
            try:
                await db.add_booking(
                    user_id=user_id,
                    watch_id=wid,
                    booking_id=bid,
                    gym_id=gym_id,
                    gym_name=gym_name,
                    class_name=session.get("name", watch["class_name"]),
                    start_datetime=start_raw,
                )
                await _notify_booked(bot, user["telegram_id"], booking, session, gym_name)
                logger.info(f"Auto-booked [{label}] for user {user_id[:8]}")
            except Exception as e:
                logger.error(
                    f"Booked-but-bookkeeping-FAILED [{label}] booking={booking.get('id')} "
                    f"user={user_id[:8]}: {e}"
                )
        except Exception as e:
            logger.exception(f"Unhandled error processing watch {wid[:8]}: {e}")
            continue



async def _check_cancel_warnings(bot: Bot) -> None:
    try:
        due = await db.get_bookings_cancel_warning_due()
    except Exception as e:
        logger.error(f"Cancel warning check failed: {e}")
        return

    for booking in due:
        try:
            telegram_id = None
            user_info = booking.get("users")
            if isinstance(user_info, dict):
                telegram_id = user_info.get("telegram_id")
            elif isinstance(user_info, list) and user_info:
                telegram_id = user_info[0].get("telegram_id")

            # Only flag warned after a successful send — an absent recipient
            # (missing user join) is left unflagged so it's retried next cycle.
            if telegram_id:
                await _notify_cancel_warning(bot, telegram_id, booking)
                await db.mark_cancel_warned(booking["id"])
            else:
                logger.warning(f"Cancel warning: no telegram_id for booking {booking.get('id')}")
        except Exception as e:
            logger.error(f"Cancel warning failed for booking {booking.get('id')}: {e}")
            continue


async def polling_loop(app) -> None:
    global _last_poll_ok
    bot: Bot = app.bot
    logger.info("Polling loop started")
    iteration = 0
    base_interval = 5
    backoff_until = 0.0  # consecutive transport/HTTP-error backoff sleep (s)
    consec_errors = 0
    while True:
        try:
            await _check_watches(bot)
            if iteration % 12 == 0:  # every 60s
                await _check_cancel_warnings(bot)
            _last_poll_ok = datetime.now(timezone.utc)
            consec_errors = 0
            backoff_until = 0.0
        except (httpx.HTTPStatusError, httpx.TransportError) as e:
            consec_errors += 1
            # Honour Retry-After on rate limits / outages, else exponential backoff.
            retry_after = None
            resp = getattr(e, "response", None)
            if resp is not None:
                ra = resp.headers.get("Retry-After")
                if ra and ra.isdigit():
                    retry_after = int(ra)
            backoff_until = retry_after if retry_after is not None else min(
                base_interval * (2 ** consec_errors), 60
            )
            logger.warning(f"Polling backing off {backoff_until:.0f}s after error #{consec_errors}: {e}")
        except Exception as e:
            logger.error(f"Polling iteration error: {e}")
        await asyncio.sleep(max(base_interval, backoff_until))
        iteration += 1
