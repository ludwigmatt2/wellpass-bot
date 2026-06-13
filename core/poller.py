import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telegram import Bot
from telegram.constants import ParseMode

import db.supabase as db
from wellpass import auth, api
from bot.messages import format_booking_confirmation, format_cancel_warning
from bot.keyboards import cancel_keyboard

logger = logging.getLogger(__name__)
_BERLIN = ZoneInfo("Europe/Berlin")

# session_id -> last known availability (True = had free spot last check)
_availability_state: dict[str, bool] = {}


async def _notify_booked(bot: Bot, telegram_id: int, booking: dict, session: dict, gym_name: str) -> None:
    text = format_booking_confirmation(booking, session, gym_name)
    kb = cancel_keyboard(booking["id"])
    try:
        await bot.send_message(telegram_id, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Failed to notify {telegram_id}: {e}")


async def _notify_cancel_warning(bot: Bot, telegram_id: int, booking: dict) -> None:
    text = format_cancel_warning(booking)
    kb = cancel_keyboard(booking["booking_id"])
    try:
        await bot.send_message(telegram_id, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Failed to send cancel warning to {telegram_id}: {e}")


async def _send(bot: Bot, telegram_id: int, text: str) -> None:
    try:
        await bot.send_message(telegram_id, text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Failed to send message to {telegram_id}: {e}")


async def _check_watches(bot: Bot) -> None:
    watches = await db.get_active_watches()
    if not watches:
        return

    by_user: dict[str, list] = {}
    for w in watches:
        by_user.setdefault(w["user_id"], []).append(w)

    user_cache: dict[str, dict] = {}
    token_cache: dict[str, str] = {}

    for user_id, user_watches in by_user.items():
        try:
            user = await db.get_user_by_id(user_id)
            if not user:
                continue
            token = await auth.get_valid_token(user, db)
            user_cache[user_id] = user
            token_cache[user_id] = token
        except Exception as e:
            logger.error(f"Token refresh failed for user {user_id}: {e}")
            continue

    now = datetime.now(timezone.utc)

    for watch in watches:
        user_id = watch["user_id"]
        token = token_cache.get(user_id)
        user = user_cache.get(user_id)
        if not token or not user:
            continue

        try:
            session = await api.get_session(watch["session_id"], token)
        except Exception as e:
            logger.warning(f"Session fetch failed for watch {watch['id'][:8]} [{watch['class_name']}]: {e}")
            continue

        booking_end = datetime.fromisoformat(session["bookingWindowEnd"].replace("Z", "+00:00"))
        start_dt = datetime.fromisoformat(session["startDateTime"].replace("Z", "+00:00"))
        start_local = start_dt.astimezone(_BERLIN)
        free = session["capacity"] - session["booked"]
        label = f"{watch['class_name']} {start_local.strftime('%a %d.%m %H:%M')}"

        # Expire watches where booking window has passed
        if booking_end <= now or start_dt <= now:
            logger.info(f"Expiring watch [{label}] — booking window closed")
            _availability_state.pop(watch["session_id"], None)
            await db.expire_watch(watch["id"])
            continue

        if session.get("status") != "ACTIVE":
            logger.info(f"Expiring watch [{label}] — session status={session.get('status')}")
            _availability_state.pop(watch["session_id"], None)
            await db.expire_watch(watch["id"])
            try:
                await bot.send_message(
                    user["telegram_id"],
                    f"ℹ️ Die Klasse *{watch['class_name']}* ({start_local.strftime('%a %d.%m %H:%M')}) "
                    f"wurde vom Studio abgesagt.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
            continue

        available = api.is_available(session)
        was_available = _availability_state.get(watch["session_id"], False)

        # Log + notify on state change (full ↔ available)
        if available != was_available:
            _availability_state[watch["session_id"]] = available
            if available:
                logger.info(f"SPOT OPEN: [{label}] {free}/{session['capacity']} frei — attempting book")
                await _send(
                    bot, user["telegram_id"],
                    f"🔔 *Platz gesehen!*\n_{label}_\n{free}/{session['capacity']} frei — versuche zu buchen…"
                )
            else:
                logger.info(f"SPOT GONE: [{label}] wieder voll ({session['booked']}/{session['capacity']})")

        if not available:
            continue

        # Check daily booking limit
        date_str = session["startDateTime"][:10]
        gym_id = session.get("gym", {}).get("serverGymsId", watch["gym_id"])
        if await db.has_booking_today(user_id, gym_id, date_str):
            logger.info(f"Daily limit reached for [{label}] — skipping")
            await _send(
                bot, user["telegram_id"],
                f"⚠️ *Tägliches Limit erreicht*\nPlatz für _{label}_ gefunden, aber du hast heute bei diesem Studio bereits gebucht."
            )
            continue

        try:
            booking = await api.book_class(watch["session_id"], token)
            gym_name = session.get("gym", {}).get("name", watch["gym_name"])
            await db.add_booking(
                user_id=user_id,
                watch_id=watch["id"],
                booking_id=booking["id"],
                gym_id=gym_id,
                gym_name=gym_name,
                class_name=session["name"],
                start_datetime=session["startDateTime"],
            )
            await db.mark_watch_booked(watch["id"])
            _availability_state.pop(watch["session_id"], None)
            await _notify_booked(bot, user["telegram_id"], booking, session, gym_name)
            logger.info(f"Auto-booked [{label}] for user {user_id[:8]}")
        except Exception as e:
            logger.error(f"Auto-book FAILED [{label}]: {e}")
            await _send(
                bot, user["telegram_id"],
                f"❌ *Buchung fehlgeschlagen*\n_{label}_\nFehler: `{str(e)[:120]}`"
            )


async def _check_cancel_warnings(bot: Bot) -> None:
    try:
        due = await db.get_bookings_cancel_warning_due()
        for booking in due:
            telegram_id = None
            user_info = booking.get("users")
            if isinstance(user_info, dict):
                telegram_id = user_info.get("telegram_id")
            elif isinstance(user_info, list) and user_info:
                telegram_id = user_info[0].get("telegram_id")

            if telegram_id:
                await _notify_cancel_warning(bot, telegram_id, booking)
            await db.mark_cancel_warned(booking["id"])
    except Exception as e:
        logger.error(f"Cancel warning check failed: {e}")


async def polling_loop(app) -> None:
    bot: Bot = app.bot
    logger.info("Polling loop started")
    iteration = 0
    while True:
        try:
            await _check_watches(bot)
            if iteration % 4 == 0:
                await _check_cancel_warnings(bot)
            if iteration % 20 == 0:  # heartbeat every 5 min
                watches = await db.get_active_watches()
                logger.info(f"Poller alive — {len(watches)} active watch(es)")
        except Exception as e:
            logger.error(f"Polling iteration error: {e}")
        await asyncio.sleep(15)
        iteration += 1
