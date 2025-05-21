import logging
import sys
import uuid
import psycopg2
import hashlib
import qrcode
import io
import base64
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.executor import set_webhook
from aiohttp import web, ClientSession
from urllib.parse import urlencode
import traceback
import asyncio
import os
from config import load_bot_configs

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)
logger.info("Начало выполнения скрипта")

# Константы
SAVE_PAYMENT_PATH = "/save_payment"
YOOMONEY_NOTIFY_PATH = "/yoomoney_notify"
CRYPTO_NOTIFY_PATH = "/crypto_notify"
HEALTH_PATH = "/health"
WEBHOOK_PATH = "/webhook"
DB_CONNECTION = "postgresql://postgres.bdjjtisuhtbrogvotves:Alex4382!@aws-0-eu-north-1.pooler.supabase.com:6543/postgres"
HOST_URL = os.getenv("HOST_URL", "https://solar-galina-clubleness-a9e3e8d6.koyeb.app")
CRYPTOCLOUD_API_KEY = os.getenv("CRYPTOCLOUD_API_KEY", "your_cryptocloud_api_key_here")
CRYPTOCLOUD_SHOP_ID = os.getenv("CRYPTOCLOUD_SHOP_ID", "your_cryptocloud_shop_id_here")

# Платформа
PLATFORM = "koyeb"
logger.info(f"Обнаружена платформа: {PLATFORM}")

# Инициализация ботов
BOTS = load_bot_configs()
logger.info(f"Инициализация {len(BOTS)} ботов")
bots = {}
dispatchers = {}

for bot_id, config in BOTS.items():
    try:
        logger.info(f"Попытка инициализации бота {bot_id}")
        bots[bot_id] = Bot(token=config["TOKEN"])
        storage = MemoryStorage()
        dispatchers[bot_id] = Dispatcher(bots[bot_id], storage=storage)
        logger.info(f"Бот {bot_id} инициализирован")
    except Exception as e:
        logger.error(f"Ошибка инициализации бота {bot_id}: {e}")
        sys.exit(1)

# Инициализация базы данных
def init_postgres_db():
    try:
        conn = psycopg2.connect(DB_CONNECTION)
        c = conn.cursor()
        for bot_id in BOTS:
            c.execute(f'''CREATE TABLE IF NOT EXISTS payments_{bot_id}
                         (label TEXT PRIMARY KEY, user_id TEXT, status TEXT, payment_type TEXT)''')
        conn.commit()
        conn.close()
        logger.info("База данных инициализирована")
    except Exception as e:
        logger.error(f"Ошибка инициализации базы данных: {e}")
        sys.exit(1)

init_postgres_db()

# Обработчики команд
for bot_id, dp in dispatchers.items():
    @dp.message_handler(commands=['start'])
    async def start_command(message: types.Message, bot_id=bot_id):
        try:
            user_id = str(message.from_user.id)
            chat_id = message.chat.id
            bot = bots[bot_id]
            logger.info(f"[{bot_id}] /start от user_id={user_id}")

            # Предложить выбор способа оплаты
            keyboard = InlineKeyboardMarkup()
            keyboard.add(InlineKeyboardButton(text="Оплатить через YooMoney", callback_data=f"yoomoney_{user_id}"))
            keyboard.add(InlineKeyboardButton(text="Оплатить криптовалютой", callback_data=f"crypto_{user_id}"))
            config = BOTS[bot_id]
            await bot.send_message(
                chat_id,
                f"Выберите способ оплаты ({config['PRICE']} рублей):",
                reply_markup=keyboard
            )
            logger.info(f"[{bot_id}] Предложен выбор оплаты user_id={user_id}")
        except Exception as e:
            logger.error(f"[{bot_id}] Ошибка в /start: {e}\n{traceback.format_exc()}")
            await bots[bot_id].send_message(message.chat.id, "Произошла ошибка, попробуйте позже.")

    @dp.callback_query_handler(lambda c: c.data.startswith('yoomoney_'))
    async def process_yoomoney_payment(callback_query: types.CallbackQuery, bot_id=bot_id):
        try:
            user_id = callback_query.data.split('_')[1]
            chat_id = callback_query.message.chat.id
            bot = bots[bot_id]
            await bot.answer_callback_query(callback_query.id)
            logger.info(f"[{bot_id}] Выбрана оплата YooMoney для user_id={user_id}")

            # Создание платежной ссылки
            payment_label = str(uuid.uuid4())
            config = BOTS[bot_id]
            payment_params = {
                "quickpay-form": "shop",
                "paymentType": "AC",
                "targets": f"Оплата подписки для user_id={user_id}",
                "sum": config["PRICE"],
                "label": payment_label,
                "receiver": config["YOOMONEY_WALLET"],
                "successURL": f"https://t.me/{(await bot.get_me()).username}"
            }
            payment_url = f"https://yoomoney.ru/quickpay/confirm.xml?{urlencode(payment_params)}"

            # Сохранение в базу
            conn = psycopg2.connect(DB_CONNECTION)
            c = conn.cursor()
            c.execute(f"INSERT INTO payments_{bot_id} (label, user_id, status, payment_type) VALUES (%s, %s, %s, %s)",
                      (payment_label, user_id, "pending", "yoomoney"))
            conn.commit()
            conn.close()
            logger.info(f"[{bot_id}] Сохранено в базе: label={payment_label}, user_id={user_id}, type=yoomoney")

            # Отправка на /save_payment
            async with ClientSession() as session:
                try:
                    save_payment_url = f"{HOST_URL}{SAVE_PAYMENT_PATH}/{bot_id}"
                    logger.info(f"[{bot_id}] Отправка на {save_payment_url} для label={payment_label}")
                    async with session.post(save_payment_url, json={"label": payment_label, "user_id": user_id}) as response:
                        response_text = await response.text()
                        logger.info(f"[{bot_id}] Ответ /save_payment: status={response.status}, text={response_text[:100]}")
                        if response.status != 200:
                            logger.error(f"[{bot_id}] Ошибка /save_payment: status={response.status}")
                            await bot.send_message(chat_id, "Ошибка сервера, попробуйте позже.")
                            return
                except Exception as e:
                    logger.error(f"[{bot_id}] Ошибка запроса /save_payment: {e}")
                    await bot.send_message(chat_id, "Ошибка сервера, попробуйте позже.")
                    return

            # Отправка ссылки
            keyboard = InlineKeyboardMarkup()
            keyboard.add(InlineKeyboardButton(text="Оплатить", url=payment_url))
            welcome_text = config["DESCRIPTION"].format(price=config["PRICE"])
            await bot.send_message(
                chat_id,
                f"{welcome_text}\n\nПерейдите по ссылке для оплаты {config['PRICE']} рублей:",
                reply_markup=keyboard
            )
            logger.info(f"[{bot_id}] Ссылка YooMoney отправлена user_id={user_id}, label={payment_label}")
        except Exception as e:
            logger.error(f"[{bot_id}] Ошибка в YooMoney: {e}\n{traceback.format_exc()}")
            await bots[bot_id].send_message(chat_id, "Произошла ошибка, попробуйте позже.")

    @dp.callback_query_handler(lambda c: c.data.startswith('crypto_'))
    async def process_crypto_payment(callback_query: types.CallbackQuery, bot_id=bot_id):
        try:
            user_id = callback_query.data.split('_')[1]
            chat_id = callback_query.message.chat.id
            bot = bots[bot_id]
            await bot.answer_callback_query(callback_query.id)
            logger.info(f"[{bot_id}] Выбрана оплата криптовалютой для user_id={user_id}")

            # Выбор криптовалюты
            keyboard = InlineKeyboardMarkup()
            keyboard.add(InlineKeyboardButton(text="USDT", callback_data=f"crypto_usdt_{user_id}"))
            keyboard.add(InlineKeyboardButton(text="Bitcoin", callback_data=f"crypto_btc_{user_id}"))
            keyboard.add(InlineKeyboardButton(text="TON", callback_data=f"crypto_ton_{user_id}"))
            await bot.send_message(chat_id, "Выберите криптовалюту:", reply_markup=keyboard)
            logger.info(f"[{bot_id}] Предложен выбор криптовалюты для user_id={user_id}")
        except Exception as e:
            logger.error(f"[{bot_id}] Ошибка в выборе криптовалюты: {e}\n{traceback.format_exc()}")
            await bots[bot_id].send_message(chat_id, "Произошла ошибка, попробуйте позже.")

    @dp.callback_query_handler(lambda c: c.data.startswith('crypto_usdt_') or c.data.startswith('crypto_btc_') or c.data.startswith('crypto_ton_'))
    async def process_crypto_currency(callback_query: types.CallbackQuery, bot_id=bot_id):
        try:
            parts = callback_query.data.split('_')
            currency = parts[1].upper()
            user_id = parts[2]
            chat_id = callback_query.message.chat.id
            bot = bots[bot_id]
            await bot.answer_callback_query(callback_query.id)
            logger.info(f"[{bot_id}] Выбрана криптовалюта {currency} для user_id={user_id}")

            # Запрос инвойса в CryptoCloud
            config = BOTS[bot_id]
            invoice_id = str(uuid.uuid4())
            amount = config["PRICE"] / 80  # Пример конверсии RUB в USD (курс условный)
            async with ClientSession() as session:
                headers = {"Authorization": f"Token {CRYPTOCLOUD_API_KEY}"}
                data = {
                    "shop_id": CRYPTOCLOUD_SHOP_ID,
                    "amount": amount,
                    "currency": "USD",
                    "order_id": invoice_id,
                    "email": f"user_{user_id}@example.com",
                    "callback_url": f"{HOST_URL}{CRYPTO_NOTIFY_PATH}/{bot_id}"
                }
                async with session.post("https://api.cryptocloud.plus/v2/invoice/create", headers=headers, json=data) as response:
                    result = await response.json()
                    logger.debug(f"[{bot_id}] CryptoCloud ответ: {result}")
                    if result.get("status") != "success":
                        logger.error(f"[{bot_id}] Ошибка создания инвойса: {result}")
                        await bot.send_message(chat_id, "Ошибка создания платежа, попробуйте позже.")
                        return
                    wallet_address = result["result"]["address"]
                    invoice_url = result["result"]["link"]

            # Сохранение в базу
            conn = psycopg2.connect(DB_CONNECTION)
            c = conn.cursor()
            c.execute(f"INSERT INTO payments_{bot_id} (label, user_id, status, payment_type) VALUES (%s, %s, %s, %s)",
                      (invoice_id, user_id, "pending", f"crypto_{currency.lower()}"))
            conn.commit()
            conn.close()
            logger.info(f"[{bot_id}] Сохранено в базе: label={invoice_id}, user_id={user_id}, type=crypto_{currency.lower()}")

            # Генерация QR-кода
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(f"{currency.lower()}:{wallet_address}?amount={amount}")
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buffered = io.BytesIO()
            img.save(buffered, format="PNG")
            qr_base64 = base64.b64encode(buffered.getvalue()).decode()

            # Отправка адреса и QR-кода
            await bot.send_photo(
                chat_id,
                photo=buffered.getvalue(),
                caption=(
                    f"Оплатите {amount:.4f} {currency} на адрес:\n`{wallet_address}`\n\n"
                    f"Или используйте [ссылку на оплату]({invoice_url})\n"
                    "После оплаты бот автоматически отправит ссылку на канал."
                ),
                parse_mode="Markdown"
            )
            logger.info(f"[{bot_id}] Адрес и QR-код отправлены user_id={user_id}, invoice_id={invoice_id}")
        except Exception as e:
            logger.error(f"[{bot_id}] Ошибка в криптоплатеже: {e}\n{traceback.format_exc()}")
            await bots[bot_id].send_message(chat_id, "Произошла ошибка, попробуйте позже.")

    @dp.message_handler(commands=['debug'])
    async def debug_command(message: types.Message, bot_id=bot_id):
        try:
            user_id = str(message.from_user.id)
            chat_id = message.chat.id
            bot = bots[bot_id]
            config = BOTS[bot_id]
            logger.info(f"[{bot_id}] /debug от user_id={user_id}")

            # Проверка прав
            bot_member = await bot.get_chat_member(chat_id=config["PRIVATE_CHANNEL_ID"], user_id=(await bot.get_me()).id)
            permissions = f"Права на создание ссылок: {bot_member.can_invite_users}"

            # Проверка платежа
            conn = psycopg2.connect(DB_CONNECTION)
            c = conn.cursor()
            c.execute(f"SELECT label, status, payment_type FROM payments_{bot_id} WHERE user_id = %s ORDER BY label DESC LIMIT 1", (user_id,))
            result = c.fetchone()
            conn.close()
            payment_info = f"Последний платеж: {result[0]} (статус: {result[1]}, тип: {result[2]})" if result else "Платежей не найдено"

            # Тест ссылки
            invite = await create_unique_invite_link(bot_id, user_id)
            invite_status = f"Тест ссылки: {'Успех' if invite else 'Неудача'}"

            await bot.send_message(chat_id, f"Отладка:\n{permissions}\n{payment_info}\n{invite_status}")
            logger.info(f"[{bot_id}] Отладочная информация отправлена user_id={user_id}")
        except Exception as e:
            logger.error(f"[{bot_id}] Ошибка в /debug: {e}\n{traceback.format_exc()}")
            await bots[bot_id].send_message(chat_id, "Ошибка отладки, свяжитесь с @YourSupportHandle.")

    @dp.message_handler(commands=['info'])
    async def info_command(message: types.Message, bot_id=bot_id):
        try:
            bot = bots[bot_id]
            me = await bot.get_me()
            await message.answer(f"Я {me.username}")
            logger.info(f"[{bot_id}] Команда /info выполнена, username: {me.username}")
        except Exception as e:
            logger.error(f"[{bot_id}] Ошибка в /info: {e}\n{traceback.format_exc()}")
            await bots[bot_id].send_message(message.chat.id, "Ошибка, попробуйте позже.")

# Проверка YooMoney
def verify_yoomoney_notification(data, bot_id):
    try:
        config = BOTS[bot_id]
        params = [
            data.get("notification_type", ""),
            data.get("operation_id", ""),
            data.get("amount", ""),
            data.get("currency", ""),
            data.get("datetime", ""),
            data.get("sender", ""),
            data.get("codepro", ""),
            config["NOTIFICATION_SECRET"],
            data.get("label", "")
        ]
        sha1_hash = hashlib.sha1("&".join(params).encode()).hexdigest()
        logger.debug(f"[{bot_id}] YooMoney хэш: input={params}, computed={sha1_hash}, received={data.get('sha1_hash', '')}")
        return sha1_hash == data.get("sha1_hash", "")
    except Exception as e:
        logger.error(f"[{bot_id}] Ошибка проверки YooMoney: {e}")
        return False

# Создание инвайт-ссылки
async def create_unique_invite_link(bot_id, user_id):
    try:
        config = BOTS[bot_id]
        bot = bots[bot_id]
        bot_member = await bot.get_chat_member(chat_id=config["PRIVATE_CHANNEL_ID"], user_id=(await bot.get_me()).id)
        if not bot_member.can_invite_users:
            logger.error(f"[{bot_id}] Нет прав на создание ссылок для chat={config['PRIVATE_CHANNEL_ID']}")
            return None
        for attempt in range(5):
            try:
                invite_link = await bot.create_chat_invite_link(
                    chat_id=config["PRIVATE_CHANNEL_ID"],
                    member_limit=1,
                    name=f"Invite for user_{user_id}"
                )
                logger.info(f"[{bot_id}] Ссылка создана для user_id={user_id}: {invite_link.invite_link}")
                return invite_link.invite_link
            except Exception as e:
                logger.warning(f"[{bot_id}] Попытка {attempt + 1} не удалась для user_id={user_id}: {e}")
                await asyncio.sleep(2)
        logger.error(f"[{bot_id}] Не удалось создать ссылку после 5 попыток для user_id={user_id}")
        return None
    except Exception as e:
        logger.error(f"[{bot_id}] Ошибка создания ссылки для user_id={user_id}: {e}\n{traceback.format_exc()}")
        return None

# Поиск bot_id
def find_bot_id_by_label(label):
    try:
        for bot_id in BOTS:
            conn = psycopg2.connect(DB_CONNECTION)
            c = conn.cursor()
            c.execute(f"SELECT user_id FROM payments_{bot_id} WHERE label = %s", (label,))
            result = c.fetchone()
            conn.close()
            if result:
                logger.info(f"[{bot_id}] Найден бот для label={label}")
                return bot_id
        logger.warning(f"Бот не найден для label={label}")
        return None
    except Exception as e:
        logger.error(f"Ошибка поиска bot_id для label={label}: {e}")
        return None

# Обработчик YooMoney
async def handle_yoomoney_notify_generic(request):
    try:
        data = await request.post()
        logger.debug(f"[{PLATFORM}] YooMoney уведомление: {dict(data)}")
        label = data.get("label")
        if not label:
            logger.error(f"[{PLATFORM}] Отсутствует label")
            return web.Response(status=400, text="Missing label")
        bot_id = find_bot_id_by_label(label)
        if not bot_id:
            logger.error(f"[{PLATFORM}] Бот не найден для label={label}")
            return web.Response(status=400, text="Bot not found")
        if not verify_yoomoney_notification(data, bot_id):
            logger.error(f"[{bot_id}] Неверный хэш YooMoney")
            return web.Response(status=400, text="Invalid hash")
        if data.get("notification_type") in ["p2p-incoming", "card-incoming"]:
            conn = psycopg2.connect(DB_CONNECTION)
            c = conn.cursor()
            c.execute(f"SELECT user_id FROM payments_{bot_id} WHERE label = %s", (label,))
            result = c.fetchone()
            if result:
                user_id = result[0]
                c.execute(f"UPDATE payments_{bot_id} SET status = %s WHERE label = %s", ("success", label))
                conn.commit()
                bot = bots[bot_id]
                await bot.send_message(user_id, "Оплата успешно получена! Доступ активирован.")
                invite_link = await create_unique_invite_link(bot_id, user_id)
                if invite_link:
                    await bot.send_message(user_id, f"Присоединяйтесь к каналу: {invite_link}")
                    logger.info(f"[{bot_id}] Платеж обработан, ссылка отправлена: label={label}, user_id={user_id}")
                else:
                    await bot.send_message(user_id, "Ошибка создания ссылки. Свяжитесь с @YourSupportHandle.")
                    logger.error(f"[{bot_id}] Не удалось создать ссылку для user_id={user_id}")
            else:
                logger.error(f"[{bot_id}] Label {label} не найден в базе")
            conn.close()
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"[{PLATFORM}] Ошибка YooMoney: {e}\n{traceback.format_exc()}")
        return web.Response(status=500)

# Обработчик CryptoCloud
async def handle_crypto_notify(request, bot_id):
    try:
        data = await request.json()
        logger.debug(f"[{bot_id}] CryptoCloud уведомление: {data}")
        invoice_id = data.get("order_id")
        status = data.get("status")
        if not invoice_id or not status:
            logger.error(f"[{bot_id}] Отсутствует order_id или status")
            return web.Response(status=400, text="Missing data")
        if status == "success":
            conn = psycopg2.connect(DB_CONNECTION)
            c = conn.cursor()
            c.execute(f"SELECT user_id FROM payments_{bot_id} WHERE label = %s", (invoice_id,))
            result = c.fetchone()
            if result:
                user_id = result[0]
                c.execute(f"UPDATE payments_{bot_id} SET status = %s WHERE label = %s", ("success", invoice_id))
                conn.commit()
                bot = bots[bot_id]
                await bot.send_message(user_id, "Криптовалютный платеж получен! Доступ активирован.")
                invite_link = await create_unique_invite_link(bot_id, user_id)
                if invite_link:
                    await bot.send_message(user_id, f"Присоединяйтесь к каналу: {invite_link}")
                    logger.info(f"[{bot_id}] Криптоплатеж обработан, ссылка отправлена: invoice_id={invoice_id}, user_id={user_id}")
                else:
                    await bot.send_message(user_id, "Ошибка создания ссылки. Свяжитесь с @YourSupportHandle.")
                    logger.error(f"[{bot_id}] Не удалось создать ссылку для user_id={user_id}")
            else:
                logger.error(f"[{bot_id}] Invoice_id {invoice_id} не найден в базе")
            conn.close()
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"[{bot_id}] Ошибка CryptoCloud: {e}\n{traceback.format_exc()}")
        return web.Response(status=500)

# Обработчик /save_payment
async def handle_save_payment(request, bot_id):
    try:
        data = await request.json()
        label = data.get("label")
        user_id = data.get("user_id")
        logger.info(f"[{bot_id}] Запрос /save_payment: label={label}, user_id={user_id}")
        if not label or not user_id:
            logger.error(f"[{bot_id}] Отсутствует label или user_id")
            return web.Response(status=400, text="Missing data")
        conn = psycopg2.connect(DB_CONNECTION)
        c = conn.cursor()
        c.execute(f"INSERT INTO payments_{bot_id} (label, user_id, status, payment_type) VALUES (%s, %s, %s, %s) ON CONFLICT (label) DO UPDATE SET user_id = %s, status = %s",
                  (label, user_id, "pending", "unknown", user_id, "pending"))
        conn.commit()
        conn.close()
        logger.info(f"[{bot_id}] Сохранено: label={label}, user_id={user_id}")
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"[{bot_id}] Ошибка /save_payment: {e}\n{traceback.format_exc()}")
        return web.Response(status=500)

# Проверка здоровья
async def handle_health(request):
    logger.info(f"[{PLATFORM}] Запрос /health")
    return web.Response(status=200, text=f"Сервер работает, {len(BOTS)} ботов активны")

# Вебхук Telegram
async def handle_webhook(request, bot_id):
    try:
        if bot_id not in dispatchers:
            logger.error(f"[{bot_id}] Неизвестный bot_id")
            return web.Response(status=400, text="Unknown bot_id")
        bot = bots[bot_id]
        dp = dispatchers[bot_id]
        Bot.set_current(bot)
        dp.set_current(dp)
        update = await request.json()
        logger.debug(f"[{bot_id}] Webhook Telegram: {update}")
        update_obj = types.Update(**update)
        asyncio.create_task(dp.process_update(update_obj))
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"[{bot_id}] Ошибка вебхука Telegram: {e}\n{traceback.format_exc()}")
        return web.Response(status=500)

# Установка вебхуков
async def set_webhooks():
    logger.info(f"Настройка вебхуков для {len(BOTS)} ботов")
    for bot_id in bots:
        try:
            bot = bots[bot_id]
            webhook_url = f"{HOST_URL}{WEBHOOK_PATH}/{bot_id}"
            await bot.delete_webhook(drop_pending_updates=True)
            await bot.set_webhook(webhook_url)
            logger.info(f"[{bot_id}] Вебхук установлен: {webhook_url}")
        except Exception as e:
            logger.error(f"[{bot_id}] Ошибка установки вебхука: {e}")
            sys.exit(1)

# Настройка сервера
app = web.Application()
app.router.add_post(YOOMONEY_NOTIFY_PATH, handle_yoomoney_notify_generic)
app.router.add_get(HEALTH_PATH, handle_health)
app.router.add_post(HEALTH_PATH, handle_health)
for bot_id in BOTS:
    app.router.add_post(f"{YOOMONEY_NOTIFY_PATH}/{bot_id}", lambda request, bot_id=bot_id: handle_yoomoney_notify_generic(request))
    app.router.add_post(f"{CRYPTO_NOTIFY_PATH}/{bot_id}", lambda request, bot_id=bot_id: handle_crypto_notify(request, bot_id))
    app.router.add_post(f"{SAVE_PAYMENT_PATH}/{bot_id}", lambda request, bot_id=bot_id: handle_save_payment(request, bot_id))
    app.router.add_post(f"{WEBHOOK_PATH}/{bot_id}", lambda request, bot_id=bot_id: handle_webhook(request, bot_id))
logger.info(f"Маршруты настроены: {HEALTH_PATH}, {YOOMONEY_NOTIFY_PATH}, {CRYPTO_NOTIFY_PATH}, {SAVE_PAYMENT_PATH}, {WEBHOOK_PATH}")

# Запуск
async def main():
    try:
        await set_webhooks()
        logger.info("Запуск сервера")
        port = int(os.getenv("PORT", 8080))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        logger.info(f"Сервер запущен на порту {port}")
        logger.info(f"Маршрут: {HOST_URL}{HEALTH_PATH}")
        logger.info(f"Маршрут: {HOST_URL}{YOOMONEY_NOTIFY_PATH}")
        logger.info(f"Маршрут: {HOST_URL}{CRYPTO_NOTIFY_PATH}")
        for bot_id in BOTS:
            logger.info(f"Маршрут: {HOST_URL}{YOOMONEY_NOTIFY_PATH}/{bot_id}")
            logger.info(f"Маршрут: {HOST_URL}{CRYPTO_NOTIFY_PATH}/{bot_id}")
            logger.info(f"Маршрут: {HOST_URL}{SAVE_PAYMENT_PATH}/{bot_id}")
            logger.info(f"Маршрут: {HOST_URL}{WEBHOOK_PATH}/{bot_id}")
        while True:
            await asyncio.sleep(3600)
    except Exception as e:
        logger.error(f"Ошибка запуска: {e}\n{traceback.format_exc()}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
