import logging
import asyncio
from aiohttp import web
from .db import Database
from .handlers import BotHandlers

logger = logging.getLogger(__name__)

class WebServer:
    def __init__(self, bots: dict, db: Database, host_url: str):
        self.bots = bots
        self.db = db
        self.host_url = host_url
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        """Настройка маршрутов веб-сервера."""
        self.app.router.add_post("/yoomoney_notify", self.handle_yoomoney_notify_generic)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_post("/health", self.handle_health)
        for bot_id in self.bots:
            self.app.router.add_post(f"/yoomoney_notify/{bot_id}", lambda request, bot_id=bot_id: self.handle_yoomoney_notify(request, bot_id))
            self.app.router.add_post(f"/save_payment/{bot_id}", lambda request, bot_id=bot_id: self.handle_save_payment(request, bot_id))
            self.app.router.add_post(f"/webhook/{bot_id}", lambda request, bot_id=bot_id: self.handle_webhook(request, bot_id))

    async def handle_yoomoney_notify_generic(self, request):
        """Обработчик YooMoney уведомлений без bot_id."""
        try:
            data = await request.post()
            label = data.get("label")
            if not label:
                logger.error("Отсутствует label")
                return web.Response(status=400, text="Missing label")

            bot_id = next((bid for bid in self.bots if self.db.get_user_by_label(bid, label)), None)
            if not bot_id:
                logger.error(f"Не найден bot_id для label={label}")
                return web.Response(status=400, text="Bot not found")

            return await self.handle_yoomoney_notify(request, bot_id)
        except Exception as e:
            logger.error(f"Ошибка обработки YooMoney: {e}")
            return web.Response(status=500)

    async def handle_yoomoney_notify(self, request, bot_id):
        """Обработчик YooMoney уведомлений с bot_id."""
        try:
            data = await request.post()
            handlers = self.bots[bot_id]["handlers"]
            if not handlers.verify_yoomoney_notification(data):
                logger.error(f"[{bot_id}] Неверный sha1_hash")
                return web.Response(status=400, text="Invalid hash")

            label = data.get("label")
            if not label:
                logger.error(f"[{bot_id}] Отсутствует label")
                return web.Response(status=400, text="Missing label")

            if data.get("notification_type") in ["p2p-incoming", "card-incoming"]:
                user_id = self.db.get_user_by_label(bot_id, label)
                if user_id:
                    self.db.update_payment_status(bot_id, label, "success")
                    bot = self.bots[bot_id]["bot"]
                    await bot.send_message(user_id, "Оплата успешно получена! Доступ к каналу активирован.")
                    invite_link = await handlers.create_invite_link(user_id)
                    if invite_link:
                        await bot.send_message(user_id, f"Присоединяйтесь к приватному каналу: {invite_link}")
                        logger.info(f"[{bot_id}] Успешная транзакция: label={label}, user_id={user_id}")
                    else:
                        await bot.send_message(user_id, "Ошибка создания ссылки. Свяжитесь с поддержкой.")
                        logger.error(f"[{bot_id}] Не удалось создать инвайт-ссылку")
                else:
                    logger.error(f"[{bot_id}] Label {label} не найден")
            return web.Response(status=200)
        except Exception as e:
            logger.error(f"[{bot_id}] Ошибка обработки YooMoney: {e}")
            return web.Response(status=500)

    async def handle_save_payment(self, request, bot_id):
        """Обработчик сохранения label:user_id."""
        try:
            data = await request.json()
            label = data.get("label")
            user_id = data.get("user_id")
            if not label or not user_id:
                logger.error(f"[{bot_id}] Отсутствует label или user_id")
                return web.Response(status=400, text="Missing label or user_id")

            self.db.save_payment(bot_id, label, user_id)
            return web.Response(status=200)
        except Exception as e:
            logger.error(f"[{bot_id}] Ошибка сохранения платежа: {e}")
            return web.Response(status=500)

    async def handle_health(self, request):
        """Обработчик проверки здоровья."""
        logger.info("Проверка здоровья сервера")
        return web.Response(status=200, text=f"Server is healthy, {len(self.bots)} bots running")

    async def handle_webhook(self, request, bot_id):
        """Обработчик webhook."""
        try:
            dp = self.bots[bot_id]["dp"]
            bot = self.bots[bot_id]["bot"]
            Bot.set_current(bot)
            dp.set_current(dp)
            update = await request.json()
            logger.info(f"[{bot_id}] Получено webhook-обновление")
            update_obj = types.Update(**update)
            asyncio.create_task(dp.process_update(update_obj))
            return web.Response(status=200)
        except Exception as e:
            logger.error(f"[{bot_id}] Ошибка обработки webhook: {e}")
            return web.Response(status=500)

    async def start(self, port: int):
        """Запуск веб-сервера."""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        logger.info(f"Веб-сервер запущен на порту {port}")
