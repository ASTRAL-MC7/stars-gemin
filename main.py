import os
import sys
import logging
import sqlite3
import json
from datetime import datetime
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
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# --- Asosiy Sozlamalar ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logging.critical("FATAL: BOT_TOKEN muhit o'zgaruvchisi topilmadi!")
    sys.exit(1)

PROVIDER_API_KEY = os.environ.get("PROVIDER_API_KEY", "hd_1729ff0cbba09a19877994bf65044cbba6bd")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")
PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
PROVIDER_BASE_URL = "https://api.geminipro7.store"

ADMIN_ID = 5523761749

# Qo'shilgan faol mahsulotlar (ID : Narxi Starsda)
ACTIVE_PRODUCTS = {
    "gp_1": {
        "title": "Gemini Pro (18 Months)",
        "stars": 150,
        "description": "📆 18 Months Plan\n⚡ 5TB cloud storage included\n⚡ You can add 5 users\n⚡ No Shared\n⚡ 100% Private\n⚡ NO NEED ANY CARD\n⚡ Works in any country no verification"
    }
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# --- SQLite Ma'lumotlar Bazasi ---
def init_db():
    conn = sqlite3.connect("bot_shop.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            service_id TEXT,
            stars_paid INTEGER,
            delivered INTEGER,
            supplier_order_id TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def record_order(user_id: int, service_id: str, stars_paid: int, delivered: bool, supplier_order_id: str):
    conn = sqlite3.connect("bot_shop.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO orders (user_id, service_id, stars_paid, delivered, supplier_order_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, service_id, stars_paid, 1 if delivered else 0, supplier_order_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def get_orders_list(limit=15):
    conn = sqlite3.connect("bot_shop.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, service_id, stars_paid, delivered, created_at FROM orders ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_stats():
    conn = sqlite3.connect("bot_shop.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(DISTINCT user_id), COUNT(*), SUM(CASE WHEN delivered = 1 THEN 1 ELSE 0 END), SUM(stars_paid) FROM orders")
    stats = cursor.fetchone()
    conn.close()
    return stats or (0, 0, 0, 0)

init_db()


# --- Upstream API Bilan Ishlash ---
async def fetch_upstream_products():
    """Yetkazib beruvchidan tovarlar ro'yxatini yuklaydi va barcha variantlarni tekshiradi."""
    headers = {"X-API-Key": PROVIDER_API_KEY}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            res = await client.get(f"{PROVIDER_BASE_URL}/products", headers=headers)
            if res.status_code != 200:
                logger.error(f"Products fetch error: {res.status_code} - {res.text}")
                return []

            data = res.json()
            
            # Agar javob to'g'ridan-to'g'ri list bo'lsa
            if isinstance(data, list):
                return data
            
            # Agar lug'at ichida kelsa
            if isinstance(data, dict):
                for key in ["products", "data", "items", "services"]:
                    if key in data and isinstance(data[key], list):
                        return data[key]
                return [data]

            return []
        except Exception as e:
            logger.error(f"API fetch exception: {e}")
            return []

async def get_provider_balance():
    headers = {"X-API-Key": PROVIDER_API_KEY}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            res = await client.get(f"{PROVIDER_BASE_URL}/me", headers=headers)
            if res.status_code == 200:
                return res.json()
            return None
        except Exception as e:
            logger.error(f"Balance check error: {e}")
            return None

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
            logger.error(f"Order API error: {e}")
            return 500, {"error": str(e)}


# --- Admin Buyruqlari ---
@dp.message(Command("orders"))
async def admin_orders(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    orders = get_orders_list(limit=15)
    if not orders:
        await message.answer("ℹ️ Hozircha buyurtmalar yo'q.")
        return

    text = "📋 **Oxirgi buyurtmalar:**\n\n"
    for order in orders:
        status_icon = "✅ Yetkazildi" if order[4] == 1 else "❌ Xato"
        text += (
            f"🔹 **Buyurtma #{order[0]}**\n"
            f"👤 User: `{order[1]}`\n"
            f"📦 Mahsulot ID: `{order[2]}`\n"
            f"⭐ To'lov: {order[3]} Stars\n"
            f"📊 Holat: {status_icon}\n"
            f"🕒 Sana: {order[5]}\n"
            f"------------------------\n"
        )
    await message.answer(text, parse_mode="Markdown")


@dp.message(Command("stats"))
async def admin_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    buyers, total_orders, delivered_orders, total_stars = get_stats()
    total_stars = total_stars or 0
    success_rate = round((delivered_orders / total_orders * 100), 1) if total_orders > 0 else 0

    text = (
        "📊 **Bot Statistikasi:**\n\n"
        f"👥 **Xaridorlar:** {buyers} ta\n"
        f"📦 **Jami buyurtmalar:** {total_orders} ta\n"
        f"✅ **Muvaffaqiyatli:** {delivered_orders} ta\n"
        f"📈 **Muvaffaqiyat foizi:** {success_rate}%\n"
        f"⭐ **Jami Stars:** {total_stars:,} Stars"
    )
    await message.answer(text, parse_mode="Markdown")


@dp.message(Command("balance"))
async def admin_balance(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    bal_data = await get_provider_balance()
    if bal_data:
        balance = bal_data.get("balance", bal_data.get("wallet", "N/A"))
        await message.answer(f"💳 **Yetkazib beruvchi balansi:** `${balance}`", parse_mode="Markdown")
    else:
        await message.answer("⚠️ Balansni olib bo'lmadi. API kalitni tekshiring.")


@dp.message(Command("products"))
async def admin_products_list(message: types.Message):
    """Admin uchun mahsulotlarning Nomi, ID si va Narxini matn ko'rinishida chiqarib beradi."""
    if message.from_user.id != ADMIN_ID:
        return

    loading = await message.answer("🔄 Tovarlar yuklanmoqda...")
    products = await fetch_upstream_products()

    if not products:
        await loading.edit_text("❌ Yetkazib beruvchida tovarlar topilmadi yoki API da xatolik.")
        return

    msg = "📦 **Ombordagi Barcha Mahsulotlar (Nomi - ID - Narxi):**\n\n"
    for item in products:
        p_id = item.get("service_id") or item.get("id") or item.get("product_id") or "Noma'lum"
        name = item.get("name") or item.get("title") or "Nomsiz tovar"
        price = item.get("price") or item.get("cost") or "0"

        msg += (
            f"🔹 **Nomi:** {name}\n"
            f"🆔 **ID:** `{p_id}`\n"
            f"💵 **Narxi:** `${price}`\n"
            f"----------------------------\n"
        )

    await loading.delete()
    # Telegram xabar chegarasidan oshmasligi uchun bo'laklab chiqarish
    if len(msg) > 4000:
        for chunk in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
            await message.answer(chunk, parse_mode="Markdown")
    else:
        await message.answer(msg, parse_mode="Markdown")


@dp.message(Command("debug"))
async def admin_debug(message: types.Message):
    """API dan kelayotgan xom javobni ko'rsatish."""
    if message.from_user.id != ADMIN_ID:
        return

    headers = {"X-API-Key": PROVIDER_API_KEY}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            res = await client.get(f"{PROVIDER_BASE_URL}/products", headers=headers)
            await message.answer(f"📡 **Status:** `{res.status_code}`\n\n```json\n{res.text[:3500]}\n```", parse_mode="Markdown")
        except Exception as e:
            await message.answer(f"❌ Xato: `{str(e)}`")


# --- Foydalanuvchi Qismi ---
@dp.message(CommandStart())
async def start_handler(message: types.Message):
    welcome_text = (
        "👋 **Xush kelibsiz!**\n\n"
        "Ushbu bot orqali rasmiy premium obunalarni qulay narxlarda Telegram Stars orqali xarid qilishingiz mumkin.\n\n"
        "Quyidagi tugma orqali katalog bilan tanishing:"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛍 Barcha Mahsulotlar (Katalog)", callback_data="show_catalog")]
        ]
    )
    await message.answer(welcome_text, parse_mode="Markdown", reply_markup=keyboard)


@dp.callback_query(F.data == "show_catalog")
async def show_catalog_handler(callback: types.CallbackQuery):
    await callback.answer("Tovarlar yuklanmoqda...")
    products = await fetch_upstream_products()

    if not products:
        await callback.message.edit_text("❌ Omborda tovarlar topilmadi.")
        return

    buttons = []
    for item in products:
        p_id = item.get("service_id") or item.get("id") or item.get("product_id") or "N/A"
        name = item.get("name") or item.get("title") or "Mahsulot"
        price = item.get("price") or item.get("cost") or "0"
        
        # Tugmada Nomi - ID - Narxi ko'rinadi
        button_text = f"📦 {name} | ID: {p_id} (${price})"
        buttons.append([InlineKeyboardButton(text=button_text, callback_data=f"info_{p_id}")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(
        "📦 **Mavjud tovarlar ro'yxati:**\n"
        "Batafsil ma'lumot va xarid qilish uchun tanlang:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


@dp.callback_query(F.data.startswith("info_"))
async def show_product_detail(callback: types.CallbackQuery):
    service_id = callback.data.replace("info_", "")
    product = ACTIVE_PRODUCTS.get(service_id)

    if product:
        text = (
            f"💎 **{product['title']}**\n\n"
            f"{product['description']}\n\n"
            f"⭐ **Narxi:** {product['stars']} Stars\n"
            f"🆔 **ID:** `{service_id}`"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"⭐ Sotib olish ({product['stars']} Stars)", callback_data=f"buy_{service_id}")],
                [InlineKeyboardButton(text="◀️ Katalogga qaytish", callback_data="show_catalog")]
            ]
        )
    else:
        text = (
            f"📦 **Mahsulot ID:** `{service_id}`\n\n"
            "ℹ️ Ushbu mahsulot hozirda sozlanish jarayonida. Tez orada xarid qilish imkoniyati ochiladi!"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Katalogga qaytish", callback_data="show_catalog")]]
        )

    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("buy_"))
async def send_invoice(callback: types.CallbackQuery):
    service_id = callback.data.replace("buy_", "")
    product = ACTIVE_PRODUCTS.get(service_id)
    if not product:
        await callback.answer("Ushbu mahsulot sotuvda mavjud emas.", show_alert=True)
        return

    await callback.answer()
    prices = [LabeledPrice(label=product["title"], amount=product["stars"])]

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=product["title"],
        description=product["description"][:250],
        payload=f"order_{service_id}_{callback.from_user.id}",
        currency="XTR",
        prices=prices,
        provider_token=""
    )


@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def process_payment(message: types.Message):
    payment = message.successful_payment
    payload_parts = payment.invoice_payload.split("_")
    service_id = payload_parts[1] if len(payload_parts) > 1 else "gp_1"

    status_msg = await message.answer("🔄 **To'lov tasdiqlandi!** Yetkazib beruvchi tizimidan litsenziya olinmoqda...")

    status_code, data = await purchase_supplier_product(service_id=service_id, quantity=1)

    if status_code in (200, 201):
        deliverable = data.get("deliverable") or data.get("data") or data.get("account") or str(data)
        sup_order_id = str(data.get("order_id", "N/A"))

        record_order(message.from_user.id, service_id, payment.total_amount, True, sup_order_id)

        await status_msg.edit_text(
            f"✅ **Xarid muvaffaqiyatli yakunlandi!**\n\n"
            f"Hisob/Litsenziya ma'lumotlaringiz:\n"
            f"```\n{deliverable}\n```\n\n"
            f"🔖 **Buyurtma ID:** `{sup_order_id}`\n"
            f"Xaridingiz uchun rahmat!",
            parse_mode="Markdown"
        )
    else:
        err = data.get("message") or data.get("error") or "Noma'lum xatolik"
        logger.error(f"Fulfillment error: {err}")

        record_order(message.from_user.id, service_id, payment.total_amount, False, "FAILED")

        await status_msg.edit_text(
            "⚠️ **Yetkazib berishda kechikish yuz berdi!**\n\n"
            "To'lovingiz qabul qilindi, biroq ombor tizimida texnik xatolik chiqdi. "
            "Iltimos, adminga quyidagi to'lov ID sini yuboring:\n"
            f"`{payment.telegram_payment_charge_id}`",
            parse_mode="Markdown"
        )


# --- Server & Webhook Lifecycle ---
async def health_check(request):
    return web.Response(text="Bot is operational")

async def on_startup(app):
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}{WEBHOOK_PATH}"
        await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
        logger.info(f"Webhook o'rnatildi: {webhook_url}")

async def on_shutdown(app):
    await bot.delete_webhook()
    await bot.session.close()

def main():
    app = web.Application()
    app.router.add_get("/", health_check)
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
