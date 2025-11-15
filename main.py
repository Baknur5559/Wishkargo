import os
from datetime import date, datetime
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, status, Query
from sqlalchemy import create_engine, func, or_, String, cast, Date as SQLDate
from sqlalchemy.orm import sessionmaker, Session, joinedload
from pydantic import BaseModel
from typing import List, Optional

from fastapi.middleware.cors import CORSMiddleware
# --- ИСПРАВЛЕНИЕ №1: Добавляем 'Setting' в импорты ---
from models import Base, Client, Order, Role, Permission, Employee, ExpenseType, Shift, Expense, Setting
import asyncio
import telegram

# --- 1. НАСТРОЙКА ---
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
# --- ИСПРАВЛЕНИЕ №2: Загружаем токен бота ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") 

if not DATABASE_URL:
    raise RuntimeError("Не найден ключ DATABASE_URL в файле .env")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
app = FastAPI(title="Cargo CRM API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ORDER_STATUSES = ["В обработке", "Ожидает выкупа", "Выкуплен", "На складе в Китае", "В пути", "На складе в КР", "Готов к выдаче", "Выдан"]
WIPE_PASSWORD = "baha_555999_"
CONFIG = {
    "price_per_kg_usd": 5.5,
    "exchange_rate_usd": 87.5,
    "card_payment_types": ["MBank", "Optima", "DemirBank", "Другое"]
}

async def send_telegram_message(chat_id: str, text: str):
    if not TELEGRAM_BOT_TOKEN:
        print("WARNING: Telegram bot token не найден.")
        return
    try:
        bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=chat_id, text=text, parse_mode='HTML')
    except Exception as e:
        print(f"Ошибка при отправке сообщения в Telegram: {e}")

async def generate_and_send_notification(db: Session, client: Client, new_status: str, track_codes: List[str]):
    if not client.telegram_chat_id:
        return

    track_codes_str = "\n".join([f"<code>{code}</code>" for code in track_codes])

    # Загружаем контакты из настроек (теперь это будет работать)
    address_setting = db.query(Setting).filter(Setting.key == 'bishkek_office_address').first()
    phone_setting = db.query(Setting).filter(Setting.key == 'contact_phone').first()

    address = address_setting.value if address_setting and address_setting.value else "4-й микрорайон, 7/2"
    phone = phone_setting.value if phone_setting and phone_setting.value else "+996 555 36-63-86"

    # Генерируем ссылку на ЛК
    secret_token = f"CLIENT-{client.id}-SECRET"
    lk_link = f"http://127.0.0.1:5500/lk.html?token={secret_token}"

    # Шаблоны сообщений в зависимости от статуса
    message = ""
    if new_status == "Готов к выдаче":
        message = (
            f"Здравствуйте, <b>{client.full_name}</b>! 👋\n\n"
            f"Ура! Есть отличные новости по вашим заказам! 📦✨\n\n"
            f"Ваши посылки с трек-кодами:\n{track_codes_str}\n\n"
            f"...уже приехали и очень ждут вас! Их статус изменился на: ✅ <b>Готов к выдаче</b> ✅\n\n"
            f"<b>Что дальше?</b>\n\n"
            f"📍 <b>Забрать лично:</b> Ждём вас в нашем офисе по адресу:\n{address}\n\n"
            f"📞 <b>Остались вопросы?</b> Смело звоните:\n<code>{phone}</code>\n\n"
            f"💻 <b>Полный контроль в личном кабинете:</b> <a href='{lk_link}'>Перейти в ЛК</a>"
        )
    else: # Стандартное уведомление для всех других статусов
        message = (
            f"🚚 Статус Ваших заказов изменился!\n\n"
            f"<b>Новый статус:</b> {new_status}\n\n"
            f"<b>Трек-коды:</b>\n{track_codes_str}"
        )

    await send_telegram_message(chat_id=client.telegram_chat_id, text=message)

# --- 2. DEPENDENCY ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- 3. Pydantic МОДЕЛИ ---
# (Этот блок остается без изменений)
class ClientCreate(BaseModel): full_name: str; phone: str; client_code_prefix: Optional[str] = None
class ClientUpdate(BaseModel): full_name: Optional[str] = None; phone: Optional[str] = None; client_code_prefix: Optional[str] = None; client_code_num: Optional[int] = None; status: Optional[str] = None
class BulkClientItem(BaseModel): full_name: str; phone: str; client_code: Optional[str] = None
class WipePayload(BaseModel): password: str
class OrderCreate(BaseModel):
    track_code: str
    purchase_type: str
    client_id: int
    comment: Optional[str] = None
    buyout_item_cost_cny: Optional[float] = None
    buyout_rate_for_client: Optional[float] = None
    buyout_commission_percent: Optional[float] = None
class OrderStatusUpdate(BaseModel): status: str
class BulkOrderItem(BaseModel):
    track_code: str
    client_code: Optional[str] = None
    phone: Optional[str] = None
    comment: Optional[str] = None
class BulkOrderImportPayload(BaseModel): orders_data: List[BulkOrderItem]; party_date: Optional[date] = None
class OrderActionPayload(BaseModel): password: str; reason: Optional[str] = None
class BulkActionPayload(BaseModel): action: str; order_ids: List[int]; new_status: Optional[str] = None; password: Optional[str] = None; new_party_date: Optional[date] = None; buyout_actual_rate: Optional[float] = None
class IssueOrderItem(BaseModel): order_id: int; weight_kg: float
class IssuePayload(BaseModel): orders: List[IssueOrderItem]; price_per_kg_usd: float; exchange_rate_usd: float; paid_cash: float; paid_card: float; card_payment_type: Optional[str] = None
class OrderPartyDateUpdate(BaseModel): party_date: date; password: str
class LoginPayload(BaseModel): password: str
class EmployeeCreate(BaseModel): full_name: str; password: str; role_id: int
class EmployeeUpdate(BaseModel): full_name: Optional[str] = None; password: Optional[str] = None; role_id: Optional[int] = None; is_active: Optional[bool] = None
class RoleCreate(BaseModel): name: str
class RolePermissionsUpdate(BaseModel): permission_ids: List[int]
class OrderUpdate(BaseModel):
    track_code: Optional[str] = None
    buyout_actual_rate: Optional[float] = None
    client_id: Optional[int] = None
class ExpenseCreate(BaseModel):
    expense_type_id: int
    amount: float
    notes: Optional[str] = None
class ExpenseUpdate(BaseModel):
    expense_type_id: Optional[int] = None
    amount: Optional[float] = None
    notes: Optional[str] = None
class ShiftOpenPayload(BaseModel):
    employee_id: int
    starting_cash: float
    exchange_rate_usd: float
    price_per_kg_usd: float
class ShiftClosePayload(BaseModel):
    closing_cash: float

# --- 4. API-ЭНДПОИНТЫ ---
# (Весь этот блок остается без изменений, так как ошибка была в импортах и настройках)

# --- АУТЕНТИФИКАЦИЯ И НАСТРОЙКА ---
@app.post("/login", tags=["Аутентификация"])
def login(payload: LoginPayload, db: Session = Depends(get_db)):
    employee = db.query(Employee).options(
        joinedload(Employee.role).joinedload(Role.permissions)
    ).filter(Employee.password == payload.password, Employee.is_active == True).first()

    if not employee:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный пароль или сотрудник неактивен.")
    permissions = [p.codename for p in employee.role.permissions]
    return {"status": "ok", "employee": {"id": employee.id, "full_name": employee.full_name, "role": employee.role.name, "permissions": permissions}}

# --- УПРАВЛЕНИЕ ПЕРСОНАЛОМ (ДЛЯ ВЛАДЕЛЬЦА) ---
@app.get("/employees", tags=["Управление персоналом"])
def get_employees(db: Session = Depends(get_db)):
    employees = db.query(Employee).options(joinedload(Employee.role)).order_by(Employee.full_name).all()
    return {"status": "ok", "employees": employees}

@app.get("/roles", tags=["Управление персоналом"])
def get_roles(db: Session = Depends(get_db)):
    roles = db.query(Role).order_by(Role.name).all()
    return {"status": "ok", "roles": roles}

@app.post("/employees", tags=["Управление персоналом"])
def create_employee(employee_data: EmployeeCreate, db: Session = Depends(get_db)):
    new_employee = Employee(**employee_data.dict())
    db.add(new_employee)
    db.commit()
    db.refresh(new_employee)
    return {"status": "ok", "message": "Сотрудник успешно создан.", "employee": new_employee}

@app.patch("/employees/{employee_id}", tags=["Управление персоналом"])
def update_employee(employee_id: int, employee_data: EmployeeUpdate, db: Session = Depends(get_db)):
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Сотрудник не найден.")
    
    update_data = employee_data.dict(exclude_unset=True)
    if 'is_active' in update_data and not update_data['is_active']:
        if employee.role.name == 'Владелец' and db.query(Employee).filter(Employee.role.has(name='Владелец'), Employee.is_active == True).count() <= 1:
            raise HTTPException(status_code=400, detail="Нельзя уволить единственного активного владельца.")

    for key, value in update_data.items():
        setattr(employee, key, value)
    
    db.commit()
    db.refresh(employee)
    return {"status": "ok", "message": "Данные сотрудника обновлены."}

# --- УПРАВЛЕНИЕ РОЛЯМИ И ДОСТУПАМИ ---
@app.post("/roles", tags=["Управление ролями и доступами"])
def create_role(role_data: RoleCreate, db: Session = Depends(get_db)):
    if db.query(Role).filter(Role.name == role_data.name).first():
        raise HTTPException(status_code=400, detail="Должность с таким названием уже существует.")
    new_role = Role(name=role_data.name)
    db.add(new_role); db.commit(); db.refresh(new_role)
    return {"status": "ok", "message": "Новая должность создана.", "role": new_role}

@app.delete("/roles/{role_id}", tags=["Управление ролями и доступами"])
def delete_role(role_id: int, db: Session = Depends(get_db)):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role: raise HTTPException(status_code=404, detail="Должность не найдена.")
    if role.name == "Владелец": raise HTTPException(status_code=400, detail="Нельзя удалить должность 'Владелец'.")
    if db.query(Employee).filter(Employee.role_id == role_id).count() > 0:
        raise HTTPException(status_code=400, detail="Нельзя удалить должность, так как к ней привязаны сотрудники.")
    db.delete(role); db.commit()
    return {"status": "ok", "message": "Должность удалена."}

@app.get("/permissions", tags=["Управление ролями и доступами"])
def get_permissions(db: Session = Depends(get_db)):
    permissions = db.query(Permission).order_by(Permission.description).all()
    return {"status": "ok", "permissions": permissions}

@app.get("/roles/{role_id}/permissions", tags=["Управление ролями и доступами"])
def get_role_permissions(role_id: int, db: Session = Depends(get_db)):
    role = db.query(Role).options(joinedload(Role.permissions)).filter(Role.id == role_id).first()
    if not role: raise HTTPException(status_code=404, detail="Должность не найдена.")
    permission_ids = [p.id for p in role.permissions]
    return {"status": "ok", "permission_ids": permission_ids}

@app.put("/roles/{role_id}/permissions", tags=["Управление ролями и доступами"])
def update_role_permissions(role_id: int, payload: RolePermissionsUpdate, db: Session = Depends(get_db)):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role: raise HTTPException(status_code=404, detail="Должность не найдена.")

    new_permissions = db.query(Permission).filter(Permission.id.in_(payload.permission_ids)).all()
    role.permissions = new_permissions
    db.commit()
    return {"status": "ok", "message": f"Доступы для должности '{role.name}' обновлены."}

# --- УПРАВЛЕНИЕ СМЕНАМИ ---
@app.get("/shifts/active", tags=["Управление сменами"])
def get_active_shift(db: Session = Depends(get_db)):
    active_shift = db.query(Shift).options(joinedload(Shift.employee)).filter(Shift.end_time == None).first()
    if not active_shift:
        raise HTTPException(status_code=404, detail="Активная смена не найдена.")
    return {"status": "ok", "shift": active_shift}

@app.post("/shifts/open", tags=["Управление сменами"])
def open_shift(payload: ShiftOpenPayload, db: Session = Depends(get_db)):
    if db.query(Shift).filter(Shift.end_time == None).first():
        raise HTTPException(status_code=400, detail="Нельзя открыть новую смену, пока не закрыта предыдущая.")

    new_shift = Shift(**payload.dict())
    db.add(new_shift)
    db.commit()
    return {"status": "ok", "message": "Смена успешно открыта."}

@app.post("/shifts/close", tags=["Управление сменами"])
def close_shift(payload: ShiftClosePayload, db: Session = Depends(get_db)):
    active_shift = db.query(Shift).filter(Shift.end_time == None).first()
    if not active_shift:
        raise HTTPException(status_code=404, detail="Активная смена не найдена.")

    active_shift.end_time = datetime.utcnow()
    active_shift.closing_cash = payload.closing_cash
    db.commit()
    return {"status": "ok", "message": "Смена успешно закрыта."}

# --- ОТЧЕТЫ ---
@app.get("/shifts/report/current", tags=["Отчеты"])
def get_current_shift_report(db: Session = Depends(get_db)):
    active_shift = db.query(Shift).filter(Shift.end_time == None).first()
    if not active_shift:
        raise HTTPException(status_code=404, detail="Активная смена не найдена.")

    start_time = active_shift.start_time
    issued_orders_in_shift = db.query(Order).filter(Order.shift_id == active_shift.id, Order.status == "Выдан").all()
    total_cash_income = sum(o.paid_cash_som for o in issued_orders_in_shift if o.paid_cash_som)
    total_card_income = sum(o.paid_card_som for o in issued_orders_in_shift if o.paid_card_som)
    expenses_in_shift = db.query(Expense).join(ExpenseType).filter(Expense.shift_id == active_shift.id, ExpenseType.name.notin_(['Зарплата', 'Аванс'])).all()
    total_expenses = sum(exp.amount for exp in expenses_in_shift)
    reverted_orders = db.query(Order).filter(Order.reverted_at >= start_time).all()
    total_returns = sum((o.paid_cash_som or 0) + (o.paid_card_som or 0) for o in reverted_orders)
    calculated_cash = active_shift.starting_cash + total_cash_income - total_expenses - total_returns

    report = {
        "shift_start_time": active_shift.start_time,
        "employee_name": active_shift.employee.full_name,
        "starting_cash": active_shift.starting_cash,
        "cash_income": total_cash_income,
        "card_income": total_card_income,
        "total_expenses": total_expenses,
        "total_returns": total_returns,
        "calculated_cash": calculated_cash
    }
    return {"status": "ok", "report": report}

@app.get("/reports/summary", tags=["Отчеты"])
def get_summary_report(start_date: date, end_date: date, db: Session = Depends(get_db)):
    issued_orders = db.query(Order).filter(
        Order.status == "Выдан",
        cast(Order.issued_at, SQLDate) >= start_date,
        cast(Order.issued_at, SQLDate) <= end_date
    ).all()
    all_expenses = db.query(Expense).options(joinedload(Expense.expense_type)).filter(
        cast(Expense.created_at, SQLDate) >= start_date,
        cast(Expense.created_at, SQLDate) <= end_date
    ).all()
    total_cash_income = sum(o.paid_cash_som for o in issued_orders if o.paid_cash_som)
    total_card_income = sum(o.paid_card_som for o in issued_orders if o.paid_card_som)
    total_income = total_cash_income + total_card_income
    total_expenses = sum(e.amount for e in all_expenses)
    expenses_by_type = {}
    for exp in all_expenses:
        type_name = exp.expense_type.name
        if type_name not in expenses_by_type:
            expenses_by_type[type_name] = 0
        expenses_by_type[type_name] += exp.amount
    net_profit = total_income - total_expenses
    shifts_in_period = db.query(Shift).options(joinedload(Shift.employee)).filter(
        cast(Shift.start_time, SQLDate) >= start_date,
        cast(Shift.start_time, SQLDate) <= end_date
    ).order_by(Shift.start_time.desc()).all()
    summary = {
        "start_date": start_date,
        "end_date": end_date,
        "total_income": total_income,
        "total_cash_income": total_cash_income,
        "total_card_income": total_card_income,
        "total_expenses": total_expenses,
        "expenses_by_type": expenses_by_type,
        "net_profit": net_profit
    }
    return {"status": "ok", "summary": summary}

@app.get("/shifts/report/{shift_id}", tags=["Отчеты"])
def get_shift_report_by_id(shift_id: int, db: Session = Depends(get_db)):
    shift = db.query(Shift).filter(Shift.id == shift_id).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Смена не найдена.")
    start_time = shift.start_time
    issued_orders_in_shift = db.query(Order).filter(Order.shift_id == shift.id, Order.status == "Выдан").all()
    total_cash_income = sum(o.paid_cash_som for o in issued_orders_in_shift if o.paid_cash_som)
    total_card_income = sum(o.paid_card_som for o in issued_orders_in_shift if o.paid_card_som)
    expenses_in_shift = db.query(Expense).join(ExpenseType).filter(Expense.shift_id == shift.id, ExpenseType.name.notin_(['Зарплата', 'Аванс'])).all()
    total_expenses = sum(exp.amount for exp in expenses_in_shift)
    reverted_orders = db.query(Order).filter(Order.reverted_at >= start_time, Order.reverted_at <= shift.end_time if shift.end_time else datetime.now()).all()
    total_returns = sum((o.paid_cash_som or 0) + (o.paid_card_som or 0) for o in reverted_orders)
    calculated_cash = shift.starting_cash + total_cash_income - total_expenses - total_returns
    report = {
        "shift_id": shift.id,
        "shift_start_time": shift.start_time,
        "shift_end_time": shift.end_time,
        "employee_name": shift.employee.full_name,
        "starting_cash": shift.starting_cash,
        "cash_income": total_cash_income,
        "card_income": total_card_income,
        "total_expenses": total_expenses,
        "total_returns": total_returns,
        "calculated_cash": calculated_cash,
        "actual_closing_cash": shift.closing_cash
    }
    return {"status": "ok", "report": report}

@app.get("/reports/buyout", tags=["Отчеты"])
def get_buyout_report(start_date: date, end_date: date, db: Session = Depends(get_db)):
    buyout_orders = db.query(Order).options(joinedload(Order.client)).filter(
        Order.purchase_type == "Выкуп",
        cast(Order.created_at, SQLDate) >= start_date,
        cast(Order.created_at, SQLDate) <= end_date
    ).all()
    report_items = []
    total_profit = 0
    for order in buyout_orders:
        price_for_client = 0
        if order.buyout_item_cost_cny and order.buyout_rate_for_client:
            commission = order.buyout_item_cost_cny * (order.buyout_commission_percent / 100)
            price_for_client = (order.buyout_item_cost_cny + commission) * order.buyout_rate_for_client
        actual_cost = 0
        if order.buyout_item_cost_cny and order.buyout_actual_rate:
            actual_cost = order.buyout_item_cost_cny * order.buyout_actual_rate
        profit = 0
        if price_for_client > 0 and actual_cost > 0:
            profit = price_for_client - actual_cost
        total_profit += profit
        report_items.append({
            "order_id": order.id,
            "track_code": order.track_code,
            "created_at": order.created_at,
            "client_name": order.client.full_name,
            "item_cost_cny": order.buyout_item_cost_cny,
            "rate_for_client": order.buyout_rate_for_client,
            "price_for_client": price_for_client,
            "actual_rate": order.buyout_actual_rate,
            "actual_cost": actual_cost,
            "profit": profit
        })
    return {
        "status": "ok",
        "report": {
            "items": report_items,
            "total_profit": total_profit
        }
    }

# --- УПРАВЛЕНИЕ РАСХОДАМИ ---
@app.get("/expense_types", tags=["Управление расходами"])
def get_expense_types(db: Session = Depends(get_db)):
    types = db.query(ExpenseType).order_by(ExpenseType.name).all()
    return {"status": "ok", "expense_types": types}

@app.post("/expenses", tags=["Управление расходами"])
def create_expense(expense_data: ExpenseCreate, db: Session = Depends(get_db)):
    active_shift = db.query(Shift).filter(Shift.end_time == None).first()
    if not active_shift:
        raise HTTPException(status_code=400, detail="Нет активной смены. Невозможно добавить расход.")
    new_expense = Expense(
        shift_id=active_shift.id,
        **expense_data.dict()
    )
    db.add(new_expense)
    db.commit()
    db.refresh(new_expense)
    return {"status": "ok", "message": "Расход успешно добавлен."}

@app.get("/expenses", tags=["Управление расходами"])
def get_expenses(start_date: date, end_date: date, db: Session = Depends(get_db)):
    expenses = db.query(Expense).options(
        joinedload(Expense.expense_type),
        joinedload(Expense.shift).joinedload(Shift.employee)
    ).filter(
        cast(Expense.created_at, SQLDate) >= start_date,
        cast(Expense.created_at, SQLDate) <= end_date
    ).order_by(Expense.created_at.desc()).all()
    return {"status": "ok", "expenses": expenses}

@app.patch("/expenses/{expense_id}", tags=["Управление расходами"])
def update_expense(expense_id: int, expense_data: ExpenseUpdate, db: Session = Depends(get_db)):
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Расход не найден.")
    update_data = expense_data.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(expense, key, value)
    db.commit()
    db.refresh(expense)
    return {"status": "ok", "message": "Расход обновлен."}

# --- УТИЛИТЫ ---
ALL_PERMISSIONS = {
    'manage_employees': 'Управлять сотрудниками (добавлять, увольнять)', 'manage_roles': 'Управлять должностями и доступами',
    'manage_expense_types': 'Управлять типами расходов', 'view_full_reports': 'Видеть полные финансовые отчеты',
    'view_shift_report': 'Видеть отчет по текущей смене',
    'add_expense': 'Добавлять расходы', 'open_close_shift': 'Открывать и закрывать смены', 'issue_orders': 'Выдавать заказы',
    'manage_clients': 'Управлять клиентами', 'manage_orders': 'Управлять заказами',
    'wipe_database': 'Полностью очищать базу данных (опасная зона)'
}
OWNER_PASSWORD = "root"

@app.get("/setup_initial_data", tags=["Утилиты"])
def setup_initial_data(db: Session = Depends(get_db)):
    existing_permissions = {p.codename for p in db.query(Permission).all()}
    for codename, description in ALL_PERMISSIONS.items():
        if codename not in existing_permissions:
            db.add(Permission(codename=codename, description=description))
    db.commit()
    owner_role = db.query(Role).filter(Role.name == "Владелец").first()
    if not owner_role:
        owner_role = Role(name="Владелец")
        db.add(owner_role)
        db.commit()
    all_permissions_in_db = db.query(Permission).all()
    owner_role.permissions = all_permissions_in_db
    db.commit()
    if db.query(Employee).count() == 0:
        owner_employee = Employee(full_name="Владелец", password=OWNER_PASSWORD, role_id=owner_role.id)
        db.add(owner_employee)
        db.commit()
    if db.query(ExpenseType).count() == 0:
        default_expense_types = [ExpenseType(name="Хоз. нужды"), ExpenseType(name="Закуп канцелярии"), ExpenseType(name="Оплата интернета"), ExpenseType(name="Ремонт"), ExpenseType(name="Зарплата"), ExpenseType(name="Аванс"), ExpenseType(name="Прочие расходы")]
        db.add_all(default_expense_types)
        db.commit()
    return {"status": "ok", "message": "Первоначальная настройка системы завершена."}

@app.get("/create_tables", tags=["Утилиты"])
def create_tables_endpoint():
    try: Base.metadata.create_all(bind=engine); return {"status": "ok", "message": "Таблицы успешно созданы/обновлены!"}
    except Exception as e: raise HTTPException(status_code=500, detail=f"Ошибка: {e}")

@app.get("/order_statuses", tags=["Утилиты"])
def get_order_statuses(): return {"status": "ok", "statuses": ORDER_STATUSES}

@app.get("/config", tags=["Выдача"])
def get_config(): return CONFIG

@app.get("/", tags=["Утилиты"])
def read_root(): return {"status": "ok", "message": "Сервер Карго CRM запущен!"}

# --- КЛИЕНТЫ ---
@app.post("/clients/bulk_import", tags=["Клиенты"])
def bulk_import_clients(clients_data: List[BulkClientItem], db: Session = Depends(get_db)):
    created_count = 0
    errors = []
    existing_phones = {c.phone for c in db.query(Client.phone).all()}
    existing_codes = {c.client_code_num for c in db.query(Client.client_code_num).filter(Client.client_code_num.isnot(None)).all()}
    for item in clients_data:
        if not item.phone:
            errors.append(f"Пропущен клиент '{item.full_name}', так как не указан номер телефона.")
            continue
        if item.phone in existing_phones:
            errors.append(f"Клиент '{item.full_name}' с телефоном {item.phone} уже существует.")
            continue
        new_client = Client(full_name=item.full_name, phone=item.phone)
        if item.client_code:
            try:
                prefix = ''.join(filter(str.isalpha, item.client_code))
                num = int(''.join(filter(str.isdigit, item.client_code)))
                if num in existing_codes:
                    errors.append(f"Не удалось добавить '{item.full_name}', так как код {num} уже занят.")
                    continue
                new_client.client_code_prefix = prefix
                new_client.client_code_num = num
                existing_codes.add(num)
            except:
                errors.append(f"Неверный формат кода '{item.client_code}' для '{item.full_name}'.")
                continue
        db.add(new_client)
        existing_phones.add(item.phone)
        created_count += 1
    db.commit()
    return {"status": "ok", "message": "Импорт завершен.", "created_clients": created_count, "errors": errors}

@app.post("/clients/wipe_all", tags=["Клиенты"])
def wipe_all_clients(payload: WipePayload, db: Session = Depends(get_db)):
    if payload.password != WIPE_PASSWORD: raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Неверный пароль.")
    db.query(Expense).delete(); db.query(Shift).delete(); db.query(Order).delete(); db.query(Client).delete(); db.query(Employee).delete(); db.query(Role).delete(); db.commit()
    return {"status": "ok", "message": f"База полностью очищена."}

@app.post("/register_client", tags=["Клиенты"])
def register_client(client_data: ClientCreate, db: Session = Depends(get_db)):
    if db.query(Client).filter(Client.phone == client_data.phone).first():
        raise HTTPException(status_code=400, detail="Клиент с таким телефоном уже существует.")
    last_client = db.query(Client).order_by(Client.client_code_num.desc()).first()
    new_code_num = (last_client.client_code_num + 1) if last_client and last_client.client_code_num else 1001
    new_client = Client(
        full_name=client_data.full_name, 
        phone=client_data.phone,
        client_code_num=new_code_num
    )
    if client_data.client_code_prefix:
        new_client.client_code_prefix = client_data.client_code_prefix
    db.add(new_client)
    db.commit()
    db.refresh(new_client)
    return {"status": "ok", "message": "Клиент зарегистрирован!", "client": new_client}

@app.get("/clients", tags=["Клиенты"])
def get_all_clients(db: Session = Depends(get_db)): return {"status": "ok", "clients": db.query(Client).order_by(Client.full_name).all()}

@app.get("/clients/search", tags=["Клиенты"])
def search_clients(q: str, db: Session = Depends(get_db)):
    if not q: return []
    search_term = f"%{q}%"
    return db.query(Client).filter(or_(Client.full_name.ilike(search_term), Client.phone.ilike(search_term), (Client.client_code_prefix + func.cast(Client.client_code_num, String)).ilike(search_term))).limit(10).all()

@app.patch("/clients/{client_id}", tags=["Клиенты"])
def update_client(client_id: int, client_data: ClientUpdate, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client: raise HTTPException(status_code=404, detail="Клиент не найден.")
    update_data = client_data.dict(exclude_unset=True)
    for key, value in update_data.items(): setattr(client, key, value)
    db.commit(); db.refresh(client)
    return {"status": "ok", "message": "Данные обновлены.", "client": client}

@app.delete("/clients/{client_id}", tags=["Клиенты"])
def delete_client(client_id: int, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client: raise HTTPException(status_code=404, detail="Клиент не найден.")
    active_orders = db.query(Order).filter(Order.client_id == client_id, Order.status != "Выдан").count()
    if active_orders > 0: raise HTTPException(status_code=400, detail=f"Невозможно удалить, у клиента есть {active_orders} незавершенных заказов.")
    db.query(Order).filter(Order.client_id == client_id).delete(); db.delete(client); db.commit()
    return {"status": "ok", "message": "Клиент и его история заказов удалены."}

@app.post("api/clients/{client_id}/generate_lk_link", tags=["Клиенты"])
def generate_lk_link(client_id: int, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден.")
    secret_token = f"CLIENT-{client.id}-SECRET" 
    client_portal_url = "http://127.0.0.1:5500/lk.html" 
    link = f"{client_portal_url}?token={secret_token}"
    return {"link": link}

# --- ЗАКАЗЫ ---
@app.post("/orders/bulk_import", tags=["Заказы"])
def bulk_import_orders(payload: BulkOrderImportPayload, db: Session = Depends(get_db)):
    created_count = 0
    errors = []
    warnings = []
    import_date = payload.party_date if payload.party_date else date.today()
    for item in payload.orders_data:
        try:
            client = None
            if item.client_code:
                client_code_str = str(item.client_code)
                prefix = ''.join(filter(str.isalpha, client_code_str))
                num_str = ''.join(filter(str.isdigit, client_code_str))
                if num_str:
                    num = int(num_str)
                    query = db.query(Client).filter(Client.client_code_num == num)
                    if prefix:
                        query = query.filter(Client.client_code_prefix == prefix)
                    client = query.first()
            if not client and item.phone:
                client = db.query(Client).filter(Client.phone == str(item.phone)).first()
            if not client:
                ident = item.client_code or item.phone or f"track-{item.track_code}"
                new_placeholder_client = Client(
                    full_name=f"Неизвестный ({ident})",
                    phone=f"placeholder_{datetime.now().timestamp()}"
                )
                db.add(new_placeholder_client)
                db.flush()
                client = new_placeholder_client
                warnings.append(f"Для заказа '{item.track_code}' создан новый неизвестный клиент '{ident}'.")
            new_order = Order(
                track_code=str(item.track_code),
                client_id=client.id,
                purchase_type="Доставка",
                status="В пути",
                party_date=import_date,
                comment=item.comment
            )
            db.add(new_order)
            created_count += 1
        except Exception as e:
            db.rollback()
            errors.append(f"Критическая ошибка для трека {item.track_code}: {str(e)}")
    db.commit()
    return {"status": "ok", "message": "Импорт завершен.", "created_orders": created_count, "errors": errors, "warnings": warnings}

@app.post("/orders/bulk_action", tags=["Заказы"])
async def bulk_order_action(payload: BulkActionPayload, db: Session = Depends(get_db)):
    if not payload.order_ids:
        raise HTTPException(status_code=400, detail="Не выбраны заказы.")
    query = db.query(Order).filter(Order.id.in_(payload.order_ids))
    if query.count() != len(payload.order_ids):
        raise HTTPException(status_code=404, detail="Один или несколько заказов не найдены.")
    if payload.action == 'update_status':
        if not payload.new_status or payload.new_status not in ORDER_STATUSES:
            raise HTTPException(status_code=400, detail="Недопустимый статус.")
        count = query.update({"status": payload.new_status}, synchronize_session=False)
        db.commit()
        updated_orders = db.query(Order).options(joinedload(Order.client)).filter(Order.id.in_(payload.order_ids)).all()
        notifications_to_send = {}
        for order in updated_orders:
            if order.client and order.client.telegram_chat_id:
                if order.client.id not in notifications_to_send:
                    notifications_to_send[order.client.id] = {"client": order.client, "track_codes": []}
                notifications_to_send[order.client.id]["track_codes"].append(order.track_code)
        for client_id, data in notifications_to_send.items():
            asyncio.create_task(generate_and_send_notification(db, data["client"], payload.new_status, data["track_codes"]))
        return {"status": "ok", "message": f"Статус обновлен для {count} заказов."}
    elif payload.action == 'buyout':
        if not payload.buyout_actual_rate:
            raise HTTPException(status_code=400, detail="Не указан реальный курс выкупа.")
        count = query.update({"buyout_actual_rate": payload.buyout_actual_rate, "status": "Выкуплен"}, synchronize_session=False)
        db.commit()
        return {"status": "ok", "message": f"Статус 'Выкуплен' установлен для {count} заказов."}
    elif payload.action == 'revert':
        orders_to_action = query.all()
        for order in orders_to_action:
            if order.status == "Выдан":
                order.reverted_at = datetime.utcnow()
                order.status = "Готов к выдаче"
                order.issued_at = None
                order.shift_id = None
        db.commit()
        return {"status": "ok", "message": f"Выполнено возвратов: {len(orders_to_action)}."}
    elif payload.action == 'update_party_date':
        if payload.password != WIPE_PASSWORD: raise HTTPException(status_code=403, detail="Неверный пароль.")
        if not payload.new_party_date: raise HTTPException(status_code=400, detail="Не указана новая дата партии.")
        count = query.update({"party_date": payload.new_party_date}, synchronize_session=False)
        db.commit()
        return {"status": "ok", "message": f"Дата партии обновлена для {count} заказов."}
    elif payload.action == 'delete':
        if payload.password != WIPE_PASSWORD: raise HTTPException(status_code=403, detail="Неверный пароль.")
        count = query.delete(synchronize_session=False)
        db.commit()
        return {"status": "ok", "message": f"Удалено {count} заказов."}
    else:
        raise HTTPException(status_code=400, detail="Неизвестное действие.")

@app.get("/orders", tags=["Заказы"])
def get_all_orders(
    db: Session = Depends(get_db), 
    party_dates: Optional[List[date]] = Query(None),
    statuses: Optional[List[str]] = Query(None)
):
    query = db.query(Order).options(joinedload(Order.client))
    if party_dates:
        query = query.filter(Order.party_date.in_(party_dates))
    if statuses:
        query = query.filter(Order.status.in_(statuses))
    return {"status": "ok", "orders": query.order_by(Order.id.desc()).all()}

@app.get("/orders/parties", tags=["Заказы"])
def get_order_parties(db: Session = Depends(get_db)):
    parties = db.query(Order.party_date).distinct().order_by(Order.party_date.desc()).all()
    return {"status": "ok", "parties": [p[0].isoformat() for p in parties if p[0]]}

@app.post("/clients/{client_id}/orders", tags=["Заказы"])
def create_order_for_client(client_id: int, order_data: OrderCreate, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail=f"Клиент с ID {client_id} не найден.")
    if not order_data.track_code or not order_data.track_code.strip():
        timestamp = int(datetime.now().timestamp())
        order_data.track_code = f"PENDING-{timestamp}"
    new_order = Order(**order_data.dict())
    if new_order.purchase_type == "Выкуп":
        new_order.status = "Ожидает выкупа"
    db.add(new_order)
    db.commit()
    db.refresh(new_order)
    return {"status": "ok", "message": f"Заказ для '{client.full_name}' создан!", "order_details": new_order}

@app.patch("/orders/{order_id}/status", tags=["Заказы"])
async def update_order_status(order_id: int, status_data: OrderStatusUpdate, db: Session = Depends(get_db)):
    order = db.query(Order).options(joinedload(Order.client)).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail=f"Заказ с ID {order_id} не найден.")
    if status_data.status not in ORDER_STATUSES:
        raise HTTPException(status_code=400, detail="Недопустимый статус.")
    order.status = status_data.status
    db.commit()
    db.refresh(order)
    if order.client and order.client.telegram_chat_id:
        await generate_and_send_notification(db, order.client, status_data.status, [order.track_code])
    return {"status": "ok", "message": "Статус обновлен!", "order": order}

@app.delete("/orders/{order_id}", tags=["Заказы"])
def delete_order(order_id: int, payload: OrderActionPayload, db: Session = Depends(get_db)):
    if payload.password != WIPE_PASSWORD: raise HTTPException(status_code=403, detail="Неверный пароль.")
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order: raise HTTPException(status_code=404, detail="Заказ не найден.")
    db.delete(order); db.commit()
    return {"status": "ok", "message": "Заказ удален."}

@app.patch("/orders/{order_id}", tags=["Заказы"])
def update_order(order_id: int, order_data: OrderUpdate, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден.")
    update_data = order_data.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(order, key, value)
    db.commit()
    db.refresh(order)
    return {"status": "ok", "message": "Заказ обновлен.", "order": order}

# --- ВЫДАЧА ---
@app.get("/orders/ready_for_issue", tags=["Выдача"])
def get_orders_ready_for_issue(db: Session = Depends(get_db)):
    orders = db.query(Order).join(Order.client).options(joinedload(Order.client)).filter(Order.status == "Готов к выдаче").order_by(Client.full_name).all()
    return {"status": "ok", "orders": orders}

@app.post("/orders/issue", tags=["Выдача"])
def issue_orders(payload: IssuePayload, db: Session = Depends(get_db)):
    active_shift = db.query(Shift).filter(Shift.end_time == None).first()
    if not active_shift:
        raise HTTPException(status_code=400, detail="Нет активной смены. Невозможно выдать заказ.")
    order_ids = [item.order_id for item in payload.orders]
    orders_to_update = db.query(Order).filter(Order.id.in_(order_ids)).all()
    if len(orders_to_update) != len(order_ids):
        raise HTTPException(status_code=404, detail="Один или несколько заказов не найдены.")
    total_paid_cash = payload.paid_cash / len(orders_to_update)
    total_paid_card = payload.paid_card / len(orders_to_update)
    for order in orders_to_update:
        item_data = next((item for item in payload.orders if item.order_id == order.id), None)
        if item_data:
            order.status = "Выдан"
            order.weight_kg = item_data.weight_kg
            order.price_per_kg_usd = payload.price_per_kg_usd
            order.exchange_rate_usd = payload.exchange_rate_usd
            order.final_cost_som = (item_data.weight_kg * payload.price_per_kg_usd * payload.exchange_rate_usd)
            order.paid_cash_som = total_paid_cash
            order.paid_card_som = total_paid_card
            order.card_payment_type = payload.card_payment_type
            order.issued_at = datetime.utcnow()
            order.shift_id = active_shift.id
    db.commit()
    return {"status": "ok", "message": f"Успешно выдано {len(orders_to_update)} заказов."}

@app.get("/orders/issued", tags=["Выдача"])
def get_issued_orders(start_date: Optional[date] = None, end_date: Optional[date] = None, db: Session = Depends(get_db)):
    query = db.query(Order).options(joinedload(Order.client)).filter(Order.status == "Выдан")
    if start_date: query = query.filter(cast(Order.issued_at, SQLDate) >= start_date)
    if end_date: query = query.filter(cast(Order.issued_at, SQLDate) <= end_date)
    return {"status": "ok", "orders": query.order_by(Order.issued_at.desc()).all()}

@app.patch("/orders/{order_id}/revert_status", tags=["Выдача"])
def revert_order_status(order_id: int, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден.")
    if order.status != "Выдан":
        raise HTTPException(status_code=400, detail="Можно вернуть только выданный заказ.")
    order.reverted_at = datetime.utcnow()
    order.status = "Готов к выдаче"
    order.issued_at = None
    order.shift_id = None
    db.commit()
    return {"status": "ok", "message": "Статус заказа возвращен."}
