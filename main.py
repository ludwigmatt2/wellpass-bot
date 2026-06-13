import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram.error import Conflict
from telegram.ext import Application

from bot.handlers import register_handlers
from core.poller import polling_loop
from core.scheduler import setup_scheduler

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

_scheduler = None
_poller_task = None


async def error_handler(update, context) -> None:
    if isinstance(context.error, Conflict):
        # Another instance is starting up — PTB will retry, just log and wait
        logger.warning("Telegram Conflict: another instance running, waiting for it to stop...")
        return
    logger.error(f"Unhandled exception: {context.error}", exc_info=context.error)


async def post_init(app: Application) -> None:
    global _scheduler, _poller_task
    # Use asyncio.create_task() directly — avoids PTB "not running" warning
    # and gives us a handle for clean cancellation on shutdown
    _poller_task = asyncio.create_task(polling_loop(app))
    _scheduler = setup_scheduler(app)
    logger.info("Polling loop and scheduler started")


async def post_shutdown(app: Application) -> None:
    global _scheduler, _poller_task
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    if _poller_task and not _poller_task.done():
        _poller_task.cancel()
        try:
            await _poller_task
        except asyncio.CancelledError:
            pass


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    register_handlers(app)
    app.add_error_handler(error_handler)

    logger.info("Starting Wellpass Bot")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
