import uuid
import hashlib
import logging
from urllib.parse import urlencode
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import ClientSession
from config import BotConfig

logger = logging.getLogger(__name__)

class BotHandlers:
    def __init__(self, bot: Bot, config: BotConfig, db: 'Database', host_url: str):
        self.bot = bot
        self.config = config
        self.db = db
        self.host_url = host_url

    async def start_command(self, message: types.Message):
        """Обработчик команды /start."""
        try:
            user_id = str(message.from_user.id)
            chat_id = message.chat.id
            logger.info(f"[{self.bot.id}] Команда /start от user_id={user_id}")

            payment_label = str(uuid.uuid4())
            payment_params = {
                "quickpay-form": "shop",
                "paymentType": "AC",
                "targets": f"Оплата подписки для user_id={user_id}",
                "sum": self.config.price,
                "label": payment_label,
                "receiver": self.config.yoomoney_wallet,
                "successURL": f"https://t.me/{(await self.bot.get_me()).username}"
            }
            payment_url = f"https://yoomoney.ru/quickpay/confirm.xml?{urlencode(payment_params)}"

            self.db.save_payment(self.bot.id, payment_label, user_id)

            async with ClientSession() as session:
                save_payment_url = f"{self.host_url}/save_payment/{self.bot.id}"
                async with session.post(save_payment_url, json={"label": payment_label, "user_id": user_id}) as response:
                    if response.status != 200:
                        logger.error(f"[{self.bot.id}] Ошибка сохранения на /save_payment: {await response.text()}")
                        await self.bot.send_message(chat_id, "Ошибка сервера, попробуйте позже.")
                        return

            keyboard = InlineKeyboardMarkup()
            keyboard.add(InlineKeyboardButton(text="Оплатить", url=payment_url))
            welcome_text = self.config.description.format(price=self.config.price)
            await self.bot.send_message(
                chat_id,
                f"{welcome_text}\n\nПерейдите по ссылке для оплаты {self.config.price} рублей:",
                reply_markup=keyboard
            )
            logger.info(f"[{self.bot.id}] Отправлена ссылка на оплату: label={payment_label}")
        except Exception as e:
            logger.error(f"[{self.bot.id}] Ошибка в /start: {e}")
            await self.bot.send_message(chat_id, "Произошла ошибка, попробуйте позже.")

    async def create_invite_link(self, user_id: str) -> str:
        """Создание уникальной инвайт-ссылки."""
        try:
            invite_link = await self.bot.create_chat_invite_link(
                chat_id=self.config.private_channel_id,
                member_limit=1,
                name=f"Invite for user_{user_id}"
            )
            logger.info(f"[{self.bot.id}] Создана инвайт-ссылка для user_id={user_id}")
            return invite_link.invite_link
        except Exception as e:
            logger.error(f"[{self.bot.id}] Ошибка создания инвайт-ссылки: {e}")
            return None

    def verify_yoomoney_notification(self, data: dict) -> bool:
        """Проверка подлинности YooMoney уведомления."""
        try:
            params = [
                data.get("notification_type", ""),
                data.get("operation_id", ""),
                data.get("amount", ""),
                data.get("currency", ""),
                data.get("datetime", ""),
                data.get("sender", ""),
                data.get("codepro", ""),
                self.config.notification_secret,
                data.get("label", "")
            ]
            sha1_hash = hashlib.sha1("&".join(params).encode()).hexdigest()
            return sha1_hash == data.get("sha1_hash", "")
        except Exception as e:
            logger.error(f"[{self.bot.id}] Ошибка проверки YooMoney: {e}")
            return False
