import logging
import sys
import uuid
import psycopg2
import hashlib
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

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)
logger.info("Starting bot application")

# Constants
SAVE_PAYMENT_PATH = "/save_payment"
YOOMONEY_NOTIFY_PATH = "/yoomoney_notify"
HEALTH_PATH = "/health"
WEBHOOK_PATH = "/webhook"
DB_CONNECTION = "postgresql://postgres.bdjjtisuhtbrogvotves:Alex4382!@aws-0-eu-north-1.pooler.supabase.com:6543/postgres"
HOST_URL = os.getenv("HOST_URL", "https://solar-galina-clubleness-a9e3e8d6.koyeb.app")

# Platform detection
PLATFORM = "koyeb"
logger.info(f"Platform detected: {PLATFORM}")

# Bot initialization
BOTS = load_bot_configs()
logger.info(f"Initializing {len(BOTS)} bots")
bots = {}
dispatchers = {}

for bot_id, config in BOTS.items():
    try:
        logger.info(f"Setting up bot {bot_id}")
        bots[bot_id] = Bot(token=config["TOKEN"])
        storage = MemoryStorage()
        dispatchers[bot_id] = Dispatcher(bots[bot_id], storage=storage)
        logger.info(f"Bot {bot_id} initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize bot {bot_id}: {e}")
        sys.exit(1)

# Database setup
def init_postgres_db():
    try:
        conn = psycopg2.connect(DB_CONNECTION)
        c = conn.cursor()
        for bot_id in BOTS:
            c.execute(f'''CREATE TABLE IF NOT EXISTS payments_{bot_id}
                         (label TEXT PRIMARY KEY, user_id TEXT, status TEXT)''')
        conn.commit()
        conn.close()
        logger.info("PostgreSQL database initialized successfully")
    except Exception as e:
        logger.error(f"Database setup failed: {e}")
        sys.exit(1)

init_postgres_db()

# Command handlers
for bot_id, dp in dispatchers.items():
    @dp.message_handler(commands=['start'])
    async def start_command(message: types.Message, bot_id=bot_id):
        try:
            user_id = str(message.from_user.id)
            chat_id = message.chat.id
            bot = bots[bot_id]
            logger.info(f"[{bot_id}] /start command from user_id={user_id}")

            # Generate payment link
            payment_label = str(uuid.uuid4())
            config = BOTS[bot_id]
            payment_params = {
                "quickpay-form": "shop",
                "paymentType": "AC",
                "targets": f"Subscription payment for user_id={user_id}",
                "sum": config["PRICE"],
                "label": payment_label,
                "receiver": config["YOOMONEY_WALLET"],
                "successURL": f"https://t.me/{(await bot.get_me()).username}"
            }
            payment_url = f"https://yoomoney.ru/quickpay/confirm.xml?{urlencode(payment_params)}"

            # Store in PostgreSQL
            conn = psycopg2.connect(DB_CONNECTION)
            c = conn.cursor()
            c.execute(f"INSERT INTO payments_{bot_id} (label, user_id, status) VALUES (%s, %s, %s)",
                      (payment_label, user_id, "pending"))
            conn.commit()
            conn.close()
            logger.info(f"[{bot_id}] Stored in PostgreSQL: label={payment_label}, user_id={user_id}")

            # Send to /save_payment
            async with ClientSession() as session:
                try:
                    save_payment_url = f"{HOST_URL}{SAVE_PAYMENT_PATH}/{bot_id}"
                    logger.info(f"[{bot_id}] Sending to {save_payment_url} for label={payment_label}")
                    async with session.post(save_payment_url, json={"label": payment_label, "user_id": user_id}) as response:
                        response_text = await response.text()
                        logger.info(f"[{bot_id}] Save payment response: status={response.status}, text={response_text[:100]}")
                        if response.status != 200:
                            logger.error(f"[{bot_id}] Save payment failed: status={response.status}")
                            await bot.send_message(chat_id, "Server error, try again later.")
                            return
                except Exception as e:
                    logger.error(f"[{bot_id}] Save payment request failed: {e}")
                    await bot.send_message(chat_id, "Server error, try again later.")
                    return

            # Send response with payment button
            keyboard = InlineKeyboardMarkup()
            keyboard.add(InlineKeyboardButton(text="Оплатить", url=payment_url))
            welcome_text = config["DESCRIPTION"].format(price=config["PRICE"])
            await bot.send_message(
                chat_id,
                f"{welcome_text}\n\nНажмите чтобы оплатить {config['PRICE']} RUB:",
                reply_markup=keyboard
            )
            logger.info(f"[{bot_id}] Payment link sent to user_id={user_id}, label={payment_label}")
        except Exception as e:
            logger.error(f"[{bot_id}] Error in /start handler: {e}\n{traceback.format_exc()}")
            await bots[bot_id].send_message(chat_id, "An error occurred, try again later.")

    @dp.message_handler(commands=['debug'])
    async def debug_command(message: types.Message, bot_id=bot_id):
        try:
            user_id = str(message.from_user.id)
            chat_id = message.chat.id
            bot = bots[bot_id]
            config = BOTS[bot_id]
            logger.info(f"[{bot_id}] /debug command from user_id={user_id}")

            # Check bot permissions
            bot_member = await bot.get_chat_member(chat_id=config["PRIVATE_CHANNEL_ID"], user_id=(await bot.get_me()).id)
            permissions = f"Bot can invite users: {bot_member.can_invite_users}"

            # Check latest payment
            conn = psycopg2.connect(DB_CONNECTION)
            c = conn.cursor()
            c.execute(f"SELECT label, status FROM payments_{bot_id} WHERE user_id = %s ORDER BY label DESC LIMIT 1", (user_id,))
            result = c.fetchone()
            conn.close()
            payment_info = f"Latest payment: {result[0]} (status: {result[1]})" if result else "No payments found"

            # Test invite link creation
            invite = await create_unique_invite_link(bot_id, user_id)
            invite_status = f"Invite link test: {'Success' if invite else 'Failed'}"

            await bot.send_message(chat_id, f"Debug info:\n{permissions}\n{payment_info}\n{invite_status}")
            logger.info(f"[{bot_id}] Debug info sent to user_id={user_id}")
        except Exception as e:
            logger.error(f"[{bot_id}] Error in /debug handler: {e}\n{traceback.format_exc()}")
            await bots[bot_id].send_message(chat_id, "Debug error, contact support.")

# Verify YooMoney notification
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
        logger.info(f"[{bot_id}] YooMoney hash input: {params}")
        logger.info(f"[{bot_id}] YooMoney hash computed: {sha1_hash}, received: {data.get('sha1_hash', '')}")
        return sha1_hash == data.get("sha1_hash", "")
    except Exception as e:
        logger.error(f"[{bot_id}] YooMoney verification failed: {e}")
        return False

# Create unique one-time invite link with retry
async def create_unique_invite_link(bot_id, user_id):
    try:
        config = BOTS[bot_id]
        bot = bots[bot_id]

        # Check bot permissions
        bot_member = await bot.get_chat_member(chat_id=config["PRIVATE_CHANNEL_ID"], user_id=(await bot.get_me()).id)
        if not bot_member.can_invite_users:
            logger.error(f"[{bot_id}] Bot lacks permission to create invite links for chat {config['PRIVATE_CHANNEL_ID']}")
            return None

        # Attempt with retries
        for attempt in range(5):
            try:
                invite_link = await bot.create_chat_invite_link(
                    chat_id=config["PRIVATE_CHANNEL_ID"],
                    member_limit=1,
                    name=f"Invite for user_{user_id}"
                )
                logger.info(f"[{bot_id}] Invite link created for user_id={user_id}: {invite_link.invite_link}")
                return invite_link.invite_link
            except Exception as e:
                logger.warning(f"[{bot_id}] Attempt {attempt + 1} failed to create invite for user_id={user_id}: {e}")
                await asyncio.sleep(2)
        logger.error(f"[{bot_id}] Failed to create invite link after 5 attempts for user_id={user_id}")
        return None
    except Exception as e:
        logger.error(f"[{bot_id}] Error creating invite for user_id={user_id}: {e}\n{traceback.format_exc()}")
        return None

# Find bot_id by label
def find_bot_id_by_label(label):
    try:
        for bot_id in BOTS:
            conn = psycopg2.connect(DB_CONNECTION)
            c = conn.cursor()
            c.execute(f"SELECT user_id FROM payments_{bot_id} WHERE label = %s", (label,))
            result = c.fetchone()
            conn.close()
            if result:
                logger.info(f"[{bot_id}] Found bot for label={label}")
                return bot_id
        logger.warning(f"No bot found for label={label}")
        return None
    except Exception as e:
        logger.error(f"Error finding bot by label={label}: {e}")
        return None

# Generic YooMoney notification handler
async def handle_yoomoney_notify_generic(request):
    try:
        data = await request.post()
        logger.info(f"[{PLATFORM}] YooMoney notification received: {dict(data)}")

        label = data.get("label")
        if not label:
            logger.error(f"[{PLATFORM}] Missing label in notification")
            return web.Response(status=400, text="Missing label")

        bot_id = find_bot_id_by_label(label)
        if not bot_id:
            logger.error(f"[{PLATFORM}] Bot not found for label={label}")
            return web.Response(status=400, text="Bot not found")

        if not verify_yoomoney_notification(data, bot_id):
            logger.error(f"[{bot_id}] Invalid YooMoney hash")
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
                await bot.send_message(user_id, "Payment confirmed! Access granted.")
                invite_link = await create_unique_invite_link(bot_id, user_id)
                if invite_link:
                    await bot.send_message(user_id, f"Join the private channel: {invite_link}")
                    logger.info(f"[{bot_id}] Payment processed, invite sent for label={label}, user_id={user_id}")
                else:
                    await bot.send_message(user_id, "Failed to generate channel link. Contact support at @YourSupportHandle.")
                    logger.error(f"[{bot_id}] Invite link creation failed for user_id={user_id}")
            else:
                logger.error(f"[{bot_id}] Label {label} not found in database")
            conn.close()

        return web.Response(status=200)
    except Exception as e:
        logger.error(f"[{PLATFORM}] YooMoney notification error: {e}\n{traceback.format_exc()}")
        return web.Response(status=500)

# Bot-specific YooMoney notification handler
async def handle_yoomoney_notify(request, bot_id):
    try:
        data = await request.post()
        logger.info(f"[{bot_id}] YooMoney notification received: {dict(data)}")

        if not verify_yoomoney_notification(data, bot_id):
            logger.error(f"[{bot_id}] Invalid YooMoney hash")
            return web.Response(status=400, text="Invalid hash")

        label = data.get("label")
        if not label:
            logger.error(f"[{bot_id}] Missing label in notification")
            return web.Response(status=400, text="Missing label")

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
                await bot.send_message(user_id, "Оплата подтверждена!")
                invite_link = await create_unique_invite_link(bot_id, user_id)
                if invite_link:
                    await bot.send_message(user_id, f"Ссылка на приватный канал: {invite_link}")
                    logger.info(f"[{bot_id}] Payment processed, invite sent for label={label}, user_id={user_id}")
                else:
                    await bot.send_message(user_id, "Failed to generate channel link. Contact support at @YourSupportHandle.")
                    logger.error(f"[{bot_id}] Invite link creation failed for user_id={user_id}")
            else:
                logger.error(f"[{bot_id}] Label {label} not found in database")
            conn.close()

        return web.Response(status=200)
    except Exception as e:
        logger.error(f"[{bot_id}] YooMoney notification error: {e}\n{traceback.format_exc()}")
        return web.Response(status=500)

# Save payment handler
async def handle_save_payment(request, bot_id):
    try:
        data = await request.json()
        label = data.get("label")
        user_id = data.get("user_id")
        logger.info(f"[{bot_id}] Save payment request: label={label}, user_id={user_id}")
        if not label or not user_id:
            logger.error(f"[{bot_id}] Missing label or user_id")
            return web.Response(status=400, text="Missing data")

        conn = psycopg2.connect(DB_CONNECTION)
        c = conn.cursor()
        c.execute(f"INSERT INTO payments_{bot_id} (label, user_id, status) VALUES (%s, %s, %s) ON CONFLICT (label) DO UPDATE SET user_id = %s, status = %s",
                  (label, user_id, "pending", user_id, "pending"))
        conn.commit()
        conn.close()
        logger.info(f"[{bot_id}] Stored: label={label}, user_id={user_id}")
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"[{bot_id}] Save payment error: {e}\n{traceback.format_exc()}")
        return web.Response(status=500)

# Health check
async def handle_health(request):
    logger.info(f"[{PLATFORM}] Health check requested")
    return web.Response(status=200, text=f"Server is healthy, {len(BOTS)} bots running")

# Webhook handler
async def handle_webhook(request, bot_id):
    try:
        if bot_id not in dispatchers:
            logger.error(f"[{bot_id}] Unknown bot_id")
            return web.Response(status=400, text="Unknown bot_id")

        bot = bots[bot_id]
        dp = dispatchers[bot_id]
        Bot.set_current(bot)
        dp.set_current(dp)

        update = await request.json()
        logger.info(f"[{bot_id}] Webhook update: {update}")

        update_obj = types.Update(**update)
        asyncio.create_task(dp.process_update(update_obj))

        return web.Response(status=200)
    except Exception as e:
        logger.error(f"[{bot_id}] Webhook error: {e}\n{traceback.format_exc()}")
        return web.Response(status=500)

# Set webhooks
async def set_webhooks():
    logger.info(f"Configuring webhooks for {len(BOTS)} bots")
    for bot_id in bots:
        try:
            bot = bots[bot_id]
            webhook_url = f"{HOST_URL}{WEBHOOK_PATH}/{bot_id}"
            await bot.delete_webhook(drop_pending_updates=True)
            await bot.set_webhook(webhook_url)
            logger.info(f"[{bot_id}] Webhook set: {webhook_url}")
        except Exception as e:
            logger.error(f"[{bot_id}] Webhook setup failed: {e}")
            sys.exit(1)

# Web server setup
app = web.Application()
app.router.add_post(YOOMONEY_NOTIFY_PATH, handle_yoomoney_notify_generic)
app.router.add_get(HEALTH_PATH, handle_health)
app.router.add_post(HEALTH_PATH, handle_health)
for bot_id in BOTS:
    app.router.add_post(f"{YOOMONEY_NOTIFY_PATH}/{bot_id}", lambda request, bot_id=bot_id: handle_yoomoney_notify(request, bot_id))
    app.router.add_post(f"{SAVE_PAYMENT_PATH}/{bot_id}", lambda request, bot_id=bot_id: handle_save_payment(request, bot_id))
    app.router.add_post(f"{WEBHOOK_PATH}/{bot_id}", lambda request, bot_id=bot_id: handle_webhook(request, bot_id))
logger.info(f"Routes configured: {HEALTH_PATH}, {YOOMONEY_NOTIFY_PATH}, {SAVE_PAYMENT_PATH}, {WEBHOOK_PATH}")

# Main function
async def main():
    try:
        await set_webhooks()
        logger.info("Starting web server")
        port = int(os.getenv("PORT", 8080))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        logger.info(f"Server running on port {port}")

        logger.info(f"Route available: {HOST_URL}{HEALTH_PATH}")
        logger.info(f"Route available: {HOST_URL}{YOOMONEY_NOTIFY_PATH}")
        for bot_id in BOTS:
            logger.info(f"Route available: {HOST_URL}{YOOMONEY_NOTIFY_PATH}/{bot_id}")
            logger.info(f"Route available: {HOST_URL}{SAVE_PAYMENT_PATH}/{bot_id}")
            logger.info(f"Route available: {HOST_URL}{WEBHOOK_PATH}/{bot_id}")

        while True:
            await asyncio.sleep(3600)
    except Exception as e:
        logger.error(f"Startup error: {e}\n{traceback.format_exc()}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
