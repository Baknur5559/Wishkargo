# bot.py (ПОЛНАЯ ПЕРЕПИСАННАЯ ВЕРСИЯ)

import os
import httpx
from typing import Optional
from sqlalchemy.orm import Session
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, joinedload

from models import Client, Order

# --- НАСТРОЙКА ---
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# Убедитесь, что ваш .env файл содержит эту переменную, или замените IP вручную
# Пример: ADMIN_API_URL=http://192.168.1.5:8000
ADMIN_API_URL = os.getenv('ADMIN_API_URL')

if not TELEGRAM_BOT_TOKEN or not DATABASE_URL or not ADMIN_API_URL:
    print("Ошибка: Убедитесь, что TELEGRAM_BOT_TOKEN, DATABASE_URL и ADMIN_API_URL заданы в .env файле.")
    exit()

# ИСПРАВЛЕНИЕ: Добавляем параметры для стабильного подключения к БД
engine = create_engine(DATABASE_URL, pool_recycle=1800, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# --- Клавиатуры (Меню) ---
main_menu_keyboard = [
    ["👤 Мой профиль", "📦 Мои заказы"],
    ["➕ Добавить заказ", "🇨🇳 Адреса складов"],
    ["🇰🇬 Наши контакты"]
]
main_menu_markup = ReplyKeyboardMarkup(main_menu_keyboard, resize_keyboard=True)

# --- Состояния для диалогов ---
TRACK_CODE, COMMENT = range(2)
GET_NAME = range(2, 3)

# --- Функции-помощники ---
def normalize_phone_number(phone_str: str) -> str:
    digits = "".join(filter(str.isdigit, phone_str))
    if len(digits) == 12 and digits.startswith("996"): return digits[3:]
    if len(digits) == 10 and digits.startswith("0"): return digits[1:]
    if len(digits) == 9: return digits
    return ""

def get_db():
    return SessionLocal()

async def get_client_from_user(user_id: int, db: Session):
    return db.query(Client).filter(Client.telegram_chat_id == str(user_id)).first()

# --- Основные функции бота ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db = get_db()
    try:
        client = await get_client_from_user(user.id, db)
        if client:
            await update.message.reply_html(
                f"👋 Здравствуйте, <b>{client.full_name}</b>!\n\nРад вас снова видеть! Используйте меню ниже для навигации.",
                reply_markup=main_menu_markup
            )
        else:
            await update.message.reply_text(
                "Здравствуйте! 🌟\n\nЧтобы я мог вас узнать, пожалуйста, отправьте мне ваш номер телефона (тот, который вы указывали при регистрации).",
                reply_markup=ReplyKeyboardRemove()
            )
    finally:
        db.close()

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE, client: Client) -> None:
    # Получаем ссылку на личный кабинет
    lk_url = None
    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.post(f"{ADMIN_API_URL}/clients/{client.id}/generate_lk_link")
            if response.status_code == 200:
                lk_url = response.json().get("link")
    except Exception as e:
        print(f"Ошибка при генерации ссылки на ЛК для клиента {client.id}: {e}")

    # Формируем текст профиля
    text = (
        f"👤 <b>Ваш профиль</b>\n\n"
        f"<b>✨ ФИО:</b> {client.full_name}\n"
        f"<b>📞 Телефон:</b> {client.phone}\n"
        f"<b>⭐️ Ваш код:</b> {client.client_code_prefix}{client.client_code_num}\n\n"
        f"<i>Пожалуйста, всегда указывайте этот код при оформлении заказов на наш склад.</i>"
    )

    # Создаем кнопку, только если ссылка успешно получена
    reply_markup = main_menu_markup
    if lk_url:
        keyboard = [[InlineKeyboardButton("Перейти в Личный Кабинет", url=lk_url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)

    # Отправляем сообщение с текстом и кнопкой (или без нее)
    await update.message.reply_html(text, reply_markup=reply_markup)


async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE, client: Client) -> None:
    db = get_db()
    try:
        client_with_orders = db.query(Client).options(joinedload(Client.orders)).filter(Client.id == client.id).one()
        active_orders = [order for order in client_with_orders.orders if order.status != "Выдан"]
        
        if not active_orders:
            await update.message.reply_text("У вас пока нет активных заказов. 🚚", reply_markup=main_menu_markup)
            return

        message = "📦 <b>Ваши текущие заказы:</b>\n\n"
        for order in sorted(active_orders, key=lambda o: o.created_at, reverse=True):
            message += f"<b>Трек:</b> <code>{order.track_code}</code>\n"
            message += f"<b>Статус:</b> {order.status}\n"
            if order.comment:
                message += f"<b>Примечание:</b> {order.comment}\n"
            message += "──────────────\n"
        await update.message.reply_html(message, reply_markup=main_menu_markup)
    finally:
        db.close()

async def china_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE, client: Client) -> None:
    client_code = f"WISH-{client.client_code_num}"
    address_text = (
        f"星星 {client_code}\n"
        f"13258515581\n"
        f"广东省 佛山市 南海区 里水镇 草场海南州工业区98号WISH启那科技园E104-1 ({client_code})"
    )
    text = (
        f"🇨🇳 <b>Адрес нашего склада в Китае</b>\n\n"
        f"Используйте этот адрес для всех ваших покупок на Pinduoduo, Taobao, 1688, Poizon.\n\n"
        f"<i>Обязательно скопируйте его полностью, вместе с вашим уникальным кодом <b>{client_code}</b>!</i>\n\n"
        f"👇 Просто нажмите на адрес ниже, чтобы скопировать:\n\n"
        f"<code>{address_text}</code>"
    )
    await update.message.reply_html(text, reply_markup=main_menu_markup)

async def bishkek_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🇰🇬 <b>Наши контакты в Бишкеке</b>\n\n"
        "📍 <b>Наш адрес:</b>\n4-й микрорайон, 7/2, цокольный этаж\n\n"
        "📞 <b>Телефон для связи:</b>\n<code>+996 555 36-63-86</code> (нажмите, чтобы скопировать)"
    )
    keyboard = [
        [InlineKeyboardButton("💬 Написать в WhatsApp", url="https://wa.me/+996555366386")],
        [InlineKeyboardButton("📸 Наш Instagram", url="https://www.instagram.com/wishcargo.kg")],
        [InlineKeyboardButton("🗺️ Показать на карте (2ГИС)", url="https://go.2gis.com/8z9s1")],
    ]
    await update.message.reply_html(text, reply_markup=InlineKeyboardMarkup(keyboard))

# Стало:
async def add_order_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [[KeyboardButton("Отмена")]]
    await update.message.reply_text(
        "📦 Пожалуйста, введите трек-код вашего нового заказа.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return TRACK_CODE

# Стало:
async def received_track_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['track_code'] = update.message.text
    keyboard = [
        [KeyboardButton("⏩ Пропустить")],
        [KeyboardButton("Отмена")]
    ]
    await update.message.reply_text(
        "Отлично! Теперь введите примечание (например, 'красные кроссовки') или нажмите 'Пропустить'.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return COMMENT

async def received_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['comment'] = update.message.text
    await save_order_from_bot(update, context)
    return ConversationHandler.END

async def skip_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['comment'] = None
    await save_order_from_bot(update, context)
    return ConversationHandler.END

async def save_order_from_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = get_db()
    try:
        client = await get_client_from_user(update.effective_user.id, db)
        if not client:
            await update.message.reply_text("Произошла ошибка, ваш профиль не найден. Попробуйте /start", reply_markup=main_menu_markup)
            return

        new_order = Order(
            track_code=context.user_data['track_code'],
            comment=context.user_data['comment'],
            client_id=client.id,
            purchase_type="Доставка",
            status="В обработке"
        )
        db.add(new_order)
        db.commit()
        await update.message.reply_html(
            f"✅ Готово! Ваш заказ с трек-кодом <code>{context.user_data['track_code']}</code> успешно добавлен.",
            reply_markup=main_menu_markup
        )
    finally:
        context.user_data.clear()
        db.close()

async def cancel_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Действие отменено.", reply_markup=main_menu_markup)
    context.user_data.clear()
    return ConversationHandler.END

async def register_new_client(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    full_name = update.message.text
    phone = context.user_data.get('phone_to_register')
    user = update.effective_user

    if not phone:
        await update.message.reply_text("Произошла ошибка. Попробуйте снова отправить /start и ваш номер телефона.", reply_markup=main_menu_markup)
        return ConversationHandler.END

    db = get_db()
    try:
        payload = {
            "full_name": full_name,
            "phone": phone,
            "client_code_prefix": "TG"
        }
        async with httpx.AsyncClient() as http_client:
            response = await http_client.post(f"{ADMIN_API_URL}/register_client", json=payload)
            if response.status_code != 200:
                error_data = response.json()
                raise Exception(error_data.get("detail", "Неизвестная ошибка регистрации"))
            new_client_data = response.json().get("client")

        client_to_update = db.query(Client).filter(Client.id == new_client_data['id']).first()
        if client_to_update:
            client_to_update.telegram_chat_id = str(user.id)
            db.commit()

        await update.message.reply_html(
            f"✅ Регистрация прошла успешно, <b>{full_name}</b>!\n\n"
            f"Ваш уникальный код клиента: <b>{new_client_data['client_code_prefix']}{new_client_data['client_code_num']}</b>\n\n"
            "Теперь вы можете пользоваться всеми функциями бота.",
            reply_markup=main_menu_markup
        )
    except Exception as e:
        await update.message.reply_text(f"Произошла ошибка при регистрации: {e}", reply_markup=main_menu_markup)
    finally:
        context.user_data.clear()
        db.close()

    return ConversationHandler.END

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    user = update.effective_user
    text = update.message.text
    db = get_db()

    try:
        client_already_linked = await get_client_from_user(user.id, db)
        
        if client_already_linked:
            if text == "👤 Мой профиль":
                await profile(update, context, client_already_linked)
            elif text == "📦 Мои заказы":
                await my_orders(update, context, client_already_linked)
            elif text == "🇨🇳 Адреса складов":
                await china_addresses(update, context, client_already_linked)
            elif text == "🇰🇬 Наши контакты":
                await bishkek_contacts(update, context)
            else:
                await update.message.reply_text("Я не понимаю эту команду. Пожалуйста, используйте кнопки меню.", reply_markup=main_menu_markup)
            return ConversationHandler.END

        normalized_phone = normalize_phone_number(text)
        
        if not normalized_phone:
            await update.message.reply_text("Неверный формат номера. Попробуйте еще раз (например, 0555123456).")
            return ConversationHandler.END

        client_found = db.query(Client).filter(Client.phone == normalized_phone).first()
        
        if client_found:
            client_found.telegram_chat_id = str(user.id)
            db.commit()
            await update.message.reply_html(
                f"🎉 Отлично, <b>{client_found.full_name}</b>! Ваш аккаунт успешно привязан.\n\n"
                "Теперь вы можете пользоваться всеми функциями. Используйте меню ниже 👇",
                reply_markup=main_menu_markup
            )
            return ConversationHandler.END
        else:
            context.user_data['phone_to_register'] = normalized_phone
            await update.message.reply_text(
                f"Клиент с номером {text} не найден. Хотите зарегистрироваться?\n\n"
                "Пожалуйста, отправьте ваше полное имя (ФИО)."
            )
            return GET_NAME
    finally:
        db.close()

def main() -> None:
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex('^➕ Добавить заказ$'), add_order_start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message)
        ],
        states={
            # Шаг 1: Ожидание трек-кода
            TRACK_CODE: [
                # СНАЧАЛА проверяем, не отмена ли это
                MessageHandler(filters.Regex('^Отмена$'), cancel_dialog),
                # ЕСЛИ НЕТ, то считаем это трек-кодом
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_track_code)
            ],
            # Шаг 2: Ожидание комментария
            COMMENT: [
                MessageHandler(filters.Regex('^⏩ Пропустить$'), skip_comment),
                # И здесь СНАЧАЛА проверяем на отмену
                MessageHandler(filters.Regex('^Отмена$'), cancel_dialog),
                # И только потом считаем это комментарием
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_comment)
            ],
            # Шаг 3 (для регистрации): Ожидание имени
            GET_NAME: [
                # И здесь тоже проверяем на отмену
                MessageHandler(filters.Regex('^Отмена$'), cancel_dialog),
                MessageHandler(filters.TEXT & ~filters.COMMAND, register_new_client)
            ],
        },
        fallbacks=[
            CommandHandler('cancel', cancel_dialog),
            MessageHandler(filters.Regex('^Отмена$'), cancel_dialog)
        ],
    )

    application.add_handler(conv_handler)

    print("Бот запущен и готов к работе...")
    application.run_polling()

if __name__ == "__main__":
    main()