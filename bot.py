#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🤖 SHEIN Price Calculator Bot v2.3 - Fixed & Secured Edition
"""

import os
import logging
import json
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler
)
from telegram.constants import ParseMode

# محاولة استيراد pymongo
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

# جلب معرف الأدمن بشكل آمن تماماً وسد الثغرة الأمنية
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

# ==================== خادم ويب وهمي متوافق مع Railway ====================
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

threading.Thread(target=run_health_server, daemon=True).start()

# ==================== إدارة البيانات ====================
def connect_to_mongo():
    global mongo_client, mongo_db
    try:
        if MONGO_AVAILABLE and MONGO_URI:
            mongo_client = MongoClient(
                MONGO_URI,
                serverSelectionTimeoutMS=2000, # تقليل وقت الانتظار لمنع تعليق البوت أثناء التشغيل
                connectTimeoutMS=5000,
                retryWrites=True,
                tls=True,
                tlsAllowInvalidCertificates=True
            )
            mongo_client.admin.command('ping')
            mongo_db = mongo_client['shein_bot']
            logger.info("✅ تم الاتصال بـ MongoDB بنجاح")
            return True
    except Exception as e:
        logger.warning(f"⚠️ خطأ قاعدة البيانات السحابية: {e}. سيتم التراجع لاستخدام ملف JSON المحلي.")
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
            logger.warning(f"خطأ قراءة سحابة: {e}")
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                config = DEFAULT_CONFIG.copy()
                config.update(loaded)
                return config
        except Exception as e:
            logger.warning(f"خطأ قراءة JSON: {e}")
    return DEFAULT_CONFIG.copy()

def save_config(config):
    if mongo_db:
        try:
            config_to_save = config.copy()
            config_to_save['_id'] = 'settings'
            mongo_db['config'].update_one({'_id': 'settings'}, {'$set': config_to_save}, upsert=True)
            logger.info("✅ تم حفظ التعديلات في السحابة")
        except Exception as e:
            logger.warning(f"خطأ حفظ السحابة: {e}")
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        logger.info("✅ تم حفظ التعديلات محلياً")
    except Exception as e:
        logger.error(f"خطأ حفظ JSON: {e}")

def format_currency(amount: float) -> str:
    return f"{amount:,.0f}"

def calculate_prices(base_price: float, category: str, config: dict):
    exchange_rate = config.get('exchange_rate', DEFAULT_CONFIG['exchange_rate'])
    usd_rate = config.get('usd_rate', DEFAULT_CONFIG['usd_rate'])
    fee_usd = config.get('clothing_fee' if category == 'clothing' else 'other_fee', 1.0)
    
    base_syp = base_price * exchange_rate
    base_usd = base_syp / usd_rate
    fee_syp = fee_usd * usd_rate
    total_syp = base_syp + fee_syp
    total_usd = base_usd + fee_usd
    return base_syp, base_usd, fee_syp, fee_usd, total_syp, total_usd

# ==================== Admin Handlers ====================
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if ADMIN_ID is None or update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("❌ عذراً، هذه القائمة مخصصة لإدارة البوت فقط والوصول إليها ممنوع.")
        return ConversationHandler.END
    
    keyboard = [
        [InlineKeyboardButton("💱 سعر صرف الريال", callback_data='set_rate'), InlineKeyboardButton("💵 سعر صرف الدولار", callback_data='set_usd_rate')],
        [InlineKeyboardButton("👕 أجور الملابس ($)", callback_data='set_clothing_fee'), InlineKeyboardButton("🎁 أجور أخرى ($)", callback_data='set_other_fee')],
        [InlineKeyboardButton("📱 رقم الواتس", callback_data='set_whatsapp'), InlineKeyboardButton("📊 عرض الإعدادات", callback_data='show_config')],
        [InlineKeyboardButton("❌ إغلاق القائمة", callback_data='cancel')]
    ]
    await update.message.reply_text("📋 <b>لوحة تحكم الإدارة</b>\n\nاختر الإعداد المطلوب:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    return ADMIN_MENU

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == 'set_rate':
        await query.edit_message_text("💱 أرسل سعر صرف <b>الريال السعودي</b> مقابل الليرة:")
        return SET_RATE
    elif query.data == 'set_usd_rate':
        await query.edit_message_text("💵 أرسل سعر صرف <b>الدولار الأمريكي</b> مقابل الليرة:")
        return SET_USD_RATE
    elif query.data == 'set_clothing_fee':
        await query.edit_message_text("👕 أرسل أجور الملابس بـ <b>الدولار</b>:")
        return SET_CATEGORY_FEE
    elif query.data == 'set_other_fee':
        await query.edit_message_text("🎁 أرسل أجور الإكسسوارات بـ <b>الدولار</b>:")
        return SET_OTHER_FEE
    elif query.data == 'set_whatsapp':
        await query.edit_message_text("📱 أرسل رقم الواتساب كاملاً مع رمز الدولة (مثال: +963900000000):")
        return SET_WHATSAPP
    elif query.data == 'show_config':
        config = load_config()
        msg = f"📊 <b>الإعدادات الحالية:</b>\n\nالريال: {format_currency(config['exchange_rate'])} ل.س\nالدولار: {format_currency(config['usd_rate'])} ل.س\nأجور الملابس: {config['clothing_fee']}$\nأجور أخرى: {config['other_fee']}$"
        await query.edit_message_text(msg, parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    elif query.data == 'cancel':
        await query.edit_message_text("❌ تم إغلاق لوحة التحكم.")
        return ConversationHandler.END

async def set_rate_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = float(update.message.text)
        if val <= 0: raise ValueError
        config = load_config()
        config['exchange_rate'] = val
        save_config(config)
        await update.message.reply_text(f"✅ تم تعديل سعر الريال إلى: {format_currency(val)} ل.س")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ أدخل رقماً صحيحاً.")
        return SET_RATE

async def set_usd_rate_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = float(update.message.text)
        if val <= 0: raise ValueError
        config = load_config()
        config['usd_rate'] = val
        save_config(config)
        await update.message.reply_text(f"✅ تم تعديل سعر الدولار إلى: {format_currency(val)} ل.س")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ أدخل رقماً صحيحاً.")
        return SET_USD_RATE

async def set_clothing_fee_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = float(update.message.text)
        if val < 0: raise ValueError
        config = load_config()
        config['clothing_fee'] = val
        save_config(config)
        await update.message.reply_text(f"✅ تم تعديل قيمة شحن الملابس إلى: {val} $")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ أدخل رقماً صحيحاً.")
        return SET_CATEGORY_FEE

async def set_other_fee_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = float(update.message.text)
        if val < 0: raise ValueError
        config = load_config()
        config['other_fee'] = val
        save_config(config)
        await update.message.reply_text(f"✅ تم تعديل قيمة شحن الإكسسوارات إلى: {val} $")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ أدخل رقماً صحيحاً.")
        return SET_OTHER_FEE

async def set_whatsapp_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    txt = update.message.text.strip()
    if len(txt) < 7:
        await update.message.reply_text("❌ رقم هاتف غير منطقي.")
        return SET_WHATSAPP
    config = load_config()
    config['whatsapp'] = txt
    save_config(config)
    await update.message.reply_text(f"✅ تم اعتماد رقم الواتساب الجديد: {txt}")
    return ConversationHandler.END

# ==================== User Handlers ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    welcome = f"""
🛍️ <b>بوت حاسبة أسعار SHEIN السوق السوري</b> 👋

احسب القيمة الإجمالية لقطع شي إن شاملة عمولات الشحن الثابتة بدقة.
1️⃣ اضبط تطبيق شي إن على <b>السعودية (SAR)</b>.
2️⃣ اختر نوع المنتج من الأزرار بالأسفل:
    """
    keyboard = [
        [InlineKeyboardButton("👕 ملابس، أحذية، حقائب", callback_data='cat_clothing')],
        [InlineKeyboardButton("🎁 منتجات أخرى وإكسسوارات", callback_data='cat_other')]
    ]
    await update.message.reply_text(welcome, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    return CATEGORY_SELECTION

async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['category'] = 'clothing' if query.data == 'cat_clothing' else 'other'
    await query.edit_message_text("💰 الآن، أرسل <b>سعر المنتج بالريال السعودي</b> كما يظهر بالتطبيق:", parse_mode=ParseMode.HTML)
    return PRICE_INPUT

async def price_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        base_price = float(update.message.text.strip())
        if base_price <= 0: raise ValueError
        
        category = context.user_data.get('category', 'other')
        config = load_config()
        base_syp, base_usd, fee_syp, fee_usd, total_syp, total_usd = calculate_prices(base_price, category, config)
        
        result = f"""
🧾 <b>تفاصيل الفاتورة المقدرة للمنتج:</b>
🏷️ السعر الأساسي: {format_currency(base_price)} ريال سعودي

🇸🇾 <b>بالليرة السورية:</b>
┌ المنتج صافي: {format_currency(base_syp)} ل.س
├ أجور الشحن: {format_currency(fee_syp)} ل.س
└ <b>الإجمالي المطلوب: {format_currency(total_syp)} ل.س</b>

💵 <b>بالدولار الأمريكي:</b>
┌ المنتج صافي: {base_usd:.2f} $
├ أجور الشحن: {fee_usd:.2f} $
└ <b>الإجمالي المطلوب: {total_usd:.2f} $</b>

📱 <b>لتأكيد الطلب:</b> يرجى تصوير الشاشة وإرسالها عبر الواتساب:
👉 <a href="https://wa.me/{config['whatsapp'].replace('+', '').replace(' ', '')}">اضغط هنا لمراسلتنا ({config['whatsapp']})</a>
        """
        keyboard = [[InlineKeyboardButton("🔄 حساب قطعة جديدة", callback_data='start_again')]]
        await update.message.reply_text(result, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return CATEGORY_SELECTION
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال سعر رقمي صحيح (مثال: 85 أو 140.5)")
        return PRICE_INPUT

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == 'start_again':
        keyboard = [
            [InlineKeyboardButton("👕 ملابس، أحذية، حقائب", callback_data='cat_clothing')],
            [InlineKeyboardButton("🎁 منتجات أخرى", callback_data='cat_other')]
        ]
        await query.edit_message_text("🛍️ <b>اختر فئة المنتج الجديد:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return CATEGORY_SELECTION

def main() -> None:
    if not TELEGRAM_TOKEN:
        logger.error("❌ لم يتم العثور على متغير البيئة TELEGRAM_BOT_TOKEN")
        return
        
    connect_to_mongo()
    
    from telegram.request import HTTPXRequest
    custom_request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    
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
    logger.info("🚀 البوت مستعد وبدأ استقبال البيانات بكفاءة...")
    
    # دع المكتبة تدير الـ Loop لمنع مشاكل بايثون 3.13
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()
