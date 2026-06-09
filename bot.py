#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🤖 SHEIN Price Calculator Bot v2.3 - Fixed & Optimized Edition
"""

import os
import logging
import json
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler
)
from telegram.constants import ParseMode

# حاول استيراد pymongo
try:
    from pymongo import MongoClient
    MONGO_AVAILABLE = True
except ImportError:
    MONGO_AVAILABLE = False

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# جلب معرف الأدمن بشكل آمن تماماً
try:
    admin_id_str = os.getenv('ADMIN_ID', '').strip()
    ADMIN_ID = int(admin_id_str) if admin_id_str.isdigit() else None
except (ValueError, TypeError):
    ADMIN_ID = None

CONFIG_FILE = 'config.json'
MONGO_URI = os.getenv('MONGO_URI', None)

CATEGORY_SELECTION, PRICE_INPUT, ADMIN_MENU, SET_RATE, SET_USD_RATE, SET_CATEGORY_FEE, SET_OTHER_FEE, SET_WHATSAPP = range(8)

DEFAULT_CONFIG = {
    'exchange_rate': 3400,   
    'usd_rate': 15000,       
    'clothing_fee': 2.0,     
    'other_fee': 1.0,        
    'whatsapp': '+963123456789'
}

mongo_client = None
mongo_db = None

# ==================== خادم ويب لـ Railway / Render ====================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Bot is running successfully!".encode("utf-8"))

    def log_message(self, format, *args):
        return

def run_health_server():
    port = int(os.getenv("PORT", 8080))
    try:
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        logger.info(f"🚀 خادم فحص الحالة يعمل على المنفذ: {port}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"⚠️ فشل تشغيل خادم فحص الحالة: {e}")

# تشغيل الخادم في خلفية الخدمة
threading.Thread(target=run_health_server, daemon=True).start()

# ==================== قاعدة البيانات ====================
def connect_to_mongo():
    global mongo_client, mongo_db
    try:
        if MONGO_AVAILABLE and MONGO_URI:
            mongo_client = MongoClient(
                MONGO_URI,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
                retryWrites=True,
                tls=True,
                tlsAllowInvalidCertificates=True
            )
            mongo_client.admin.command('ping')
            mongo_db = mongo_client['shein_bot']
            logger.info("✅ تم الاتصال بـ MongoDB")
            return True
    except Exception as e:
        logger.warning(f"⚠️ خطأ MongoDB: {e}. سيتم استخدام JSON كبديل محلي.")
    return False

def load_config():
    if mongo_db:
        try:
            config_doc = mongo_db['config'].find_one({'_id': 'settings'})
            if config_doc:
                config_doc.pop('_id', None)
                config = DEFAULT_CONFIG.copy()
                config.update(config_doc)
                return config
        except Exception as e:
            logger.warning(f"خطأ في قراءة MongoDB: {e}")
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                config = DEFAULT_CONFIG.copy()
                config.update(loaded)
                return config
        except Exception as e:
            logger.warning(f"خطأ في قراءة JSON: {e}")
    
    return DEFAULT_CONFIG.copy()

def save_config(config):
    if mongo_db:
        try:
            config_to_save = config.copy()
            config_to_save['_id'] = 'settings'
            mongo_db['config'].update_one(
                {'_id': 'settings'},
                {'$set': config_to_save},
                upsert=True
            )
            logger.info("✅ تم حفظ البيانات في MongoDB")
        except Exception as e:
            logger.warning(f"خطأ في حفظ MongoDB: {e}")
    
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        logger.info("✅ تم حفظ البيانات في JSON")
    except Exception as e:
        logger.error(f"خطأ في حفظ JSON: {e}")

def format_currency(amount: float) -> str:
    return f"{amount:,.0f}"

def calculate_prices(base_price: float, category: str, config: dict):
    exchange_rate = config.get('exchange_rate', DEFAULT_CONFIG['exchange_rate'])
    usd_rate = config.get('usd_rate', DEFAULT_CONFIG['usd_rate'])
    
    if category == 'clothing':
        fee_usd = config.get('clothing_fee', DEFAULT_CONFIG['clothing_fee'])
    else:
        fee_usd = config.get('other_fee', DEFAULT_CONFIG['other_fee'])
    
    base_syp = base_price * exchange_rate
    base_usd = base_syp / usd_rate
    fee_syp = fee_usd * usd_rate
    total_syp = base_syp + fee_syp
    total_usd = base_usd + fee_usd
    
    return base_syp, base_usd, fee_syp, fee_usd, total_syp, total_usd

# ==================== Admin Handlers ====================
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # إصلاح الأمان: لو لم يتم تعيين ADMIN_ID أو تعيينه بشكل خاطئ، يتم حظر الجميع تلقائياً لحين ضبطه
    if ADMIN_ID is None or update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("❌ غير مسموح لك بالوصول إلى لوحة التحكم الخاصة بالإدارة.")
        return ConversationHandler.END
    
    keyboard = [
        [InlineKeyboardButton("💱 سعر صرف الريال", callback_data='set_rate'), InlineKeyboardButton("💵 سعر صرف الدولار", callback_data='set_usd_rate')],
        [InlineKeyboardButton("👕 أجور الملابس ($)", callback_data='set_clothing_fee'), InlineKeyboardButton("🎁 أجور أخرى ($)", callback_data='set_other_fee')],
        [InlineKeyboardButton("📱 رقم الواتس", callback_data='set_whatsapp'), InlineKeyboardButton("📊 عرض الإعدادات", callback_data='show_config')],
        [InlineKeyboardButton("❌ إغلاق القائمة", callback_data='cancel')]
    ]
    
    await update.message.reply_text(
        "📋 <b>لوحة تحكم الإدارة</b>\n\nاختر الإعداد الذي ترغب بتعديله:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
    return ADMIN_MENU

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    if query.data == 'set_rate':
        await query.edit_message_text("💱 أرسل سعر صرف <b>الريال السعودي</b> مقابل الليرة (مثال: 3400)", parse_mode=ParseMode.HTML)
        return SET_RATE
    elif query.data == 'set_usd_rate':
        await query.edit_message_text("💵 أرسل سعر صرف <b>الدولار الأمريكي</b> مقابل الليرة (مثال: 15000)", parse_mode=ParseMode.HTML)
        return SET_USD_RATE
    elif query.data == 'set_clothing_fee':
        await query.edit_message_text("👕 أرسل أجور شحن الملابس والأحذية بـ <b>الدولار</b> (مثال: 2)", parse_mode=ParseMode.HTML)
        return SET_CATEGORY_FEE
    elif query.data == 'set_other_fee':
        await query.edit_message_text("🎁 أرسل أجور شحن المنتجات الأخرى بـ <b>الدولار</b> (مثال: 1)", parse_mode=ParseMode.HTML)
        return SET_OTHER_FEE
    elif query.data == 'set_whatsapp':
        await query.edit_message_text("📱 أرسل رقم الواتس (مثال: +963123456789)")
        return SET_WHATSAPP
    elif query.data == 'show_config':
        config = load_config()
        usd_rate = config.get('usd_rate', DEFAULT_CONFIG['usd_rate'])
        base_syp, base_usd, fee_syp, fee_usd, total_syp, total_usd = calculate_prices(100, 'clothing', config)
        
        msg = f"""
📊 <b>الإعدادات الحالية:</b>

💱 الريال: <b>{format_currency(config['exchange_rate'])} ل.س</b>
💵 الدولار: <b>{format_currency(usd_rate)} ل.س</b>
👕 شحن الملابس: <b>{config['clothing_fee']} $</b>
🎁 شحن أخرى: <b>{config['other_fee']} $</b>

📐 <b>معاينة لفاتورة قطعة ملابس بـ 100 ريال:</b>
ليرة: {format_currency(base_syp)} + شحن {format_currency(fee_syp)} = <b>{format_currency(total_syp)} ل.س</b>
دولار: {base_usd:.2f} + شحن {fee_usd:.2f} = <b>{total_usd:.2f} $</b>
        """
        await query.edit_message_text(msg, parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    elif query.data == 'cancel':
        await query.edit_message_text("❌ تم إغلاق لوحة التحكم.")
        return ConversationHandler.END

async def set_rate_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        new_rate = float(update.message.text)
        if new_rate <= 0: raise ValueError
        config = load_config()
        config['exchange_rate'] = new_rate
        save_config(config)
        await update.message.reply_text(f"✅ تم الحفظ! سعر الريال الآن: {format_currency(new_rate)} ل.س")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ رقم غير صالح، حاول مجدداً.")
        return SET_RATE

async def set_usd_rate_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        new_rate = float(update.message.text)
        if new_rate <= 0: raise ValueError
        config = load_config()
        config['usd_rate'] = new_rate
        save_config(config)
        await update.message.reply_text(f"✅ تم الحفظ! سعر الدولار الآن: {format_currency(new_rate)} ل.س")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ رقم غير صالح، حاول مجدداً.")
        return SET_USD_RATE

async def set_clothing_fee_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        new_fee = float(update.message.text)
        if new_fee < 0: raise ValueError
        config = load_config()
        config['clothing_fee'] = new_fee
        save_config(config)
        await update.message.reply_text(f"✅ تم الحفظ! أجور الملابس: {new_fee} $")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ رقم غير صالح، حاول مجدداً.")
        return SET_CATEGORY_FEE

async def set_other_fee_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        new_fee = float(update.message.text)
        if new_fee < 0: raise ValueError
        config = load_config()
        config['other_fee'] = new_fee
        save_config(config)
        await update.message.reply_text(f"✅ تم الحفظ! الأجور الأخرى: {new_fee} $")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ رقم غير صالح، حاول مجدداً.")
        return SET_OTHER_FEE

async def set_whatsapp_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    whatsapp = update.message.text.strip()
    if len(whatsapp) < 5:
        await update.message.reply_text("❌ رقم غير صالح.")
        return SET_WHATSAPP
    config = load_config()
    config['whatsapp'] = whatsapp
    save_config(config)
    await update.message.reply_text(f"✅ تم الحفظ! الرقم الجديد: {whatsapp}")
    return ConversationHandler.END

# ==================== User Handlers ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_name = update.message.from_user.first_name or "المستخدم"
    welcome = f"""
═════════════════════════════
🛍️ <b>بوت حاسبة أسعار SHEIN</b> 🛍️
═════════════════════════════

مرحباً {user_name}! 👋
يساعدك هذا البوت على حساب التكلفة الدقيقة لمنتجات شي إن متضمنة أجور الشحن.

📝 <b>الخطوات المطلوبة:</b>
1️⃣ تأكد أن تطبيق SHEIN مضبوط على <b>السعودية (SAR)</b>.
2️⃣ اختر فئة المنتج من الأزرار بالأسفل.
3️⃣ أدخل السعر بالريال ليتم حساب الفاتورة لك.
    """
    keyboard = [
        [InlineKeyboardButton("👕 ملابس، أحذية، حقائب", callback_data='cat_clothing')],
        [InlineKeyboardButton("🎁 منتجات أخرى (إكسسوارات وغيرها)", callback_data='cat_other')]
    ]
    await update.message.reply_text(welcome, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    return CATEGORY_SELECTION

async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['category'] = 'clothing' if query.data == 'cat_clothing' else 'other'
    
    msg = """
✅ ممتاز!
الآن قم بكتابة <b>سعر المنتج بالريال السعودي</b> كما يظهر لك في التطبيق:
<i>(مثال: 120)</i>
    """
    await query.edit_message_text(msg, parse_mode=ParseMode.HTML)
    return PRICE_INPUT

async def price_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        base_price = float(update.message.text.strip())
        if base_price <= 0: raise ValueError
        
        category = context.user_data.get('category', 'other')
        config = load_config()
        
        base_syp, base_usd, fee_syp, fee_usd, total_syp, total_usd = calculate_prices(base_price, category, config)
        
        result = f"""
═════════════════════════════
🧾 <b>تفاصيل الفاتورة النهائية</b> 🧾
═════════════════════════════
🏷️ سعر المنتج الأصلي: <b>{format_currency(base_price)} ر.س</b> 🇸🇦

🇸🇾 <b>الفاتورة بالليرة السورية:</b>
┌ سعر المنتج: {format_currency(base_syp)} ل.س
├ أجور الشحن: {format_currency(fee_syp)} ل.س
└ <b>الإجمالي المطلوب: {format_currency(total_syp)} ل.س</b>

💵 <b>الفاتورة بالدولار الأمريكي:</b>
┌ سعر المنتج: {base_usd:.2f} $
├ أجور الشحن: {fee_usd:.2f} $
└ <b>الإجمالي المطلوب: {total_usd:.2f} $</b>
═════════════════════════════

📱 <b>للطلب:</b> يرجى تصوير هذه الشاشة ومراسلتنا على الواتساب عبر الرابط التالي:
<a href="https://wa.me/{config['whatsapp'].replace('+', '').replace(' ', '')}">{config['whatsapp']}</a>
        """
        
        keyboard = [[InlineKeyboardButton("🔄 حساب منتج جديد", callback_data='start_again')]]
        await update.message.reply_text(result, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return CATEGORY_SELECTION
        
    except ValueError:
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح (مثال: 150)", parse_mode=ParseMode.HTML)
        return PRICE_INPUT

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == 'start_again':
        keyboard = [
            [InlineKeyboardButton("👕 ملابس، أحذية، حقائب", callback_data='cat_clothing')],
            [InlineKeyboardButton("🎁 منتجات أخرى", callback_data='cat_other')]
        ]
        await query.edit_message_text("🛍️ <b>اختر فئة المنتج:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return CATEGORY_SELECTION

def main() -> None:
    if not TELEGRAM_TOKEN:
        logger.error("❌ لم يتم تعيين TELEGRAM_BOT_TOKEN كمتغير بيئة!")
        return
        
    connect_to_mongo()
    
    from telegram.request import HTTPXRequest
    custom_request = HTTPXRequest(connect_timeout=60.0, read_timeout=60.0, write_timeout=60.0, pool_timeout=60.0)
    
    app = Application.builder().token(TELEGRAM_TOKEN).request(custom_request).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("admin", admin_menu)],
        states={
            CATEGORY_SELECTION: [CallbackQueryHandler(category_callback, pattern='^cat_')],
            PRICE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, price_input)],
            ADMIN_MENU: [CallbackQueryHandler(admin_callback)],
            SET_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_rate_input)],
            SET_USD_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_usd_rate_input)],
            SET_CATEGORY_FEE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_clothing_fee_input)],
            SET_OTHER_FEE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_other_fee_input)],
            SET_WHATSAPP: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_whatsapp_input)],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("admin", admin_menu),
            CallbackQueryHandler(callback_handler, pattern='^start_again$')
        ]
    )
    
    app.add_handler(conv_handler)
    logger.info("✅ البوت يعمل الآن وبانتظار التحديثات...")
    
    # دالة run_polling تتكفل بكل شيء بخصوص الـ Event Loop داخلياً تلقائياً
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()
