import asyncio
import hashlib
import logging
import os
import sys
import uuid
from urllib.parse import urlencode
from aiohttp import web, ClientSession
from aiogram import Bot, Dispatcher, types
from aiogram.utils.executor import set_webhook
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.contrib.fsm_storage.memory import MemoryStorage
import psycopg2
import traceback
from config import load_bot_configs

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    stream=sys.stdout
)
log = logging.getLogger(__name__)
log.info("Starting bot application")

# Constants
SAVE_PAYMENT_ENDPOINT = "/store_payment"
YOOMONEY_CALLBACK_ENDPOINT = "/yoomoney_callback"
HEALTH_ENDPOINT = "/status"
WEBHOOK_ENDPOINT = "/hook"
DB_URL = "postgresql://postgres.bdjjtisuhtbrogvotves:Alex4382!@aws-0-eu-north-1.pooler.supabase.com:6543/postgres"
BASE_URL = os.getenv("HOST_URL", "https://retired-rosalinde-damndamndamn33-7ca23064.koyeb.app")

# Platform detection
ENV_PLATFORM = "koyeb"
log.info(f"Platform detected: {ENV_PLATFORM}")

# Bot initialization
BOT_CONFIGS = load_bot_configs()
log.info(f"Initializing {len(BOT_CONFIGS)} bots")
bot_instances = {}
dispatch_instances = {}

for bot_key, cfg in BOT_CONFIGS.items():
    try:
        log.info(f"Setting up bot {bot_key}")
        bot_instances[bot_key] = Bot(token=cfg["TOKEN"])
        storage = MemoryStorage()
        dispatch_instances[bot_key] = Dispatcher(bot_instances[bot_key], storage=storage)
        log.info(f"Bot {bot_key} initialized successfully")
    except Exception as e:
        log.error(f"Failed to initialize bot {bot_key}: {e}")
        sys.exit(1)

# Database setup
def setup_database():
    try:
        conn = psycopg2.connect(DB_URL)
        cursor = conn.cursor()
        for bot_key in BOT_CONFIGS:
            cursor.execute(f'''CREATE TABLE IF NOT EXISTS payments_{bot_key}
                             (label TEXT PRIMARY KEY, user_id TEXT, status TEXT)''')
        conn.commit()
        conn.close()
        log.info("Database initialized successfully")
    except Exception as e:
        log.error(f"Database setup failed: {e}")
        sys.exit(1)

setup_database()

# Command handlers
for bot_key, dp in dispatch_instances.items():
    @dp.message_handler(commands=['start'])
    async def process_start(msg: types.Message, bot_key=bot_key):
        try:
            user_id = str(msg.from_user.id)
            chat_id = msg.chat.id
            bot = bot_instances[bot_key]
            log.info(f"[{bot_key}] /start command from user_id={user_id}")

            # Generate payment link
            payment_id = str(uuid.uuid4())
            cfg = BOT_CONFIGS[bot_key]
            payment_data = {
                "quickpay-form": "shop",
                "paymentType": "AC",
                "targets": f"Subscription payment for user_id={user_id}",
                "sum": cfg["PRICE"],
                "label": payment_id,
                "receiver": cfg["YOOMONEY_WALLET"],
                "successURL": f"https://t.me/{(await bot.get_me()).username}"
            }
            payment_link = f"https://yoomoney.ru/quickpay/confirm.xml?{urlencode(payment_data)}"

            # Store payment in DB
            conn = psycopg2.connect(DB_URL)
            cursor = conn.cursor()
            cursor.execute(f"INSERT INTO payments_{bot_key} (label, user_id, status) VALUES (%s, %s, %s)",
                          (payment_id, user_id, "pending"))
            conn.commit()
            conn.close()
            log.info(f"[{bot_key}] Stored in DB: label={payment_id}, user_id={user_id}")

            # Send to save_payment endpoint
            async with ClientSession() as session:
                try:
                    save_url = f"{BASE_URL}{SAVE_PAYMENT_ENDPOINT}/{bot_key}"
                    log.info(f"[{bot_key}] Sending to {save_url} for label={payment_id}")
                    async with session.post(save_url, json={"label": payment_id, "user_id": user_id}) as resp:
                        resp_text = await resp.text()
                        log.info(f"[{bot_key}] Save payment response: status={resp.status}, text={resp_text[:100]}")
                        if resp.status != 200:
                            log.error(f"[{bot_key}] Save payment failed: status={resp.status}")
                            await bot.send_message(chat_id, "Server error, try again later.")
                            return
                except Exception as e:
                    log.error(f"[{bot_key}] Save payment request failed: {e}")
                    await bot.send_message(chat_id, "Server error, try again later.")
                    return

            # Send response with payment button
            keyboard = InlineKeyboardMarkup()
            keyboard.add(InlineKeyboardButton(text="Pay Now", url=payment_link))
            msg_text = cfg["DESCRIPTION"].format(price=cfg["PRICE"])
            await bot.send_message(
                chat_id,
                f"{msg_text}\n\nClick the link to pay {cfg['PRICE']} RUB:",
                reply_markup=keyboard
            )
            log.info(f"[{bot_key}] Payment link sent to user_id={user_id}, label={payment_id}")
        except Exception as e:
            log.error(f"[{bot_key}] Error in /start handler: {e}\n{traceback.format_exc()}")
            await bot_instances[bot_key].send_message(chat_id, "An error occurred, try again later.")

# YooMoney verification
def check_yoomoney_auth(data, bot_key):
    try:
        cfg = BOT_CONFIGS[bot_key]
        fields = [
            data.get("notification_type", ""),
            data.get("operation_id", ""),
            data.get("amount", ""),
            data.get("currency", ""),
            data.get("datetime", ""),
            data.get("sender", ""),
            data.get("codepro", ""),
            cfg["NOTIFICATION_SECRET"],
            data.get("label", "")
        ]
        computed_hash = hashlib.sha1("&".join(fields).encode()).hexdigest()
        return computed_hash == data.get("sha1_hash", "")
    except Exception as e:
        log.error(f"[{bot_key}] YooMoney verification failed: {e}")
        return False

# Create invite link
async def generate_invite(bot_key, user_id):
    try:
        cfg = BOT_CONFIGS[bot_key]
        bot = bot_instances[bot_key]
        link = await bot.create_chat_invite_link(
            chat_id=cfg["PRIVATE_CHANNEL_ID"],
            member_limit=1,
            name=f"User_{user_id}_invite"
        )
        log.info(f"[{bot_key}] Invite link created for user_id={user_id}: {link.invite_link}")
        return link.invite_link
    except Exception as e:
        log.error(f"[{bot_key}] Failed to create invite for user_id={user_id}: {e}")
        return None

# Find bot by label
def locate_bot_by_label(label):
    try:
        for bot_key in BOT_CONFIGS:
            conn = psycopg2.connect(DB_URL)
            cursor = conn.cursor()
            cursor.execute(f"SELECT user_id FROM payments_{bot_key} WHERE label = %s", (label,))
            result = cursor.fetchone()
            conn.close()
            if result:
                return bot_key
        return None
    except Exception as e:
        log.error(f"Error finding bot by label={label}: {e}")
        return None

# Generic YooMoney callback
async def process_yoomoney_callback_generic(request):
    try:
        data = await request.post()
        log.info(f"[{ENV_PLATFORM}] YooMoney callback received: {dict(data)}")

        label = data.get("label")
        if not label:
            log.error(f"[{ENV_PLATFORM}] Missing label in callback")
            return web.Response(status=400, text="No label provided")

        bot_key = locate_bot_by_label(label)
        if not bot_key:
            log.error(f"[{ENV_PLATFORM}] Bot not found for label={label}")
            return web.Response(status=400, text="Bot not found")

        if not check_yoomoney_auth(data, bot_key):
            log.error(f"[{bot_key}] Invalid YooMoney hash")
            return web.Response(status=400, text="Hash verification failed")

        if data.get("notification_type") in ["p2p-incoming", "card-incoming"]:
            conn = psycopg2.connect(DB_URL)
            cursor = conn.cursor()
            cursor.execute(f"SELECT user_id FROM payments_{bot_key} WHERE label = %s", (label,))
            result = cursor.fetchone()
            if result:
                user_id = result[0]
                cursor.execute(f"UPDATE payments_{bot_key} SET status = %s WHERE label = %s", ("success", label))
                conn.commit()
                bot = bot_instances[bot_key]
                await bot.send_message(user_id, "Payment confirmed! Access granted.")
                invite = await generate_invite(bot_key, user_id)
                if invite:
                    await bot.send_message(user_id, f"Join the private channel: {invite}")
                    log.info(f"[{bot_key}] Payment processed, invite sent for label={label}")
                else:
                    await bot.send_message(user_id, "Failed to generate channel link. Contact support.")
                    log.error(f"[{bot_key}] Invite link creation failed for user_id={user_id}")
            else:
                log.error(f"[{bot_key}] Label {label} not found")
            conn.close()

        return web.Response(status=200)
    except Exception as e:
        log.error(f"[{ENV_PLATFORM}] YooMoney callback error: {e}\n{traceback.format_exc()}")
        return web.Response(status=500)

# Bot-specific YooMoney callback
async def process_yoomoney_callback(request, bot_key):
    try:
        data = await request.post()
        log.info(f"[{bot_key}] YooMoney callback received: {dict(data)}")

        if not check_yoomoney_auth(data, bot_key):
            log.error(f"[{bot_key}] Invalid YooMoney hash")
            return web.Response(status=400, text="Hash verification failed")

        label = data.get("label")
        if not label:
            log.error(f"[{bot_key}] Missing label in callback")
            return web.Response(status=400, text="No label provided")

        if data.get("notification_type") in ["p2p-incoming", "card-incoming"]:
            conn = psycopg2.connect(DB_URL)
            cursor = conn.cursor()
            cursor.execute(f"SELECT user_id FROM payments_{bot_key} WHERE label = %s", (label,))
            result = cursor.fetchone()
            if result:
                user_id = result[0]
                cursor.execute(f"UPDATE payments_{bot_key} SET status = %s WHERE label = %s", ("success", label))
                conn.commit()
                bot = bot_instances[bot_key]
                await bot.send_message(user_id, "Payment confirmed! Access granted.")
                invite = await generate_invite(bot_key, user_id)
                if invite:
                    await bot.send_message(user_id, f"Join the private channel: {invite}")
                    log.info(f"[{bot_key}] Payment processed, invite sent for label={label}")
                else:
                    await bot.send_message(user_id, "Failed to generate channel link. Contact support.")
                    log.error(f"[{bot_key}] Invite link creation failed for user_id={user_id}")
            else:
                log.error(f"[{bot_key}] Label {label} not found")
            conn.close()

        return web.Response(status=200)
    except Exception as e:
        log.error(f"[{bot_key}] YooMoney callback error: {e}\n{traceback.format_exc()}")
        return web.Response(status=500)

# Save payment handler
async def store_payment(request, bot_key):
    try:
        data = await request.json()
        label = data.get("label")
        user_id = data.get("user_id")
        log.info(f"[{bot_key}] Store payment request: label={label}, user_id={user_id}")
        if not label or not user_id:
            log.error(f"[{bot_key}] Missing label or user_id")
            return web.Response(status=400, text="Missing data")

        conn = psycopg2.connect(DB_URL)
        cursor = conn.cursor()
        cursor.execute(f"INSERT INTO payments_{bot_key} (label, user_id, status) VALUES (%s, %s, %s) ON CONFLICT (label) DO UPDATE SET user_id = %s, status = %s",
                      (label, user_id, "pending", user_id, "pending"))
        conn.commit()
        conn.close()
        log.info(f"[{bot_key}] Stored: label={label}, user_id={user_id}")
        return web.Response(status=200)
    except Exception as e:
        log.error(f"[{bot_key}] Store payment error: {e}\n{traceback.format_exc()}")
        return web.Response(status=500)

# Health check
async def check_status(request):
    log.info(f"[{ENV_PLATFORM}] Status check requested")
    return web.Response(status=200, text=f"System operational, {len(BOT_CONFIGS)} bots active")

# Webhook handler
async def process_hook(request, bot_key):
    try:
        if bot_key not in dispatch_instances:
            log.error(f"[{bot_key}] Invalid bot_key")
            return web.Response(status=400, text="Invalid bot_key")

        bot = bot_instances[bot_key]
        dp = dispatch_instances[bot_key]
        Bot.set_current(bot)
        dp.set_current(dp)

        update = await request.json()
        log.info(f"[{bot_key}] Webhook update: {update}")

        update_obj = types.Update(**update)
        asyncio.create_task(dp.process_update(update_obj))

        return web.Response(status=200)
    except Exception as e:
        log.error(f"[{bot_key}] Webhook error: {e}\n{traceback.format_exc()}")
        return web.Response(status=500)

# Set webhooks
async def configure_webhooks():
    log.info(f"Configuring webhooks for {len(BOT_CONFIGS)} bots")
    for bot_key in bot_instances:
        try:
            bot = bot_instances[bot_key]
            hook_url = f"{BASE_URL}{WEBHOOK_ENDPOINT}/{bot_key}"
            await bot.delete_webhook(drop_pending_updates=True)
            await bot.set_webhook(hook_url)
            log.info(f"[{bot_key}] Webhook set: {hook_url}")
        except Exception as e:
            log.error(f"[{bot_key}] Webhook setup failed: {e}")
            sys.exit(1)

# Web server setup
app = web.Application()
app.router.add_post(YOOMONEY_CALLBACK_ENDPOINT, process_yoomoney_callback_generic)
app.router.add_get(HEALTH_ENDPOINT, check_status)
app.router.add_post(HEALTH_ENDPOINT, check_status)
for bot_key in BOT_CONFIGS:
    app.router.add_post(f"{YOOMONEY_CALLBACK_ENDPOINT}/{bot_key}", lambda request, bot_key=bot_key: process_yoomoney_callback(request, bot_key))
    app.router.add_post(f"{SAVE_PAYMENT_ENDPOINT}/{bot_key}", lambda request, bot_key=bot_key: store_payment(request, bot_key))
    app.router.add_post(f"{WEBHOOK_ENDPOINT}/{bot_key}", lambda request, bot_key=bot_key: process_hook(request, bot_key))
log.info(f"Routes configured: {HEALTH_ENDPOINT}, {YOOMONEY_CALLBACK_ENDPOINT}, {SAVE_PAYMENT_ENDPOINT}, {WEBHOOK_ENDPOINT}")

# Main function
async def run_app():
    try:
        await configure_webhooks()
        log.info("Starting web server")
        port = int(os.getenv("PORT", 8000))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        log.info(f"Server running on port {port}")

        log.info(f"Route available: {BASE_URL}{HEALTH_ENDPOINT}")
        log.info(f"Route available: {BASE_URL}{YOOMONEY_CALLBACK_ENDPOINT}")
        for bot_key in BOT_CONFIGS:
            log.info(f"Route available: {BASE_URL}{YOOMONEY_CALLBACK_ENDPOINT}/{bot_key}")
            log.info(f"Route available: {BASE_URL}{SAVE_PAYMENT_ENDPOINT}/{bot_key}")
            log.info(f"Route available: {BASE_URL}{WEBHOOK_ENDPOINT}/{bot_key}")

        while True:
            await asyncio.sleep(3600)
    except Exception as e:
        log.error(f"Startup error: {e}\n{traceback.format_exc()}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(run_app())
