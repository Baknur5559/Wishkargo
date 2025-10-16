from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey, func, Date, Boolean, Table
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

# --- СВЯЗУЮЩАЯ ТАБЛИЦА ДЛЯ СИСТЕМЫ ДОСТУПОВ ---
role_permissions_table = Table('role_permissions', Base.metadata,
    Column('role_id', Integer, ForeignKey('roles.id'), primary_key=True),
    Column('permission_id', Integer, ForeignKey('permissions.id'), primary_key=True)
)

# --- ОСНОВНЫЕ МОДЕЛИ: КЛИЕНТЫ И ЗАКАЗЫ ---

class Client(Base):
    __tablename__ = 'clients'
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    phone = Column(String, unique=True, index=True, nullable=False)
    client_code_prefix = Column(String, default="KB")
    client_code_num = Column(Integer, unique=True, nullable=True)
    # Это поле - ключ к интеграции с Telegram-ботом. Здесь будет храниться ID чата с клиентом.
    telegram_chat_id = Column(String, unique=True, nullable=True) 
    status = Column(String, default="Розница")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    orders = relationship("Order", back_populates="client")

# ЗАМЕНИТЬ ВЕСЬ КЛАСС Order
class Order(Base):
    __tablename__ = 'orders'
    id = Column(Integer, primary_key=True, index=True)
    track_code = Column(String, index=True, nullable=False)
    status = Column(String, default="В обработке")
    purchase_type = Column(String, nullable=False)
    comment = Column(String, nullable=True)
    party_date = Column(Date, server_default=func.current_date(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    weight_kg = Column(Float, nullable=True)
    price_per_kg_usd = Column(Float, nullable=True)
    exchange_rate_usd = Column(Float, nullable=True)
    final_cost_som = Column(Float, nullable=True)
    paid_cash_som = Column(Float, nullable=True)
    paid_card_som = Column(Float, nullable=True)
    card_payment_type = Column(String, nullable=True)
    issued_at = Column(DateTime(timezone=True), nullable=True)
    reverted_at = Column(DateTime(timezone=True), nullable=True)

    client_id = Column(Integer, ForeignKey('clients.id'))
    client = relationship("Client", back_populates="orders")

    # --- НОВЫЕ ПОЛЯ ДЛЯ "ДВУХ ЧЕКОВ" ---
    buyout_item_cost_cny = Column(Float, nullable=True)
    buyout_commission_percent = Column(Float, default=10.0)
    buyout_rate_for_client = Column(Float, nullable=True)
    buyout_actual_rate = Column(Float, nullable=True)

    client_id = Column(Integer, ForeignKey('clients.id'))
    client = relationship("Client", back_populates="orders")

    # --- НОВОЕ ПОЛЕ ДЛЯ СВЯЗИ С ОТЧЕТАМИ ---
    shift_id = Column(Integer, ForeignKey('shifts.id'), nullable=True)

# --- МОДЕЛИ ДЛЯ УПРАВЛЕНИЯ ПЕРСОНАЛОМ ---

class Role(Base):
    __tablename__ = 'roles'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    
    employees = relationship("Employee", back_populates="role")
    permissions = relationship("Permission", secondary=role_permissions_table, back_populates="roles")

class Permission(Base):
    __tablename__ = 'permissions'
    id = Column(Integer, primary_key=True)
    codename = Column(String, unique=True, nullable=False)
    description = Column(String, nullable=False)
    
    roles = relationship("Role", secondary=role_permissions_table, back_populates="permissions")

class Employee(Base):
    __tablename__ = 'employees'
    id = Column(Integer, primary_key=True)
    full_name = Column(String, nullable=False)
    password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    
    role_id = Column(Integer, ForeignKey('roles.id'))
    role = relationship("Role", back_populates="employees")
    shifts = relationship("Shift", back_populates="employee")

# --- МОДЕЛИ ДЛЯ УЧЕТА ФИНАНСОВ (СМЕНЫ И РАСХОДЫ) ---

class Shift(Base):
    __tablename__ = 'shifts'
    id = Column(Integer, primary_key=True)
    start_time = Column(DateTime(timezone=True), server_default=func.now())
    end_time = Column(DateTime(timezone=True), nullable=True)
    starting_cash = Column(Float, nullable=False)
    closing_cash = Column(Float, nullable=True)
    exchange_rate_usd = Column(Float, nullable=False)
    price_per_kg_usd = Column(Float, nullable=False)
    
    employee_id = Column(Integer, ForeignKey('employees.id'))
    employee = relationship("Employee", back_populates="shifts")
    expenses = relationship("Expense", back_populates="shift")

class ExpenseType(Base):
    __tablename__ = 'expense_types'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    
    expenses = relationship("Expense", back_populates="expense_type")

class Expense(Base):
    __tablename__ = 'expenses'
    id = Column(Integer, primary_key=True)
    amount = Column(Float, nullable=False)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    shift_id = Column(Integer, ForeignKey('shifts.id'))
    shift = relationship("Shift", back_populates="expenses")
    
    expense_type_id = Column(Integer, ForeignKey('expense_types.id'))
    expense_type = relationship("ExpenseType", back_populates="expenses")

# --- НОВАЯ МОДЕЛЬ ДЛЯ ОБЩИХ НАСТРОЕК СИСТЕМЫ ---

class Setting(Base):
    __tablename__ = 'settings'
    id = Column(Integer, primary_key=True)
    # Например, 'china_warehouse_address', 'whatsapp_link', 'telegram_bot_token'
    key = Column(String, unique=True, nullable=False) 
    # Например, 'г. Гуанчжоу, ул. ...', 'https://wa.me/996...'
    value = Column(String, nullable=True)