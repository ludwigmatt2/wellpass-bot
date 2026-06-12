import asyncio
import logging
from datetime import datetime, timezone

from telegram import Bot
from telegram.constants import ParseMode

import db.supabase as db
from wellpass import auth, api
from bot.messages import format_booking_confirmation, format_cancel_warning
from bot.keyboards import cancel_keyboard

logger = logging.getLogger(__name__)


async def _notify_booked(bot: Bot, telegram_id: int, booking: dict, session: dict, gym_name: str) -> None:
    text = format_booking_confirmation(booking, session, gym_name)
    kb = cancel_keyboard(booking["id"], booking["id"])
    try:
        await bot.send_message(telegram_id, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Failed to notify {telegram_id}: {e}")


async def _notify_cancel_warning(bot: Bot, telegram_id: int, booking: dict) -> None:
    text = format_cancel_warning(booking)
    kb = cancel_keyboard(booking["booking_id"], booking["id"])
    try:
        await bot.send_message(telegram_id, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Failed to send cancel warning to {telegram_id}: {e}")


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
            logger.warning(f"Session fetch failed for watch {watch['id']}: {e}")
            continue

        booking_end = datetime.fromisoformat(session["bookingWindowEnd"].replace("Z", "+00:00"))
        start_dt = datetime.fromisoformat(session["startDateTime"].replace("Z", "+00:00"))

        # Expire watches where booking window has passed
        if booking_end <= now or start_dt <= now:
            await db.expire_watch(watch["id"])
            continue

        if session.get("status") != "ACTIVE":
            await db.expire_watch(watch["id"])
            try:
                await bot.send_message(
                    user["telegram_id"],
                    f"ℹ️ Die Klasse *{watch['class_name']}* ({start_dt.strftime('%a %d.%m %H:%M')}) "
                    f"wurde vom Studio abgesagt.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
            continue

        if not api.is_available(session):
            continue

        # Check daily booking limit
        date_str = session["startDateTime"][:10]
        gym_id = session.get("gym", {}).get("serverGymsId", watch["gym_id"])
        if await db.has_booking_today(user_id, gym_id, date_str):
            logger.info(f"User {user_id} already has booking for {gym_id} on {date_str}, skipping watch {watch['id']}")
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
            await _notify_booked(bot, user["telegram_id"], booking, session, gym_name)
            logger.info(f"Auto-booked {session['name']} for user {user_id}")
        except Exception as e:
            logger.error(f"Auto-book failed for watch {watch['id']}: {e}")


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
            if iteration % 4 == 0:  # every 60s
                await _check_cancel_warnings(bot)
        except Exception as e:
            logger.error(f"Polling iteration error: {e}")
        await asyncio.sleep(15)
        iteration += 1
