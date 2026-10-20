import os
import sys
import logging
import httpx
from aiohttp import web

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    PreCheckoutQuery,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

# --- Konfiguratsiya ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logging.critical("BOT_TOKEN muhit o'zgaruvchisi topilmadi!")
    sys.exit(1)

PROVIDER_API_KEY = os.environ.get("PROVIDER_API_KEY", "hd_1729ff0cbba09a19877994bf65044cbba6bd")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://englih.onrender.com")
PORT = int(os.environ.get("PORT", 8080))

PROVIDER_BASE_URL = "https://api.geminipro7.store"
SERVICE_ID = os.environ.get("SERVICE_ID", "gp_1")
STARS_PRICE = 150
ADMIN_ID = 5523761749

# Birinchi ishlagan asl webhook yo'li
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL.rstrip('/')}{WEBHOOK_PATH}"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# --- Upstream API Helper ---
async def purchase_supplier_product(service_id: str, quantity: int = 1):
    headers = {
        "X-API-Key": PROVIDER_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {"service_id": service_id, "quantity": quantity}
    async with httpx.AsyncClient(timeout=25.0) as client:
        try:
            res = await client.post(f"{PROVIDER_BASE_URL}/order", headers=headers, json=payload)
            return res.status_code, res.json()
        except Exception as e:
            logger.error(f"Fulfillment xatosi: {e}")
            return 500, {"error": str(e)}


# --- Admin Buyruqlari ---
@dp.message(Command("balance"))
async def admin_balance(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    headers = {"X-API-Key": PROVIDER_API_KEY}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            res = await client.get(f"{PROVIDER_BASE_URL}/me", headers=headers)
            await message.answer(f"💳 **Balans javobi:**\n`{res.text}`", parse_mode="Markdown")
        except Exception as e:
            await message.answer(f"Xato: {e}")


@dp.message(Command("products"))
async def admin_products(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    headers = {"X-API-Key": PROVIDER_API_KEY}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            res = await client.get(f"{PROVIDER_BASE_URL}/products", headers=headers)
            await message.answer(f"📦 **Mahsulotlar API javobi:**\n```json\n{res.text[:3500]}\n```", parse_mode="Markdown")
        except Exception as e:
            await message.answer(f"Xato: {e}")


# --- Asosiy Sotuv Qismi ---
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

    prices = [LabeledPrice(label="Gemini Pro 18M", amount=STARS_PRICE)]

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Gemini Pro (18 Months)",
        description="18-Month Plan, 5TB Cloud Storage, 5 Users, 100% Private.",
        payload=f"gemini_order_{callback.from_user.id}",
        currency="XTR",
        prices=prices,
        provider_token=""
    )


@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    payment_info = message.successful_payment
    logger.info(f"To'lov tushdi: {payment_info.total_amount} Stars")

    status_msg = await message.answer("🔄 **To'lov qabul qilindi!** Mahsulot olinmoqda...")

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

        await status_msg.edit_text(
            f"✅ **Purchase Successful!**\n\n"
            f"Here are your access details:\n"
            f"```\n{deliverable}\n```\n\n"
            f"🔖 **Order ID:** `{order_id}`\n"
            f"Thank you for your purchase!",
            parse_mode="Markdown"
        )
    else:
        await status_msg.edit_text(
            "⚠️ **Order Delivery Notice**\n\n"
            "To'lov qabul qilindi, lekin yetkazib berishda kechikish bo'ldi. "
            "Iltimos, adminga quyidagi to'lov ID sini yuboring:\n"
            f"`{payment_info.telegram_payment_charge_id}`",
            parse_mode="Markdown"
        )


# --- Birinchi ishlagan original server lifecycle ---
async def health_check(request):
    return web.Response(text="Bot is running!")

async def on_startup(app):
    await bot.set_webhook(url=WEBHOOK_URL, drop_pending_updates=True)
    logger.info(f"Webhook o'rnatildi: {WEBHOOK_URL}")

async def on_shutdown(app):
    await bot.delete_webhook()
    await bot.session.close()


def main():
    app = web.Application()
    app.router.add_get("/", health_check)

    # Birinchi ishlagan original registratsiya
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
