# main.py (ИСПРАВЛЕННАЯ ВЕРСЯ 3.0)

import os
from datetime import date, datetime, time, timedelta, timezone
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, status, Query, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, func, or_, String, cast, Date as SQLDate
from sqlalchemy.orm import sessionmaker, Session, joinedload
from pydantic import BaseModel, Field
from typing import List, Optional
import asyncio
import telegram
from telegram import InlineKeyboardMarkup, InlineKeyboardButton # <-- ДОБАВЛЕНО
import httpx
import traceback
import re
import logging # <-- Убедись, что этот импорт есть
import sys # <-- Убедись, что этот импорт есть

# --- НАСТРОЙКА ЛОГИРОВАНИЯ (СКОПИРУЙ ЭТОТ БЛОК) ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
# Мы создаем глобальную переменную 'logger'
logger = logging.getLogger(__name__) 
# --- КОНЕЦ НАСТРОЙКИ ---

# === НАЧАЛО ИЗМЕНЕНИЯ ===
# Определяем статусы ЗДЕСЬ, в глобальной области видимости, ПОСЛЕ импортов
ORDER_STATUSES = ["В обработке", "Ожидает выкупа", "Выкуплен", "На складе в Китае", "В пути", "На складе в КР", "Готов к выдаче", "Выдан"]
# === КОНЕЦ ИЗМЕНЕНИЯ ===

# --- Импортируем ВСЕ наши НОВЫE модели ---
from models import (
    Base, Company, Location, Client, Order, Role, Permission, Employee,
    ExpenseType, Shift, Expense, Setting,
    Broadcast, BroadcastReaction, OrderHistory, # <-- ДОБАВЛЕНО
    role_permissions_table
)
# Импортируем Session и List для типизации
from sqlalchemy.orm import Session
from typing import List, Optional # Убедись, что List импортирован


# --- Функция отправки уведомлений ---
# (Убедись, что 'SessionLocal' импортирован или определен вверху 'main.py')
# (Например: from models import SessionLocal)

async def generate_and_send_notification(client: Client, new_status: str, track_codes: List[str]):
    """
    (ИСПРАВЛЕНО - Задача 3-Б) Отправляет уведомление, ИСПОЛЬЗУЯ ТОКЕН КОМПАНИИ.
    (ВЕРСИЯ С ИСТОРИЕЙ СТАТУСОВ, ФИЛИАЛОМ, ЭМОДЗИ и СОБСТВЕННОЙ СЕССИЕЙ DB)
    """
    
    # --- НОВОЕ: Создаем свою сессию ---
    db = SessionLocal()
    try:
    # --- КОНЕЦ НОВОГО ---

        # --- Блок проверки chat_id и форматирования трек-кодов ---
        if not client.telegram_chat_id:
            print(f"INFO: У клиента {client.full_name} (ID: {client.id}) нет telegram_chat_id. Уведомление не отправлено.")
            return # Выходим, если ID чата нет
        track_codes_str = "\n".join([f"<code>{code}</code>" for code in track_codes])

        # --- (Задача 3-Б) Получаем историю статусов ---
        history_str = ""
        if track_codes:
            first_track_code = track_codes[0]
            # Ищем ОДИН заказ, чтобы получить его ID
            order_for_history = db.query(Order.id).filter(
                Order.track_code == first_track_code,
                Order.client_id == client.id,
                Order.company_id == client.company_id
            ).first()
            
            if order_for_history:
                history_entries = db.query(OrderHistory).filter(
                    OrderHistory.order_id == order_for_history.id
                ).order_by(OrderHistory.created_at.asc()).all()
                
                if history_entries:
                    history_str = "\n<b>⏳ История статусов:</b>\n"
                    bishkek_tz = timezone(timedelta(hours=6)) # UTC+6
                    for entry in history_entries:
                        bishkek_date = entry.created_at.astimezone(bishkek_tz)
                        hist_date = bishkek_date.strftime('%d.%m %H:%M')
                        history_str += f"<i>- {hist_date}: {entry.status}</i>\n"
        # --- Конец (Задача 3-Б) ---

        # --- Получаем токен бота ИЗ КОМПАНИИ клиента (Используем нашу 'db') ---
        company_bot_token = None
        if client.company_id:
            company = db.query(Company).filter(Company.id == client.company_id).first()
            if company and company.telegram_bot_token:
                company_bot_token = company.telegram_bot_token
            else:
                print(f"WARNING: Не найден токен Telegram-бота для компании ID {client.company_id}. Уведомление для клиента ID {client.id} не будет отправлено.")
                return
        else:
            print(f"WARNING: У клиента ID {client.id} не указана компания. Уведомление не будет отправлено.")
            return
        if not company_bot_token:
            return
        # --- Конец блока получения токена ---

        # --- Блок загрузки контактов и генерации ссылки на ЛК (Используем нашу 'db') ---
        phone_setting = db.query(Setting).filter(Setting.key == 'contact_phone', Setting.company_id == client.company_id).first()
        phone = phone_setting.value if phone_setting and phone_setting.value else "Телефон не указан"
        
        secret_token = f"CLIENT-{client.id}-COMPANY-{client.company_id}-SECRET"
        client_portal_base_url = os.getenv("CLIENT_PORTAL_URL", "http://ВАШ_ДОМЕН_ИЛИ_IP/lk.html") 
        lk_link = f"{client_portal_base_url}?token={secret_token}"
        # --- Конец блока контактов и ЛК ---

        # --- Получаем данные о заказе и филиале (Используем нашу 'db') ---
        orders_in_db = db.query(Order).options(
            joinedload(Order.location) # <-- ЗАГРУЖАЕМ ФИЛИАЛ
        ).filter(
            Order.client_id == client.id,
            Order.track_code.in_(track_codes),
            Order.company_id == client.company_id
        ).all()

        location_name = "Наш офис"
        location_address = "Адрес уточняется у менеджера"
        total_cost = 0
        total_weight = 0

        if orders_in_db:
            first_order = orders_in_db[0]
            if first_order.location:
                location_name = first_order.location.name 
                location_address = first_order.location.address or f"Филиал '{location_name}' (адрес не указан)"
            
            for order in orders_in_db:
                total_cost += order.calculated_final_cost_som or 0
                total_weight += order.calculated_weight_kg or 0

        # --- Формирование сообщения (с `history_str`) ---
        message = f"Здравствуйте, <b>{client.full_name}</b>! 👋\n\n"
        
        if new_status == "Готов к выдаче":
            cost_str = f"К оплате: <b>{total_cost:.2f} сом</b> 💰\n\n" if total_cost > 0 else ""
            weight_str = f"Общий вес: <b>{total_weight:.3f} кг</b> ⚖️\n\n" if total_weight > 0 else ""

            message += (
                f"🎉🎉🎉 <b>ПОСЫЛКИ НА МЕСТЕ!</b> 🎉🎉🎉\n\n"
                f"Спешим сообщить, что ваши заказы уже прибыли в наш филиал <b>'{location_name}'</b> и очень ждут вас!\n\n"
                f"<b>Трек-коды:</b>\n{track_codes_str}\n\n"
                f"<b>Статус:</b> ✅ <b>{new_status}</b> ✅\n" # <-- Убрал \n\n
                f"{history_str}\n" # <-- ДОБАВЛЕНО
                f"{weight_str}"
                f"{cost_str}"
                f"📍 <b>Забрать можно здесь:</b>\n{location_address}\n\n" 
                f"📞 <b>Вопросы? Звоните:</b> <code>{phone}</code>\n"
                f"💻 <b>Ваш Личный кабинет:</b> <a href='{lk_link}'>Перейти</a>"
            )
        
        elif new_status == "В пути":
            message += (
                f"Ваши заказы уже мчатся к вам! 🚚💨\n\n"
                f"<b>Статус отправлений:</b>\n{track_codes_str}\n\n"
                f"...изменился на: ➡️ <b>{new_status}</b>\n" # <-- Убрал \n\n
                f"{history_str}\n" # <-- ДОБАВЛЕНО
                f"Мы сообщим, как только они прибудут! 🥳\nСледить за заказами можно в <a href='{lk_link}'>личном кабинете</a>."
            )
        
        elif new_status == "На складе в КР":
            message += (
                f"Отличные новости! 🤩 Ваши заказы прибыли на наш склад в Кыргызстане!\n\n"
                f"<b>Статус посылок:</b>\n{track_codes_str}\n\n"
                f"...изменился на: 🇰🇬 <b>{new_status}</b> 🇰🇬\n" # <-- Убрал \n\n
                f"{history_str}\n" # <-- ДОБАВЛЕНО
                f"Сейчас мы их сортируем и скоро они будут готовы к выдаче! 🚀\n"
                f"Подробности в <a href='{lk_link}'>личном кабинете</a>."
            )
        
        else: # Стандартное уведомление
            message += (
                f"Обновление по вашим заказам! 📄\n\n"
                f"<b>Новый статус для:</b>\n{track_codes_str}\n\n"
                f"➡️ <b>{new_status}</b>\n" # <-- Убрал \n\n
                f"{history_str}\n" # <-- ДОБАВЛЕНО
                f"Подробности в <a href='{lk_link}'>личном кабинете</a>."
            )
        # --- Конец формирования сообщения ---

        # --- Отправка сообщения ---
        try:
            bot = telegram.Bot(token=company_bot_token)
            await bot.send_message(chat_id=client.telegram_chat_id, text=message, parse_mode='HTML')
            print(f"INFO: Уведомление успешно отправлено клиенту {client.full_name} (ID: {client.id}, Company: {client.company_id}) о статусе '{new_status}'.")
        except Exception as e:
            print(f"ERROR: Ошибка при отправке Telegram сообщения клиенту ID {client.id} (ChatID: {client.telegram_chat_id}, Company: {client.company_id}) через токен компании: {e}")

    # --- НОВОЕ: Закрываем сессию ---
    finally:
        db.close()
    # --- КОНЕЦ НОВОГО ---
    
# Определяем статусы ЗДЕСЬ, в глобальной области видимости, ПОСЛЕ импортов
ORDER_STATUSES = ["В обработке", "Ожидает выкупа", "Выкуплен", "На складе в Китае", "В пути", "На складе в КР", "Готов к выдаче", "Выдан"]

# --- 1. НАСТРОЙКА ---
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
#TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")#

if not DATABASE_URL:
    raise RuntimeError("Не найден ключ DATABASE_URL в файле .env")

# Добавляем параметры для управления пулом соединений
engine = create_engine(
    DATABASE_URL,
    pool_recycle=1800, # Переподключаться каждые 30 минут (1800 секунд)
    pool_pre_ping=True # Проверять соединение перед использованием
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
app = FastAPI(title="Cargo CRM API - Multi-Tenant")

# --- 2. DEPENDENCIES (Аутентификация) ---

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Разрешаем всем
    allow_credentials=True,
    allow_methods=["*"], # Разрешаем все методы
    allow_headers=["*"], # Разрешаем все заголовки (включая наш X-Employee-ID)
)

# --- ФУНКЦИИ ДЛЯ TELEGRAM УВЕДОМЛЕНИЙ (Multi-Tenant) ---

async def send_telegram_message(
    token: str, 
    chat_id: str, 
    text: str, 
    photo_id: Optional[str] = None,
    broadcast_id: Optional[int] = None # <-- ДОБАВЛЕНО
):
    """
    Асинхронно отправляет сообщение (или фото с подписью) в Telegram, 
    используя КОНКРЕТНЫЙ токен.
    Если передан broadcast_id, добавляет кнопки реакций.
    """
    if not token:
        print("WARNING: [Notification] Передан пустой токен. Уведомление не отправлено.")
        return

    # --- ДОБАВЛЕНО: Создание кнопок реакций ---
    reply_markup = None
    if broadcast_id:
        keyboard = [
            [
                InlineKeyboardButton("👍", callback_data=f"react_{broadcast_id}_like"),
                InlineKeyboardButton("👎", callback_data=f"react_{broadcast_id}_dislike"),
                # (Можно добавить больше кнопок)
                # InlineKeyboardButton("🔥", callback_data=f"react_{broadcast_id}_fire"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
    # --- КОНЕЦ ДОБАВЛЕНИЯ ---

    try:
        bot = telegram.Bot(token=token)
        
        if photo_id:
            # Если есть photo_id, отправляем фото с подписью
            await bot.send_photo(
                chat_id=chat_id, 
                photo=photo_id, 
                caption=text, 
                parse_mode='HTML',
                reply_markup=reply_markup # <-- ДОБАВЛЕНО
            )
            print(f"[Notification] ФОТО+Текст успешно отправлено в chat_id {chat_id}")
        else:
            # Если нет, отправляем просто текст
            await bot.send_message(
                chat_id=chat_id, 
                text=text, 
                parse_mode='HTML', 
                disable_web_page_preview=True,
                reply_markup=reply_markup # <-- ДОБАВЛЕНО
            )
            print(f"[Notification] Сообщение успешно отправлено в chat_id {chat_id}")

    except Exception as e:
        print(f"!!! ОШИБКА [Notification] при отправке в chat_id {chat_id} (токен ...{token[-4:]}): {e}")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# НАША ГЛАВНАЯ DEPENDENCY ДЛЯ БЕЗОПАСНОСТИ
def get_current_active_employee(
    x_employee_id: Optional[str] = Header(None),  
    db: Session = Depends(get_db)
) -> Employee:
    """
    Проверяет заголовок X-Employee-ID, находит сотрудника в БД.
    Это - наша "сессия" пользователя.
    """
    if not x_employee_id:
        raise HTTPException(status_code=401, detail="Отсутствует заголовок X-Employee-ID (Не авторизован)")
    
    try:
        employee_id = int(x_employee_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Неверный формат X-Employee-ID")

    employee = db.query(Employee).options(
        joinedload(Employee.role).joinedload(Role.permissions)
    ).filter(Employee.id == employee_id).first()
    
    db.refresh(employee)
    print("----- DEBUG: Employee Attributes after refresh -----")
    print(dir(employee)) # Эта строка покажет все атрибуты объекта
    print("----- END DEBUG -----") # Принудительно обновить атрибуты объекта из БД

    if not employee:
        raise HTTPException(status_code=401, detail="Сотрудник не найден (Не авторизован)")
    
    if not employee.is_active:
        raise HTTPException(status_code=403, detail="Сотрудник неактивен")

    return employee

# Dependency для проверки прав СУПЕР-АДМИНА
def get_super_admin(employee: Employee = Depends(get_current_active_employee)):
    if employee.company_id is not None or employee.role.name != "Super-Admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен. Требуются права Super-Admin.")
    return employee

# Dependency для проверки прав ВЛАДЕЛЬЦА КОМПАНИИ
def get_company_owner(employee: Employee = Depends(get_current_active_employee)):
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Это действие только для сотрудников компании.")
    
    # Проверяем, есть ли у него нужные права
    permissions = {p.codename for p in employee.role.permissions}
    if 'manage_employees' not in permissions and 'manage_roles' not in permissions and 'manage_locations' not in permissions:
         raise HTTPException(status_code=403, detail="У вас нет прав на управление персоналом или филиалами.")
        
    return employee

# --- НОВАЯ ЗАВИСИМОСТЬ: Для обычных сотрудников ---
def get_current_company_employee(employee: Employee = Depends(get_current_active_employee)):
    """
    Проверяет, что сотрудник (не SuperAdmin) принадлежит компании.
    Используется для эндпоинтов, доступных всем сотрудникам (например, просмотр клиентов, заказов).
    """
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Это действие доступно только сотрудникам компании.")
    return employee


# --- НОВАЯ ЗАВИСИМОСТЬ: Для управления Клиентами ---
def get_client_manager(employee: Employee = Depends(get_current_active_employee)):
    """
    Проверяет, что сотрудник (не SuperAdmin) принадлежит компании
    И имеет право 'manage_clients'.
    """
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Это действие доступно только сотрудникам компании.")

    # Проверяем, есть ли у него нужные права
    permissions = {p.codename for p in employee.role.permissions}
    if 'manage_clients' not in permissions:
         raise HTTPException(status_code=403, detail="У вас нет прав на управление клиентами.")

    return employee
# --- КОНЕЦ НОВОЙ ЗАВИСИМОСТИ ---


# --- 3. Pydantic МОДЕЛИ ---

# Модель для создания Супер-Админа
class SuperAdminSetupPayload(BaseModel):
    full_name: str
    password: str

# --- Модели для Компаний (Super-Admin) ---
class CompanyBase(BaseModel):
    name: str
    company_code: str = Field(..., pattern=r'^[A-Z0-9_]{3,15}$')
    contact_person: Optional[str] = None
    contact_phone: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_bot_username: Optional[str] = None
class CompanyCreate(CompanyBase):
    subscription_paid_until: date
    owner_full_name: str
    owner_password: str
class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    contact_person: Optional[str] = None
    contact_phone: Optional[str] = None
    is_active: Optional[bool] = None
    subscription_paid_until: Optional[date] = None
    telegram_bot_token: Optional[str] = None # Позволяем обновлять
    telegram_bot_username: Optional[str] = None # Позволяем обновлять
class CompanyOut(CompanyBase):
    id: int
    is_active: bool
    subscription_paid_until: Optional[date]
    created_at: datetime
    class Config:
        orm_mode = True

# --- Модели для Логина ---
class LoginPayload(BaseModel):
    password: str
    company_code: Optional[str] = None
class LoginResponse(BaseModel):
    status: str
    employee: dict  # {id, full_name, role, permissions, is_super_admin, location_id}
    company: Optional[dict] # {id, name, company_code}

# --- Модели для Управления Персоналом (Владелец Компании) ---

class LocationBase(BaseModel):
    name: str
    address: Optional[str] = None
    phone: Optional[str] = None
    whatsapp_link: Optional[str] = None
    instagram_link: Optional[str] = None
    map_link: Optional[str] = None

class LocationCreate(LocationBase):
    pass

class LocationUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    whatsapp_link: Optional[str] = None
    instagram_link: Optional[str] = None
    map_link: Optional[str] = None

class LocationOut(LocationBase):
    id: int
    company_id: int
    class Config:
        orm_mode = True

class RoleBase(BaseModel):
    name: str
# ИСПРАВЛЕНИЕ: Добавлены поля для ORM Mode
class RoleOut(RoleBase):
    id: int
    class Config:
        orm_mode = True

class RolePermissionsUpdate(BaseModel):
    permission_ids: List[int]

class PermissionOut(BaseModel):
    id: int
    codename: str
    description: str
    class Config:
        orm_mode = True

class EmployeeBase(BaseModel):
    full_name: str
    location_id: int
    role_id: int
class EmployeeCreate(EmployeeBase):
    password: str
class EmployeeUpdate(BaseModel):
    full_name: Optional[str] = None
    location_id: Optional[int] = None
    role_id: Optional[int] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None
class EmployeeOut(EmployeeBase):
    id: int
    is_active: bool
    # ИСПРАВЛЕНИЕ: RoleOut должна быть здесь для ответа
    role: RoleOut  
    class Config:
        orm_mode = True

# === НАЧАЛО НОВЫХ МОДЕЛЕЙ (СМЕНЫ И РАСХОДЫ) ===

# --- Модели для Смен ---
class ShiftBase(BaseModel):
    starting_cash: float
    exchange_rate_usd: float
    price_per_kg_usd: float

class ShiftOpenPayload(ShiftBase):
    # При открытии смены сотрудник выбирает себя (или Владелец выбирает сотрудника)
    employee_id: int # ID сотрудника, который открывает смену
    location_id: int # ID филиала, где открывается смена

class ShiftClosePayload(BaseModel):
    closing_cash: float

class ShiftOut(ShiftBase):
    id: int
    start_time: datetime
    end_time: Optional[datetime] = None
    closing_cash: Optional[float] = None
    employee_id: int
    location_id: int
    company_id: int
    # Можно добавить данные сотрудника и филиала при необходимости
    # employee: EmployeeOut
    # location: LocationOut
    class Config:
        orm_mode = True

# --- Модели для Отчетов по смене ---
class ShiftReport(BaseModel):
    shift_id: Optional[int] = None # Для отчета по ID
    shift_start_time: datetime
    shift_end_time: Optional[datetime] = None
    employee_name: str
    location_name: str # Добавляем название филиала
    starting_cash: float
    cash_income: float = 0
    card_income: float = 0
    total_expenses: float = 0 # Расходы БЕЗ зарплат/авансов
    total_returns: float = 0
    calculated_cash: float # Расчетный остаток на конец
    actual_closing_cash: Optional[float] = None # Фактический остаток (если смена закрыта)
    discrepancy: Optional[float] = None # Расхождение (если смена закрыта)

# --- Модели для Типов Расходов ---
class ExpenseTypeBase(BaseModel):
    name: str

class ExpenseTypeCreate(ExpenseTypeBase):
    pass

class ExpenseTypeUpdate(ExpenseTypeBase):
    pass

class ExpenseTypeOut(ExpenseTypeBase):
    id: int
    company_id: int
    class Config:
        orm_mode = True

# === КОНЕЦ НОВЫХ МОДЕЛЕЙ ===

# --- Модели для Настроек (Settings) ---
class SettingOut(BaseModel):
    key: str
    value: Optional[str]
    class Config:
        orm_mode = True

class SettingsUpdatePayload(BaseModel):
    # Мы будем принимать словарь {key: value, ...}
    settings: dict[str, Optional[str]]

# main.py (Добавление новой модели)

class ShiftForceClosePayload(BaseModel):
    closing_cash: float
    password: str # Требуем пароль Владельца

# main.py

class BotUnlinkPayload(BaseModel):
    telegram_chat_id: str
    company_id: int

# main.py (Добавление нового эндпоинта)
@app.post("/api/shifts/{shift_id}/force_close", tags=["Смены"])
def force_close_shift(
    shift_id: int,
    payload: ShiftForceClosePayload,
    employee: Employee = Depends(get_company_owner), # Только Владелец может
    db: Session = Depends(get_db)
):
    """Принудительное закрытие смены (доступно только Владельцу)."""
    # 1. Проверка пароля Владельца
    if employee.password != payload.password:
        raise HTTPException(status_code=403, detail="Неверный пароль Владельца.")

    # 2. Находим смену
    shift = db.query(Shift).filter(
        Shift.id == shift_id,
        Shift.company_id == employee.company_id # Принадлежит этой компании
    ).first()

    if not shift:
        raise HTTPException(status_code=404, detail="Смена не найдена в вашей компании.")
    
    if shift.end_time is not None:
        raise HTTPException(status_code=400, detail="Смена уже закрыта.")

    # 3. Закрываем смену
    shift.end_time = datetime.now()
    shift.closing_cash = payload.closing_cash
    db.commit()
    db.refresh(shift)
    
    return {"status": "ok", "message": f"Смена #{shift_id} принудительно закрыта Владельцем."}

# === НАЧАЛО НОВЫХ МОДЕЛЕЙ (РАСХОДЫ) ===

# --- Модели для Расходов ---
# Вспомогательная модель для сотрудника в ShiftInfoOut
class EmployeeSmallOut(BaseModel):
    id: int
    full_name: str
    class Config:
        orm_mode = True

# Вспомогательная модель для информации о смене в ExpenseOut
class ShiftInfoOut(BaseModel):
    employee: EmployeeSmallOut
    end_time: Optional[datetime] = None
    class Config:
        orm_mode = True

# --- Модели для Расходов ---
class ExpenseBase(BaseModel):
    amount: float = Field(..., gt=0) # Сумма должна быть больше 0
    notes: Optional[str] = None
    expense_type_id: int

class ExpenseCreate(ExpenseBase):
    shift_id: Optional[int] = None
    pass # Все поля уже в ExpenseBase

class ExpenseUpdate(BaseModel):
    amount: Optional[float] = Field(None, gt=0) # Сумма опциональна, но если есть, > 0
    notes: Optional[str] = None
    expense_type_id: Optional[int] = None

# Модель для вывода расхода с доп. информацией
class ExpenseOut(ExpenseBase):
    id: int
    created_at: datetime
    shift_id: Optional[int] = None
    company_id: int
    # Включаем данные о типе расхода
    expense_type: ExpenseTypeOut
    # Включаем данные о сотруднике смены через ShiftInfoOut
    shift: Optional[ShiftInfoOut] = None

    class Config:
        orm_mode = True

# === КОНЕЦ НОВЫХ МОДЕЛЕЙ (РАСХОДЫ) ===

# --- МОДЕЛИ ДЛЯ ОТЧЕТОВ ---
# (Перенесены с конца файла для исправления NameError)

class SummaryReportItem(BaseModel):
    total_income: float
    total_cash_income: float
    total_card_income: float
    total_expenses: float
    net_profit: float
    expenses_by_type: dict[str, float] = {}
    shifts: List[ShiftOut] = [] # ShiftOut должен быть определен ВЫШЕ

    class Config:
        orm_mode = True 
        # (Если используете Pydantic V2, замените на: from_attributes = True)

class SummaryReportResponse(BaseModel):
    status: str
    summary: SummaryReportItem

    class Config:
        orm_mode = True
        # (Если используете Pydantic V2, замените на: from_attributes = True)
# --- КОНЕЦ МОДЕЛЕЙ ДЛЯ ОТЧЕТОВ ---

# main.py (ДОБАВИТЬ ЭТУ МОДЕЛЬ)
class SettingUpdate(BaseModel):
    key: str
    value: Optional[str] # Разрешаем устанавливать null (или пустую строку)

# --- 4. ЭНДПОИНТЫ АУТЕНТИФИКАЦИИ ---

ALL_PERMISSIONS = {
    'manage_companies': 'Управлять Компаниями (создавать, блокировать, продлевать)',
    'impersonate_company': 'Входить "от имени" компании (для техподдержки)',
    'manage_employees': 'Управлять сотрудниками (добавлять, увольнять)',  
    'manage_roles': 'Управлять должностями и доступами',
    'manage_locations': 'Управлять филиалами (точками)',
    'manage_expense_types': 'Управлять типами расходов',  
    'view_full_reports': 'Видеть полные финансовые отчеты',
    'view_shift_report': 'Видеть отчет по текущей смене',
    'add_expense': 'Добавлять расходы',  
    'open_close_shift': 'Открывать и закрывать смены',  
    'issue_orders': 'Выдавать заказы',
    'manage_clients': 'Управлять клиентами',  
    'manage_orders': 'Управлять заказами',
    'wipe_database': 'Полностью очищать базу данных (опасная зона)'
}

@app.post("/api/superadmin/setup", tags=["Super-Admin"])
def setup_super_admin(payload: SuperAdminSetupPayload, db: Session = Depends(get_db)):
    if db.query(Employee).count() > 0:
        raise HTTPException(status_code=403, detail="Система уже настроена.")

    # 1. Создаем все ГЛОБАЛЬНЫЕ разрешения
    existing_permissions = {p.codename for p in db.query(Permission).all()}
    for codename, description in ALL_PERMISSIONS.items():
        if codename not in existing_permissions:
            db.add(Permission(codename=codename, description=description))
    db.commit()

    # 2. Создаем Роль "Super-Admin" (без company_id)
    all_permissions_in_db = db.query(Permission).all()
    super_admin_role = Role(name="Super-Admin", company_id=None, permissions=all_permissions_in_db)
    db.add(super_admin_role)
    db.commit()

    # 3. Создаем Сотрудника "Super-Admin" (вас)
    super_admin_employee = Employee(
        full_name=payload.full_name,
        password=payload.password,  
        is_active=True,
        role_id=super_admin_role.id,
        company_id=None,
        location_id=None
    )
    db.add(super_admin_employee)
    db.commit()

    return {"status": "ok", "message": f"Пользователь Super-Admin '{payload.full_name}' успешно создан."}


@app.post("/api/login", tags=["Аутентификация"], response_model=LoginResponse)
def login(payload: LoginPayload, db: Session = Depends(get_db)):
    """
    Аутентификация сотрудника (ВЕРСИЯ С НАДЕЖНОЙ ЗАГРУЗКОЙ ПРАВ)
    """
    employee = None
    company = None
    
    # Приводим код компании к верхнему регистру для надежности
    company_code_upper = payload.company_code.upper() if payload.company_code else None

    if company_code_upper == 'SUPER':
        # --- Вход для СУПЕР-АДМИНА ---
        employee = db.query(Employee).options(
            joinedload(Employee.role) # Загружаем только роль
        ).filter(
            Employee.password == payload.password,
            Employee.company_id == None 
        ).first()
        if not employee:
            raise HTTPException(status_code=401, detail="Неверный пароль Super-Admin.")
    
    else:
        # --- Вход для СОТРУДНИКА КОМПАНИИ ---
        if not company_code_upper:
            raise HTTPException(status_code=400, detail="Не указан Код Компании.")
        
        company = db.query(Company).filter(Company.company_code == company_code_upper).first()
        if not company:
            raise HTTPException(status_code=404, detail=f"Компания с кодом '{company_code_upper}' не найдена.")
        
        if not company.is_active or (company.subscription_paid_until and company.subscription_paid_until < date.today()):
            raise HTTPException(status_code=403, detail="Доступ для компании заблокирован или подписка истекла.")

        # 1. Находим сотрудника (загружаем только его роль)
        employee = db.query(Employee).options(
            joinedload(Employee.role) 
        ).filter(
            Employee.password == payload.password,
            Employee.company_id == company.id, 
            Employee.is_active == True
        ).first()

        if not employee:
            raise HTTPException(status_code=401, detail="Неверный пароль или сотрудник неактивен.")

    # --- КРИТИЧЕСКАЯ ЧАСТЬ: НАДЕЖНАЯ ЗАГРУЗКА ПРАВ ---
    # Мы нашли сотрудника и его ID роли (employee.role_id).
    # Теперь мы отдельно и принудительно загружаем ОБЪЕКТ РОЛИ и ЕГО ПРАВА
    
    permissions = []
    if employee.role_id:
        # 2. Находим объект роли, принудительно загружая связанные с ним permissions
        role_with_permissions = db.query(Role).options(
            joinedload(Role.permissions)
        ).filter(Role.id == employee.role_id).first()
        
        if role_with_permissions and role_with_permissions.permissions:
            permissions = [p.codename for p in role_with_permissions.permissions]
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    print(f"[Login] Сотрудник {employee.full_name} (Роль: {employee.role.name}) вошел. Найдено прав: {len(permissions)}")
    
    if 'open_close_shift' in permissions:
        print(f"[Login DEBUG] Право 'open_close_shift' НАЙДЕНО для {employee.full_name}")
    else:
        print(f"[Login DEBUG] Право 'open_close_shift' НЕ НАЙДЕНО для {employee.full_name}")


    return {
        "status": "ok", 
        "employee": {
            "id": employee.id, 
            "full_name": employee.full_name, 
            "role": employee.role.name, 
            "permissions": permissions, # <-- Отправляем свежие права на фронтенд
            "is_super_admin": (employee.company_id is None),
            "location_id": employee.location_id 
        },
        "company": {"id": company.id, "name": company.name, "company_code": company.company_code} if company else None
    }

# --- 5. ЭНДПОИНТЫ: SUPER-ADMIN ---

@app.get("/api/superadmin/companies", tags=["Super-Admin"])
def get_all_companies(
    employee: Employee = Depends(get_super_admin),
    db: Session = Depends(get_db)
):
    """Получает список всех компаний (для Super-Admin)."""
    try:
        companies_orm = db.query(Company).order_by(Company.name).all()

        # --- ДОБАВЛЕНО: Явное преобразование в список словарей ---
        # Это более надежный способ вернуть данные, если response_model вызывает проблемы
        companies_list = []
        for company in companies_orm:
            companies_list.append({
                "id": company.id,
                "name": company.name,
                "company_code": company.company_code,
                "is_active": company.is_active,
                "subscription_paid_until": company.subscription_paid_until.isoformat() if company.subscription_paid_until else None, # Форматируем дату
                "contact_person": company.contact_person,
                "contact_phone": company.contact_phone,
                "created_at": company.created_at.isoformat(), # Форматируем дату-время
                "telegram_bot_token": company.telegram_bot_token,
                "telegram_bot_username": company.telegram_bot_username
            })
        # --- КОНЕЦ ДОБАВЛЕНИЯ ---

        # Возвращаем преобразованный список
        return companies_list

    except Exception as e:
        # Логируем ошибку на сервере для диагностики
        import traceback
        print(f"!!! Ошибка в get_all_companies:\n{traceback.format_exc()}")
        # Возвращаем ошибку 500
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера при получении списка компаний: {e}")
# --- КОНЕЦ ИЗМЕНЕНИЙ ---

# --- Обновить эндпоинт создания компании ---
@app.post("/api/superadmin/companies", tags=["Super-Admin"], response_model=CompanyOut)
def create_company(
    payload: CompanyCreate, # Модель теперь содержит поля для бота
    employee: Employee = Depends(get_super_admin),
    db: Session = Depends(get_db)
):
    """Создает новую компанию, включая данные для ее Telegram-бота."""
    # Проверка корректности кода компании
    if not re.match(r'^[A-Z0-9_]{3,15}$', payload.company_code):
         raise HTTPException(status_code=400, detail="Код компании некорректен. 3-15 знаков, только A-Z, 0-9, _")
    # Проверка уникальности названия
    if db.query(Company).filter(Company.name == payload.name).first():
        raise HTTPException(status_code=400, detail="Компания с таким названием уже существует.")
    # Проверка уникальности кода
    if db.query(Company).filter(Company.company_code == payload.company_code).first():
        raise HTTPException(status_code=400, detail="Компания с таким кодом уже существует.")
    # ДОБАВЛЕНО: Проверка уникальности токена бота, если он указан
    if payload.telegram_bot_token and db.query(Company).filter(Company.telegram_bot_token == payload.telegram_bot_token).first():
        raise HTTPException(status_code=400, detail="Компания с таким Telegram Bot Token уже существует.")

    db.begin_nested() # Начинаем транзакцию для атомарности
    try:
        # Создаем объект Company, включая данные бота
        new_company = Company(
            name=payload.name, company_code=payload.company_code,
            contact_person=payload.contact_person, contact_phone=payload.contact_phone,
            subscription_paid_until=payload.subscription_paid_until, is_active=True,
            # ДОБАВЛЕНО: Сохраняем данные бота
            telegram_bot_token=payload.telegram_bot_token, # Может быть None
            telegram_bot_username=payload.telegram_bot_username # Может быть None
        )
        db.add(new_company)
        db.flush() # Получаем ID новой компании

        # Создаем главный филиал
        main_location = Location(name="Главный филиал", address="Не указан", company_id=new_company.id)
        db.add(main_location)
        db.flush() # Получаем ID филиала

        # Находим все права, кроме управления компаниями, и создаем роль "Владелец"
        owner_permissions = db.query(Permission).filter(
            Permission.codename.notin_(['manage_companies', 'impersonate_company'])
        ).all()
        owner_role = Role(name="Владелец", company_id=new_company.id, permissions=owner_permissions)
        db.add(owner_role)
        db.flush() # Получаем ID роли

        # Создаем сотрудника-владельца
        owner_employee = Employee(
            full_name=payload.owner_full_name, password=payload.owner_password,
            is_active=True, role_id=owner_role.id,
            company_id=new_company.id, location_id=main_location.id # Привязываем к компании и филиалу
        )
        db.add(owner_employee)

        # Создаем типы расходов по умолчанию для компании
        default_expense_types = ["Хоз. нужды", "Зарплата", "Аванс", "Аренда", "Прочие расходы"]
        for exp_type_name in default_expense_types:
            db.add(ExpenseType(name=exp_type_name, company_id=new_company.id))

        # Добавляем базовые настройки (адрес, телефон) для компании
        db.add(Setting(key='bishkek_office_address', value='Адрес не указан', company_id=new_company.id))
        db.add(Setting(key='contact_phone', value='Телефон не указан', company_id=new_company.id))

        db.commit() # Применяем все изменения транзакции
        db.refresh(new_company) # Обновляем объект из БД для возврата
        print(f"INFO: Компания '{new_company.name}' (ID: {new_company.id}) успешно создана.")
        return new_company

    except Exception as e:
        db.rollback() # Откатываем все изменения в случае ошибки
        import traceback
        print(f"!!! Ошибка при создании компании:\n{traceback.format_exc()}") # Логируем полную ошибку
        # Возвращаем общую ошибку сервера
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера при создании компании: {e}")


# --- Обновить эндпоинт редактирования компании ---
@app.patch("/api/superadmin/companies/{company_id}", tags=["Super-Admin"], response_model=CompanyOut)
def update_company(
    company_id: int,
    payload: CompanyUpdate, # Модель теперь содержит поля для бота
    employee: Employee = Depends(get_super_admin),
    db: Session = Depends(get_db)
):
    """Обновляет данные компании, включая данные ее Telegram-бота."""
    # Находим компанию по ID
    company = db.query(Company).filter(Company.id == company_id).first()
    # Если компания не найдена, возвращаем ошибку 404
    if not company:
        raise HTTPException(status_code=404, detail="Компания не найдена.")

    # Получаем данные для обновления из payload, исключая неустановленные поля
    update_data = payload.dict(exclude_unset=True)
    print(f"INFO: Обновление компании ID {company_id}. Получены данные: {update_data}")

    # ДОБАВЛЕНО: Проверка уникальности токена бота при ИЗМЕНЕНИИ
    # Проверяем, есть ли поле токена в данных для обновления
    # И отличается ли новый токен от текущего токена компании
    if 'telegram_bot_token' in update_data and update_data['telegram_bot_token'] != company.telegram_bot_token:
        new_token = update_data['telegram_bot_token']
        # Если новый токен не пустой (т.е. не удаление токена)
        if new_token:
            # Ищем ДРУГУЮ компанию с таким же токеном
            existing_company_with_token = db.query(Company).filter(
                Company.telegram_bot_token == new_token,
                Company.id != company_id # Исключаем текущую компанию из поиска
            ).first()
            # Если найдена другая компания с таким токеном, возвращаем ошибку
            if existing_company_with_token:
                raise HTTPException(status_code=400, detail="Другая компания уже использует этот Telegram Bot Token.")
        else:
             # Если новый токен пустой или null, это означает удаление токена
             update_data['telegram_bot_token'] = None # Явно устанавливаем None для записи в БД

    # Применяем обновления к объекту компании
    for key, value in update_data.items():
        # Отдельно обрабатываем удаление токена (если пришло '' или null)
        if key == 'telegram_bot_token' and not value:
             setattr(company, key, None)
             print(f"INFO: Поле {key} установлено в None.")
        else:
             setattr(company, key, value)
             print(f"INFO: Поле {key} обновлено на {value}.")

    try:
        db.commit() # Сохраняем изменения в БД
        db.refresh(company) # Обновляем объект из БД
        print(f"INFO: Компания ID {company_id} успешно обновлена.")
        return company # Возвращаем обновленные данные
    except Exception as e:
        db.rollback() # Откатываем изменения при ошибке
        import traceback
        print(f"!!! Ошибка при обновлении компании ID {company_id}:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при обновлении компании: {e}")

@app.delete("/api/superadmin/companies/{company_id}", tags=["Super-Admin"], status_code=status.HTTP_204_NO_CONTENT)
def delete_company(
    company_id: int,
    employee: Employee = Depends(get_super_admin),
    db: Session = Depends(get_db)
):
    """(ИСПРАВЛЕНО) Удаляет компанию и ВСЕ связанные с ней данные."""
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Компания не найдена.")

    print(f"[Delete Company] Super-Admin {employee.id} удаляет компанию {company.name} (ID: {company_id})")

    try:
        # --- НАЧИНАЕМ КАСКАДНОЕ УДАЛЕНИЕ (от "детей" к "родителям") ---
        # Важно соблюдать порядок из-за FOREIGN KEY
        
        # 1. Удаляем Заказы (Orders) - они ссылаются на Клиентов, Смены, Филиалы
        print(f"  > Удаление {db.query(Order).filter(Order.company_id == company_id).count()} заказов...")
        db.query(Order).filter(Order.company_id == company_id).delete(synchronize_session=False)

        # 2. Удаляем Клиентов (Clients)
        print(f"  > Удаление {db.query(Client).filter(Client.company_id == company_id).count()} клиентов...")
        db.query(Client).filter(Client.company_id == company_id).delete(synchronize_session=False)

        # 3. Удаляем Расходы (Expenses) - они ссылаются на Смены и Типы Расходов
        print(f"  > Удаление {db.query(Expense).filter(Expense.company_id == company_id).count()} расходов...")
        db.query(Expense).filter(Expense.company_id == company_id).delete(synchronize_session=False)
        
        # 4. Удаляем Смены (Shifts) - они ссылаются на Сотрудников и Филиалы
        print(f"  > Удаление {db.query(Shift).filter(Shift.company_id == company_id).count()} смен...")
        db.query(Shift).filter(Shift.company_id == company_id).delete(synchronize_session=False)

        # 5. Удаляем Сотрудников (Employees) - они ссылаются на Роли и Филиалы
        print(f"  > Удаление {db.query(Employee).filter(Employee.company_id == company_id).count()} сотрудников...")
        db.query(Employee).filter(Employee.company_id == company_id).delete(synchronize_session=False)

        # 6. Удаляем Роли (Roles)
        print(f"  > Удаление {db.query(Role).filter(Role.company_id == company_id).count()} ролей...")
        
        # --- (НОВЫЙ БЛОК) СНАЧАЛА удаляем связи в role_permissions ---
        # Находим все ID ролей, которые мы собираемся удалить
        roles_to_delete_ids_query = db.query(Role.id).filter(Role.company_id == company_id)
        
        # Создаем SQL-команду на удаление из M2M таблицы
        delete_perms_stmt = role_permissions_table.delete().where(
            role_permissions_table.c.role_id.in_(roles_to_delete_ids_query.scalar_subquery())
        )
        db.execute(delete_perms_stmt)
        print(f"  > Связи M2M (role_permissions) для ролей удалены.")
        # --- (КОНЕЦ НОВОГО БЛОКА) ---

        # Теперь, когда "дети" (связи) удалены, удаляем "родителей" (роли)
        db.query(Role).filter(Role.company_id == company_id).delete(synchronize_session=False)

        # 7. Удаляем Типы Расходов (ExpenseTypes)
        print(f"  > Удаление {db.query(ExpenseType).filter(ExpenseType.company_id == company_id).count()} типов расходов...")
        db.query(ExpenseType).filter(ExpenseType.company_id == company_id).delete(synchronize_session=False)

        # 8. Удаляем Филиалы (Locations)
        print(f"  > Удаление {db.query(Location).filter(Location.company_id == company_id).count()} филиалов...")
        db.query(Location).filter(Location.company_id == company_id).delete(synchronize_session=False)

        # 9. Удаляем Настройки (Settings)
        print(f"  > Удаление {db.query(Setting).filter(Setting.company_id == company_id).count()} настроек...")
        db.query(Setting).filter(Setting.company_id == company_id).delete(synchronize_session=False)

        # 10. Удаляем Рассылки и Реакции (Broadcasts / BroadcastReactions)
        print(f"  > Удаление {db.query(Broadcast).filter(Broadcast.company_id == company_id).count()} рассылок...")
        db.query(Broadcast).filter(Broadcast.company_id == company_id).delete(synchronize_session=False)
        # Реакции удалятся каскадно, так как мы настроили `cascade="all, delete-orphan"` в models.py

        # 11. НАКОНЕЦ, удаляем саму Компанию
        print(f"  > Удаление компании {company.name}...")
        db.delete(company)
        
        # Фиксируем все удаления
        db.commit()
        print(f"[Delete Company] Компания ID {company_id} успешно удалена.")
        
    except Exception as e:
        db.rollback()
        logger.error(f"!!! [Delete Company] Ошибка БД при удалении компании {company_id}: {e}", exc_info=True)
        # Если что-то пошло не так, возвращаем 500
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при удалении: {e}")

    # Возвращаем 204 No Content, так как компания удалена
    return None

# --- 6. ЭНДПОИНТЫ: ВЛАДЕЛЕЦ КОМПАНИИ (Управление персоналом) ---
# main.py (ИСПРАВЛЕННАЯ ВЕРСИЯ get_locations)

@app.get("/api/locations", tags=["Персонал (Владелец)", "Telegram Bot"], response_model=List[LocationOut])
def get_locations(
    # --- ИЗМЕНЕНИЕ: Делаем заголовок НЕОБЯЗАТЕЛЬНЫМ ---
    x_employee_id: Optional[str] = Header(None),
    # --- ИЗМЕНЕНИЕ: Делаем query параметр НЕОБЯЗАТЕЛЬНЫМ ---
    company_id_query: Optional[int] = Query(None, alias="company_id"),
    db: Session = Depends(get_db)
):
    """
    Получает ВСЕ филиалы компании.
    Определяет компанию либо по X-Employee-ID (для админки),
    либо по query параметру company_id (для бота/ЛК).
    """
    target_company_id: Optional[int] = None
    employee: Optional[Employee] = None

    print(f"[Get Locations] Запрос филиалов. Header: {x_employee_id}, Query Param: {company_id_query}")

    # --- Сценарий 1: Запрос из Админки (есть заголовок) ---
    if x_employee_id:
        try:
            employee_id_int = int(x_employee_id)
            # Пытаемся найти активного сотрудника
            employee = db.query(Employee).options(joinedload(Employee.role)).filter(
                Employee.id == employee_id_int,
                Employee.is_active == True
            ).first()
            if employee and employee.company_id:
                # Если сотрудник найден и принадлежит компании, берем ID его компании
                target_company_id = employee.company_id
                print(f"[Get Locations] Запрос от сотрудника ID={employee.id}, Компания ID={target_company_id}")
            elif employee:
                 # Сотрудник найден, но без company_id (SuperAdmin?) - ему филиалы не нужны
                 print(f"[Get Locations] Запрос от сотрудника без компании (ID={employee.id}). Возвращаем пустой список.")
                 return [] # Возвращаем пустой список для SuperAdmin
            else:
                 # Сотрудник по ID не найден или не активен
                 print(f"[Get Locations] Сотрудник по X-Employee-ID={x_employee_id} не найден/не активен.")
                 # Не возвращаем ошибку 401 сразу, даем шанс второму сценарию (для бота)

        except ValueError:
            print(f"[Get Locations] Неверный формат X-Employee-ID: {x_employee_id}")
            # Не возвращаем ошибку, даем шанс второму сценарию

    # --- Сценарий 2: Запрос от Бота/ЛК (company_id в query) ---
    # Проверяем, если компания еще не определена И передан company_id_query
    if target_company_id is None and company_id_query is not None:
        # Проверяем, существует ли компания с таким ID
        company_check = db.query(Company.id).filter(Company.id == company_id_query).first()
        if company_check:
            target_company_id = company_id_query
            print(f"[Get Locations] Запрос для компании ID={target_company_id} (из query параметра).")
        else:
            # Если компания из query параметра не найдена
            print(f"!!! [Get Locations] Компания ID={company_id_query} из query параметра не найдена.")
            # Возвращаем ошибку 404
            raise HTTPException(status_code=404, detail=f"Компания с ID {company_id_query} не найдена.")

    # --- Если компанию так и не удалось определить ---
    if target_company_id is None:
        print("!!! [Get Locations] Не удалось определить компанию ни по заголовку, ни по параметру.")
        # Возвращаем ошибку 401/400, т.к. непонятно, чьи филиалы запрашиваются
        raise HTTPException(status_code=400, detail="Не удалось определить компанию для запроса филиалов (отсутствует X-Employee-ID или company_id).")

    # --- Запрос филиалов для найденной компании ---
    locations = db.query(Location).filter(Location.company_id == target_company_id).order_by(Location.name).all()
    print(f"INFO: Запрос филиалов для компании ID {target_company_id}. Найдено: {len(locations)}")
    return locations

@app.post("/api/locations", tags=["Персонал (Владелец)"], response_model=LocationOut)
def create_location(
    payload: LocationCreate,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Создает новый филиал для ТЕКУЩЕЙ компании."""
    new_location = Location(
        **payload.dict(),
        company_id=employee.company_id # Привязываем к компании
    )
    db.add(new_location)
    db.commit()
    db.refresh(new_location)
    return new_location

# --- ДОБАВИТЬ ЭТУ НОВУЮ ФУНКЦИЮ ---
@app.patch("/api/locations/{location_id}", tags=["Персонал (Владелец)"], response_model=LocationOut)
def update_location(
    location_id: int,
    payload: LocationUpdate, # Используем модель LocationUpdate для частичного обновления
    employee: Employee = Depends(get_company_owner), # Только Владелец может менять
    db: Session = Depends(get_db)
):
    """Обновляет данные филиала ТЕКУЩЕЙ компании."""
    # 1. Находим филиал по ID и проверяем, принадлежит ли он компании текущего Владельца
    location_to_update = db.query(Location).filter(
        Location.id == location_id,
        Location.company_id == employee.company_id
    ).first()

    # 2. Если филиал не найден, возвращаем ошибку 404
    if not location_to_update:
        raise HTTPException(status_code=404, detail="Филиал не найден в вашей компании.")

    # 3. Получаем данные для обновления из payload, исключая неустановленные (None)
    update_data = payload.dict(exclude_unset=True)
    print(f"INFO: Обновление филиала ID {location_id}. Получены данные: {update_data}")

    # 4. Проверяем, есть ли что обновлять
    if not update_data:
         raise HTTPException(status_code=400, detail="Не переданы данные для обновления.")

    # 5. Применяем обновления к объекту филиала
    for key, value in update_data.items():
        setattr(location_to_update, key, value)
        print(f"INFO: Поле {key} обновлено на {value}.")

    # 6. Сохраняем изменения в БД
    try:
        db.commit() # Сохраняем
        db.refresh(location_to_update) # Обновляем объект из БД
        print(f"INFO: Филиал ID {location_id} успешно обновлен.")
        return location_to_update # Возвращаем обновленные данные
    except Exception as e:
        db.rollback() # Откатываем при ошибке
        import traceback
        print(f"!!! Ошибка при обновлении филиала ID {location_id}:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при обновлении филиала: {e}")
# --- КОНЕЦ НОВОЙ ФУНКЦИИ ---

@app.get("/api/employees", tags=["Персонал (Владелец)"], response_model=List[EmployeeOut])
def get_employees(
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Получает ВСЕХ сотрудников ТЕКУЩЕЙ компании."""
    employees = db.query(Employee).options(
        joinedload(Employee.role)
    ).filter(
        Employee.company_id == employee.company_id
    ).order_by(Employee.full_name).all()
    return employees

@app.post("/api/employees", tags=["Персонал (Владелец)"], response_model=EmployeeOut)
def create_employee(
    payload: EmployeeCreate,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Создает нового сотрудника для ТЕКУЩЕЙ компании."""
    # Проверяем, что location_id и role_id принадлежат этой же компании
    loc = db.query(Location).filter(Location.id == payload.location_id, Location.company_id == employee.company_id).first()
    rol = db.query(Role).filter(Role.id == payload.role_id, Role.company_id == employee.company_id).first()
    if not loc or not rol:
        raise HTTPException(status_code=404, detail="Филиал или Должность не найдены в вашей компании.")

    new_employee = Employee(
        **payload.dict(),
        company_id=employee.company_id # Привязываем к компании
    )
    db.add(new_employee)
    db.commit()
    db.refresh(new_employee)
    # Загружаем роль, чтобы она была в ответе
    new_employee = db.query(Employee).options(joinedload(Employee.role)).get(new_employee.id)
    return new_employee

# main.py (Вставляем после @app.post("/employees", ...)
# ... (Код функции create_employee)

# --- ДОБАВЛЕН /api/employees/{id} ---
@app.patch("/api/employees/{employee_id}", tags=["Персонал (Владелец)"], response_model=EmployeeOut)
def update_employee(
    employee_id: int,
    payload: EmployeeUpdate,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Обновляет данные сотрудника ТЕКУЩЕЙ компании."""
    target_employee = db.query(Employee).options(joinedload(Employee.role)).filter(
        Employee.id == employee_id,
        Employee.company_id == employee.company_id
    ).first()

    if not target_employee:
        raise HTTPException(status_code=404, detail="Сотрудник не найден в вашей компании.")

    update_data = payload.dict(exclude_unset=True)

    # 1. Проверка на увольнение единственного активного Владельца
    if 'is_active' in update_data and update_data['is_active'] is False:
        if target_employee.role.name == 'Владелец':
            active_owners_count = db.query(Employee).filter(
                Employee.company_id == employee.company_id,
                Employee.is_active == True,
                Employee.role.has(name='Владелец')
            ).count()
            if active_owners_count <= 1:
                raise HTTPException(status_code=400, detail="Нельзя уволить единственного активного Владельца компании.")

    # 2. Проверка, что location_id и role_id принадлежат этой же компании
    if 'location_id' in update_data:
        loc = db.query(Location).filter(Location.id == update_data['location_id'], Location.company_id == employee.company_id).first()
        if not loc:
            raise HTTPException(status_code=404, detail="Указанный филиал не найден в вашей компании.")
        
    if 'role_id' in update_data:
        rol = db.query(Role).filter(Role.id == update_data['role_id'], Role.company_id == employee.company_id).first()
        if not rol:
            raise HTTPException(status_code=404, detail="Указанная должность не найдена в вашей компании.")
        # Запрет смены роли Владельца, если он не меняет ее на другую роль Владельца (проверка для безопасности)
        if target_employee.role.name == 'Владелец' and rol.name != 'Владелец':
             raise HTTPException(status_code=400, detail="Нельзя изменить роль 'Владелец' на другую роль.")


    # 3. Применяем обновления
    for key, value in update_data.items():
        setattr(target_employee, key, value)
    
    db.commit()
    db.refresh(target_employee)

    # Загружаем роль, чтобы она была в ответе
    target_employee = db.query(Employee).options(joinedload(Employee.role)).get(target_employee.id)
    return target_employee


@app.get("/api/roles", tags=["Персонал (Владелец)"], response_model=List[RoleOut])
def get_roles(
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Получает ВСЕ роли ТЕКУЩЕЙ компании."""
    roles = db.query(Role).filter(
        Role.company_id == employee.company_id
    ).order_by(Role.name).all()
    return roles

@app.get("/api/permissions", tags=["Персонал (Владелец)"], response_model=List[PermissionOut])
def get_permissions(
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Получает ВСЕ доступные (глобальные) права в системе."""
    # Владелец не может выдавать права Super-Admin
    permissions = db.query(Permission).filter(
        Permission.codename.notin_(['manage_companies', 'impersonate_company'])
    ).all()
    return permissions

    # === НАЧАЛО НОВОГО КОДА ===

@app.post("/api/roles", tags=["Персонал (Владелец)"], response_model=RoleOut)
def create_role(
    payload: RoleBase, # Используем базовую модель для имени
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Создает новую роль для ТЕКУЩЕЙ компании."""
    if db.query(Role).filter(Role.name == payload.name, Role.company_id == employee.company_id).first():
        raise HTTPException(status_code=400, detail="Должность с таким названием уже существует в вашей компании.")
    
    # Права по умолчанию - пока пустые
    new_role = Role(
        name=payload.name,
        company_id=employee.company_id # Привязываем к компании
    )
    db.add(new_role)
    db.commit()
    db.refresh(new_role)
    return new_role

@app.delete("/api/roles/{role_id}", tags=["Персонал (Владелец)"], status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    role_id: int,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Удаляет роль в ТЕКУЩЕЙ компании."""
    role_to_delete = db.query(Role).filter(
        Role.id == role_id,
        Role.company_id == employee.company_id # Убеждаемся, что роль принадлежит этой компании
    ).first()

    if not role_to_delete:
        raise HTTPException(status_code=404, detail="Должность не найдена в вашей компании.")
    if role_to_delete.name == "Владелец":
        raise HTTPException(status_code=400, detail="Нельзя удалить стандартную роль 'Владелец'.")
    
    # Проверка, есть ли сотрудники с этой ролью
    assigned_employees = db.query(Employee).filter(Employee.role_id == role_id).count()
    if assigned_employees > 0:
        raise HTTPException(status_code=400, detail=f"Нельзя удалить должность '{role_to_delete.name}', так как к ней привязано {assigned_employees} сотрудников.")

    db.delete(role_to_delete)
    db.commit()
    return None # Возвращаем 204 No Content

@app.get("/api/roles/{role_id}/permissions", tags=["Персонал (Владелец)"], response_model=List[int])
def get_role_permissions(
    role_id: int,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Получает ID прав для указанной роли ТЕКУЩЕЙ компании."""
    role = db.query(Role).options(joinedload(Role.permissions)).filter(
        Role.id == role_id,
        Role.company_id == employee.company_id
    ).first()
    if not role:
        raise HTTPException(status_code=404, detail="Должность не найдена в вашей компании.")
    
    return [p.id for p in role.permissions]


@app.put("/api/roles/{role_id}/permissions", tags=["Персонал (Владелец)"])
def update_role_permissions(
    role_id: int,
    payload: RolePermissionsUpdate, # Ожидаем список ID прав
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Обновляет права для указанной роли ТЕКУЩЕЙ компании."""
    role = db.query(Role).filter(
        Role.id == role_id,
        Role.company_id == employee.company_id
    ).first()
    if not role:
        raise HTTPException(status_code=404, detail="Должность не найдена в вашей компании.")
    if role.name == "Владелец":
         raise HTTPException(status_code=400, detail="Нельзя изменять права для роли 'Владелец'.")

    # Находим объекты Permission по ID из payload, НО только те, которые доступны Владельцу
    allowed_permission_codenames = set(ALL_PERMISSIONS.keys()) - {'manage_companies', 'impersonate_company'}
    
    new_permissions = db.query(Permission).filter(
        Permission.id.in_(payload.permission_ids),
        Permission.codename.in_(allowed_permission_codenames) # Доп. проверка безопасности
    ).all()

    # Проверяем, все ли запрошенные ID были найдены и разрешены
    if len(new_permissions) != len(set(payload.permission_ids)):
         print(f"Запрошено: {payload.permission_ids}, Найдено разрешенных: {[p.id for p in new_permissions]}")
         # Не прерываем, просто назначаем то, что разрешено

    role.permissions = new_permissions # SQLAlchemy сам разберется с many-to-many связью
    db.commit()
    
    return {"status": "ok", "message": f"Доступы для должности '{role.name}' обновлены."}

# --- Эндпоинты для Настроек (Владелец) ---

@app.get("/api/settings", tags=["Настройки (Владелец)"], response_model=List[SettingOut])
def get_company_settings(
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Получает все настройки для ТЕКУЩЕЙ компании."""
    settings = db.query(Setting).filter(
        Setting.company_id == employee.company_id
    ).all()
    return settings

@app.put("/api/settings", tags=["Настройки (Владелец)"], response_model=List[SettingOut])
def update_company_settings(
    payload: SettingsUpdatePayload,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Обновляет (создает или изменяет) настройки для ТЕКУЩЕЙ компании."""
    
    # Загружаем существующие настройки компании
    existing_settings_db = db.query(Setting).filter(
        Setting.company_id == employee.company_id
    ).all()
    
    # Преобразуем в словарь для быстрого доступа
    settings_map = {s.key: s for s in existing_settings_db}
    
    # Проходим по настройкам, которые прислал пользователь
    for key, value in payload.settings.items():
        if key in settings_map:
            # Если настройка существует, обновляем
            settings_map[key].value = value
        else:
            # Если настройка новая, создаем ее
            new_setting = Setting(
                key=key,
                value=value,
                company_id=employee.company_id
            )
            db.add(new_setting)
    
    try:
        db.commit()
        # Перезагружаем все настройки, чтобы вернуть актуальный список
        updated_settings = db.query(Setting).filter(
            Setting.company_id == employee.company_id
        ).all()
        return updated_settings
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения настроек: {e}")

# === КОНЕЦ НОВОГО КОДА ===

# === НАЧАЛО НОВОГО КОДА (КЛИЕНТЫ) ===

# --- Pydantic Модели для Клиентов ---
class ClientBase(BaseModel):
    full_name: str
    phone: str
    client_code_prefix: Optional[str] = None
    client_code_num: Optional[int] = None # Теперь можно редактировать
    status: Optional[str] = "Розница"

class ClientCreate(ClientBase):
    pass # Все поля уже в ClientBase

class ClientUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    client_code_prefix: Optional[str] = None
    client_code_num: Optional[int] = None
    status: Optional[str] = None
    telegram_chat_id: Optional[str] = None # Добавим возможность отвязки (редко нужно)

class ClientOut(ClientBase):
    id: int
    company_id: int
    telegram_chat_id: Optional[str]
    created_at: datetime
    class Config:
        # ЗАМЕНИТЬ orm_mode на from_attributes
        from_attributes = True

class BulkClientItem(BaseModel):
    full_name: str
    phone: str
    client_code: Optional[str] = None # Оставляем как строку для гибкости импорта

class GenerateLKLinkResponse(BaseModel):
    link: str

# --- НОВЫЕ Модели для идентификации пользователя Ботом ---
class BotIdentifyPayload(BaseModel):
    company_id: int
    telegram_chat_id: str
    phone_number: Optional[str] = None

# --- Эндпоинты для Клиентов ---

@app.get("/api/clients", tags=["Клиенты (Владелец)"], response_model=List[ClientOut])
def get_clients(
    employee: Employee = Depends(get_client_manager), # <-- ИСПРАВЛЕНО
    db: Session = Depends(get_db)
):
    """Получает ВСЕХ клиентов ТЕКУЩЕЙ компании."""
    clients = db.query(Client).filter(
        Client.company_id == employee.company_id
    ).order_by(Client.full_name).all()
    return clients

# main.py (Для админ-панели, которая использует get_company_owner)

@app.post("/api/clients", tags=["Клиенты (Владелец)"], response_model=ClientOut)
def create_client(
    payload: ClientCreate,
    background_tasks: BackgroundTasks, 
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    # Проверка на дубликат телефона ВНУТРИ компании
    if db.query(Client).filter(Client.phone == payload.phone, Client.company_id == employee.company_id).first():
        raise HTTPException(status_code=400, detail="Клиент с таким телефоном уже существует в вашей компании.")

    # Если префикс не указан, используем код компании или "KB"
    if payload.client_code_prefix is None:
        payload.client_code_prefix = employee.company.company_code or "KB"

    # === НОВАЯ ЛОГИКА АВТО-ГЕНЕРАЦИИ КОДА (ЗАДАЧА 1) ===
    if payload.client_code_num is None:
        print(f"[Generate Code] (Admin) Авто-генерация кода для {payload.phone}")
        # 1. Получаем настройку начального кода
        start_code_setting = db.query(Setting).filter(Setting.key == 'client_code_start', Setting.company_id == employee.company_id).first()
        start_from = 1001 # Значение по умолчанию
        if start_code_setting and start_code_setting.value:
            try:
                start_from = int(start_code_setting.value)
            except ValueError:
                pass
        print(f"[Generate Code] (Admin) Настройка 'client_code_start' = {start_from}")

        # 2. Находим максимальный существующий код, который МЕНЬШЕ, чем 'start_from'
        # (Игнорируем "аномальные" большие коды)
        max_normal_code = db.query(
            func.max(Client.client_code_num)
        ).filter(
            Client.company_id == employee.company_id,
            Client.client_code_num < start_from # <-- Ключевой фильтр
        ).scalar()

        print(f"[Generate Code] (Admin) Максимальный 'нормальный' код (< {start_from}) = {max_normal_code}")

        # 3. Определяем, с какого номера начать проверку
        next_code_to_check = start_from # По умолчанию начинаем с настройки
        if max_normal_code is not None:
            # Если нашли 'нормальный' код, берем следующий за ним, но не меньше, чем настройка
            next_code_to_check = max(max_normal_code + 1, start_from)

        print(f"[Generate Code] (Admin) Начинаем поиск свободного кода с: {next_code_to_check}")

        # 4. Ищем первый свободный код, начиная с next_code_to_check
        current_code = next_code_to_check
        while db.query(Client).filter(
            Client.company_id == employee.company_id,
            Client.client_code_num == current_code
        ).first():
            current_code += 1 # Если код занят, проверяем следующий

        payload.client_code_num = current_code
        print(f"[Generate Code] (Admin) Найден свободный код: {payload.client_code_num}")

    # === КОНЕЦ НОВОЙ ЛОГИКИ ===

    # Проверка на дубликат КОМБИНАЦИИ (префикс + код) (если код был введен вручную)
    if payload.client_code_num and db.query(Client).filter(
        Client.client_code_prefix == payload.client_code_prefix,
        Client.client_code_num == payload.client_code_num, 
        Client.company_id == employee.company_id
    ).first():
        raise HTTPException(status_code=400, detail=f"Клиентский код {payload.client_code_prefix}{payload.client_code_num} уже занят в вашей компании.")

    new_client = Client(
        **payload.dict(),
        company_id=employee.company_id # Привязываем к компании
    )
    db.add(new_client)
    db.commit()
    db.refresh(new_client)

    # --- Уведомление Владельцу (остается) ---
    background_tasks.add_task(
        notify_owner_of_new_client,
        company_id=employee.company_id,
        new_client_id=new_client.id,
        registered_by="Администратор"
    )

    return new_client

@app.patch("/api/clients/{client_id}", tags=["Клиенты (Владелец)"], response_model=ClientOut)
async def update_client(
    client_id: int,
    payload: ClientUpdate,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """(ИСПОЛНЕНИЕ ЗАДАЧИ 2) Обновляет данные клиента и отправляет "живое" уведомление."""

    client = db.query(Client).filter(
        Client.id == client_id,
        Client.company_id == employee.company_id
    ).first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден в вашей компании.")

    update_data = payload.dict(exclude_unset=True)

    # --- НОВАЯ ЛОГИКА: Собираем "живое" сообщение ---
    changes_list = [] # Список изменений

    # 1. Проверяем ФИО
    if 'full_name' in update_data and update_data['full_name'] != client.full_name:
        changes_list.append(f"– <b>ФИО:</b> <code>{client.full_name}</code> ➡️ <b>{update_data['full_name']}</b>")

    # 2. Проверяем Телефон
    if 'phone' in update_data and update_data['phone'] != client.phone:
        # Проверка на уникальность телефона (остается)
        if db.query(Client).filter(Client.phone == update_data['phone'], Client.company_id == employee.company_id).first():
            raise HTTPException(status_code=400, detail="Другой клиент с таким телефоном уже существует в вашей компании.")
        changes_list.append(f"– <b>Телефон:</b> <code>{client.phone}</code> ➡️ <b>{update_data['phone']}</b>")

    # 3. Проверяем Код (Префикс или Номер)
    new_prefix = update_data.get('client_code_prefix', client.client_code_prefix)
    new_num = update_data.get('client_code_num', client.client_code_num)
    old_code = f"{client.client_code_prefix}{client.client_code_num or ''}"
    new_code = f"{new_prefix}{new_num or ''}"

    if new_code != old_code:
        # Проверка на уникальность кода (остается)
        if new_num and db.query(Client).filter(
            Client.client_code_prefix == new_prefix,
            Client.client_code_num == new_num,
            Client.company_id == employee.company_id,
            Client.id != client_id
        ).first():
             raise HTTPException(status_code=400, detail=f"Клиентский код {new_prefix}{new_num} уже занят в вашей компании.")
        changes_list.append(f"– <b>Код клиента:</b> <code>{old_code}</code> ➡️ <b>{new_code}</b>")

    # 4. Проверяем Статус
    if 'status' in update_data and update_data['status'] != client.status:
        changes_list.append(f"– <b>Статус:</b> <code>{client.status}</code> ➡️ <b>{update_data['status']}</b>")

    # 5. (Опционально) Отвязка Telegram
    if 'telegram_chat_id' in update_data and update_data['telegram_chat_id'] is None and client.telegram_chat_id is not None:
         changes_list.append(f"– <b>Telegram:</b> <code>Привязан</code> ➡️ <b>Отвязан</b>")

    # --- Конец сбора изменений ---

    # Применяем обновления
    for key, value in update_data.items():
        setattr(client, key, value)

    db.commit()
    db.refresh(client)

    # (Задача 2) Отправляем уведомление, ЕСЛИ БЫЛИ ИЗМЕНЕНИЯ
    if changes_list and client.telegram_chat_id:
        company_token = db.query(Company.telegram_bot_token).filter(Company.id == employee.company_id).scalar()
        if company_token:

            # Собираем сообщение
            changes_str = "\n".join(changes_list)
            full_notify_text = (
                f"<b>Внимание!</b> 🔒\n"
                f"Администратор обновил данные вашего профиля:\n\n"
                f"{changes_str}"
            )

            # Используем await, так как функция теперь async
            await send_telegram_message(
                token=company_token,
                chat_id=client.telegram_chat_id,
                text=full_notify_text
            )
            print(f"[Update Client] Уведомление об изменениях отправлено клиенту ID {client.id}")
        else:
            print(f"[Update Client] WARNING: Не найден токен для отправки уведомления клиенту ID {client.id}")

    return client

@app.delete("/api/clients/{client_id}", tags=["Клиенты (Владелец)"], status_code=status.HTTP_204_NO_CONTENT)
def delete_client(
    client_id: int,
    employee: Employee = Depends(get_client_manager), # <-- ИСПРАВЛЕНО
    db: Session = Depends(get_db)
):
    """Удаляет клиента ТЕКУЩЕЙ компании."""
    client = db.query(Client).filter(
        Client.id == client_id,
        Client.company_id == employee.company_id
    ).first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден в вашей компании.")

    # ПРОВЕРКА: Есть ли у клиента активные (не выданные) заказы?
    active_orders_count = db.query(Order).filter(
        Order.client_id == client_id,
        Order.status != "Выдан" # Используем статус "Выдан" как признак завершенного
    ).count()

    if active_orders_count > 0:
        raise HTTPException(status_code=400, detail=f"Невозможно удалить клиента, так как у него есть {active_orders_count} незавершенных заказов.")

    # TODO: Подумать, нужно ли удалять ИСТОРИЮ заказов клиента или только самого клиента.
    # Пока удаляем только клиента, заказы останутся без привязки (или можно настроить каскадное удаление).
    # Рекомендуется НЕ удалять заказы, чтобы сохранялась история.

    db.delete(client)
    db.commit()
    return None

@app.get("/api/clients/search", tags=["Клиенты (Владелец)"], response_model=List[ClientOut])
def search_clients(
    q: str = Query(..., min_length=1), # Запрос должен быть не пустым
    employee: Employee = Depends(get_client_manager), # <-- ИСПРАВЛЕНО
    db: Session = Depends(get_db)
):
    """Ищет клиентов по имени, телефону или коду ВНУТРИ ТЕКУЩЕЙ компании."""
    search_term = f"%{q.lower()}%" # Поиск без учета регистра
    
    # Ищем совпадения в имени, телефоне ИЛИ коде (префикс + номер)
    clients = db.query(Client).filter(
        Client.company_id == employee.company_id, # Только в текущей компании
        or_(
            func.lower(Client.full_name).ilike(search_term),
            Client.phone.ilike(search_term),
            (func.lower(Client.client_code_prefix) + func.cast(Client.client_code_num, String)).ilike(search_term)
        )
    ).limit(15).all() # Ограничиваем количество результатов
    
    return clients

@app.post("/api/clients/{client_id}/generate_lk_link", tags=["Клиенты (Владелец)", "Telegram Bot"], response_model=GenerateLKLinkResponse)
def generate_lk_link_for_client(
    client_id: int,
    company_id: int = Query(...), # <-- ИЗМЕНЕНИЕ: Требуем ID компании от бота
    db: Session = Depends(get_db)
):
    """
    (ИСПРАВЛЕНО) Генерирует ссылку на ЛК.
    Теперь доступно для бота (требует company_id).
    """
    client = db.query(Client).filter(
        Client.id == client_id,
        Client.company_id == company_id # <-- ИЗМЕНЕНИЕ: Проверяем по company_id
    ).first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден в вашей компании.")

    # Формируем токен
    secret_token = f"CLIENT-{client.id}-COMPANY-{company_id}-SECRET"  

    # Получаем базовый URL (остается как было)
    client_portal_base_url = os.getenv("CLIENT_PORTAL_URL", "http://ВАШ_ДОМЕН_ИЛИ_IP/lk.html")  

    link = f"{client_portal_base_url}?token={secret_token}"
    return {"link": link}


# === НАЧАЛО НОВОГО КОДА (ИМПОРТ КЛИЕНТОВ) ===

# Модель для ответа после импорта
class BulkImportResponse(BaseModel):
    status: str
    message: str
    created_clients: int
    errors: List[str]
    warnings: List[str]  

# Используем модель BulkClientItem, которая уже есть

@app.post("/api/clients/bulk_import", tags=["Клиенты (Владелец)"], response_model=BulkImportResponse)
def bulk_import_clients(
    clients_data: List[BulkClientItem], # FastAPI автоматически распарсит JSON-массив
    employee: Employee = Depends(get_client_manager), # <-- ИСПРАВЛЕНО
    db: Session = Depends(get_db)
):
    """Массовый импорт клиентов из списка (например, из Excel) для ТЕКУЩЕЙ компании."""
    print(f"[Import Clients] Начало импорта для компании ID: {employee.company_id}. Получено строк: {len(clients_data)}") # Лог начала
    created_count = 0
    errors = []
    warnings = []

    # Получаем ВСЕХ существующих клиентов ЭТОЙ компании для быстрой проверки дубликатов
    try:
        existing_clients_in_company = db.query(Client).filter(Client.company_id == employee.company_id).all()
        existing_phones = {c.phone for c in existing_clients_in_company} # Используем set для быстрой проверки
        existing_codes = {(c.client_code_prefix, c.client_code_num) for c in existing_clients_in_company if c.client_code_num is not None} # Используем set
        print(f"[Import Clients] Загружено {len(existing_phones)} существующих телефонов и {len(existing_codes)} кодов.") # Лог загрузки
    except Exception as e_load:
        print(f"!!! [Import Clients] КРИТИЧЕСКАЯ ОШИБКА при загрузке существующих клиентов: {e_load}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при подготовке к импорту: {e_load}")

    # --- Начало основного цикла импорта ---
    for index, item in enumerate(clients_data): # Добавляем index для логирования
        print(f"\n[Import Clients] Обработка строки {index + 1}: {item.dict()}") # Лог обработки строки
        try: # Оборачиваем обработку КАЖДОЙ строки в try...except
            # Валидация базовых полей
            if not item.full_name or not item.phone:
                errors.append(f"Строка {index + 1}: Пропущена - Отсутствует ФИО или Телефон.")
                print(f"[Import Clients] Строка {index + 1}: Пропущена (нет ФИО/Телефона).") # Лог пропуска
                continue # Переходим к следующей строке

            # Убираем лишние символы из телефона
            cleaned_phone = re.sub(r'\D', '', str(item.phone)) # Удаляем всё, кроме цифр
            if not cleaned_phone:
                 errors.append(f"Строка {index + 1} ('{item.full_name}'): Пропущена - Некорректный номер телефона '{item.phone}'.")
                 print(f"[Import Clients] Строка {index + 1}: Пропущена (некорректный телефон).") # Лог пропуска
                 continue

            # Проверка на дубликат телефона ВНУТРИ компании (в загруженных и уже добавленных в этом импорте)
            if cleaned_phone in existing_phones:
                warnings.append(f"Строка {index + 1} ('{item.full_name}'): Клиент с телефоном {cleaned_phone} уже существует (пропущен).")
                print(f"[Import Clients] Строка {index + 1}: Пропущена (дубликат телефона {cleaned_phone}).") # Лог пропуска
                continue

            # Создаем объект клиента (пока без кода)
            new_client = Client(
                full_name=item.full_name,
                phone=cleaned_phone,
                company_id=employee.company_id # Привязываем к компании
            )
            print(f"[Import Clients] Строка {index + 1}: Объект Client создан для '{item.full_name}' / {cleaned_phone}.") # Лог создания объекта

            # --- Обработка кода клиента ---
            parsed_prefix = None
            parsed_num = None
            if item.client_code:
                code_str = str(item.client_code).strip()
                print(f"[Import Clients] Строка {index + 1}: Обработка кода '{code_str}'.") # Лог обработки кода
                if code_str:
                    match_prefix = re.match(r'^([a-zA-Z]+)', code_str)
                    match_num = re.search(r'(\d+)$', code_str)
                    temp_prefix = "KB" # Префикс по умолчанию
                    if match_prefix: temp_prefix = match_prefix.group(1).upper()

                    if match_num:
                        try:
                            num_val = int(match_num.group(1))
                            # Проверка на дубликат кода ВНУТРИ компании (в загруженных и уже добавленных)
                            if (temp_prefix, num_val) in existing_codes:
                                warnings.append(f"Строка {index + 1} ('{item.full_name}'): Код '{temp_prefix}{num_val}' уже занят и будет проигнорирован.")
                                print(f"[Import Clients] Строка {index + 1}: Код {temp_prefix}{num_val} проигнорирован (дубликат).") # Лог
                            else:
                                parsed_num = num_val # Код уникален
                                parsed_prefix = temp_prefix # Используем найденный или KB
                                print(f"[Import Clients] Строка {index + 1}: Код {parsed_prefix}{parsed_num} распознан.") # Лог
                        except ValueError:
                             warnings.append(f"Строка {index + 1} ('{item.full_name}'): Не удалось распознать номер в коде '{code_str}'. Код проигнорирован.")
                             print(f"[Import Clients] Строка {index + 1}: Ошибка ValueError при парсинге номера кода '{code_str}'.") # Лог
                    else:
                         warnings.append(f"Строка {index + 1} ('{item.full_name}'): Не найден номер в коде '{code_str}'. Код проигнорирован.")
                         print(f"[Import Clients] Строка {index + 1}: Номер не найден в коде '{code_str}'.") # Лог
                else:
                     warnings.append(f"Строка {index + 1} ('{item.full_name}'): Пустая строка в client_code. Код не присвоен.")
                     print(f"[Import Clients] Строка {index + 1}: Пустой client_code.") # Лог
            else:
                 print(f"[Import Clients] Строка {index + 1}: Код клиента не указан.") # Лог отсутствия кода

            # Присваиваем распознанный код (или None)
            new_client.client_code_prefix = parsed_prefix
            new_client.client_code_num = parsed_num
            # --- Конец обработки кода ---

            # Добавляем нового клиента в сессию SQLAlchemy
            db.add(new_client)
            print(f"[Import Clients] Строка {index + 1}: db.add(new_client) выполнен.") # Лог добавления в сессию

            # Обновляем множества для проверки следующих строк В ЭТОМ ЖЕ ИМПОРТЕ
            existing_phones.add(cleaned_phone)
            if parsed_num is not None:
                 existing_codes.add((parsed_prefix, parsed_num))

            created_count += 1 # Увеличиваем счетчик успешно обработанных (но еще не сохраненных)

            # Периодически сбрасываем сессию (flush), чтобы проверить возможные ошибки на уровне БД раньше
            if created_count % 100 == 0:
                print(f"[Import Clients] Выполнение промежуточного db.flush() после {created_count} клиентов...") # Лог flush
                try:
                    db.flush() # Отправляет команды INSERT/UPDATE в БД, но не завершает транзакцию
                    print(f"[Import Clients] Промежуточный db.flush() успешен.") # Лог успеха flush
                except Exception as e_flush:
                     db.rollback() # Откатываем ВСЮ транзакцию при ошибке flush
                     print(f"!!! [Import Clients] КРИТИЧЕСКАЯ ОШИБКА при промежуточном db.flush() на строке ~{index + 1}: {e_flush}") # Лог ошибки flush
                     print(traceback.format_exc()) # Печатаем traceback ошибки
                     errors.append(f"Критическая ошибка базы данных при записи блока ~{created_count}: {e_flush}")
                     # Прерываем импорт при серьезной ошибке записи
                     break # Выходим из цикла for

        except Exception as e_row: # Ловим ЛЮБУЮ другую ошибку при обработке строки
             print(f"!!! [Import Clients] НЕОЖИДАННАЯ ОШИБКА при обработке строки {index + 1}: {e_row}") # Лог неожиданной ошибки
             print(traceback.format_exc()) # Печатаем traceback ошибки
             errors.append(f"Строка {index + 1}: Неожиданная ошибка обработки - {e_row}")
             # Решаем, прерывать ли импорт (можно continue, если ошибка не критична)
             # continue # Пока пропустим строку и попробуем продолжить
             # Или прервать, если ошибка серьезная:
             # break

    # --- Конец основного цикла импорта ---

    # Финальный коммит (если цикл не был прерван ошибкой flush)
    if not errors or "Критическая ошибка базы данных" not in " ".join(errors): # Проверяем, не было ли критической ошибки
        print(f"\n[Import Clients] Попытка выполнить финальный db.commit() для {created_count} клиентов...") # Лог финального commit
        try:
            db.commit() # Завершает транзакцию, делая изменения постоянными
            print(f"[Import Clients] Финальный db.commit() успешен.") # Лог успеха commit
        except Exception as e_commit:
            db.rollback() # Откатываем транзакцию при ошибке commit
            print(f"!!! [Import Clients] КРИТИЧЕСКАЯ ОШИБКА при финальном db.commit(): {e_commit}") # Лог ошибки commit
            print(traceback.format_exc()) # Печатаем traceback ошибки
            # Если была ошибка на финальном коммите, возможно, часть данных не записалась
            errors.append(f"Критическая ошибка при финальной записи: {e_commit}. Возможно, часть клиентов не была импортирована.")
            # Обнуляем счетчик, так как не уверены, что всё записалось
            created_count = 0
            print(f"[Import Clients] Счетчик created_count сброшен из-за ошибки commit.") # Лог сброса счетчика

    # Формируем и возвращаем результат
    result = {
        "status": "ok",
        "message": "Импорт завершен.",
        "created_clients": created_count,
        "errors": errors,
        "warnings": warnings
    }
    print(f"[Import Clients] Завершение импорта. Результат: {result}") # Лог результата
    return result

# === КОНЕЦ НОВОГО КОДА (ИМПОРТ КЛИЕНТОВ) ===
# === КОНЕЦ НОВОГО КОДА (КЛИЕНТЫ) ===

# === НАЧАЛО НОВОГО КОДА (ЗАКАЗЫ) ===

# --- Pydantic Модели для Заказов ---

# --- НОВАЯ МОДЕЛЬ (Задача 3) ---
class OrderHistoryOut(BaseModel):
    id: int
    status: str
    created_at: datetime
    employee_id: Optional[int] = None

    class Config:
        from_attributes = True
# --- КОНЕЦ НОВОЙ МОДЕЛИ ---

# Базовая модель заказа (для вывода и создания/обновления)
class OrderBase(BaseModel):
    track_code: str
    status: Optional[str] = "В обработке"
    purchase_type: str = "Доставка" # По умолчанию Доставка
    comment: Optional[str] = None
    party_date: Optional[date] = None # Теперь опционально при создании

    # Поля для выкупа (опциональные)
    buyout_item_cost_cny: Optional[float] = None
    buyout_commission_percent: Optional[float] = 10.0 # По умолчанию 10%
    buyout_rate_for_client: Optional[float] = None
    buyout_actual_rate: Optional[float] = None # Заполняется позже

    # Поля для расчета (только для чтения в ответе)
    calculated_weight_kg: Optional[float] = None
    calculated_price_per_kg_usd: Optional[float] = None
    calculated_exchange_rate_usd: Optional[float] = None
    calculated_final_cost_som: Optional[float] = None

# Модель для создания заказа (требуем ID клиента, компании, филиала)
class OrderCreate(OrderBase):
    client_id: int
    company_id: int # ДОБАВЛЕНО: ID компании, к которой относится заказ
    location_id: int # ДОБАВЛЕНО: ID филиала, к которому относится заказ
    # purchase_type уже есть в OrderBase
    # track_code уже есть в OrderBase
    # comment уже есть в OrderBase
    # party_date уже есть в OrderBase
    # Поля выкупа уже есть в OrderBase
# --- КОНЕЦ ИЗМЕНЕНИЙ ---

# Модель для обновления заказа
class OrderUpdate(BaseModel):
    # Позволяем менять почти все основные поля
    track_code: Optional[str] = None
    status: Optional[str] = None
    purchase_type: Optional[str] = None
    comment: Optional[str] = None
    party_date: Optional[date] = None
    client_id: Optional[int] = None # Возможность сменить клиента
    location_id: Optional[int] = None

    # Поля выкупа
    buyout_item_cost_cny: Optional[float] = None
    buyout_commission_percent: Optional[float] = None
    buyout_rate_for_client: Optional[float] = None
    buyout_actual_rate: Optional[float] = None

    # Поля расчета (эти поля обновляются через /calculate)
    # calculated_weight_kg: Optional[float] = None
    # ...

# Модель для вывода заказа (включая данные клиента)
class OrderOut(OrderBase):
    id: int
    company_id: int
    client: Optional[ClientOut] = None # Вложенная модель (ТЕПЕРЬ ОПЦИОНАЛЬНО)
    created_at: datetime
    issued_at: Optional[datetime] # Поля для выданных
    weight_kg: Optional[float]
    final_cost_som: Optional[float]

    history_entries: List[OrderHistoryOut] = []

    class Config:
        orm_mode = True

# Модели для массовых действий
class BulkOrderItem(BaseModel): # Используется для ИМПОРТА
    track_code: str
    client_code: Optional[str] = None # Идентификация клиента по коду
    phone: Optional[str] = None      # ИЛИ по телефону
    comment: Optional[str] = None
    purchase_type: Optional[str] = "Доставка" # Тип заказа для импорта
    # Добавляем поля выкупа для импорта
    buyout_item_cost_cny: Optional[float] = None
    buyout_rate_for_client: Optional[float] = None
    buyout_commission_percent: Optional[float] = 10.0
    # party_date можно будет указать отдельно

class BulkOrderImportPayload(BaseModel):
    orders_data: List[BulkOrderItem]
    party_date: Optional[date] = None # Общая дата партии для импорта
    location_id: Optional[int] = None

# Используется для смены статуса, даты, удаления
class BulkActionPayload(BaseModel):
    action: str # 'update_status', 'update_party_date', 'delete', 'buyout', 'revert', 'assign_client'
    order_ids: List[int]
    # Опциональные поля в зависимости от action
    new_status: Optional[str] = None
    new_party_date: Optional[date] = None
    buyout_actual_rate: Optional[float] = None
    client_id: Optional[int] = None # <-- ИСПРАВЛЕНО (было new_client_id)
    password: Optional[str] = None

# Используется для расчета стоимости
class CalculateOrderItem(BaseModel):
    order_id: int
    weight_kg: float = Field(..., gt=0)
class CalculatePayload(BaseModel):
    orders: List[CalculateOrderItem] # Список заказов с их весом
    price_per_kg_usd: float = Field(..., gt=0)
    exchange_rate_usd: float = Field(..., gt=0)
    new_status: Optional[str] = None # Новый статус (опционально)

# --- Модели для Массового Добавления из Бота (Версия 2) ---
class BotBulkAddItem(BaseModel):
    track_code: str
    comment: Optional[str] = None

class BotBulkAddPayload(BaseModel):
    client_id: int
    location_id: int
    company_id: int
    items: List[BotBulkAddItem]

class BotBulkAddResponse(BaseModel):
    created: int
    assigned: int # <-- ДОБАВЛЕНО
    skipped: int
    errors: List[str]

# Используется для выдачи
class IssueOrderItem(BaseModel):
    order_id: int
    weight_kg: float = Field(..., gt=0)
class IssuePayload(BaseModel):
    orders: List[IssueOrderItem]
    price_per_kg_usd: float = Field(..., gt=0)
    exchange_rate_usd: float = Field(..., gt=0)
    paid_cash: float = Field(..., ge=0) # Может быть 0
    paid_card: float = Field(..., ge=0) # Может быть 0
    card_payment_type: Optional[str] = None # Тип карты, если оплата картой

# --- Эндпоинты для Заказов ---

# main.py (ЗАМЕНИТЬ ПОЛНОСТЬЮ функцию get_orders)
from sqlalchemy.orm import contains_eager # <-- ДОБАВЬ ЭТОТ ИМПОРТ в начало файла (рядом с joinedload)

@app.get("/api/orders", tags=["Заказы (Владелец)", "Telegram Bot"], response_model=List[OrderOut])
def get_orders(
    company_id: int = Query(...), 
    client_id: Optional[int] = Query(None), 

    # --- НОВОЕ: Добавлен поиск и лимит ---
    q: Optional[str] = Query(None, description="Поиск по трек-коду, ФИО клиента или телефону"),
    limit: Optional[int] = Query(None, description="Лимит результатов (по умолчанию нет)"),
    # --- КОНЕЦ НОВОГО ---

    party_dates: Optional[List[date]] = Query(None),
    statuses: Optional[List[str]] = Query(default=None),
    location_id: Optional[int] = Query(None),
    x_employee_id: Optional[str] = Header(None), 
    db: Session = Depends(get_db)
):
    """
    Получает список заказов компании с фильтрацией.
    (Версия с поддержкой поиска 'q' для Владельца)
    """
    print(f"[Get Orders] Запрос для Company ID={company_id}. Employee Header: {x_employee_id}. Client ID: {client_id}. Поиск: '{q}'")

    # --- Проверка компании ---
    company = db.query(Company.id).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"Компания с ID {company_id} не найдена.")

    # --- ИЗМЕНЕНИЕ (Задача 3): Добавляем joinedload(Order.history_entries) ---
    query = db.query(Order).options(
        joinedload(Order.client),
        joinedload(Order.history_entries) # <-- УБЕДИСЬ, ЧТО ЭТА СТРОКА ДОБАВЛЕНА
    ).filter(
        Order.company_id == company_id
    )
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    # --- НОВОЕ: Логика поиска по 'q' ---
    if q:
        search_term = f"%{q.lower()}%"
        # Присоединяем Client, чтобы искать по имени/телефону
        # Используем isouter=True на случай, если клиент был удален, а заказы остались
        query = query.join(Client, Client.id == Order.client_id, isouter=True).filter( 
            or_(
                func.lower(Order.track_code).ilike(search_term),
                func.lower(Client.full_name).ilike(search_term),
                Client.phone.ilike(search_term)
            )
        )
        print(f"[Get Orders] Применен текстовый поиск: '{q}'")
    # --- КОНЕЦ НОВОГО ---

    employee: Optional[Employee] = None
    target_location_id: Optional[int] = None

    # --- Попытка определить сотрудника из админки ---
    if x_employee_id:
        try:
            employee_id_int = int(x_employee_id)
            # Загружаем сотрудника, его роль и права
            employee = db.query(Employee).options(
                joinedload(Employee.role).joinedload(Role.permissions)
            ).filter(
                Employee.id == employee_id_int,
                Employee.company_id == company_id, 
                Employee.is_active == True
            ).first()
        except ValueError:
            employee = None

        if employee:
            # --- ИСПРАВЛЕНИЕ: Добавлена проверка, что 'employee.role' существует ---
            if not employee.role:
                # Если у сотрудника нет роли (ошибка данных), считаем его обычным сотрудником без прав
                logger.error(f"[Get Orders][ОШИБКА] Сотрудник ID={employee.id} найден, но у него нет роли (role is None).")
                target_location_id = employee.location_id
                if target_location_id is None:
                    logger.error(f"[Get Orders][ОШИБКА] Сотрудник ID={employee.id} не привязан к филиалу!")
                    return [] # Возвращаем пустой список, а не ошибку 500
                print(f"[Get Orders] Сотрудник (без роли) видит свой филиал ID={target_location_id}")
            else:
                # Роль существует, продолжаем
                print(f"[Get Orders] Запрос идентифицирован как от сотрудника ID={employee.id} (Роль: {employee.role.name})")
                # Владелец может фильтровать по филиалу, сотрудник видит только свой
                if employee.role.name == 'Владелец':
                    if location_id is not None:
                        # Владелец выбрал филиал
                        loc_check = db.query(Location.id).filter(Location.id == location_id, Location.company_id == company_id).first()
                        if not loc_check: raise HTTPException(status_code=404, detail="Указанный филиал не найден.")
                        target_location_id = location_id
                        print(f"[Get Orders] Владелец фильтрует по филиалу ID={target_location_id}")
                    else:
                        # Владелец видит все филиалы
                        print(f"[Get Orders] Владелец видит все филиалы.")
                        target_location_id = None
                else: 
                    # Обычный сотрудник видит только свой филиал
                    target_location_id = employee.location_id
                    if target_location_id is None:
                        print(f"[Get Orders][ОШИБКА] Сотрудник ID={employee.id} не привязан к филиалу!")
                        return []
                    print(f"[Get Orders] Сотрудник видит свой филиал ID={target_location_id}")
        else:
            print("[Get Orders] Заголовок X-Employee-ID передан, но сотрудник не найден/не активен.")
            # (Если заголовок был, но невалидный, можно вернуть 401,
            # но для бота/ЛК мы продолжаем без сотрудника)

    # --- Фильтрация по client_id (применяется всегда, если передан) ---
    # (Это используется ботом для "Мои Заказы")
    if client_id is not None:
        client_check = db.query(Client.id).filter(Client.id == client_id, Client.company_id == company_id).first()
        if not client_check:
            raise HTTPException(status_code=404, detail=f"Клиент ID {client_id} не найден в компании ID {company_id}.")
        query = query.filter(Order.client_id == client_id)
        print(f"[Get Orders] Применен фильтр по Client ID={client_id}")

    # --- Применяем остальные фильтры ---

    # Фильтр по филиалу (если он был определен для сотрудника/Владельца)
    if target_location_id is not None:
        query = query.filter(Order.location_id == target_location_id)

    # Фильтр по датам партий
    if party_dates:
        query = query.filter(Order.party_date.in_(party_dates))

    # Фильтр по статусам
    statuses_to_filter = statuses
    # Если статусы не переданы И это запрос из админки (employee определен),
    # то по умолчанию скрываем "Выданные"
    if not statuses_to_filter and employee:
        statuses_to_filter = [s for s in ORDER_STATUSES if s != "Выдан"]

    # Применяем фильтр по статусам, если он есть
    if statuses_to_filter:
        query = query.filter(Order.status.in_(statuses_to_filter))

    # --- НОВОЕ: Добавляем limit к запросу ---
    query = query.order_by(Order.party_date.desc().nullslast(), Order.id.desc())
    if limit:
        query = query.limit(limit)

    orders = query.all()

    print(f"[Get Orders] Найдено заказов: {len(orders)}")
    return orders

@app.post("/api/orders", tags=["Заказы (Владелец)", "Telegram Bot"], response_model=OrderOut)
def create_order(
    payload: OrderCreate,
    background_tasks: BackgroundTasks, # <-- ДОБАВЛЕНО
    db: Session = Depends(get_db)
):
    """
    Создает новый заказ (вызывается ботом или админкой).
    УВЕДОМЛЯЕТ ВЛАДЕЛЬЦА.
    """
    print(f"[Create Order API] Получен payload: {payload.dict()}")

    # --- Шаг 1: Проверка ---
    company = db.query(Company).filter(Company.id == payload.company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"Компания с ID {payload.company_id} не найдена.")

    client = db.query(Client).filter(
        Client.id == payload.client_id,
        Client.company_id == payload.company_id
    ).first()
    if not client:
        raise HTTPException(status_code=404, detail=f"Клиент ID {payload.client_id} не найден.")

    location = db.query(Location).filter(
        Location.id == payload.location_id,
        Location.company_id == payload.company_id
    ).first()
    if not location:
        raise HTTPException(status_code=404, detail=f"Филиал ID {payload.location_id} не найден.")

    # --- Шаг 2: Проверка трек-кода ---
    track_code_to_save = payload.track_code
    if not track_code_to_save and payload.purchase_type == "Выкуп":
         timestamp = int(datetime.now().timestamp() * 1000)
         track_code_to_save = f"PENDING-{timestamp}"
    if not track_code_to_save:
         raise HTTPException(status_code=400, detail="Трек-код обязателен для 'Доставки'.")

    if not track_code_to_save.startswith("PENDING-"):
         existing_order = db.query(Order).filter(
              Order.track_code == track_code_to_save,
              Order.company_id == payload.company_id
         ).first()
         if existing_order:
              raise HTTPException(status_code=400, detail=f"Заказ с '{track_code_to_save}' уже существует.")

    # --- Шаг 3: Определение статуса и даты ---
    order_status = "Ожидает выкупа" if payload.purchase_type == "Выкуп" else "В обработке"
    order_party_date = payload.party_date if payload.party_date else date.today()

    # --- Шаг 4: Создание ---
    new_order = Order(
        client_id=payload.client_id,
        track_code=track_code_to_save,
        status=order_status,
        purchase_type=payload.purchase_type,
        comment=payload.comment,
        party_date=order_party_date,
        buyout_item_cost_cny=payload.buyout_item_cost_cny,
        buyout_commission_percent=payload.buyout_commission_percent,
        buyout_rate_for_client=payload.buyout_rate_for_client,
        buyout_actual_rate=payload.buyout_actual_rate,
        company_id=payload.company_id,
        location_id=payload.location_id
    )

    try:
        db.add(new_order)
        db.commit() 
        db.refresh(new_order) 

        history_entry = OrderHistory(
            order_id=new_order.id,
            status=new_order.status,
            employee_id=None # Создано ботом
        )
        db.add(history_entry)
        db.commit()

        db.refresh(new_order, attribute_names=['client'])

        # --- НОВОЕ: Уведомление Владельцу ---
        comment_str = f"\n<i>Комментарий: {new_order.comment}</i>" if new_order.comment else ""
        message = (
            f"🔔 <b>Добавлен новый заказ (1 шт)</b>\n\n"
            f"Клиент: <b>{client.full_name}</b>\n"
            f"Трек-код: <code>{new_order.track_code}</code>{comment_str}"
        )
        background_tasks.add_task(
            notify_owners,
            company_id=new_order.company_id,
            message_text=message
        )
        # --- КОНЕЦ УВЕДОМЛЕНИЯ ---

        print(f"[Create Order API] Заказ ID={new_order.id} успешно создан.")
        return new_order
    except Exception as e:
        db.rollback() 
        import traceback
        print(f"!!! Ошибка БД при создании заказа:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных: {e}")

# main.py (Полностью заменяет функцию update_order)

@app.patch("/api/orders/{order_id}", tags=["Заказы (Владелец)"], response_model=OrderOut)
async def update_order( # Убедись, что 'async' здесь есть
    order_id: int,
    payload: OrderUpdate,
    employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db)
):
    """(С ИСПРАВЛЕНИЯМИ) Обновляет данные заказа и отправляет уведомление."""
    
    # 1. Находим заказ
    # НЕ ИСПОЛЬЗУЕМ joinedload(Order.client) здесь, т.к. он может загрузить СТАРОГО клиента
    order = db.query(Order).filter( 
        Order.id == order_id,
        Order.company_id == employee.company_id
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден в вашей компании.")

    update_data = payload.dict(exclude_unset=True) 
    original_status = order.status 
    original_client_id = order.client_id # Запоминаем ID старого клиента

    # 2. Обработка изменения location_id (ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА)
    if 'location_id' in update_data:
        if employee.role.name != 'Владелец':
            del update_data['location_id']  
        elif update_data['location_id'] != order.location_id: 
            new_location = db.query(Location).filter(
                Location.id == update_data['location_id'],
                Location.company_id == employee.company_id
            ).first()
            if not new_location:
                raise HTTPException(status_code=404, detail="Новый филиал не найден в вашей компании.")

    # 3. Обработка смены клиента (если client_id передан)
    new_client_id = None
    if 'client_id' in update_data:
        new_client_id = update_data['client_id'] # Запоминаем ID нового клиента
        if new_client_id != original_client_id:
            new_client_check = db.query(Client.id).filter( # Проверяем только ID
                Client.id == new_client_id,
                Client.company_id == employee.company_id
            ).first()
            if not new_client_check:
                 raise HTTPException(status_code=404, detail="Новый клиент не найден в вашей компании.")
            print(f"[Update Order] Заказ ID={order_id} переносится на клиента ID={new_client_id}")

    # 4. Проверка дубликата трек-кода при изменении
    if 'track_code' in update_data and update_data['track_code'] != order.track_code:
        if not update_data['track_code'].startswith("PENDING-"):
             existing_order = db.query(Order).filter(
                 Order.track_code == update_data['track_code'],
                 Order.company_id == employee.company_id,
                 Order.id != order_id 
             ).first()
             if existing_order:
                  raise HTTPException(status_code=400, detail=f"Другой заказ с трек-кодом '{update_data['track_code']}' уже существует.")

    # 5. Проверка корректности статуса
    if 'status' in update_data and update_data['status'] not in ORDER_STATUSES:
         raise HTTPException(status_code=400, detail=f"Недопустимый статус заказа: {update_data['status']}")
         
    # 6. Применяем все собранные обновления к объекту заказа
    # 6. Применяем все собранные обновления к объекту заказа
    try:
        for key, value in update_data.items():
            setattr(order, key, value)
        
        # (Задача 3) Добавляем запись в историю, ЕСЛИ СТАТУС ИЗМЕНИЛСЯ
        if 'status' in update_data and update_data['status'] != original_status:
            history_entry = OrderHistory(
                order_id=order.id,
                status=update_data['status'],
                employee_id=employee.id
            )
            db.add(history_entry)
            
        db.commit() # Коммитим и заказ, и историю
        # db.refresh(order)
        
        # --- ИСПРАВЛЕНИЕ: ПЕРЕЗАГРУЖАЕМ ОБЪЕКТ ПОЛНОСТЬЮ ---
        # После commit(), делаем НОВЫЙ запрос, чтобы получить
        # обновленный заказ С ГАРАНТИРОВАННО ПОДГРУЖЕННЫМ НОВЫМ КЛИЕНТОМ
        updated_order_with_client = db.query(Order).options(
            joinedload(Order.client) # <-- Принудительно грузим КЛИЕНТА
        ).filter(
            Order.id == order_id
        ).first()
        
        if not updated_order_with_client:
             # Этого не должно случиться, но на всякий случай
             raise HTTPException(status_code=500, detail="Ошибка: Обновленный заказ не найден после сохранения.")
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---


        # --- Логика уведомлений (ИСПОЛЬЗУЕМ НОВЫЙ ОБЪЕКТ) ---
        if 'status' in update_data and update_data['status'] != original_status:
            new_status = update_data['status']
            
            # ВАЖНО: Уведомление должно уйти НОВОМУ клиенту, если он сменился
            client_to_notify = updated_order_with_client.client 
            
            if client_to_notify and client_to_notify.telegram_chat_id and new_status in ["Готов к выдаче", "В пути", "На складе в КР"]:
                print(f"[Notification] Статус заказа {updated_order_with_client.id} изменился на '{new_status}'. Вызов await generate_and_send_notification...")
                await generate_and_send_notification(
                        client=client_to_notify, 
                        new_status=new_status, 
                        track_codes=[updated_order_with_client.track_code]
                )
            else:
                chat_id_debug = client_to_notify.telegram_chat_id if client_to_notify else "Нет клиента"
                print(f"[Notification] Уведомление для заказа {updated_order_with_client.id} не отправлено (статус: '{new_status}', chat_id: {chat_id_debug})")
        # --- КОНЕЦ Логики уведомлений ---

        print(f"[Update Order] Заказ ID={order_id} успешно обновлен.")
        # Возвращаем объект, который мы принудительно перезагрузили
        return updated_order_with_client 
        
    except Exception as e:
        db.rollback() 
        import traceback
        print(f"!!! Ошибка БД при обновлении заказа ID={order_id}:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при обновлении заказа: {e}")

@app.delete("/api/orders/{order_id}", tags=["Заказы (Владелец)"], status_code=status.HTTP_204_NO_CONTENT)
def delete_order(
    order_id: int,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db),
    # Добавляем требование пароля через Query параметр или Header
    password: str = Query(...) # Требуем пароль как параметр запроса
):
    """Удаляет заказ ТЕКУЩЕЙ компании (ТРЕБУЕТ ПАРОЛЬ ВЛАДЕЛЬЦА)."""
    # Проверяем пароль текущего сотрудника (Владельца)
    if employee.password != password:
         raise HTTPException(status_code=403, detail="Неверный пароль для подтверждения удаления.")

    order = db.query(Order).filter(
        Order.id == order_id,
        Order.company_id == employee.company_id
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден в вашей компании.")

    # Доп. проверка: Нельзя удалить ВЫДАННЫЙ заказ? (По желанию)
    # if order.status == "Выдан":
    #     raise HTTPException(status_code=400, detail="Нельзя удалить уже выданный заказ.")

    db.delete(order)
    db.commit()
    return None

@app.get("/api/orders/parties", tags=["Заказы (Владелец)"], response_model=List[date])
def get_order_parties(
    employee: Employee = Depends(get_current_company_employee),
    db: Session = Depends(get_db)
):
    """Получает список уникальных дат партий для ТЕКУЩЕЙ компании."""
    parties = db.query(Order.party_date).filter(
        Order.company_id == employee.company_id,
        Order.party_date.isnot(None) # Исключаем заказы без даты
    ).distinct().order_by(Order.party_date.desc()).all()
    
    # Извлекаем даты из кортежей
    return [p[0] for p in parties]


# === НАЧАЛО ПОЛНОЙ ИСПРАВЛЕННОЙ ФУНКЦИИ bulk_order_action ===

# Эндпоинт для массовых действий (смена статуса, даты, удаление)

@app.post("/api/orders/bulk_action", tags=["Заказы (Владелец)"])
def bulk_order_action(
    payload: BulkActionPayload,
    background_tasks: BackgroundTasks, # <-- Используем BackgroundTasks
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """(Версия 3.0) Выполняет массовые действия (синхронно) и запускает уведомления в фоне."""

    if not payload.order_ids:
        raise HTTPException(status_code=400, detail="Не выбраны заказы для действия.")

    # 1. Загружаем заказы СРАЗУ с клиентами
    query = db.query(Order).options(joinedload(Order.client)).filter(
        Order.id.in_(payload.order_ids),
        Order.company_id == employee.company_id 
    )
    orders_to_action = query.all()

    requested_ids_set = set(payload.order_ids)
    found_ids_set = {o.id for o in orders_to_action}

    if len(found_ids_set) != len(requested_ids_set):
        missing_ids = list(requested_ids_set - found_ids_set)
        raise HTTPException(status_code=404, detail=f"Некоторые заказы не найдены в вашей компании: {missing_ids}")

    # --- Блок IF для 'update_status' ---
    if payload.action == 'update_status':
        new_status = payload.new_status
        if not new_status or new_status not in ORDER_STATUSES:
            raise HTTPException(status_code=400, detail="Недопустимый статус для массового обновления.")

        # --- Логика уведомлений (Группировка) ---
        notifications_to_send = {}
        for order in orders_to_action:
            if order.status != new_status and order.client and order.client.telegram_chat_id:
                if order.client.id not in notifications_to_send:
                    notifications_to_send[order.client.id] = {"client": order.client, "track_codes": []}
                notifications_to_send[order.client.id]["track_codes"].append(order.track_code)
        
        print(f"[Notification] Найдено {len(notifications_to_send)} клиентов для массовой рассылки.")
        # --- КОНЕЦ Группировки ---

        count = query.update({"status": new_status}, synchronize_session='fetch')
        
        # (Задача 3) Добавляем записи в историю
        if count > 0:
            history_entries = []
            for order in orders_to_action: # Используем 'orders_to_action', которые мы уже загрузили
                if order.status != new_status: 
                    history_entries.append(
                        OrderHistory(
                            order_id=order.id,
                            status=new_status,
                            employee_id=employee.id
                        )
                    )
            if history_entries:
                db.bulk_save_objects(history_entries)
        db.commit()

        # --- Отправка (ПОСЛЕ commit) ---
        if notifications_to_send and new_status in ["Готов к выдаче", "В пути", "На складе в КР"]:
            print(f"[Notification] Добавление {len(notifications_to_send)} задач в фон...")
            
            # ИСПРАВЛЕНИЕ: Добавляем async-функцию в BackgroundTasks ПРАВИЛЬНО
            for client_id, data in notifications_to_send.items():
                background_tasks.add_task(
                    generate_and_send_notification, # <--- Наша async-функция
                    client=data["client"], 
                    new_status=new_status, 
                    track_codes=data["track_codes"]
                )
            
            print(f"[Notification] Все {len(notifications_to_send)} задач по отправке добавлены в фон.")
        else:
             print(f"[Notification] Массовая рассылка не требуется (статус: '{new_status}' или нет клиентов).")
        # --- КОНЕЦ Отправки ---

        return {"status": "ok", "message": f"Статус '{new_status}' установлен для {count} заказов."}

    # --- Остальные 'elif' ---
    elif payload.action == 'update_party_date':
        if not payload.password or employee.password != payload.password:
            raise HTTPException(status_code=403, detail="Неверный пароль для подтверждения смены даты партии.")
        if not payload.new_party_date:
            raise HTTPException(status_code=400, detail="Не указана новая дата партии.")
        count = query.update({"party_date": payload.new_party_date}, synchronize_session='fetch') 
        db.commit()
        return {"status": "ok", "message": f"Дата партии обновлена для {count} заказов."}

    elif payload.action == 'buyout':
        if not payload.buyout_actual_rate or payload.buyout_actual_rate <= 0:
            raise HTTPException(status_code=400, detail="Не указан корректный реальный курс выкупа.")
        if not all(o.status == "Ожидает выкупа" for o in orders_to_action):
            raise HTTPException(status_code=400, detail="Массовый выкуп возможен только для заказов со статусом 'Ожидает выкупа'.")
        try:
            count = query.update({
                "status": "Выкуплен", 
                "buyout_actual_rate": payload.buyout_actual_rate
            }, synchronize_session='fetch')

            # (Задача 3) Добавляем записи в историю
            if count > 0:
                history_entries = [
                    OrderHistory(
                        order_id=order.id,
                        status="Выкуплен",
                        employee_id=employee.id
                    )
                    for order in orders_to_action # 'orders_to_action' уже загружены
                ]
                db.bulk_save_objects(history_entries)

            db.commit() # Коммитим и update, и историю
            return {"status": "ok", "message": f"Выкуп и статус 'Выкуплен' успешно применены к {count} заказам."}
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Ошибка базы данных при массовом выкупе: {e}")

    # --- ИСПРАВЛЕННЫЙ БЛОК 'assign_client' (v3.2) ---
    elif payload.action == 'assign_client':
        
        # ИСПРАВЛЕНИЕ: Используем 'client_id', как ожидает твой JS
        new_client_id = payload.client_id 

        new_status = payload.new_status
        if not new_status or new_status not in ORDER_STATUSES or new_status == "Выдан":
            new_status = "В пути" # Статус по умолчанию при назначении

        if not new_client_id:
            # Ошибка теперь соответствует полю Pydantic
            raise HTTPException(status_code=400, detail="Не указан ID клиента для назначения (client_id).")

        client = db.query(Client).filter(Client.id == new_client_id, Client.company_id == employee.company_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Клиент, которому вы пытаетесь назначить заказ, не найден.")

        # Проверяем, не назначен ли заказ УЖЕ этому клиенту
        already_assigned_tracks = []
        ids_to_update = []
        for order in orders_to_action:
            if order.client_id == new_client_id:
                already_assigned_tracks.append(order.track_code)
            else:
                # Добавляем в список на обновление, ТОЛЬКО если он не назначен
                ids_to_update.append(order.id)

        if not ids_to_update:
             # Если все заказы УЖЕ принадлежат этому клиенту
             raise HTTPException(status_code=400, detail=f"Все выбранные заказы ({already_assigned_tracks}) уже назначены этому клиенту.")

        # Обновляем только те, которые еще не назначены
        count = db.query(Order).filter(
            Order.id.in_(ids_to_update),
            Order.company_id == employee.company_id
        ).update(
            {"client_id": new_client_id, "status": new_status}, 
            synchronize_session=False # 'fetch' не нужен, т.к. мы знаем ID
        )
        db.commit()

        # Отправляем уведомление новому клиенту в фоне
        track_codes = [o.track_code for o in orders_to_action if o.id in ids_to_update]
        if track_codes: # Отправляем, только если что-то реально назначили
            background_tasks.add_task(
                generate_and_send_notification,
                client=client,
                new_status=new_status, 
                track_codes=track_codes
            )

        message = f"{count} заказов успешно назначены клиенту {client.full_name} (статус '{new_status}')."
        if already_assigned_tracks:
            message += f" {len(already_assigned_tracks)} заказов были пропущены, т.к. уже принадлежали ему."

        return {"status": "ok", "message": message}
    # --- КОНЕЦ ИСПРАВЛЕННОГО БЛОКА ---

    elif payload.action == 'delete':
        if not payload.password or employee.password != payload.password:
            raise HTTPException(status_code=403, detail="Неверный пароль для подтверждения удаления.")
        ids_to_delete = [o.id for o in orders_to_action] 
        query_to_delete = db.query(Order).filter(Order.id.in_(ids_to_delete))
        count = query_to_delete.delete(synchronize_session=False) 
        db.commit()
        return {"status": "ok", "message": f"Удалено {count} заказов."}

    else:
        raise HTTPException(status_code=400, detail="Неизвестное массовое действие.")


@app.post("/api/orders/bulk_import", tags=["Заказы (Владелец)"], response_model=BulkImportResponse)
def bulk_import_orders(
    payload: BulkOrderImportPayload,
    employee: Employee = Depends(get_current_active_employee), # Используем общую зависимость
    db: Session = Depends(get_db)
):
    """Массовый импорт заказов из Excel для ТЕКУЩЕЙ компании с привязкой к филиалу."""
    if employee.company_id is None: # SuperAdmin не может
        raise HTTPException(status_code=403, detail="Действие недоступно.")

    created_count = 0
    errors = []
    warnings = []
    # Дата партии по умолчанию - сегодня, если не указана в payload
    import_party_date = payload.party_date if payload.party_date else date.today()

    # --- ЛОГИКА ОПРЕДЕЛЕНИЯ ФИЛИАЛА ДЛЯ ИМПОРТА ---
    import_location_id = None
    if employee.role.name == 'Владелец':
        # Владелец: Используем location_id из payload ИЛИ его собственный location_id
        if payload.location_id:
            # Владелец выбрал филиал в интерфейсе, проверяем его
            loc_check = db.query(Location).filter(Location.id == payload.location_id, Location.company_id == employee.company_id).first()
            if not loc_check:
                raise HTTPException(status_code=404, detail="Выбранный для импорта филиал не найден в вашей компании.")
            import_location_id = payload.location_id
            print(f"[Import Orders] Владелец ID={employee.id} импортирует в филиал ID={import_location_id}")
        elif employee.location_id:
             # Используем основной филиал Владельца, если не выбран другой
             import_location_id = employee.location_id
             print(f"[Import Orders] Владелец ID={employee.id} импортирует в свой филиал ID={import_location_id} (по умолчанию)")
        else:
             # Если у Владельца нет location_id и он не выбрал филиал, ищем первый филиал компании
             first_location = db.query(Location).filter(Location.company_id == employee.company_id).first()
             if not first_location:
                  # Это не должно произойти, т.к. при создании компании создается "Главный филиал"
                  raise HTTPException(status_code=400, detail="Критическая ошибка: Не найден ни один филиал в компании для импорта заказов.")
             import_location_id = first_location.id
             print(f"[Import Orders] Владелец ID={employee.id} не привязан к филиалу, импорт в первый найденный: ID={import_location_id}")
    else:
        # Обычный сотрудник: Всегда импортирует в свой филиал
        if not employee.location_id:
            raise HTTPException(status_code=400, detail="Ошибка: Ваш профиль не привязан к филиалу, импорт невозможен.")
        import_location_id = employee.location_id
        print(f"[Import Orders] Сотрудник ID={employee.id} импортирует в свой филиал ID={import_location_id}")
    # --- КОНЕЦ ЛОГИКИ ОПРЕДЕЛЕНИЯ ФИЛИАЛА ---

    # --- Загрузка существующих данных для проверок ---
    company_clients = db.query(Client).filter(Client.company_id == employee.company_id).all()
    clients_by_phone = {c.phone: c for c in company_clients}
    clients_by_code_num = {c.client_code_num: c for c in company_clients if c.client_code_num is not None}
    existing_track_codes = {o.track_code for o in db.query(Order.track_code).filter(Order.company_id == employee.company_id)}
    unknown_client_counter = 1
    # --- Конец загрузки ---

    # --- Обработка каждой строки из payload ---
    for item in payload.orders_data:
        # Валидация трек-кода
        if not item.track_code or not item.track_code.strip():
            errors.append(f"Пропущена строка: отсутствует трек-код.")
            continue
        track_code = item.track_code.strip()
        if track_code in existing_track_codes:
             warnings.append(f"Заказ с трек-кодом '{track_code}' уже существует (пропущен).")
             continue

        # Поиск/создание клиента
        # Поиск клиента (БЕЗ СОЗДАНИЯ)
        client = None
        client_identifier = ""
        if item.client_code:
             code_str = str(item.client_code).strip()
             client_identifier = f"код '{code_str}'"
             match_num = re.search(r'(\d+)$', code_str)
             if match_num:
                 try:
                     num_val = int(match_num.group(1))
                     client = clients_by_code_num.get(num_val)
                 except ValueError: pass
        if not client and item.phone:
             cleaned_phone = re.sub(r'\D', '', str(item.phone))
             if not client_identifier: client_identifier = f"тел. '{cleaned_phone}'"
             if cleaned_phone: client = clients_by_phone.get(cleaned_phone)

        # (БЛОК СОЗДАНИЯ "НЕИЗВЕСТНОГО КЛИЕНТА" УДАЛЕН)

        if not client:
            warnings.append(f"Для заказа '{track_code}' не найден клиент ({client_identifier or 'нет идентификатора'}). Заказ импортирован как 'Невостребованный'.")

        # Определение статуса
        order_status = "Ожидает выкупа" if item.purchase_type == "Выкуп" else "В обработке"

        # Создание объекта Order с ОБЯЗАТЕЛЬНЫМ location_id
        new_order = Order(
            track_code=track_code,
            client_id=client.id if client else None, # <-- ГЛАВНОЕ ИЗМЕНЕНИЕ
            company_id=employee.company_id,
            location_id=import_location_id, # <-- ПРИВЯЗКА К ФИЛИАЛУ
            purchase_type=item.purchase_type or "Доставка",
            status=order_status,
            party_date=import_party_date,
            comment=item.comment,
            buyout_item_cost_cny=item.buyout_item_cost_cny,
            buyout_rate_for_client=item.buyout_rate_for_client,
            buyout_commission_percent=item.buyout_commission_percent or 10.0
        )
        db.add(new_order)
        existing_track_codes.add(track_code)
        created_count += 1

        # Периодический flush
        if created_count % 100 == 0:
            try: db.flush()
            except Exception as e_f:
                 db.rollback(); errors.append(f"Ошибка flush (~{created_count}): {e_f}"); break
    # --- Конец цикла обработки строк ---

    # Финальный commit
    try: db.commit()
    except Exception as e_c:
         db.rollback(); errors.append(f"Критическая ошибка commit: {e_c}"); created_count = 0

    return {
        "status": "ok",
        "message": "Импорт заказов завершен.",
        "created_clients": created_count, # Название поля неудачное, но оставим
        "errors": errors,
        "warnings": warnings
    }

# === КОНЕЦ НОВОГО КОДА (ЗАКАЗЫ) ===

# === НАЧАЛО НОВОГО КОДА (СМЕНЫ И ТИПЫ РАСХОДОВ) ===

# --- Эндпоинты для Смен ---

@app.get("/api/shifts/active", tags=["Смены"], response_model=Optional[ShiftOut])
def get_active_shift(
    employee: Employee = Depends(get_current_active_employee),  
    db: Session = Depends(get_db)
):
    """
    Возвращает активную смену для филиала текущего сотрудника.
    Если сотрудник - Владелец, возвращает активную смену его основного филиала 
    для возможности закрытия.
    """
    if employee.company_id is None:
        return None

    location_id_to_check = employee.location_id
    
    if location_id_to_check is None:
        return None

    # Ищем активную смену в филиале, к которому привязан сотрудник/владелец.
    # Если найдена, это активная смена "этого рабочего места".
    active_shift = db.query(Shift).filter(
        Shift.company_id == employee.company_id,
        Shift.location_id == location_id_to_check,
        Shift.end_time == None
    ).first()

    return active_shift

# main.py (Функция get_all_active_shifts)

@app.get("/api/shifts/all_active", tags=["Смены"], response_model=List[ShiftOut])
def get_all_active_shifts(
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Возвращает ВСЕ активные смены для ТЕКУЩЕЙ компании."""
    active_shifts = db.query(Shift).options(
        joinedload(Shift.employee) # <-- ЭТО КРИТИЧЕСКАЯ ЧАСТЬ
    ).filter(
        Shift.company_id == employee.company_id,
        Shift.end_time == None
    ).all()
    return active_shifts
    
# === КОНЕЦ ИСПРАВЛЕННОЙ ФУНКЦИИ get_active_shift ===

# === НАЧАЛО ПОЛНОЙ ИСПРАВЛЕННОЙ ФУНКЦИИ open_shift ===
@app.post("/api/shifts/open", tags=["Касса и Смены"], response_model=ShiftOut)
def open_shift(
    payload: ShiftOpenPayload,
    opener_employee: Employee = Depends(get_current_active_employee), # Сотрудник, выполняющий действие
    db: Session = Depends(get_db)
):
    """Открывает новую смену."""
    # --- ДОБАВЛЕНО ЛОГИРОВАНИЕ ---
    print(f"--- Попытка открыть смену ---")
    print(f"Действие выполняет: ID={opener_employee.id}, Имя={opener_employee.full_name}, Роль={opener_employee.role.name}, Компания={opener_employee.company_id}")
    print(f"Полученные данные (payload): {payload.dict()}")
    # --- КОНЕЦ ЛОГИРОВАНИЯ ---

    try: # Оборачиваем всю логику в try...except
        if opener_employee.company_id is None:
             print("[ОШИБКА] Супер-админ не может открывать смены.") # Лог
             raise HTTPException(status_code=403, detail="Супер-админ не может открывать смены.")

        # Проверяем права на открытие смены
        opener_perms = {p.codename for p in opener_employee.role.permissions}
        if 'open_close_shift' not in opener_perms:
            print(f"[ОШИБКА] У сотрудника ID={opener_employee.id} нет прав 'open_close_shift'.") # Лог
            raise HTTPException(status_code=403, detail="У вас нет прав на открытие/закрытие смен.")

        # 1. Проверка: Сотрудник, ФИЛИАЛ и Компания должны совпадать
        print(f"Проверка целевого сотрудника ID={payload.employee_id} и филиала ID={payload.location_id} в компании ID={opener_employee.company_id}...") # Лог
        target_employee = db.query(Employee).filter(
             Employee.id == payload.employee_id,
             Employee.company_id == opener_employee.company_id
        ).first()
        target_location = db.query(Location).filter(
             Location.id == payload.location_id,
             Location.company_id == opener_employee.company_id
        ).first()

        if not target_employee:
             print(f"[ОШИБКА] Целевой сотрудник ID={payload.employee_id} не найден в компании ID={opener_employee.company_id}.") # Лог
             raise HTTPException(status_code=404, detail="Целевой сотрудник не найден в вашей компании.")
        if not target_location:
             print(f"[ОШИБКА] Целевой филиал ID={payload.location_id} не найден в компании ID={opener_employee.company_id}.") # Лог
             raise HTTPException(status_code=404, detail="Целевой филиал не найден в вашей компании.")
        print(f"Сотрудник и филиал найдены: Сотрудник='{target_employee.full_name}', Филиал='{target_location.name}'.") # Лог

        # 2. Проверка: В этом филиале не должно быть уже открытой смены
        print(f"Проверка существующей активной смены в филиале ID={payload.location_id}...") # Лог
        existing_active_shift = db.query(Shift).filter(
            Shift.company_id == opener_employee.company_id,
            Shift.location_id == payload.location_id, # Проверяем именно ЦЕЛЕВОЙ филиал
            Shift.end_time == None
        ).first()
        if existing_active_shift:
            print(f"[ОШИБКА] Активная смена ID={existing_active_shift.id} уже существует в филиале ID={payload.location_id}.") # Лог
            raise HTTPException(status_code=400, detail=f"Нельзя открыть новую смену в филиале '{target_location.name}', пока не закрыта предыдущая.")
        print("Активной смены в этом филиале нет. Продолжаем...") # Лог

        # 3. Создаем новую смену
        print("Создание объекта Shift...") # Лог
        new_shift = Shift(
            starting_cash=payload.starting_cash,
            exchange_rate_usd=payload.exchange_rate_usd,
            price_per_kg_usd=payload.price_per_kg_usd,
            employee_id=payload.employee_id, # Сотрудник, который будет работать
            location_id=payload.location_id, # Филиал, где открыта смена
            company_id=opener_employee.company_id # Компания
        )
        print(f"Объект Shift создан (еще не в БД): {new_shift.__dict__}") # Лог

        try:
            print("Добавление смены в сессию (db.add)...") # Лог
            db.add(new_shift)
            print("Выполнение db.commit...") # Лог
            db.commit()
            print("Выполнение db.refresh...") # Лог
            db.refresh(new_shift)
            print(f"Смена ID={new_shift.id} успешно сохранена в БД.") # Лог
            return new_shift
        except Exception as e_db:
            db.rollback()
            import traceback
            print(f"!!! КРИТИЧЕСКАЯ ОШИБКА БАЗЫ ДАННЫХ при сохранении смены:\n{traceback.format_exc()}") # Лог
            raise HTTPException(status_code=500, detail=f"Ошибка базы данных при открытии смены: {e_db}")

    except HTTPException as http_exc:
         # Просто пробрасываем HTTP исключения дальше
         raise http_exc
    except Exception as e_main:
        # Ловим любые другие неожиданные ошибки
        import traceback
        print(f"!!! НЕОЖИДАННАЯ КРИТИЧЕСКАЯ ОШИБКА в функции open_shift:\n{traceback.format_exc()}") # Лог
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера при открытии смены: {e_main}")

# === КОНЕЦ ПОЛНОЙ ИСПРАВЛЕННОЙ ФУНКЦИИ open_shift ===

@app.post("/api/shifts/close", tags=["Касса и Смены"], response_model=ShiftOut)
def close_shift(
    payload: ShiftClosePayload,
    closer_employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db)
):
    """Закрывает ТЕКУЩУЮ АКТИВНУЮ смену в ФИЛИАЛЕ сотрудника."""
    if closer_employee.company_id is None:
         raise HTTPException(status_code=403, detail="Супер-админ не может закрывать смены.")

    # Проверяем права на закрытие смены
    closer_perms = {p.codename for p in closer_employee.role.permissions}
    if 'open_close_shift' not in closer_perms:
         raise HTTPException(status_code=403, detail="У вас нет прав на открытие/закрытие смен.")

    # Находим активную смену в ТЕКУЩЕМ филиале сотрудника
    active_shift = db.query(Shift).filter(
        Shift.company_id == closer_employee.company_id,
        Shift.location_id == closer_employee.location_id, # Важно: закрываем смену своего филиала
        Shift.end_time == None
    ).first()

    if not active_shift:
        # Используем 404, чтобы фронтенд понял, что активной смены нет
        raise HTTPException(status_code=404, detail="Активная смена в вашем филиале не найдена.")

    # Закрываем смену
    active_shift.end_time = datetime.now() # Используем aware datetime
    active_shift.closing_cash = payload.closing_cash
    db.commit()
    db.refresh(active_shift)
    return active_shift


# --- Эндпоинты для Типов Расходов ---

@app.get("/api/expense_types", tags=["Расходы (Владелец)"], response_model=List[ExpenseTypeOut])
def get_expense_types(
    employee: Employee = Depends(get_current_company_employee), # <-- ИСПРАВЛЕНО
    db: Session = Depends(get_db)
):
    """Получает все типы расходов для ТЕКУЩЕЙ компании."""
    types = db.query(ExpenseType).filter(
        ExpenseType.company_id == employee.company_id
    ).order_by(ExpenseType.name).all()
    return types

@app.post("/api/expense_types", tags=["Расходы (Владелец)"], response_model=ExpenseTypeOut)
def create_expense_type(
    payload: ExpenseTypeCreate,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Создает новый тип расхода для ТЕКУЩЕЙ компании."""
    # Проверка прав (на всякий случай, хотя依赖 уже проверила)
    perms = {p.codename for p in employee.role.permissions}
    if 'manage_expense_types' not in perms:
         raise HTTPException(status_code=403, detail="Нет прав на управление типами расходов.")

    # Проверка на дубликат имени ВНУТРИ компании
    if db.query(ExpenseType).filter(ExpenseType.name == payload.name, ExpenseType.company_id == employee.company_id).first():
        raise HTTPException(status_code=400, detail="Тип расхода с таким названием уже существует.")

    new_type = ExpenseType(
        name=payload.name,
        company_id=employee.company_id
    )
    db.add(new_type)
    db.commit()
    db.refresh(new_type)
    return new_type

@app.patch("/api/expense_types/{type_id}", tags=["Расходы (Владелец)"], response_model=ExpenseTypeOut)
def update_expense_type(
    type_id: int,
    payload: ExpenseTypeUpdate,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Обновляет название типа расхода ТЕКУЩЕЙ компании."""
    perms = {p.codename for p in employee.role.permissions}
    if 'manage_expense_types' not in perms:
         raise HTTPException(status_code=403, detail="Нет прав на управление типами расходов.")

    exp_type = db.query(ExpenseType).filter(
        ExpenseType.id == type_id,
        ExpenseType.company_id == employee.company_id
    ).first()
    if not exp_type:
        raise HTTPException(status_code=404, detail="Тип расхода не найден.")

    # Проверка на дубликат нового имени
    if payload.name != exp_type.name and db.query(ExpenseType).filter(ExpenseType.name == payload.name, ExpenseType.company_id == employee.company_id).first():
         raise HTTPException(status_code=400, detail="Тип расхода с таким новым названием уже существует.")

    exp_type.name = payload.name
    db.commit()
    db.refresh(exp_type)
    return exp_type

@app.delete("/api/expense_types/{type_id}", tags=["Расходы (Владелец)"], status_code=status.HTTP_204_NO_CONTENT)
def delete_expense_type(
    type_id: int,
    employee: Employee = Depends(get_company_owner),
    db: Session = Depends(get_db)
):
    """Удаляет тип расхода ТЕКУЩЕЙ компании."""
    perms = {p.codename for p in employee.role.permissions}
    if 'manage_expense_types' not in perms:
         raise HTTPException(status_code=403, detail="Нет прав на управление типами расходов.")

    exp_type = db.query(ExpenseType).filter(
        ExpenseType.id == type_id,
        ExpenseType.company_id == employee.company_id
    ).first()
    if not exp_type:
        raise HTTPException(status_code=404, detail="Тип расхода не найден.")

    # Проверка, используется ли тип в каких-либо расходах
    expense_count = db.query(Expense).filter(Expense.expense_type_id == type_id).count()
    if expense_count > 0:
        raise HTTPException(status_code=400, detail=f"Нельзя удалить тип '{exp_type.name}', так как он используется в {expense_count} записях о расходах.")

    db.delete(exp_type)
    db.commit()
    return None

# === НАЧАЛО НОВОГО КОДА (РАСХОДЫ) ===

# --- Эндпоинты для Расходов ---

# main.py (ПОЛНОСТЬЮ ЗАМЕНЯЕТ create_expense)
@app.post("/api/expenses", tags=["Расходы"], response_model=ExpenseOut)
def create_expense(
    payload: ExpenseCreate, # Теперь payload содержит shift_id
    employee: Employee = Depends(get_current_active_employee), 
    db: Session = Depends(get_db)
):
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Действие недоступно.")

    perms = {p.codename for p in employee.role.permissions}
    if 'add_expense' not in perms:
        raise HTTPException(status_code=403, detail="У вас нет прав на добавление расходов.")

    expense_type = db.query(ExpenseType).filter(
        ExpenseType.id == payload.expense_type_id,
        ExpenseType.company_id == employee.company_id
    ).first()
    if not expense_type:
        raise HTTPException(status_code=404, detail="Указанный тип расхода не найден.")

    shift_id_for_expense = None 

    if employee.role.name == 'Владелец':
        # Владелец: Используем shift_id из payload (если он есть и валиден)
        if payload.shift_id is not None:
            shift_check = db.query(Shift).filter(
                Shift.id == payload.shift_id, 
                Shift.company_id == employee.company_id,
                Shift.end_time == None).first()
            if shift_check:
                shift_id_for_expense = payload.shift_id
                print(f"[Expense] Владелец ID={employee.id} привязывает расход к смене ID={payload.shift_id}")
            else:
                print(f"[Expense] Владелец ID={employee.id} пытался привязать расход к неактивной/чужой смене {payload.shift_id}. Сохранено как Общий.")
                shift_id_for_expense = None 
        else:
            shift_id_for_expense = None
            print(f"[Expense] Владелец ID={employee.id} добавляет расход без привязки (Общий).")
    else:
        # Сотрудник: Требуется активная смена в его филиале
        active_shift = db.query(Shift).filter(
            Shift.company_id == employee.company_id,
            Shift.location_id == employee.location_id,
            Shift.end_time == None
        ).first()
        if not active_shift:
            raise HTTPException(status_code=400, detail="Нет активной смены для добавления расхода. Откройте смену.")
        shift_id_for_expense = active_shift.id 
        print(f"[Expense] Сотрудник ID={employee.id} добавляет расход к смене ID={active_shift.id}")

    new_expense = Expense(
        amount=payload.amount,
        notes=payload.notes,
        expense_type_id=payload.expense_type_id,
        shift_id=shift_id_for_expense, 
        company_id=employee.company_id 
    )

    try:
        db.add(new_expense)
        db.commit()
        db.refresh(new_expense)
        db.refresh(new_expense, attribute_names=['expense_type'])
        print(f"[Expense] Расход ID={new_expense.id} успешно добавлен.")
        return new_expense
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных: {e}")

# main.py (ПОЛНОСТЬЮ ЗАМЕНЯЕТ get_expenses)

@app.get("/api/expenses", tags=["Расходы"], response_model=List[ExpenseOut])
def get_expenses(
    start_date: date, # Обязательный параметр начала периода
    end_date: date,   # Обязательный параметр конца периода
    employee: Employee = Depends(get_current_active_employee), # Любой сотрудник компании
    # ДОБАВЛЕН ЭТОТ ПАРАМЕТР:
    location_id: Optional[int] = Query(None), # <-- Добавляем фильтр по филиалу
    db: Session = Depends(get_db)
):
    """Получает список расходов ТЕКУЩЕЙ компании за указанный период с фильтрацией по филиалу."""
    # === ИСПРАВЛЕНИЕ КРИТИЧЕСКОЙ ОШИБКИ: Используем company_id вместо is_super_admin ===
    # Проверяем, что это не Супер-Админ
    if employee.company_id is None:
         # Супер-админу пока не даем доступ к расходам компаний
         raise HTTPException(status_code=403, detail="Доступ к расходам для SuperAdmin не реализован.")
    # === КОНЕЦ ИСПРАВЛЕНИЯ ===

    # Проверка прав на просмотр расходов
    perms = {p.codename for p in employee.role.permissions}
    # Разрешаем просмотр, если есть право на отчет по смене ИЛИ на полные отчеты
    if 'view_shift_report' not in perms and 'view_full_reports' not in perms:
        raise HTTPException(status_code=403, detail="У вас нет прав на просмотр расходов.")

    print(f"[Expense] Запрос списка расходов для компании ID={employee.company_id} за период {start_date} - {end_date}")

    # Формируем границы периода (включая весь день end_date)
    # Используем datetime для корректного сравнения с DateTime полем created_at
    start_datetime = datetime.combine(start_date, datetime.min.time())
    # Конец дня end_date (23:59:59.999999)
    end_datetime = datetime.combine(end_date, datetime.max.time())

    # --- ИСПРАВЛЕНИЕ ОШИБКИ ЗАГРУЗКИ ---
    # Начинаем строить базовый запрос, сразу подгружая связанные данные
    query = db.query(Expense).options(
        # ИСПРАВЛЕНИЕ 1: Правильно загружаем Тип Расхода
        joinedload(Expense.expense_type),
        # ИСПРАВЛЕНИЕ 2: Правильно загружаем Смену и Сотрудника смены
        joinedload(Expense.shift).joinedload(Shift.employee)
    ).filter(
    # --- КОНЕЦ ИСПРАВЛЕНИЙ ---
        # Фильтруем по компании и дате создания
        Expense.company_id == employee.company_id,
        Expense.created_at >= start_datetime,
        Expense.created_at <= end_datetime # Используем <= с концом дня
    ) # Пока не выполняем .all()

    # --- НОВАЯ ЛОГИКА ФИЛЬТРАЦИИ ПО ФИЛИАЛУ ДЛЯ РАСХОДОВ ---
    if employee.role.name == 'Владелец':
        # Владелец: фильтруем по location_id, ЕСЛИ он передан
        if location_id is not None:
            # Проверяем, что филиал принадлежит компании (защита от некорректных запросов)
            loc_check = db.query(Location).filter(Location.id == location_id, Location.company_id == employee.company_id).first()
            if not loc_check:
                 raise HTTPException(status_code=404, detail="Указанный филиал не найден в вашей компании.")
            # Фильтруем расходы:
            # 1. Привязанные к сменам ИМЕННО ЭТОГО филиала
            # 2. ИЛИ "Общие расходы" Владельца (где shift_id = NULL)
            # Используем LEFT JOIN (isouter=True), чтобы включить расходы без смены
            query = query.join(Shift, Expense.shift_id == Shift.id, isouter=True).filter(
                 or_(
                      Shift.location_id == location_id, # Расходы смен этого филиала
                      Expense.shift_id == None          # ИЛИ общие расходы
                 )
            )
            print(f"[Расходы] Владелец ID={employee.id} фильтрует расходы по филиалу ID={location_id}")
        else:
             # Если location_id не передан, Владелец видит ВСЕ расходы компании (всех филиалов + общие)
             print(f"[Расходы] Владелец ID={employee.id} просматривает расходы ВСЕХ филиалов и Общие.")
             # Дополнительно фильтровать query не нужно, базовый фильтр по company_id уже есть
             pass
    else:
        # ОБЫЧНЫЙ СОТРУДНИК: Всегда видит расходы ТОЛЬКО своего филиала, привязанные к сменам
        if employee.location_id is None:
             # Если сотрудник не привязан к филиалу, он не должен видеть расходы смен
             print(f"[Расходы][ОШИБКА] Сотрудник ID={employee.id} не привязан к филиалу! Не может видеть расходы смен.")
             return [] # Возвращаем пустой список
        # Фильтруем расходы, привязанные к сменам ЕГО филиала
        # Используем INNER JOIN (isouter=False - по умолчанию), т.к. сотрудник видит ТОЛЬКО расходы смен
        query = query.join(Shift, Expense.shift_id == Shift.id).filter(
            Shift.location_id == employee.location_id
        )
        print(f"[Расходы] Сотрудник ID={employee.id} просматривает расходы своего филиала ID={employee.location_id}")
    # --- КОНЕЦ НОВОЙ ЛОГИКИ ФИЛЬТРАЦИИ ПО ФИЛИАЛУ ---

    # Добавляем сортировку по дате создания (новые вверху) и выполняем запрос
    expenses = query.order_by(Expense.created_at.desc()).all()

    print(f"[Expense] Найдено {len(expenses)} расходов за период (с учетом фильтра филиала).")
    # Возвращаем результат (FastAPI сам преобразует в JSON благодаря response_model)
    return expenses


@app.patch("/api/expenses/{expense_id}", tags=["Расходы"], response_model=ExpenseOut)
def update_expense(
    expense_id: int,
    payload: ExpenseUpdate,
    employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db)
):
    """Обновляет существующий расход."""
    if employee.company_id is None:
         raise HTTPException(status_code=403, detail="Супер-админ не может редактировать расходы.")

    # Находим расход, который нужно обновить
    expense = db.query(Expense).options(
         joinedload(Expense.shift) # Загружаем смену для проверки даты
    ).filter(
        Expense.id == expense_id,
        Expense.company_id == employee.company_id # Убеждаемся, что расход из той же компании
    ).first()

    if not expense:
        raise HTTPException(status_code=404, detail="Расход не найден в вашей компании.")

    # --- Проверка Прав на Редактирование ---
    can_edit = False
    perms = {p.codename for p in employee.role.permissions}

    # Проверяем, активна ли смена, к которой привязан расход
    is_shift_active = expense.shift and expense.shift.end_time is None
    
    # 1. Можно редактировать расходы в ТЕКУЩЕЙ АКТИВНОЙ смене, если есть право 'add_expense'
    if is_shift_active and 'add_expense' in perms:
          can_edit = True
          print(f"[Expense Update] Разрешено: Редактирование в активной смене.")

    # 2. Владелец может редактировать ЛЮБЫЕ расходы своей компании
    if employee.role.name == 'Владелец':
         can_edit = True
         print(f"[Expense Update] Разрешено: Редактирование Владельцем.")

    if not can_edit:
        print(f"[Expense Update] Запрещено: Сотрудник ID={employee.id} не может редактировать расход ID={expense_id} (Смена закрыта или нет прав).")
        raise HTTPException(status_code=403, detail="У вас нет прав на редактирование этого расхода (возможно, он из закрытой смены).")
    # --- Конец Проверки Прав ---


    update_data = payload.dict(exclude_unset=True) # Берем только переданные поля

    # Проверяем новый тип расхода, если он передан
    if 'expense_type_id' in update_data:
        new_expense_type = db.query(ExpenseType).filter(
            ExpenseType.id == update_data['expense_type_id'],
            ExpenseType.company_id == employee.company_id
        ).first()
        if not new_expense_type:
            raise HTTPException(status_code=404, detail="Новый тип расхода не найден в вашей компании.")

    # Применяем обновления
    print(f"[Expense Update] Обновление расхода ID={expense_id}. Данные:", update_data)
    for key, value in update_data.items():
        setattr(expense, key, value)

    try:
        db.commit()
        db.refresh(expense)
        # Перезагружаем тип расхода для корректного ответа
        db.refresh(expense, attribute_names=['expense_type'])
        print(f"[Expense Update] Расход ID={expense_id} успешно обновлен.")
        return expense
    except Exception as e:
        db.rollback()
        import traceback
        print(f"!!! Ошибка БД при обновлении расхода ID={expense_id}:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при обновлении расхода: {e}")

# === НАЧАЛО НОВОЙ ФУНКЦИИ DELETE ===
@app.delete("/api/expenses/{expense_id}", tags=["Расходы"], status_code=status.HTTP_204_NO_CONTENT)
def delete_expense(
    expense_id: int,
    employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db),
    password: str = Query(...) # Требуем пароль как параметр запроса
):
    """Удаляет запись о расходе (ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА И ТРЕБУЕТ ПАРОЛЬ)."""
    
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Супер-админ не может удалять расходы компаний.")

    # Проверка: Только Владелец может удалять расходы
    if employee.role.name != 'Владелец':
        raise HTTPException(status_code=403, detail="Только Владелец компании может удалять записи о расходах.")
    
    # Проверка пароля Владельца
    if employee.password != password:
        raise HTTPException(status_code=403, detail="Неверный пароль Владельца для подтверждения удаления.")

    # Находим расход, который нужно удалить
    expense = db.query(Expense).options(
        joinedload(Expense.shift)
    ).filter(
        Expense.id == expense_id,
        Expense.company_id == employee.company_id # Убеждаемся, что расход из той же компании
    ).first()

    if not expense:
        raise HTTPException(status_code=404, detail="Расход не найден в вашей компании.")

    # Запрещаем удаление, если смена уже закрыта (дополнительная мера безопасности)
    if expense.shift and expense.shift.end_time is not None:
        raise HTTPException(status_code=400, detail="Нельзя удалить расход из закрытой смены.")

    # Удаляем расход
    try:
        db.delete(expense)
        db.commit()
        print(f"[Expense Delete] Расход ID={expense_id} успешно удален Владельцем ID={employee.id}.")
        return None
    except Exception as e:
        db.rollback()
        import traceback
        print(f"!!! Ошибка БД при удалении расхода ID={expense_id}:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при удалении расхода: {e}")
# === КОНЕЦ НОВОЙ ФУНКЦИИ DELETE ===

# === КОНЕЦ НОВОГО КОДА (РАСХОДЫ) ===

# main.py (Добавляем полный блок для Выдачи)

# --- Эндпоинты для Выдачи Заказов ---
# main.py (Полностью заменяет get_orders_ready_for_issue)

@app.get("/api/orders/ready_for_issue", tags=["Выдача"], response_model=List[OrderOut])
def get_orders_ready_for_issue(
    employee: Employee = Depends(get_current_active_employee), 
    db: Session = Depends(get_db),
    # --- НОВЫЙ ПАРАМЕТР: Фильтр по филиалу (для Владельца) ---
    location_id: Optional[int] = Query(None) 
):
    """
    Получает список заказов со статусом 'Готов к выдаче'.
    - Владелец: Может фильтровать по location_id или видеть все.
    - Сотрудник: Всегда видит только заказы своего филиала.
    Требует права 'issue_orders'.
    """
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Действие недоступно для SuperAdmin.")

    perms = {p.codename for p in employee.role.permissions}
    if 'issue_orders' not in perms:
        raise HTTPException(status_code=403, detail="У вас нет прав на просмотр заказов для выдачи.")

    query = db.query(Order).options(
        joinedload(Order.client) 
    ).filter(
        Order.company_id == employee.company_id,
        Order.status == "Готов к выдаче" # Основной фильтр для выдачи
    )

    # --- НОВАЯ ЛОГИКА ФИЛЬТРАЦИИ ПО ФИЛИАЛУ ---
    if employee.role.name == 'Владелец':
        if location_id is not None:
            loc_check = db.query(Location).filter(Location.id == location_id, Location.company_id == employee.company_id).first()
            if not loc_check:
                 raise HTTPException(status_code=404, detail="Указанный филиал не найден.")
            query = query.filter(Order.location_id == location_id)
            print(f"[Выдача] Владелец ID={employee.id} фильтрует по филиалу ID={location_id}")
        else:
             print(f"[Выдача] Владелец ID={employee.id} видит готовые заказы ВСЕХ филиалов.")
             pass # Владелец видит все, если location_id не указан
    else:
        # ОБЫЧНЫЙ СОТРУДНИК: Всегда фильтруем по его location_id
        if employee.location_id is None:
             print(f"[Выдача][ОШИБКА] Сотрудник ID={employee.id} не привязан к филиалу!")
             return [] 
        query = query.filter(Order.location_id == employee.location_id)
        print(f"[Выдача] Сотрудник ID={employee.id} видит готовые заказы своего филиала ID={employee.location_id}")
    # --- КОНЕЦ НОВОЙ ЛОГИКИ ---

    # Сортировка
    orders = query.order_by(Order.client_id, Order.id).all() 

    print(f"[Выдача] Найдено {len(orders)} заказов для выдачи (с учетом фильтра филиала).")
    return orders

@app.post("/api/orders/issue", tags=["Выдача"])
def issue_orders(
    payload: IssuePayload,
    employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db)
):
    """
    Оформляет выдачу одного или нескольких заказов.
    Требует права 'issue_orders' и активную смену.
    """
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Действие недоступно для SuperAdmin.")

    # Проверка прав
    perms = {p.codename for p in employee.role.permissions}
    if 'issue_orders' not in perms:
        raise HTTPException(status_code=403, detail="У вас нет прав на выдачу заказов.")

    order_ids = [item.order_id for item in payload.orders]
    
    # Находим заказы, проверяем их статус и принадлежность компании
    orders_to_issue = db.query(Order).filter(
        Order.id.in_(order_ids),
        Order.company_id == employee.company_id
    ).all()

    # --- НОВЫЙ БЛОК ПРОВЕРКИ СМЕНЫ (вставить сюда) ---
    if not orders_to_issue:
         # Эта проверка дублирует существующую, но нужна здесь для безопасности
         raise HTTPException(status_code=404, detail="Заказы не найдены.")

    # 1. Получаем location_id из ПЕРВОГО заказа
    order_location_id = orders_to_issue[0].location_id

    # 2. Убедимся, что все заказы из ОДНОГО филиала
    if not all(o.location_id == order_location_id for o in orders_to_issue):
        raise HTTPException(status_code=400, detail="Нельзя выдать заказы из разных филиалов одновременно.")

    # 3. Ищем активную смену в ФИЛИАЛЕ ЗАКАЗА (а не сотрудника, который нажимает кнопку)
    active_shift = db.query(Shift).filter(
        Shift.company_id == employee.company_id,
        Shift.location_id == order_location_id, # <-- ПРАВИЛЬНАЯ ПРОВЕРКА
        Shift.end_time == None
    ).first()

    if not active_shift:
        # Находим имя филиала для красивой ошибки
        location_name_obj = db.query(Location.name).filter(Location.id == order_location_id).first()
        location_name = location_name_obj[0] if location_name_obj else f"ID {order_location_id}"
        raise HTTPException(status_code=400, detail=f"Нет активной смены в филиале '{location_name}'. Невозможно оформить выдачу.")
    # --- КОНЕЦ НОВОГО БЛОКА ---

    # Проверки
    if len(orders_to_issue) != len(order_ids):
        found_ids = {o.id for o in orders_to_issue}
        missing_ids = [oid for oid in order_ids if oid not in found_ids]
        raise HTTPException(status_code=404, detail=f"Заказы с ID {missing_ids} не найдены в вашей компании.")
        
    for order in orders_to_issue:
        if order.status != "Готов к выдаче":
            raise HTTPException(status_code=400, detail=f"Заказ #{order.id} ({order.track_code}) не готов к выдаче (статус: {order.status}).")
            
    # Расчет общей суммы к оплате
    total_cost_to_pay = 0
    order_weights = {item.order_id: item.weight_kg for item in payload.orders}
    
    for order in orders_to_issue:
        weight = order_weights.get(order.id)
        if not weight or weight <= 0:
             raise HTTPException(status_code=400, detail=f"Не указан корректный вес для заказа #{order.id}.")
        
        # Используем предрасчитанную стоимость, ЕСЛИ она есть, иначе считаем по данным из payload
        if order.calculated_final_cost_som and order.calculated_weight_kg == weight:
             cost = order.calculated_final_cost_som
        else:
             cost = weight * payload.price_per_kg_usd * payload.exchange_rate_usd
        
        total_cost_to_pay += cost

    total_paid = payload.paid_cash + payload.paid_card
    
    # Проверка оплаты (оплачено должно быть не меньше, чем к оплате)
    # Допускаем небольшую погрешность в 1 сом
    if total_paid < (total_cost_to_pay - 1): 
         raise HTTPException(status_code=400, detail=f"Недостаточно оплаты. К оплате: {total_cost_to_pay:.2f} сом, Оплачено: {total_paid:.2f} сом.")

    # Оформляем выдачу для каждого заказа
    now = datetime.now() # Время выдачи
    issued_count = 0
    try:
        for order in orders_to_issue:
            item_data = next((item for item in payload.orders if item.order_id == order.id), None)
            if item_data: # Должен всегда находиться
                order.status = "Выдан"
                
                # (Задача 3) Добавляем запись в историю
                history_entry = OrderHistory(
                    order_id=order.id,
                    status="Выдан",
                    employee_id=employee.id
                )
                db.add(history_entry)
                
                order.weight_kg = item_data.weight_kg # Фактический вес при выдаче
                order.price_per_kg_usd = payload.price_per_kg_usd
                order.exchange_rate_usd = payload.exchange_rate_usd
                # Перезаписываем final_cost_som на основе фактических данных при выдаче
                order.final_cost_som = (item_data.weight_kg * payload.price_per_kg_usd * payload.exchange_rate_usd)
                
                # Распределяем оплату пропорционально (упрощенный вариант - делим поровну)
                # TODO: Можно улучшить распределение, если нужно
                order.paid_cash_som = payload.paid_cash / len(orders_to_issue)
                order.paid_card_som = payload.paid_card / len(orders_to_issue)
                order.card_payment_type = payload.card_payment_type if payload.paid_card > 0 else None
                
                order.issued_at = now # Время выдачи
                order.shift_id = active_shift.id # Привязка к смене
                order.reverted_at = None # Сбрасываем флаг возврата, если он был
                issued_count += 1
                
        db.commit()
        print(f"[Выдача] Успешно выдано {issued_count} заказов. Смена ID={active_shift.id}, Сотрудник ID={employee.id}")
        return {"status": "ok", "message": f"Успешно выдано {issued_count} заказов."}
        
    except Exception as e:
        db.rollback()
        import traceback
        print(f"!!! Ошибка БД при оформлении выдачи:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при оформлении выдачи: {e}")


# main.py (ПОЛНОСТЬЮ ЗАМЕНЯЕТ get_issued_orders)

@app.get("/api/orders/issued", tags=["Выдача"], response_model=List[OrderOut])
def get_issued_orders(
    start_date: date, 
    end_date: date,
    employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db),
    # --- НОВЫЙ НЕОБЯЗАТЕЛЬНЫЙ ПАРАМЕТР ---
    location_id: Optional[int] = Query(None)
):
    """
    Получает историю выданных заказов за период.
    - Владелец: Может фильтровать по location_id или видеть все.
    - Сотрудник: Видит только свой филиал.
    """
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Действие недоступно для SuperAdmin.")
        
    perms = {p.codename for p in employee.role.permissions}
    if 'view_shift_report' not in perms and 'view_full_reports' not in perms:
        raise HTTPException(status_code=403, detail="У вас нет прав на просмотр истории выданных.")

    print(f"[Выдача История] Запрос для компании ID={employee.company_id} за {start_date} - {end_date}")

    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())

    query = db.query(Order).options(
        joinedload(Order.client)
    ).filter(
        Order.company_id == employee.company_id,
        Order.status == "Выдан",
        Order.issued_at >= start_datetime,
        Order.issued_at <= end_datetime
    )

    # --- НОВАЯ ЛОГИКА ФИЛЬТРАЦИИ ПО ФИЛИАЛУ ---
    if employee.role.name == 'Владелец':
        if location_id is not None:
            # Проверяем филиал на всякий случай
            loc_check = db.query(Location).filter(Location.id == location_id, Location.company_id == employee.company_id).first()
            if not loc_check:
                 raise HTTPException(status_code=404, detail="Указанный филиал не найден.")
            query = query.filter(Order.location_id == location_id)
            print(f"[Выдача История] Владелец ID={employee.id} фильтрует по филиалу ID={location_id}")
        # else: Владелец видит все, если location_id не указан
            
    else:
        # ОБЫЧНЫЙ СОТРУДНИК: Всегда фильтруем по его location_id
        if employee.location_id is None:
             print(f"[Выдача История][ОШИБКА] Сотрудник ID={employee.id} не привязан к филиалу!")
             return [] 
        query = query.filter(Order.location_id == employee.location_id)
        print(f"[Выдача История] Сотрудник ID={employee.id} видит историю своего филиала ID={employee.location_id}")
    # --- КОНЕЦ НОВОЙ ЛОГИКИ ---

    orders = query.order_by(Order.issued_at.desc()).all()

    print(f"[Выдача История] Найдено {len(orders)} выданных заказов за период (с учетом фильтра).")
    return orders

class RevertOrderPayload(BaseModel):
    password: Optional[str] = None
    revert_reason: str = Field(..., min_length=5) # Причина обязательна

@app.patch("/api/orders/{order_id}/revert_status", tags=["Выдача"], response_model=OrderOut)
def revert_order_status(
    order_id: int,
    employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db)
):
    """
    Возвращает статус выданного заказа обратно на 'Готов к выдаче'.
    Требует права 'issue_orders'. Доступно Владельцу или сотруднику в активной смене.
    """
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Действие недоступно для SuperAdmin.")

    perms = {p.codename for p in employee.role.permissions}
    if 'issue_orders' not in perms: # Требуем те же права, что и на выдачу
        raise HTTPException(status_code=403, detail="У вас нет прав на отмену выдачи.")

    order = db.query(Order).options(joinedload(Order.shift)).filter( # Загружаем смену для проверки
        Order.id == order_id,
        Order.company_id == employee.company_id
    ).first()

    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден в вашей компании.")
    if order.status != "Выдан":
        raise HTTPException(status_code=400, detail="Можно вернуть только заказ со статусом 'Выдан'.")

    # --- ПРОВЕРКА ВОЗМОЖНОСТИ ВОЗВРАТА ---
    can_revert = False
    if employee.role.name == 'Владелец':
        can_revert = True # Владелец может всегда
    else:
        # Сотрудник может вернуть, только если СМЕНА, в которую была выдача, ЕЩЕ АКТИВНА
        if order.shift and order.shift.end_time is None:
            # И если это смена ТЕКУЩЕГО СОТРУДНИКА (доп. безопасность)
            if order.shift.employee_id == employee.id:
                 can_revert = True
    
    if not can_revert:
         raise HTTPException(status_code=403, detail="Отмена выдачи невозможна (смена закрыта или у вас нет прав).")
    # --- КОНЕЦ ПРОВЕРКИ ---

    try:
        order.status = "Готов к выдаче"
        
        # (Задача 3) Добавляем запись в историю
        history_entry = OrderHistory(
            order_id=order.id,
            status="Готов к выдаче",
            employee_id=employee.id
        )
        db.add(history_entry)
        
        order.reverted_at = datetime.now() # Фиксируем время возврата
        # Обнуляем данные о выдаче
        order.issued_at = None
        order.shift_id = None
        order.weight_kg = None # Сбрасываем фактический вес
        order.final_cost_som = None # Сбрасываем фактическую стоимость
        order.paid_cash_som = None
        order.paid_card_som = None
        order.card_payment_type = None
        
        db.commit()
        db.refresh(order)
        db.refresh(order, attribute_names=['client']) # Обновляем клиента для ответа
        print(f"[Выдача] Статус заказа ID={order_id} возвращен на 'Готов к выдаче'. Сотрудник ID={employee.id}")
        return order
        
    except Exception as e:
        db.rollback()
        import traceback
        print(f"!!! Ошибка БД при возврате статуса заказа ID={order_id}:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при возврате статуса: {e}")
# main.py (Добавьте этот блок)

# --- Эндпоинты для Отчетов (Multi-Tenant) ---

def calculate_shift_report_data(db: Session, shift: Shift) -> ShiftReport:
    """Вспомогательная функция для расчета данных по одной смене."""
    
    # 1. Доходы (только заказы, выданные в ЭТУ смену)
    issued_orders_in_shift = db.query(Order).filter(
        Order.shift_id == shift.id, 
        Order.status == "Выдан"
    ).all()
    total_cash_income = sum(o.paid_cash_som for o in issued_orders_in_shift if o.paid_cash_som)
    total_card_income = sum(o.paid_card_som for o in issued_orders_in_shift if o.paid_card_som)

    # 2. Расходы (только расходы, привязанные к ЭТОЙ смене)
    # Исключаем ЗП и Аванс из операционных расходов смены
    expenses_in_shift = db.query(Expense).join(ExpenseType).filter(
        Expense.shift_id == shift.id,
        ExpenseType.name.notin_(['Зарплата', 'Аванс']) 
    ).all()
    total_expenses = sum(exp.amount for exp in expenses_in_shift)

    # 3. Возвраты (Заказы, возвращенные В ТЕЧЕНИЕ этой смены)
    # (Эта логика может быть сложной, если возврат происходит в другую смену, пока упрощаем)
    total_returns = 0 # TODO: Реализовать логику возвратов, если потребуется

    # 4. Расчет
    calculated_cash = shift.starting_cash + total_cash_income - total_expenses - total_returns
    discrepancy = None
    if shift.end_time and shift.closing_cash is not None:
        discrepancy = shift.closing_cash - calculated_cash

    # Загружаем связанные данные (если они еще не загружены)
    location_name = db.query(Location.name).filter(Location.id == shift.location_id).scalar() or "Неизвестный филиал"
    employee_name = db.query(Employee.full_name).filter(Employee.id == shift.employee_id).scalar() or "Неизвестный сотрудник"

    return ShiftReport(
        shift_id=shift.id,
        shift_start_time=shift.start_time,
        shift_end_time=shift.end_time,
        employee_name=employee_name,
        location_name=location_name,
        starting_cash=shift.starting_cash,
        cash_income=total_cash_income,
        card_income=total_card_income,
        total_expenses=total_expenses,
        total_returns=total_returns,
        calculated_cash=calculated_cash,
        actual_closing_cash=shift.closing_cash,
        discrepancy=discrepancy
    )

@app.get("/api/reports/shift/current", tags=["Отчеты"], response_model=ShiftReport)
def get_current_shift_report(
    employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db)
):
    """
    Получает отчет для ТЕКУЩЕЙ АКТИВНОЙ смены сотрудника.
    Доступно сотрудникам с правом 'view_shift_report'.
    """
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Действие недоступно.")
        
    perms = {p.codename for p in employee.role.permissions}
    if 'view_shift_report' not in perms:
        raise HTTPException(status_code=403, detail="У вас нет прав на просмотр отчета по смене.")

    if employee.location_id is None:
         raise HTTPException(status_code=400, detail="Вы не привязаны к филиалу.")

    # Ищем активную смену в филиале сотрудника
    active_shift = db.query(Shift).filter(
        Shift.company_id == employee.company_id,
        Shift.location_id == employee.location_id,
        Shift.end_time == None
    ).first()

    if not active_shift:
        raise HTTPException(status_code=404, detail="Активная смена в вашем филиале не найдена.")

    # Рассчитываем отчет
    report_data = calculate_shift_report_data(db, active_shift)
    return report_data

# main.py (Добавьте этот НОВЫЙ эндпоинт)

@app.get("/api/reports/shift/location/{location_id}", tags=["Отчеты"], response_model=ShiftReport)
def get_current_shift_report_by_location(
    location_id: int,
    employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db)
):
    """
    Получает отчет для АКТИВНОЙ смены в УКАЗАННОМ ФИЛИАЛЕ.
    Доступно Владельцу или сотруднику этого филиала.
    """
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Действие недоступно.")

    perms = {p.codename for p in employee.role.permissions}
    if 'view_shift_report' not in perms:
        raise HTTPException(status_code=403, detail="У вас нет прав на просмотр отчета.")

    # Проверка, что филиал принадлежит компании
    loc_check = db.query(Location).filter(Location.id == location_id, Location.company_id == employee.company_id).first()
    if not loc_check:
         raise HTTPException(status_code=404, detail="Филиал не найден в вашей компании.")

    # Владелец может смотреть любой свой филиал
    if employee.role.name != 'Владелец':
        # Сотрудник может смотреть только свой филиал
        if employee.location_id != location_id:
             raise HTTPException(status_code=403, detail="Вы не можете просматривать отчеты других филиалов.")

    # Ищем активную смену в УКАЗАННОМ филиале
    active_shift = db.query(Shift).filter(
        Shift.company_id == employee.company_id,
        Shift.location_id == location_id, # <-- Используем location_id из URL
        Shift.end_time == None
    ).first()

    if not active_shift:
        raise HTTPException(status_code=404, detail=f"Активная смена в филиале '{loc_check.name}' не найдена.")

    # Рассчитываем отчет
    report_data = calculate_shift_report_data(db, active_shift)
    return report_data

@app.get("/api/reports/shift/{shift_id}", tags=["Отчеты"], response_model=ShiftReport)
def get_past_shift_report(
    shift_id: int,
    employee: Employee = Depends(get_current_active_employee),
    db: Session = Depends(get_db)
):
    """Получает отчет для УКАЗАННОЙ (закрытой) смены. Доступно Владельцу."""
    # (Мы можем расширить права, если нужно)
    perms = {p.codename for p in employee.role.permissions}
    if 'view_full_reports' not in perms: # Только те, кто видит сводные отчеты
        raise HTTPException(status_code=403, detail="У вас нет прав на просмотр истории отчетов.")

    shift = db.query(Shift).filter(
        Shift.id == shift_id,
        Shift.company_id == employee.company_id # Проверка принадлежности компании
    ).first()

    if not shift:
        raise HTTPException(status_code=404, detail="Смена не найдена в вашей компании.")

    report_data = calculate_shift_report_data(db, shift)
    return report_data

# main.py (Полностью заменяет get_summary_report)

@app.get("/api/reports/summary", tags=["Отчеты"]) # Убираем response_model, т.к. возвращаем словарь
def get_summary_report(
    start_date: date,
    end_date: date,
    location_id: Optional[int] = Query(None), # Добавляем необязательный фильтр по филиалу
    db: Session = Depends(get_db),
    # МЕНЯЕМ ЗАВИСИМОСТЬ на get_current_active_employee
    current_employee: Employee = Depends(get_current_active_employee) # Используем общую зависимость
):
    """
    Формирует сводный отчет по доходам, расходам и сменам за период.
    - Владелец: Может фильтровать по location_id или видеть все.
    - Сотрудник: Всегда видит только свой филиал.
    Требует права 'view_full_reports'.
    """
    # Проверка прав (только те, кто может видеть полные отчеты)
    perms = {p.codename for p in current_employee.role.permissions}
    if 'view_full_reports' not in perms:
        raise HTTPException(status_code=403, detail="У вас нет прав на просмотр сводных отчетов.")

    # Проверка, что это не Супер-Админ
    if current_employee.company_id is None:
        raise HTTPException(status_code=403, detail="Действие недоступно для SuperAdmin.")

    company_id = current_employee.company_id # ID компании текущего сотрудника

    print(f"[Summary Report] Запрос для компании ID={company_id}, период {start_date} - {end_date}, фильтр филиала: {location_id}")

    # --- ЛОГИКА ОПРЕДЕЛЕНИЯ ФИЛИАЛОВ ДЛЯ ФИЛЬТРАЦИИ ---
    accessible_location_ids = [] # Список ID филиалов, данные которых нужно включить в отчет
    if current_employee.role.name == 'Владелец':
        if location_id is not None: # Если Владелец выбрал конкретный филиал
             # Проверяем, что выбранный филиал принадлежит этой компании
             location = db.query(Location).filter(Location.id == location_id, Location.company_id == company_id).first()
             if not location:
                  raise HTTPException(status_code=404, detail="Выбранный филиал не найден или не принадлежит вашей компании.")
             accessible_location_ids = [location_id]
             print(f"[Summary Report] Владелец фильтрует по филиалу ID={location_id}")
        else: # Если Владелец не выбрал филиал (отчет по всей компании)
             # Получаем ID всех филиалов компании
             accessible_location_ids = [loc.id for loc in db.query(Location.id).filter(Location.company_id == company_id).all()]
             print(f"[Summary Report] Владелец просматривает отчет по ВСЕМ филиалам.")
    else: # Обычный сотрудник видит отчет только по своему филиалу
        if current_employee.location_id is None:
             # Это не должно произойти, если сотрудник активен, но на всякий случай
             raise HTTPException(status_code=400, detail="Ваш профиль не привязан к филиалу.")
        accessible_location_ids = [current_employee.location_id]
        print(f"[Summary Report] Сотрудник просматривает отчет по своему филиалу ID={current_employee.location_id}")

    if not accessible_location_ids:
         # Если список филиалов пуст (например, у компании нет филиалов)
         # Возвращаем пустой отчет или ошибку, здесь вернем пустой
         print("[Summary Report] Не найдено доступных филиалов для отчета.")
         # Формируем пустой ответ
         empty_summary = {
             "start_date": start_date, "end_date": end_date, "location_id_filter": location_id,
             "total_income": 0, "total_cash_income": 0, "total_card_income": 0,
             "total_expenses": 0, "expenses_by_type": {}, "net_profit": 0, "shifts": []
         }
         return {"status": "ok", "summary": empty_summary}
    # --- КОНЕЦ ЛОГИКИ ОПРЕДЕЛЕНИЯ ФИЛИАЛОВ ---


    # --- Корректная обработка диапазона дат ---
    start_datetime = datetime.combine(start_date, time.min) # Начало дня start_date 00:00:00
    # Используем конец дня end_date (23:59:59...) для включения всего дня
    end_datetime = datetime.combine(end_date, time.max)

    # --- Фильтруем выданные заказы по компании, доступным филиалам и дате ---
    issued_orders_query = db.query(Order).filter(
        Order.company_id == company_id,
        Order.location_id.in_(accessible_location_ids), # Фильтр по доступным филиалам
        Order.status == "Выдан",
        Order.issued_at >= start_datetime,
        Order.issued_at <= end_datetime # Используем <= с концом дня
    )
    issued_orders = issued_orders_query.all()
    print(f"[Summary Report] Найдено выданных заказов: {len(issued_orders)}")

    # --- Фильтруем все расходы по компании, доступным филиалам и дате ---
    all_expenses_query = db.query(Expense).options(joinedload(Expense.expense_type)).filter(
        Expense.company_id == company_id,
        Expense.created_at >= start_datetime,
        Expense.created_at <= end_datetime # Используем <= с концом дня
    )
    # Фильтруем по shift.location_id ИЛИ учитываем общие расходы (shift_id is NULL),
    # НО ТОЛЬКО если Владелец смотрит отчет по ВСЕЙ компании (location_id is None)
    # ИЛИ если Владелец смотрит отчет по КОНКРЕТНОМУ филиалу (включаем расходы этого филиала + общие)
    # Сотрудник видит ТОЛЬКО расходы своего филиала (без общих)
    if current_employee.role.name == 'Владелец':
        # Если отчет по ВСЕМ филиалам (location_id не задан), включаем расходы ВСЕХ филиалов + Общие
        if location_id is None:
            all_expenses_query = all_expenses_query.join(Shift, Expense.shift_id == Shift.id, isouter=True).filter(
                 or_(
                      Shift.location_id.in_(accessible_location_ids), # Расходы смен всех доступных филиалов
                      Expense.shift_id == None                      # И Общие расходы
                 )
            )
        else: # Если отчет по КОНКРЕТНОМУ филиалу, включаем расходы этого филиала + Общие
             all_expenses_query = all_expenses_query.join(Shift, Expense.shift_id == Shift.id, isouter=True).filter(
                 or_(
                      Shift.location_id == location_id, # Расходы смен ТОЛЬКО этого филиала
                      Expense.shift_id == None          # И Общие расходы
                 )
            )
    else: # Обычный сотрудник
         # Видит ТОЛЬКО расходы смен своего филиала (INNER JOIN)
         all_expenses_query = all_expenses_query.join(Shift, Expense.shift_id == Shift.id).filter(
             Shift.location_id == current_employee.location_id
         )

    all_expenses = all_expenses_query.all()
    print(f"[Summary Report] Найдено расходов: {len(all_expenses)}")

    # --- Расчеты (остаются без изменений) ---
    total_cash_income = sum(o.paid_cash_som for o in issued_orders if o.paid_cash_som)
    total_card_income = sum(o.paid_card_som for o in issued_orders if o.paid_card_som)
    total_income = total_cash_income + total_card_income

    total_expenses = sum(e.amount for e in all_expenses)

    expenses_by_type = {}
    for exp in all_expenses:
        # Безопасно получаем имя типа расхода
        type_name = exp.expense_type.name if exp.expense_type else "Без типа"
        if type_name not in expenses_by_type:
            expenses_by_type[type_name] = 0
        expenses_by_type[type_name] += exp.amount

    net_profit = total_income - total_expenses

    # --- Фильтруем смены по компании, доступным филиалам и дате (остается без изменений) ---
    shifts_in_period_query = db.query(Shift).options(
        joinedload(Shift.employee),
        joinedload(Shift.location) # Загружаем локацию
    ).filter(
        Shift.company_id == company_id,
        Shift.location_id.in_(accessible_location_ids), # Фильтр по доступным филиалам
        Shift.start_time >= start_datetime, # Смены, начавшиеся в периоде
        Shift.start_time <= end_datetime # Используем <=
        # Можно добавить фильтр по end_time, если нужно включать только ЗАВЕРШЕННЫЕ смены
        # Shift.end_time != None,
        # Shift.end_time <= end_datetime
    )
    shifts_in_period = shifts_in_period_query.order_by(Shift.start_time.desc()).all()
    print(f"[Summary Report] Найдено смен: {len(shifts_in_period)}")

    # --- Формируем ответ (словарь) ---
    summary = {
        "start_date": start_date.isoformat(), # Преобразуем в строку для JSON
        "end_date": end_date.isoformat(),
        "location_id_filter": location_id, # Добавляем информацию о фильтре
        "total_income": total_income,
        "total_cash_income": total_cash_income,
        "total_card_income": total_card_income,
        "total_expenses": total_expenses,
        "expenses_by_type": expenses_by_type,
        "net_profit": net_profit,
        # Добавляем больше деталей о сменах в список словарей
        "shifts": [
            {
                "id": shift.id,
                "start_time": shift.start_time.isoformat(),
                "end_time": shift.end_time.isoformat() if shift.end_time else None,
                "employee": {
                    "id": shift.employee.id,
                    "full_name": shift.employee.full_name
                } if shift.employee else None,
                 "location": { # Добавляем информацию о филиале смены
                     "id": shift.location.id,
                     "name": shift.location.name
                 } if shift.location else None,
                 # Дополнительно можно вернуть cash/card income и expenses для этой смены, если нужно
            } for shift in shifts_in_period
        ]
    }
    # Возвращаем словарь напрямую, без Pydantic модели
    return {"status": "ok", "summary": summary}

# main.py (Добавить этот НОВЫЙ эндпоинт)

@app.get("/api/reports/buyout", tags=["Отчеты"])
def get_buyout_report(
    start_date: date,
    end_date: date,
    location_id: Optional[int] = Query(None), # Фильтр по филиалу
    db: Session = Depends(get_db),
    current_employee: Employee = Depends(get_current_active_employee) # Общая зависимость
):
    """
    Формирует отчет по выкупленным заказам и курсовой разнице за период.
    - Владелец: Может фильтровать по location_id или видеть все.
    - Сотрудник: Видит только свой филиал.
    Требует права 'view_full_reports'.
    """
    # Проверка прав
    perms = {p.codename for p in current_employee.role.permissions}
    if 'view_full_reports' not in perms:
        raise HTTPException(status_code=403, detail="У вас нет прав на просмотр отчетов по выкупу.")

    if current_employee.company_id is None:
        raise HTTPException(status_code=403, detail="Действие недоступно для SuperAdmin.")

    company_id = current_employee.company_id
    print(f"[Buyout Report] Запрос для компании ID={company_id}, период {start_date} - {end_date}, фильтр филиала: {location_id}")

    # --- ОПРЕДЕЛЕНИЕ ДОСТУПНЫХ ФИЛИАЛОВ (аналогично Сводному отчету) ---
    accessible_location_ids = []
    if current_employee.role.name == 'Владелец':
        if location_id is not None:
             location = db.query(Location).filter(Location.id == location_id, Location.company_id == company_id).first()
             if not location: raise HTTPException(status_code=404, detail="Филиал не найден.")
             accessible_location_ids = [location_id]
             print(f"[Buyout Report] Владелец фильтрует по филиалу ID={location_id}")
        else:
             accessible_location_ids = [loc.id for loc in db.query(Location.id).filter(Location.company_id == company_id).all()]
             print(f"[Buyout Report] Владелец просматривает отчет по ВСЕМ филиалам.")
    else: # Обычный сотрудник
        if current_employee.location_id is None: raise HTTPException(status_code=400, detail="Профиль не привязан к филиалу.")
        accessible_location_ids = [current_employee.location_id]
        print(f"[Buyout Report] Сотрудник просматривает отчет по своему филиалу ID={current_employee.location_id}")

    if not accessible_location_ids:
         print("[Buyout Report] Не найдено доступных филиалов.")
         return {"status": "ok", "report": {"items": [], "total_profit": 0}} # Возвращаем пустой отчет
    # --- КОНЕЦ ОПРЕДЕЛЕНИЯ ФИЛИАЛОВ ---

    # --- Даты (аналогично Сводному отчету) ---
    start_datetime = datetime.combine(start_date, time.min)
    end_datetime = datetime.combine(end_date, time.max)

    # --- ЗАПРОС К БД ---
    # Выбираем заказы типа "Выкуп", созданные в указанный период И относящиеся к доступным филиалам
    buyout_orders_query = db.query(Order).options(joinedload(Order.client)).filter(
        Order.company_id == company_id,
        Order.location_id.in_(accessible_location_ids), # Фильтр по филиалам
        Order.purchase_type == "Выкуп",
        Order.created_at >= start_datetime, # Используем дату СОЗДАНИЯ заказа для выкупа
        Order.created_at <= end_datetime
    ).order_by(Order.created_at.desc()) # Сортируем по дате создания

    buyout_orders = buyout_orders_query.all()
    print(f"[Buyout Report] Найдено заказов на выкуп: {len(buyout_orders)}")

    # --- РАСЧЕТ ДАННЫХ ОТЧЕТА ---
    report_items = []
    total_profit = 0
    for order in buyout_orders:
        price_for_client = 0
        actual_cost = 0
        profit = 0

        # Рассчитываем цену для клиента (если есть данные)
        if order.buyout_item_cost_cny and order.buyout_rate_for_client:
            # Считаем комиссию (используем % из заказа или 10% по умолчанию)
            commission_percent = order.buyout_commission_percent if order.buyout_commission_percent is not None else 10.0
            commission_amount = order.buyout_item_cost_cny * (commission_percent / 100.0)
            # Итоговая цена = (Стоимость товара + Комиссия) * Курс для клиента
            price_for_client = (order.buyout_item_cost_cny + commission_amount) * order.buyout_rate_for_client

        # Рассчитываем себестоимость (если есть реальный курс)
        if order.buyout_item_cost_cny and order.buyout_actual_rate:
            actual_cost = order.buyout_item_cost_cny * order.buyout_actual_rate

        # Рассчитываем прибыль (только если обе суммы посчитаны)
        if price_for_client > 0 and actual_cost > 0:
            profit = price_for_client - actual_cost

        total_profit += profit # Добавляем к общей прибыли

        # Добавляем данные по заказу в список
        report_items.append({
            "order_id": order.id,
            "track_code": order.track_code,
            "created_at": order.created_at.isoformat(), # В строку для JSON
            "client_name": order.client.full_name if order.client else "?",
            "item_cost_cny": order.buyout_item_cost_cny,
            "commission_percent": order.buyout_commission_percent, # Добавили %
            "rate_for_client": order.buyout_rate_for_client,
            "price_for_client": price_for_client, # Рассчитанная цена
            "actual_rate": order.buyout_actual_rate, # Реальный курс
            "actual_cost": actual_cost, # Рассчитанная себестоимость
            "profit": profit # Рассчитанная прибыль
        })
    # --- КОНЕЦ РАСЧЕТА ---

    # Возвращаем результат
    return {
        "status": "ok",
        "report": {
            "items": report_items,
            "total_profit": total_profit
        }
    }

# --- ДОБАВИТЬ ЭТОТ НОВЫЙ ЭНДПОИНТ ---
@app.post("/api/orders/calculate", tags=["Заказы (Владелец)"])
async def calculate_orders( # Добавляем async для уведомлений
    payload: CalculatePayload,
    employee: Employee = Depends(get_current_active_employee), # Используем общую зависимость
    db: Session = Depends(get_db)
):
    """
    Рассчитывает стоимость для выбранных заказов и сохраняет расчетные данные.
    Может опционально изменить статус заказов.
    Доступно сотрудникам с правом 'manage_orders'.
    """
    if employee.company_id is None:
        raise HTTPException(status_code=403, detail="Действие недоступно для SuperAdmin.")

    # Проверка прав (например, 'manage_orders' или 'issue_orders'?)
    # Давайте пока разрешим тем, кто может управлять заказами
    perms = {p.codename for p in employee.role.permissions}
    if 'manage_orders' not in perms:
        raise HTTPException(status_code=403, detail="У вас нет прав на расчет стоимости заказов.")

    order_ids = [item.order_id for item in payload.orders]
    if not order_ids:
        raise HTTPException(status_code=400, detail="Не выбраны заказы для расчета.")

    # Проверка нового статуса (если передан)
    if payload.new_status and payload.new_status not in ORDER_STATUSES:
        raise HTTPException(status_code=400, detail=f"Недопустимый новый статус: {payload.new_status}")

    # 1. Находим заказы в базе, проверяем принадлежность к компании и статус
    orders_to_update_query = db.query(Order).options(joinedload(Order.client)).filter(
        Order.id.in_(order_ids),
        Order.company_id == employee.company_id
    )
    orders_to_update = orders_to_update_query.all()

    # Проверка, все ли заказы найдены
    found_ids = {o.id for o in orders_to_update}
    missing_ids = [oid for oid in order_ids if oid not in found_ids]
    if missing_ids:
        raise HTTPException(status_code=404, detail=f"Заказы с ID {missing_ids} не найдены в вашей компании.")

    # Проверка, что заказы не выданы
    issued_orders = [o.track_code for o in orders_to_update if o.status == "Выдан"]
    if issued_orders:
        raise HTTPException(status_code=400, detail=f"Нельзя пересчитать уже выданные заказы: {issued_orders}")

    # 2. Обновляем расчетные данные и статус для каждого заказа
    updated_count = 0
    notifications_to_send = {} # Словарь для группировки уведомлений по клиентам
    try:
        for order in orders_to_update:
            item_data = next((item for item in payload.orders if item.order_id == order.id), None)
            if item_data: # Должен всегда находиться
                original_status = order.status # Запоминаем старый статус

                # Обновляем расчетные поля
                order.calculated_weight_kg = item_data.weight_kg
                order.calculated_price_per_kg_usd = payload.price_per_kg_usd
                order.calculated_exchange_rate_usd = payload.exchange_rate_usd
                order.calculated_final_cost_som = (
                    item_data.weight_kg * payload.price_per_kg_usd * payload.exchange_rate_usd
                )

                # Обновляем статус, если он передан и отличается от текущего
                if payload.new_status and payload.new_status != original_status:
                    order.status = payload.new_status
                    
                    # (Задача 3) Добавляем запись в историю
                    history_entry = OrderHistory(
                        order_id=order.id,
                        status=payload.new_status,
                        employee_id=employee.id
                    )
                    db.add(history_entry) # Добавляем в сессию

                    # Готовим данные для уведомления
                    if order.client and order.client.telegram_chat_id:
                        client_id = order.client.id
                        if client_id not in notifications_to_send:
                            notifications_to_send[client_id] = {"client": order.client, "track_codes": []}
                        notifications_to_send[client_id]["track_codes"].append(order.track_code)

                updated_count += 1

        db.commit() # Сохраняем все изменения
        print(f"[Calculate Orders] Расчет сохранен для {updated_count} заказов. Новый статус: {payload.new_status or 'не изменен'}")

        # --- НАЧАЛО ИСПРАВЛЕНИЯ: ОТПРАВКА УВЕДОМЛЕНИЙ ---
        # Проверяем, был ли изменен статус и есть ли
        # подготовленные уведомления
        if payload.new_status and notifications_to_send and payload.new_status in ["Готов к выдаче", "В пути", "На складе в КР"]:
            print(f"[Calculate Orders] Запуск {len(notifications_to_send)} задач на отправку (await) о статусе '{payload.new_status}'...")
            tasks = []
            for client_id, data in notifications_to_send.items():
                # Создаем задачи
                tasks.append(
                    generate_and_send_notification(
                        client=data["client"], 
                        new_status=payload.new_status, 
                        track_codes=data["track_codes"]
                    )
                )
            # Ждем выполнения ВСЕХ задач по отправке
            await asyncio.gather(*tasks)
            print(f"[Calculate Orders] Все {len(tasks)} задач по отправке завершены.")
        else:
            print(f"[Calculate Orders] Массовая рассылка не требуется (статус: '{payload.new_status}' или нет клиентов).")

        return {"status": "ok", "message": f"Расчет сохранен для {updated_count} заказов." + (f" Статус обновлен на '{payload.new_status}'." if payload.new_status else "")}

    except Exception as e:
        db.rollback()
        import traceback
        print(f"!!! Ошибка БД при сохранении расчета заказов:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при сохранении расчета: {e}")

# --- НОВЫЕ Модели для идентификации пользователя Ботом ---
class BotIdentifyPayload(BaseModel):
    company_id: int
    telegram_chat_id: str # ID чата пользователя в Telegram
    phone_number: Optional[str] = None # Номер телефона (если пользователь отправил контакт)

# --- ИЗМЕНИТЬ ClientBotInfo ---
class ClientBotInfo(ClientOut): # Наследуется от ClientOut
    pass # Дополнительных полей нет
    # ДОБАВИТЬ Config (для надежности, хотя должно наследоваться)
    class Config:
        from_attributes = True # <--- ДОБАВЛЕНО
# --- КОНЕЦ ИЗМЕНЕНИЙ ClientBotInfo ---

class BotIdentifyResponse(BaseModel):
    client: ClientBotInfo
    is_owner: bool
    employee_id: Optional[int] = None
    # ДОБАВИТЬ Config и сюда, так как она содержит вложенную модель с from_attributes
    class Config:
        from_attributes = True

# --- НОВЫЙ ЭНДПОИНТ для Идентификации Пользователя Ботом ---
@app.post("/api/bot/identify_user", tags=["Telegram Bot"], response_model=BotIdentifyResponse)
def identify_bot_user(
    payload: BotIdentifyPayload,
    db: Session = Depends(get_db)
):
    """
    Ищет клиента по Telegram Chat ID или номеру телефона для указанной компании.
    Если найден по номеру, привязывает Chat ID.
    Возвращает данные клиента и флаг, является ли он Владельцем.
    Вызывается Telegram-ботом.
    """
    client = None
    is_owner = False
    print(f"[Bot Identify] Поиск пользователя для Company ID: {payload.company_id}, Chat ID: {payload.telegram_chat_id}, Phone: {payload.phone_number}")

    # --- Шаг 1: Проверка компании ---
    company = db.query(Company).filter(Company.id == payload.company_id).first()
    if not company:
        print(f"!!! [Bot Identify] Ошибка: Компания ID {payload.company_id} не найдена.")
        raise HTTPException(status_code=404, detail=f"Компания с ID {payload.company_id} не найдена.")

    # --- Шаг 2: Поиск по Telegram Chat ID ---
    if payload.telegram_chat_id:
        client = db.query(Client).filter(
            Client.telegram_chat_id == payload.telegram_chat_id,
            Client.company_id == payload.company_id
        ).first()
        if client:
             print(f"[Bot Identify] Клиент найден по Chat ID: {client.id} - {client.full_name}")

    # --- Шаг 3: Поиск по номеру телефона (если не найден по Chat ID и номер передан) ---
    if not client and payload.phone_number:
        
        # --- НОВАЯ УЛЬТРА-НАДЕЖНАЯ ЛОГИКА ПОИСКА ---
        
        # 1. Получаем номер от бота (бот присылает '996555366386')
        phone_from_bot = re.sub(r'\D', '', str(payload.phone_number))
        
        # 2. Извлекаем ПОСЛЕДНИЕ 9 цифр (e.g., '555366386')
        last_9_digits = ""
        if len(phone_from_bot) >= 9:
            last_9_digits = phone_from_bot[-9:]
            print(f"[Bot Identify] Поиск по универсальному ключу (последние 9 цифр): {last_9_digits}")

            # 3. Ищем в БД, СРАВНИВАЯ ТОЛЬКО КОНЕЦ строки в базе
            # (Это найдет '996555366386', '0555366386', '555366386' и даже '+996555366386')
            client = db.query(Client).filter(
                Client.company_id == payload.company_id,
                Client.phone.endswith(last_9_digits) 
            ).first()
            
        else:
            # Если номер от бота почему-то короткий, ищем как есть
            print(f"[Bot Identify] Номер от бота слишком короткий, ищем как есть: {phone_from_bot}")
            client = db.query(Client).filter(
                Client.company_id == payload.company_id,
                Client.phone == phone_from_bot
            ).first()
        # --- КОНЕЦ НОВОЙ ЛОГИКИ ---

        if client:
            # (Этот блок остается без изменений)
            print(f"[Bot Identify] Клиент найден по номеру (формат в БД: {client.phone}): {client.id} - {client.full_name}")
            
            # --- Привязка Chat ID, если его еще нет или он другой ---
            if client.telegram_chat_id != payload.telegram_chat_id:
                 existing_client_with_chat_id = db.query(Client).filter(
                     Client.telegram_chat_id == payload.telegram_chat_id,
                     Client.company_id == payload.company_id
                 ).first()
                 if existing_client_with_chat_id:
                      print(f"!!! [Bot Identify] Ошибка: Chat ID {payload.telegram_chat_id} уже привязан к другому клиенту (ID: {existing_client_with_chat_id.id}) в этой компании.")
                      raise HTTPException(status_code=409, detail="Этот Telegram аккаунт уже привязан к другому клиенту.")
                 else:
                     print(f"[Bot Identify] Привязка Chat ID {payload.telegram_chat_id} к клиенту ID {client.id}")
                     client.telegram_chat_id = payload.telegram_chat_id
                     try:
                         db.commit()
                         db.refresh(client)
                     except Exception as e_commit:
                          db.rollback()
                          print(f"!!! [Bot Identify] Ошибка при сохранении Chat ID: {e_commit}")
                          raise HTTPException(status_code=500, detail="Ошибка базы данных при привязке Telegram.")
        else:
             print(f"[Bot Identify] Клиент с телефоном (ключ: {last_9_digits}) не найден в компании {payload.company_id}.")

    # --- Шаг 4: Проверка, является ли найденный клиент Владельцем ---
    if client:
        # Ищем сотрудника-владельца В ЭТОЙ компании с таким же ПОЛНЫМ ИМЕНЕМ
        owner_employee = db.query(Employee).join(Role).filter(
            Employee.company_id == payload.company_id,
            # --- ИЗМЕНЕНИЕ ЗДЕСЬ ---
            Employee.full_name == client.full_name, # Сравниваем по полному имени
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---
            Role.name == "Владелец"
        ).first()
        if owner_employee:
            is_owner = True
            print(f"[Bot Identify] Найденный клиент (ID: {client.id}) является Владельцем (ID сотрудника: {owner_employee.id}).")
        else:
             print(f"[Bot Identify] Найденный клиент (ID: {client.id}) НЕ является Владельцем.")

    # --- Шаг 5: Возвращаем результат или 404 ---
    if client:
        try:
            client_response_data = ClientBotInfo.from_orm(client)
            # --- ИЗМЕНЕНИЕ ЗДЕСЬ ---
            return BotIdentifyResponse(
                client=client_response_data, 
                is_owner=is_owner,
                # Передаем ID сотрудника, если это владелец
                employee_id=owner_employee.id if is_owner and owner_employee else None 
            )
        except Exception as pydantic_error:
            # Ловим возможные ошибки при преобразовании в Pydantic модель
            import traceback
            print(f"!!! [Bot Identify] Ошибка Pydantic при формировании ответа для клиента ID {client.id}:\n{traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера при обработке данных клиента: {pydantic_error}")
    else:
        # Если клиент не найден ни по Chat ID, ни по телефону
        raise HTTPException(status_code=404, detail="Клиент не найден. Пожалуйста, проверьте номер или зарегистрируйтесь.")

# --- КОНЕЦ ИСПРАВЛЕННОЙ ФУНКЦИИ ---

# main.py

# --- НОВАЯ Модель Pydantic для регистрации через бота ---
class BotClientRegisterPayload(BaseModel):
    full_name: str
    phone: str
    company_id: int
    telegram_chat_id: str
    client_code_prefix: Optional[str] = "TG" # Префикс по умолчанию для бот-регистраций

# --- НОВЫЙ ЭНДПОИНТ ДЛЯ РЕГИСТРАЦИИ КЛИЕНТА БОТОМ (ПУБЛИЧНЫЙ) ---
@app.post("/api/bot/register_client", tags=["Telegram Bot"], response_model=ClientOut)
def register_client_from_bot(
    payload: BotClientRegisterPayload, 
    background_tasks: BackgroundTasks, 
    db: Session = Depends(get_db)
):
    """
    Регистрирует нового клиента из Telegram-бота.
    (Версия с ИСПРАВЛЕННОЙ логикой генерации кодов и ПРЕФИКСА)
    """
    print(f"[Bot Register] Попытка регистрации: {payload.dict()}")

    # 1. Проверка компании (ЗАГРУЖАЕМ ОБЪЕКТ, А НЕ ТОЛЬКО ID)
    company = db.query(Company).filter(Company.id == payload.company_id).first() # <-- ИЗМЕНЕНО
    if not company:
        print(f"!!! [Bot Register] Ошибка: Компания ID {payload.company_id} не найдена.")
        raise HTTPException(status_code=404, detail=f"Компания (ID: {payload.company_id}) не найдена.")

    # 2. Проверка дубликата телефона ВНУТРИ компании
    if db.query(Client).filter(Client.phone == payload.phone, Client.company_id == payload.company_id).first():
        print(f"!!! [Bot Register] Ошибка: Телефон {payload.phone} уже занят.")
        raise HTTPException(status_code=400, detail="Клиент с таким телефоном уже существует в этой компании.")

    # 3. Проверка дубликата Chat ID ВНУТРИ компании
    if db.query(Client).filter(Client.telegram_chat_id == payload.telegram_chat_id, Client.company_id == payload.company_id).first():
        print(f"!!! [Bot Register] Ошибка: Chat ID {payload.telegram_chat_id} уже занят.")
        raise HTTPException(status_code=409, detail="Этот Telegram-аккаунт уже привязан к другому клиенту.")

    # 4. Авто-генерация кода клиента (с Настройкой)
    print(f"[Generate Code] (Bot) Авто-генерация кода для {payload.phone}")
    start_code_setting = db.query(Setting).filter(Setting.key == 'client_code_start', Setting.company_id == payload.company_id).first()
    start_from = 1001
    if start_code_setting and start_code_setting.value:
        try:
            start_from = int(start_code_setting.value)
        except ValueError:
            pass
    print(f"[Generate Code] (Bot) Настройка 'client_code_start' = {start_from}")

    max_normal_code = db.query(
        func.max(Client.client_code_num)
    ).filter(
        Client.company_id == payload.company_id,
        Client.client_code_num < start_from
    ).scalar()

    print(f"[Generate Code] (Bot) Максимальный 'нормальный' код (< {start_from}) = {max_normal_code}")

    next_code_to_check = start_from
    if max_normal_code is not None:
        next_code_to_check = max(max_normal_code + 1, start_from)

    print(f"[Generate Code] (Bot) Начинаем поиск свободного кода с: {next_code_to_check}")

    current_code = next_code_to_check
    while db.query(Client).filter(
        Client.company_id == payload.company_id,
        Client.client_code_num == current_code
    ).first():
        current_code += 1

    new_code_num = current_code
    print(f"[Generate Code] (Bot) Найден свободный код: {new_code_num}")

     # --- ИСПРАВЛЕНИЕ ПРЕФИКСА (Версия 2) ---
     # Приоритет:
     # 1. Код компании (WISH, KBE)
     # 2. Префикс из payload (если он не 'TG')
     # 3. 'TG'
    client_prefix = company.company_code # 1. Берем код компании

    if not client_prefix: # Если у компании нет кода
        if payload.client_code_prefix and payload.client_code_prefix != "TG":
             client_prefix = payload.client_code_prefix # 2. Берем из payload (если он не TG)
        else:
             client_prefix = "TG" # 3. Ставим TG

    print(f"[Bot Register] Установлен префикс: {client_prefix}")
     # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    # 5. Создание клиента
    new_client = Client(
        full_name=payload.full_name,
        phone=payload.phone,
        telegram_chat_id=payload.telegram_chat_id,
        company_id=payload.company_id,
        client_code_prefix=client_prefix, # <-- ИСПОЛЬЗУЕМ ИСПРАВЛЕННЫЙ ПРЕФИКС
        client_code_num=new_code_num
    )

    try:
        db.add(new_client)
        db.commit()
        db.refresh(new_client)
        print(f"[Bot Register] Успешно создан клиент ID={new_client.id}")

        background_tasks.add_task(
            notify_owner_of_new_client,
            company_id=payload.company_id,
            new_client_id=new_client.id, 
            registered_by="Telegram Бот"
        )

        return new_client
    except Exception as e_db:
        db.rollback()
        print(f"!!! [Bot Register] Ошибка БД: {e_db}")
        raise HTTPException(status_code=500, detail="Ошибка базы данных при создании клиента.")

# main.py (ДОБАВИТЬ ЛОГИРОВАНИЕ в get_client_by_id)

@app.get("/api/clients/{client_id}", tags=["Клиенты (Владелец)", "Telegram Bot"], response_model=ClientOut)
def get_client_by_id(
    client_id: int,
    company_id: int = Query(...), # Требуем company_id
    db: Session = Depends(get_db)
):
    """Получает данные одного клиента по ID для указанной компании."""
    # --- ДОБАВИТЬ ЛОГ ---
    print(f"--- [Get Client By ID] Запрос клиента ID={client_id} для компании ID={company_id} ---")
    # --- КОНЕЦ ДОБАВЛЕНИЯ ---
    client = db.query(Client).filter(
        Client.id == client_id,
        Client.company_id == company_id
    ).first()
    if not client:
        # --- ДОБАВИТЬ ЛОГ ---
        print(f"!!! [Get Client By ID] Клиент ID={client_id} НЕ НАЙДЕН в компании ID={company_id}.")
        # --- КОНЕЦ ДОБАВЛЕНИЯ ---
        raise HTTPException(status_code=404, detail=f"Клиент ID {client_id} не найден в компании ID {company_id}.")
    # --- ДОБАВИТЬ ЛОГ ---
    print(f"--- [Get Client By ID] Клиент ID={client_id} найден: {client.full_name} ---")
    # --- КОНЕЦ ДОБАВЛЕНИЯ ---
    return client

# main.py (ДОБАВИТЬ этот эндпоинт)

# --- НОВЫЙ ЭНДПОИНТ для получения настроек компании (для бота и ЛК) ---
# Модель для ответа
class SettingOut(BaseModel):
    key: str
    value: Optional[str]

@app.get("/api/bot/settings", tags=["Telegram Bot"], response_model=List[SettingOut])
def get_bot_company_settings(
    company_id: int = Query(...), # Обязательный ID компании
    keys: Optional[List[str]] = Query(None), # Необязательный список ключей для фильтрации
    db: Session = Depends(get_db)
    # Здесь не нужна аутентификация сотрудника, т.к. вызывается ботом/ЛК
):
    """
    Возвращает настройки для указанной компании.
    Можно фильтровать по списку ключей (параметр 'keys').
    """
    print(f"[Get Settings] Запрос настроек для Company ID: {company_id}. Ключи: {keys}")
    # Проверяем, существует ли компания
    company = db.query(Company.id).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"Компания с ID {company_id} не найдена.")

    # Запрашиваем настройки для этой компании
    query = db.query(Setting).filter(Setting.company_id == company_id)

    # Если передан список ключей, фильтруем по ним
    if keys:
        query = query.filter(Setting.key.in_(keys))

    settings = query.all()
    print(f"[Get Settings] Найдено настроек: {len(settings)}")

    # Преобразуем результат в нужный формат (можно использовать Pydantic напрямую)
    # FastAPI сделает это сам благодаря response_model=List[SettingOut]
    return settings

# --- КОНЕЦ НОВОГО ЭНДПОИНТА ---

# main.py (ДОБАВИТЬ ЭТОТ ЭНДПОИНТ)
@app.patch("/api/settings", tags=["Настройки"])
def update_company_settings(
    payload: List[SettingUpdate], # Принимаем список настроек для обновления
    employee: Employee = Depends(get_company_owner), # Только Владелец может менять
    db: Session = Depends(get_db)
):
    """Обновляет одну или несколько настроек для компании Владельца."""
    updated_count = 0
    errors = []
    company_id = employee.company_id
    print(f"[Update Settings] Владелец ID={employee.id} обновляет настройки для компании ID={company_id}")

    # Получаем текущие настройки компании из БД для сравнения
    current_settings = {s.key: s for s in db.query(Setting).filter(Setting.company_id == company_id).all()}

    for item in payload:
        key_to_update = item.key
        new_value = item.value # Может быть None или ""

        # Ищем существующую настройку по ключу
        setting_obj = current_settings.get(key_to_update)

        if setting_obj:
            # Если настройка существует, обновляем ее значение, если оно изменилось
            if setting_obj.value != new_value:
                print(f"  - Обновление ключа '{key_to_update}': '{setting_obj.value}' -> '{new_value}'")
                setting_obj.value = new_value
                updated_count += 1
            else:
                 print(f"  - Ключ '{key_to_update}': Значение не изменилось.")
        else:
            # Если настройки с таким ключом нет, СОЗДАЕМ ее
            print(f"  - Создание нового ключа '{key_to_update}' со значением '{new_value}'")
            new_setting = Setting(key=key_to_update, value=new_value, company_id=company_id)
            db.add(new_setting)
            updated_count += 1 # Считаем создание как обновление

    # Сохраняем все изменения в БД
    if updated_count > 0:
        try:
            db.commit()
            print(f"[Update Settings] Успешно обновлено/создано {updated_count} настроек.")
            return {"status": "ok", "message": f"Настройки ({updated_count} шт.) успешно сохранены."}
        except Exception as e:
            db.rollback()
            import traceback
            print(f"!!! [Update Settings] Ошибка при сохранении настроек:\n{traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"Ошибка базы данных при сохранении настроек: {e}")
    else:
        print("[Update Settings] Нет изменений для сохранения.")
        return {"status": "ok", "message": "Нет изменений для сохранения."}


# main.py (ДОБАВИТЬ ЭТОТ НОВЫЙ ЭНДПОИНТ)

@app.get("/api/bot/locations", tags=["Telegram Bot"], response_model=List[LocationOut])
def get_locations_for_bot(
    company_id: int = Query(...), # Обязательный ID компании от бота
    db: Session = Depends(get_db)
    # Нет аутентификации сотрудника
):
    """Возвращает список филиалов для указанной компании (для бота)."""
    # Проверяем, существует ли компания
    company = db.query(Company.id).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"Компания с ID {company_id} не найдена.")

    locations = db.query(Location).filter(Location.company_id == company_id).order_by(Location.name).all()
    print(f"INFO: [Bot Locations] Запрос филиалов для компании ID {company_id}. Найдено: {len(locations)}")
    return locations

# --- КОНЕЦ НОВОГО ЭНДПОИНТА ---

# --- КОНЕЦ БЛОКА УВЕДОМЛЕНИЙ ---

# main.py

# --- НОВЫЙ ЭНДПОИНТ ---
@app.get("/api/locations/{location_id}", tags=["Персонал (Владелец)", "Telegram Bot"], response_model=LocationOut)
def get_location_by_id(
    location_id: int,
    company_id: int = Query(...), # Обязательный ID компании от бота/ЛК
    db: Session = Depends(get_db)
    # Не требует аутентификации сотрудника
):
    """Возвращает данные одного филиала по ID (для бота/ЛК)."""
    
    print(f"[Get Location By ID] Запрос филиала ID={location_id} для компании ID={company_id}")
    location = db.query(Location).filter(
        Location.id == location_id,
        Location.company_id == company_id
    ).first()

    if not location:
        print(f"!!! [Get Location By ID] Филиал ID={location_id} НЕ НАЙДЕН в компании ID={company_id}.")
        raise HTTPException(status_code=404, detail="Филиал не найден в указанной компании.")
    
    return location
# --- КОНЕЦ НОВОГО ЭНДПОИНТА ---

# main.py

# --- Добавь эти Pydantic модели (например, после BotClientRegisterPayload) ---
class BotIdentifyCompanyPayload(BaseModel):
    token: str

class BotIdentifyCompanyResponse(BaseModel):
    company_id: int
    company_name: str
# --- Конец Pydantic моделей ---


# --- ДОБАВЬ ЭТОТ НОВЫЙ ЭНДПОИНТ ---
@app.post("/api/bot/identify_company", tags=["Telegram Bot"], response_model=BotIdentifyCompanyResponse)
def identify_company_by_token(
    payload: BotIdentifyCompanyPayload,
    db: Session = Depends(get_db)
):
    """
    Идентифицирует компанию по токену бота.
    Вызывается ботом при запуске, чтобы узнать, к какой компании он относится.
    """
    print(f"[Bot Identify Company] Поиск компании по токену: ...{payload.token[-6:]}")
    
    # Ищем компанию с этим токеном в БД
    company = db.query(Company).filter(
        Company.telegram_bot_token == payload.token
    ).first()

    if not company:
        print(f"!!! [Bot Identify Company] Компания с токеном ...{payload.token[-6:]} не найдена.")
        raise HTTPException(
            status_code=404, 
            detail="Компания с таким токеном Telegram-бота не найдена в системе."
        )
    
    if not company.is_active:
         print(f"!!! [Bot Identify Company] Компания {company.name} (ID: {company.id}) не активна.")
         raise HTTPException(
            status_code=403, 
            detail="Компания, к которой привязан этот бот, не активна."
        )

    print(f"[Bot Identify Company] Токен соответствует компании: {company.name} (ID: {company.id})")
    return BotIdentifyCompanyResponse(
        company_id=company.id, 
        company_name=company.name
    )
# --- КОНЕЦ НОВОГО ЭНДПОИНТА ---

# main.py

# --- Добавь эти Pydantic модели (например, после BotClientRegisterPayload) ---
class BotBroadcastPayload(BaseModel):
    text: str = Field(..., min_length=1)
    photo_file_id: Optional[str] = None # <-- ДОБАВЛЕНО

class BotBroadcastResponse(BaseModel):
    status: str
    message: str
    sent_to_clients: int
# --- Конец Pydantic моделей ---


# --- ДОБАВЬ ЭТОТ НОВЫЙ ЭНДПОИНТ ---
@app.post("/api/bot/broadcast", tags=["Telegram Bot"], response_model=BotBroadcastResponse)
async def bot_broadcast( # <--- Убедись, что 'async' здесь есть
    payload: BotBroadcastPayload,
    # Требуем, чтобы запрос делал Владелец
    employee: Employee = Depends(get_company_owner), 
    db: Session = Depends(get_db)
):
    """
    Выполняет рассылку сообщения всем клиентам компании, привязавшим бота.
    Вызывается ботом, аутентифицируется по X-Employee-ID Владельца.
    """
    company_id = employee.company_id
    print(f"[Broadcast] Владелец {employee.full_name} (ID: {employee.id}) запускает рассылку для компании ID: {company_id}")

    # 1. Находим токен бота компании (берем из модели Company)
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company or not company.telegram_bot_token:
        print(f"!!! [Broadcast] Ошибка: Не найден токен бота для компании ID: {company_id}")
        raise HTTPException(status_code=400, detail="Токен Telegram-бота не настроен для этой компании в админ-панели.")

    bot_token = company.telegram_bot_token

    # 2. СОХРАНЯЕМ РАССЫЛКУ В БД (ШАГ 2)
    try:
        new_broadcast = Broadcast(
            text=payload.text,
            photo_file_id=payload.photo_file_id,
            company_id=company_id
        )
        db.add(new_broadcast)
        db.commit()
        db.refresh(new_broadcast)
        broadcast_id = new_broadcast.id # Получаем ID новой рассылки
        print(f"[Broadcast] Рассылка сохранена в БД, ID: {broadcast_id}")
    except Exception as e:
        db.rollback()
        logger.error(f"!!! [Broadcast] Ошибка сохранения рассылки в БД: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка базы данных при сохранении рассылки.")


    # 3. Находим всех клиентов (ШАГ 3)
    clients_to_notify = db.query(Client).filter(
        Client.company_id == company_id,
        Client.telegram_chat_id != None
    ).all()

    if not clients_to_notify:
        return BotBroadcastResponse(status="ok", message="Рассылка сохранена, но нет клиентов для отправки.", sent_to_clients=0)

    # 4. Запускаем асинхронную рассылку (ШАГ 4)
    tasks = []
    bot = telegram.Bot(token=bot_token)
    
    for client in clients_to_notify:
        # Создаем задачу на отправку
        tasks.append(
            send_telegram_message(
                token=bot_token, 
                chat_id=client.telegram_chat_id,
                text=payload.text, 
                photo_id=payload.photo_file_id,
                broadcast_id=broadcast_id # <-- ДОБАВЛЕНО (ID для кнопок)
            )
        )
    
    # Ожидаем завершения всех отправок
    await asyncio.gather(*tasks)

    sent_count = len(clients_to_notify)
    print(f"[Broadcast] Рассылка для компании ID: {company_id} завершена. Отправлено: {sent_count} сообщений.")
    
    return BotBroadcastResponse(
        status="ok",
        message=f"Рассылка успешно отправлена.",
        sent_to_clients=sent_count
    )
# --- КОНЕЦ НОВОГО ЭНДПОИНТА ---

# --- Pydantic модели для Реакций ---
class BotReactionPayload(BaseModel):
    client_id: int
    broadcast_id: int
    reaction_type: str
    company_id: int

class BotReactionResponse(BaseModel):
    status: str
    message: str
    new_counts: dict # {"like": 10, "dislike": 2}

# --- Pydantic модели для Отчета по Рассылкам ---
class BroadcastStatItem(BaseModel):
    id: int
    sent_at: datetime
    text: str
    photo_file_id: Optional[str] = None
    like_count: int = 0
    dislike_count: int = 0
    # (Если добавляли другие реакции, добавьте счетчики сюда)

class BroadcastReportResponse(BaseModel):
    status: str
    report: List[BroadcastStatItem]

class ReactionDetailItem(BaseModel):
    client_id: int
    # full_name: str # Убираем
    # phone: str # Убираем
    reaction_type: str
    created_at: datetime
    client: ClientOut # <-- ДОБАВЛЯЕМ вложенную модель клиента

    class Config:
        from_attributes = True

class BroadcastReactionDetailResponse(BaseModel):
    status: str
    broadcast_id: int
    reactions: List[ReactionDetailItem]

    class Config: # <-- Убедись, что этот блок есть
        from_attributes = True

# --- НОВЫЙ ЭНДПОИНТ ДЛЯ ЛОВЛИ РЕАКЦИЙ ---
@app.post("/api/bot/react", tags=["Telegram Bot"], response_model=BotReactionResponse)
def handle_bot_reaction(
    payload: BotReactionPayload,
    db: Session = Depends(get_db)
):
    """
    Обрабатывает нажатие кнопки реакции от клиента.
    Сохраняет реакцию и возвращает новые счетчики.
    """
    print(f"[Bot Reaction] Получена реакция: {payload.dict()}")
    
    # 1. Проверяем, существует ли рассылка
    broadcast = db.query(Broadcast.id).filter(
        Broadcast.id == payload.broadcast_id,
        Broadcast.company_id == payload.company_id
    ).first()
    if not broadcast:
        raise HTTPException(status_code=404, detail="Рассылка не найдена.")
        
    # 2. Проверяем, существует ли клиент
    client = db.query(Client.id).filter(
        Client.id == payload.client_id,
        Client.company_id == payload.company_id
    ).first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден.")

    # 3. Ищем существующую реакцию этого клиента на этот пост
    existing_reaction = db.query(BroadcastReaction).filter(
        BroadcastReaction.broadcast_id == payload.broadcast_id,
        BroadcastReaction.client_id == payload.client_id
    ).first()

    if existing_reaction:
        # Если реакция уже есть
        if existing_reaction.reaction_type == payload.reaction_type:
            # Пользователь нажал ту же кнопку - УДАЛЯЕМ реакцию
            print(f"[Bot Reaction] Клиент {payload.client_id} УДАЛИЛ реакцию '{payload.reaction_type}'")
            db.delete(existing_reaction)
        else:
            # Пользователь сменил реакцию - ОБНОВЛЯЕМ
            print(f"[Bot Reaction] Клиент {payload.client_id} СМЕНИЛ реакцию на '{payload.reaction_type}'")
            existing_reaction.reaction_type = payload.reaction_type
    else:
        # Если реакции нет - СОЗДАЕМ
        print(f"[Bot Reaction] Клиент {payload.client_id} ДОБАВИЛ реакцию '{payload.reaction_type}'")
        new_reaction = BroadcastReaction(
            broadcast_id=payload.broadcast_id,
            client_id=payload.client_id,
            reaction_type=payload.reaction_type
        )
        db.add(new_reaction)

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"!!! [Bot Reaction] Ошибка БД при сохранении реакции: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка базы данных при сохранении реакции.")

    # 4. Считаем и возвращаем НОВЫЕ итоги для этой рассылки
    reaction_counts = db.query(
        BroadcastReaction.reaction_type, 
        func.count(BroadcastReaction.id)
    ).filter(
        BroadcastReaction.broadcast_id == payload.broadcast_id
    ).group_by(
        BroadcastReaction.reaction_type
    ).all()
    
    # Преобразуем в словарь {"like": 10, "dislike": 2}
    new_counts = {reaction_type: count for reaction_type, count in reaction_counts}
    print(f"[Bot Reaction] Новые счетчики для broadcast {payload.broadcast_id}: {new_counts}")

    return BotReactionResponse(
        status="ok",
        message="Реакция обработана",
        new_counts=new_counts
    )

# main.py

# --- НОВЫЙ ЭНДПОИНТ ДЛЯ СТАТИСТИКИ РАССЫЛОК (ВЛАДЕЛЕЦ) ---
@app.get("/api/reports/broadcasts", tags=["Отчеты", "Telegram Bot"], response_model=BroadcastReportResponse)
def get_broadcast_report(
    employee: Employee = Depends(get_company_owner), # Только Владелец
    db: Session = Depends(get_db)
):
    """
    Возвращает статистику по реакциям на все рассылки компании.
    """
    company_id = employee.company_id
    print(f"[Broadcast Report] Владелец {employee.id} запросил статистику для компании {company_id}")

    # 1. Сначала считаем все реакции, сгруппированные по broadcast_id
    reaction_counts_query = db.query(
        BroadcastReaction.broadcast_id,
        BroadcastReaction.reaction_type,
        func.count(BroadcastReaction.id).label('count')
    ).join(Broadcast, Broadcast.id == BroadcastReaction.broadcast_id).filter(
        Broadcast.company_id == company_id # Убеждаемся, что реакции из нашей компании
    ).group_by(
        BroadcastReaction.broadcast_id,
        BroadcastReaction.reaction_type
    )
    
    reaction_counts_raw = reaction_counts_query.all()
    
    # Преобразуем в удобный словарь:
    # { 123: {"like": 10, "dislike": 2}, 124: {"like": 5} }
    stats_map = {}
    for broadcast_id, reaction_type, count in reaction_counts_raw:
        if broadcast_id not in stats_map:
            stats_map[broadcast_id] = {}
        stats_map[broadcast_id][reaction_type] = count
        
    print(f"[Broadcast Report] Подсчитаны реакции: {stats_map}")

    # 2. Теперь получаем сами рассылки (например, 10 последних)
    broadcasts = db.query(Broadcast).filter(
        Broadcast.company_id == company_id
    ).order_by(
        Broadcast.sent_at.desc()
    ).limit(10).all() # Ограничим 10-ю последними

    # 3. Собираем итоговый отчет
    report_list = []
    for b in broadcasts:
        counts = stats_map.get(b.id, {}) # Получаем счетчики для этой рассылки
        
        stat_item = BroadcastStatItem(
            id=b.id,
            sent_at=b.sent_at,
            text=b.text,
            photo_file_id=b.photo_file_id,
            like_count=counts.get("like", 0),
            dislike_count=counts.get("dislike", 0)
            # (добавьте 'fire_count' и т.д., если нужно)
        )
        report_list.append(stat_item)

    return BroadcastReportResponse(status="ok", report=report_list)

# --- НОВЫЙ ЭНДПОИНТ ДЛЯ ДЕТАЛИЗАЦИИ РЕАКЦИЙ (ВЛАДЕЛЕЦ) ---
# --- НОВЫЙ ЭНДПОИНТ ДЛЯ ДЕТАЛИЗАЦИИ РЕАКЦИЙ (ВЛАДЕЛЕЦ) ---
@app.get("/api/reports/broadcast/{broadcast_id}/reactions", tags=["Отчеты", "Telegram Bot"], response_model=BroadcastReactionDetailResponse)
def get_broadcast_reaction_details(
    broadcast_id: int,
    employee: Employee = Depends(get_company_owner), # Только Владелец
    db: Session = Depends(get_db)
):
    """
    (ИСПРАВЛЕННАЯ ВЕРСИЯ)
    Возвращает список клиентов, которые отреагировали на конкретную рассылку.
    """
    company_id = employee.company_id
    print(f"[Broadcast Details] Владелец {employee.id} запросил реакции для {broadcast_id}")

    # 1. Проверяем, существует ли рассылка
    broadcast = db.query(Broadcast.id).filter(
        Broadcast.id == broadcast_id,
        Broadcast.company_id == company_id
    ).first()
    if not broadcast:
        raise HTTPException(status_code=404, detail="Рассылка не найдена в вашей компании.")

    # 2. Запрашиваем реакции, объединяя с клиентами
    reactions_query = db.query(
        BroadcastReaction
    ).options(
        joinedload(BroadcastReaction.client) # Подгружаем данные клиента
    ).filter(
        BroadcastReaction.broadcast_id == broadcast_id
    ).order_by(
        BroadcastReaction.created_at.desc()
    ).all()

    # 3. Формируем ответ (ВРУЧНУЮ) - Это самый надежный способ
    response_reactions = []
    for reaction in reactions_query:
        if reaction.client: # Убедимся, что клиент не был удален
            response_reactions.append(
                ReactionDetailItem(
                    client_id=reaction.client_id,
                    reaction_type=reaction.reaction_type,
                    created_at=reaction.created_at,
                    # Pydantic сам преобразует 'reaction.client' (SQLAlchemy)
                    # в 'ClientOut', т.к. у ClientOut есть from_attributes
                    client=reaction.client
                )
            )
        else:
            # Если клиент был удален, а реакция осталась
            logger.warning(f"[Broadcast Details] Реакция ID {reaction.id} ссылается на удаленного клиента ID {reaction.client_id}")

    return BroadcastReactionDetailResponse(
        status="ok",
        broadcast_id=broadcast_id,
        reactions=response_reactions # Передаем вручную собранный список
    )

# --- НОВЫЙ ЭНДПОИНТ ДЛЯ ВЫХОДА ИЗ СИСТЕМЫ (ОТРЫВКИ) ---
@app.post("/api/bot/unlink", tags=["Telegram Bot"])
def unlink_bot_user(
    payload: BotUnlinkPayload,
    db: Session = Depends(get_db)
):
    """
    Отвязывает Telegram Chat ID от профиля клиента в указанной компании.
    Вызывается ботом при команде /logout.
    """
    chat_id = payload.telegram_chat_id
    company_id = payload.company_id
    
    logger.info(f"[Bot Unlink] Попытка отвязки Chat ID {chat_id} от компании {company_id}")

    # Находим клиента, к которому привязан этот Chat ID
    client_to_unlink = db.query(Client).filter(
        Client.company_id == company_id,
        Client.telegram_chat_id == chat_id
    ).first()

    if not client_to_unlink:
        logger.warning(f"[Bot Unlink] Chat ID {chat_id} не был ни к кому привязан. Игнорируем.")
        # Все равно возвращаем успех, т.к. цель (отвязка) достигнута
        return {"status": "ok", "message": "Аккаунт не был привязан."}

    try:
        # --- ГЛАВНОЕ ДЕЙСТВИЕ ---
        client_to_unlink.telegram_chat_id = None
        db.commit()
        # --- КОНЕЦ ГЛАВНОГО ДЕЙСТВИЯ ---
        
        logger.info(f"[Bot Unlink] Chat ID {chat_id} успешно отвязан от клиента ID {client_to_unlink.id} ({client_to_unlink.full_name})")
        return {"status": "ok", "message": "Аккаунт успешно отвязан."}
        
    except Exception as e:
        db.rollback()
        logger.error(f"!!! [Bot Unlink] Ошибка БД при отвязке Chat ID {chat_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка базы данных при отвязке аккаунта.")
# --- КОНЕЦ НОВОГО ЭНДПОИНТА ---

# --- НОВЫЙ ЭНДПОИНТ ДЛЯ "МАГИИ" БОТА ---
class BotClaimOrderPayload(BaseModel):
    track_code: str
    client_id: int
    company_id: int

@app.post("/api/bot/claim_order", tags=["Telegram Bot"], response_model=OrderOut)
def claim_order_from_bot(
    payload: BotClaimOrderPayload,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Ищет невостребованный заказ по трек-коду и назначает его клиенту.
    УВЕДОМЛЯЕТ ВЛАДЕЛЬЦА.
    """
    logger.info(f"[Bot Claim] Клиент ID={payload.client_id} пытается забрать трек-код '{payload.track_code}'")

    # 1. Проверяем клиента
    client = db.query(Client).filter(Client.id == payload.client_id, Client.company_id == payload.company_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден.")

    # 2. Ищем невостребованный заказ
    order_to_claim = db.query(Order).filter(
        Order.track_code == payload.track_code,
        Order.company_id == payload.company_id,
        Order.client_id == None 
    ).first()

    if not order_to_claim:
        logger.warning(f"[Bot Claim] Невостребованный заказ '{payload.track_code}' не найден.")
        raise HTTPException(status_code=404, detail="Невостребованный заказ с таким трек-кодом не найден.")

    # 3. Назначаем заказ клиенту
    try:
        order_to_claim.client_id = payload.client_id
        order_to_claim.status = "В пути" # Сразу ставим "В пути"

        # (Задача 3) Добавляем историю
        history_entry = OrderHistory(
            order_id=order_to_claim.id,
            status="В пути",
            employee_id=None # Присвоено ботом
        )
        db.add(history_entry)

        db.commit()

        # --- Уведомление КЛИЕНТУ (остается) ---
        background_tasks.add_task(
            generate_and_send_notification,
            client=client,
            new_status="В пути",
            track_codes=[order_to_claim.track_code]
        )

        # --- НОВОЕ: Уведомление ВЛАДЕЛЬЦУ ---
        message = (
            f"🔔 <b>Заказ присвоен (Магия)</b>\n\n"
            f"Клиент: <b>{client.full_name}</b>\n"
            f"Присвоил невостребованный заказ:\n"
            f"Трек-код: <code>{order_to_claim.track_code}</code>"
        )
        background_tasks.add_task(
            notify_owners,
            company_id=payload.company_id,
            message_text=message
        )
        # --- КОНЕЦ УВЕДОМЛЕНИЯ ---

        db.refresh(order_to_claim, attribute_names=['client']) 
        logger.info(f"[Bot Claim] УСПЕХ: Заказ ID={order_to_claim.id} назначен клиенту ID={payload.client_id}")
        return order_to_claim

    except Exception as e:
        db.rollback()
        logger.error(f"!!! [Bot Claim] Ошибка БД при назначении заказа: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка базы данных при назначении заказа.")
# --- КОНЕЦ НОВОГО ЭНДПОИНТА "МАГИИ" ---

# --- 7. УТИЛИТЫ ---

# Этот эндпоинт больше не нужен, т.к. таблицы создаются при запуске
# @app.get("/api/create_tables", tags=["Утилиты"])
# def create_tables_endpoint():
#     try:  
#         Base.metadata.create_all(bind=engine)
#         return {"status": "ok", "message": "Таблицы успешно созданы/обновлены!"}
#     except Exception as e:  
#         raise HTTPException(status_code=500, detail=f"Ошибка: {e}")

# Этот эндпоинт нам пока не нужен
# @app.get("/api/order_statuses", tags=["Утилиты"])
# def get_order_statuses():  
#     return {"status": "ok", "statuses": ORDER_STATUSES}

# === НОВАЯ УНИВЕРСАЛЬНАЯ ФУНКЦИЯ УВЕДОМЛЕНИЯ ВЛАДЕЛЬЦЕВ ===
async def notify_owners(company_id: int, message_text: str):
    """
    (ФОНОВАЯ ЗАДАЧA) Отправляет сообщение всем Владельцам компании.
    САМА СОЗДАЕТ СЕССИЮ.
    """
    db = SessionLocal() # <--- СОЗДАЕМ СВОЮ СЕССИЮ
    try:
        logger.info(f"[Notify Owner] (BG Task) Поиск Владельцев для компании {company_id}...")

        # 1. Находим токен бота компании
        company = db.query(Company).filter(Company.id == company_id).first()
        if not company or not company.telegram_bot_token:
            logger.warning(f"[Notify Owner] Не найден токен для компании {company_id}.")
            return

        # 2. Находим сотрудников-Владельцев
        owner_employees = db.query(Employee).join(Role).filter(
            Employee.company_id == company_id,
            Role.name == "Владелец",
            Employee.is_active == True
        ).all()

        if not owner_employees:
            logger.warning(f"[Notify Owner] Не найдены активные Владельцы для {company_id}.")
            return

        # 3. Находим их Клиентские аккаунты (для chat_id)
        owner_names = [emp.full_name for emp in owner_employees]
        owner_clients = db.query(Client).filter(
            Client.company_id == company_id,
            Client.full_name.in_(owner_names),
            Client.telegram_chat_id != None
        ).all()

        if not owner_clients:
            logger.warning(f"[Notify Owner] Найдены Владельцы ({owner_names}), но их Telegram не привязан.")
            return

        # 4. Отправляем
        tasks = []
        for owner in owner_clients:
            tasks.append(
                send_telegram_message(
                    token=company.telegram_bot_token,
                    chat_id=owner.telegram_chat_id,
                    text=message_text
                )
            )

        await asyncio.gather(*tasks) # <-- ДОБАВЛЕНА ЗВЕЗДОЧКА
        print(f"[Notify Owner] Уведомления Владельцам ( {len(tasks)} ) отправлены.")

    except Exception as e:
        logger.error(f"!!! [Notify Owner] Ошибка в фоновой задаче: {e}", exc_info=True)
    finally:
        db.close()
# === КОНЕЦ УНИВЕРСАЛЬНОЙ ФУНКЦИИ ===

@app.on_event("startup")
def on_startup():
    """Создает все таблицы при запуске, если их нет."""
    try:
        Base.metadata.create_all(bind=engine)
        print("Таблицы успешно проверены/созданы.")
    except Exception as e:
        print(f"ОШИБКА при создании таблиц: {e}")

@app.post("/api/bot/bulk_add_orders", tags=["Telegram Bot"], response_model=BotBulkAddResponse)
def bulk_add_orders_from_bot(
    payload: BotBulkAddPayload,
    background_tasks: BackgroundTasks, # <-- ДОБАВЛЕНО
    db: Session = Depends(get_db)
):
    """
    Массово создает ИЛИ ПРИСВАИВАЕТ заказы от клиента из бота.
    УВЕДОМЛЯЕТ ВЛАДЕЛЬЦА.
    """
    logger.info(f"[Bot Bulk Add] Клиент ID={payload.client_id} массово добавляет {len(payload.items)} заказов.")

    # 1. Проверяем клиента и филиал
    client = db.query(Client).filter(Client.id == payload.client_id, Client.company_id == payload.company_id).first()
    location = db.query(Location).filter(Location.id == payload.location_id, Client.company_id == payload.company_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден.")
    if not location:
        raise HTTPException(status_code=404, detail="Филиал не найден.")

    # 2. Получаем ВСЕ существующие заказы компании (как {трек: объект})
    existing_orders_map = {
        o.track_code: o for o in db.query(Order).filter(Order.company_id == payload.company_id)
    }

    created_count = 0
    assigned_count = 0 
    skipped_count = 0
    errors = []

    orders_to_add = [] # Список для НОВЫХ
    history_entries_to_add = [] 

    # --- НОВОЕ: Списки для уведомления Владельцу ---
    created_tracks_for_notify = []
    assigned_tracks_for_notify = []
    # --- КОНЕЦ ---

    # 3. Обрабатываем каждый заказ в списке
    for item in payload.items:
        track_code = item.track_code.strip()

        if not track_code:
            skipped_count += 1
            continue

        existing_order = existing_orders_map.get(track_code)

        if existing_order:
            # --- ЗАКАЗ УЖЕ СУЩЕСТВУЕТ ---
            if existing_order.client_id is None:
                # "МАГИЯ" - ПРИСВАИВАЕМ ЗАКАЗ
                logger.info(f"[Bot Bulk Add] Присвоение {track_code} клиенту {payload.client_id}")
                existing_order.client_id = payload.client_id
                existing_order.comment = item.comment 
                existing_order.location_id = payload.location_id 

                history_entries_to_add.append(
                    OrderHistory(order_id=existing_order.id, status=existing_order.status, employee_id=None)
                )
                assigned_count += 1
                assigned_tracks_for_notify.append(track_code) # <-- Собираем для отчета
            else:
                logger.warning(f"[Bot Bulk Add] Пропуск дубликата: {track_code}")
                skipped_count += 1
        else:
            # --- ЗАКАЗ НОВЫЙ ---
            new_order = Order(
                client_id=payload.client_id,
                track_code=track_code,
                comment=item.comment,
                status="В обработке",
                purchase_type="Доставка",
                party_date=date.today(),
                company_id=payload.company_id,
                location_id=payload.location_id
            )
            orders_to_add.append(new_order)
            created_count += 1
            created_tracks_for_notify.append(track_code) # <-- Собираем для отчета

    # 4. Сохраняем все изменения ОДНОЙ транзакцией
    if orders_to_add or assigned_count > 0: # Если есть что сохранять
        try:
            if orders_to_add:
                db.bulk_save_objects(orders_to_add, return_defaults=True)
                logger.info(f"[Bot Bulk Add] Создано {created_count} новых заказов.")

                for order in orders_to_add:
                    history_entries_to_add.append(
                        OrderHistory(order_id=order.id, status=order.status, employee_id=None)
                    )

            if history_entries_to_add:
                db.bulk_save_objects(history_entries_to_add)

            db.commit()

            # --- НОВОЕ: Уведомление Владельцу (одно, общее) ---
            message = f"🔔 <b>Клиент добавил заказы (Бот)</b>\n\nКлиент: <b>{client.full_name}</b>\n\n"
            if created_count > 0:
                message += f"✔️ Новых добавлено: <b>{created_count} шт.</b>\n"
            if assigned_count > 0:
                message += f"✨ Присвоено невостребованных: <b>{assigned_count} шт.</b>\n"

            background_tasks.add_task(
                notify_owners,
                company_id=payload.company_id,
                message_text=message
            )
            # --- КОНЕЦ УВЕДОМЛЕНИЯ ---

        except Exception as e:
            db.rollback()
            logger.error(f"!!! [Bot Bulk Add] Ошибка БД: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Ошибка базы данных: {e}")

    return BotBulkAddResponse(
        created=created_count,
        assigned=assigned_count,
        skipped=skipped_count,
        errors=errors
    )

@app.get("/", tags=["Утилиты"])
def read_root():  
    return {"status": "ok", "message": "Сервер Карго CRM (Multi-Tenant) запущен!"}

async def notify_owner_of_new_client(company_id: int, new_client_id: int, registered_by: str):
    """
    (ФОНОВАЯ ЗАДАЧA) Форматирует сообщение о регистрации и вызывает notify_owners.
    """
    db = SessionLocal()
    try:
        # Нам нужно быстро получить данные клиента
        new_client = db.query(Client).filter(Client.id == new_client_id).first()
        if not new_client:
             logger.warning(f"[Notify Owner] (New Client) Не найден клиент ID {new_client_id}.")
             return

        # 1. Форматируем сообщение
        client_code = f"{new_client.client_code_prefix}{new_client.client_code_num}"
        message = (
            f"🔔 <b>Новый клиент!</b>\n\n"
            f"Зарегистрирован (через: {registered_by}):\n"
            f"<b>ФИО:</b> {new_client.full_name}\n"
            f"<b>Телефон:</b> <code>{new_client.phone}</code>\n"
            f"<b>Код:</b> {client_code}\n"
        )

        # 2. Вызываем универсальную функцию
        await notify_owners(company_id=company_id, message_text=message)

    except Exception as e:
        logger.error(f"!!! [Notify Owner] (New Client) Ошибка: {e}", exc_info=True)
    finally:
        db.close()