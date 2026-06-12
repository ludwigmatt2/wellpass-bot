import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot
from telegram.constants import ParseMode

import db.supabase as db
from wellpass import auth, api
from bot.messages import format_schedule
from bot.keyboards import schedule_keyboard

logger = logging.getLogger(__name__)


async def send_weekly_schedule(app) -> None:
    bot: Bot = app.bot
    users = await db.get_all_users()
    logger.info(f"Sending weekly schedule to {len(users)} users")

    for user in users:
        try:
            studios = await db.get_user_studios(user["id"])
            if not studios:
                continue

            token = await auth.get_valid_token(user, db)
            now = datetime.now(timezone.utc)
            # Send tomorrow's schedule (most actionable on Sunday evening)
            tomorrow = (now + timedelta(days=1)).date()
            from_dt = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0, tzinfo=timezone.utc)
            to_dt = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 23, 59, 59, tzinfo=timezone.utc)

            for studio in studios:
                try:
                    sessions = await api.get_schedule(studio["gym_id"], token, from_dt, to_dt)
                    filters_list = await db.get_class_filters(user["id"], studio["gym_id"])
                    if filters_list:
                        filter_names = {f["class_name"].lower() for f in filters_list}
                        sessions = [s for s in sessions if s["name"].lower() in filter_names]

                    text = "📅 *Neue Woche, neue Klassen!*\n\n" + format_schedule(
                        sessions, studio["gym_name"], filters_list, tomorrow
                    )
                    keyboard = schedule_keyboard(sessions, studio["gym_id"], tomorrow)
                    await bot.send_message(
                        user["telegram_id"],
                        text,
                        reply_markup=keyboard,
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception as e:
                    logger.error(f"Weekly schedule error for studio {studio['gym_id']}: {e}")
        except Exception as e:
            logger.error(f"Weekly schedule error for user {user['id']}: {e}")


def setup_scheduler(app) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Berlin")
    scheduler.add_job(
        send_weekly_schedule,
        CronTrigger(day_of_week="sun", hour=18, minute=0, timezone="Europe/Berlin"),
        args=[app],
        id="weekly_schedule",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started — weekly schedule job registered (Sun 18:00 Europe/Berlin)")
    return scheduler
