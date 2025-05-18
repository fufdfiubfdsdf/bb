import logging
import asyncio
import os
import sys
from aiogram import Bot, Dispatcher
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from .config import load_bot_configs
from .db import Database
from .handlers import BotHandlers
from .web import WebServer

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

async def set_webhooks(bots: dict, host_url: str):
    """Установка webhooks для всех ботов."""
    for bot_id, bot_data in bots.items():
        try:
            bot = bot_data["bot"]
            webhook_url = f"{host_url}/webhook/{bot_id}"
            await bot.delete_webhook(drop_pending_updates=True)
            await bot.set_webhook(webhook_url)
            logger.info(f"[{bot_id}] Webhook установлен: {webhook_url}")
        except Exception as e:
            logger.error(f"[{bot_id}] Ошибка установки webhook: {e}")
            sys.exit(1)

async def main():
    try:
        # Загрузка конфигураций
        bot_configs = load_bot_configs()
        logger.info(f"Инициализация {len(bot_configs)} ботов")

        # Инициализация базы данных
        db_connection = os.getenv("DB_CONNECTION", "postgresql://postgres.bdjjtisuhtbrogvotves:Alex4382!@aws-0-eu-north-1.pooler.supabase.com:6543/postgres")
        db = Database(db_connection)

        # Инициализация ботов
        bots = {}
        host_url = os.getenv("HOST_URL", "https://your-app-name.onrender.com")
        for bot_id, config in bot_configs.items():
            try:
                bot = Bot(token=config.token)
                storage = MemoryStorage()
                dp = Dispatcher(bot, storage=storage)
                handlers = BotHandlers(bot, config, db, host_url)
                dp.register_message_handler(handlers.start_command, commands=['start'])
                bots[bot_id] = {"bot": bot, "dp": dp, "handlers": handlers}
                logger.info(f"[{bot_id}] Бот инициализирован")
            except Exception as e:
                logger.error(f"[{bot_id}] Ошибка инициализации бота: {e}")
                sys.exit(1)

        # Настройка веб-сервера
        web_server = WebServer(bots, db, host_url)
        await set_webhooks(bots, host_url)
        await web_server.start(port=int(os.getenv("PORT", 8000)))

        # Держим приложение работающим
        while True:
            await asyncio.sleep(3600)
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
