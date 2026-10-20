import os
import logging
import httpx
from aiohttp import web

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    PreCheckoutQuery,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# --- Configuration ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PROVIDER_API_KEY = os.environ.get("PROVIDER_API_KEY", "hd_1729ff0cbba09a19877994bf65044cbba6bd")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")  # e.g., https://my-service.onrender.com
PORT = int(os.environ.get("PORT", 8080))

PROVIDER_BASE_URL = "https://api.geminipro7.store"
SERVICE_ID = os.environ.get("SERVICE_ID", "gp_1")
STARS_PRICE = 150

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# --- Supplier API Helper ---
async def purchase_supplier_product(service_id: str, quantity: int = 1):
    headers = {
        "X-API-Key": PROVIDER_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "service_id": service_id,
        "quantity": quantity
    }
    async with httpx.AsyncClient(timeout=25.0) as client:
        try:
            res = await client.post(f"{PROVIDER_BASE_URL}/order", headers=headers, json=payload)
            return res.status_code, res.json()
        except Exception as e:
            logger.error(f"Upstream provider call error: {e}")
            return 500, {"error": str(e)}


# --- Bot Handlers ---
@dp.message(CommandStart())
async def start_handler(message: types.Message):
    product_text = (
        "💎 **Gemini Pro (18 Months)**\n\n"
        "📆 18 Months Plan\n"
        "⚡ 5TB cloud storage included\n"
        "⚡ You can add 5 users\n"
        "⚡ No Shared\n"
        "⚡ 100% Private\n"
        "⚡ NO NEED ANY CARD\n"
        "⚡ Works in any country no verification\n\n"
        f"⭐ **Price:** {STARS_PRICE} Stars"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"⭐ Pay {STARS_PRICE} Stars", callback_data="buy_gemini")]
        ]
    )
    await message.answer(product_text, parse_mode="Markdown", reply_markup=keyboard)


@dp.callback_query(F.data == "buy_gemini")
async def send_star_invoice(callback: types.CallbackQuery):
    await callback.answer()

    # Telegram Stars (XTR) specification
    prices = [LabeledPrice(label="Gemini Pro 18M", amount=STARS_PRICE)]

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Gemini Pro (18 Months)",
        description="18-Month Plan, 5TB Cloud Storage, 5 Users, 100% Private.",
        payload=f"gemini_order_{callback.from_user.id}",
        currency="XTR",          # Telegram Stars currency code
        prices=prices,
        provider_token=""        # Must be empty string for Telegram Stars
    )


@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    # Telegram requires answering pre_checkout within 10 seconds
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    payment_info = message.successful_payment
    logger.info(f"Payment received: {payment_info.total_amount} Stars from {message.from_user.id}")

    processing_msg = await message.answer("🔄 **Payment verified!** Contacting supplier for your subscription...")

    # Purchase directly from upstream API
    status_code, data = await purchase_supplier_product(service_id=SERVICE_ID, quantity=1)

    if status_code in (200, 201):
        deliverable = (
            data.get("deliverable") 
            or data.get("data") 
            or data.get("account") 
            or data.get("key") 
            or str(data)
        )
        order_id = data.get("order_id", "N/A")

        await processing_msg.edit_text(
            f"✅ **Purchase Successful!**\n\n"
            f"Here are your access details:\n"
            f"```\n{deliverable}\n```\n\n"
            f"🔖 **Order ID:** `{order_id}`\n"
            f"Thank you for your purchase!",
            parse_mode="Markdown"
        )
    else:
        err = data.get("message") or data.get("error") or "Unknown error"
        logger.error(f"Fulfillment failed: {err}")
        await processing_msg.edit_text(
            "⚠️ **Order Delivery Notice**\n\n"
            "Your payment was received, but the supplier API reported an issue. "
            "Please provide your transaction ID to support:\n"
            f"Transaction ID: `{payment_info.telegram_payment_charge_id}`",
            parse_mode="Markdown"
        )


# --- Health Check and Lifecycle Handlers ---
async def health_check(request):
    return web.Response(text="Bot is operational")

async def on_startup(app):
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}{WEBHOOK_PATH}"
        await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
        logger.info(f"Webhook set to: {webhook_url}")
    else:
        logger.warning("RENDER_EXTERNAL_URL not set. Webhook skipped.")

async def on_shutdown(app):
    await bot.delete_webhook()
    await bot.session.close()
    logger.info("Webhook cleared and session closed.")


def main():
    app = web.Application()
    app.router.add_get("/", health_check)

    # Register webhook handler
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
