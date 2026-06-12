import asyncio
import logging
import os

from dotenv import load_dotenv
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


async def post_init(app: Application) -> None:
    global _scheduler
    app.create_task(polling_loop(app))
    _scheduler = setup_scheduler(app)
    logger.info("Bot initialized — polling loop and scheduler running")


async def post_shutdown(app: Application) -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)


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

    logger.info("Starting Wellpass Bot")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
