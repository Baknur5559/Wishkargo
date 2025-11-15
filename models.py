# models.py (ИСПРАВЛЕННАЯ ВЕРСИЯ ДЛЯ SUPER-ADMIN)

from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey, func, Date, Boolean, Table, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

# --- СВЯЗУЮЩАЯ ТАБЛИЦА ДЛЯ СИСТЕМЫ ДОСТУПОВ ---
role_permissions_table = Table('role_permissions', Base.metadata,
    Column('role_id', Integer, ForeignKey('roles.id'), primary_key=True),
    Column('permission_id', Integer, ForeignKey('permissions.id'), primary_key=True)
)

# --- НОВЫЕ ОСНОВНЫЕ МОДЕЛИ: КОМПАНИЯ И ФИЛИАЛ ---

class Company(Base):
    """
    Представляет "Арендатора" (Tenant) - отдельную карго-компанию.
    """
    __tablename__ = 'companies'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False) # Название компании
    company_code = Column(String, unique=True, index=True, nullable=True) 
    is_active = Column(Boolean, default=True) # Контроль оплаты
    subscription_paid_until = Column(Date, nullable=True) # Контроль оплаты
    contact_person = Column(String, nullable=True) # Контактное лицо (для вас)
    contact_phone = Column(String, nullable=True) # Телефон (для вас)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # --- ДОБАВИТЬ ЭТИ ПОЛЯ ---
    telegram_bot_token = Column(String, nullable=True, unique=True) # Токен бота (должен быть уникальным)
    telegram_bot_username = Column(String, nullable=True) # Имя пользователя бота (опционально)
    # --- КОНЕЦ ДОБАВЛЕНИЯ --

    # Связи (кто принадлежит этой компании)
    locations = relationship("Location", back_populates="company")
    clients = relationship("Client", back_populates="company")
    orders = relationship("Order", back_populates="company")
    employees = relationship("Employee", back_populates="company")
    roles = relationship("Role", back_populates="company")
    shifts = relationship("Shift", back_populates="company")
    expenses = relationship("Expense", back_populates="company")
    expense_types = relationship("ExpenseType", back_populates="company")
    settings = relationship("Setting", back_populates="company")

class Location(Base):
    """
    Представляет "Точку" или "Филиал" (Отделение)
    """
    __tablename__ = 'locations'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False) # "Главный офис", "Склад 1"
    address = Column(String, nullable=True)
    
    # --- НОВЫЕ ПОЛЯ ДЛЯ КОНТАКТОВ ФИЛИАЛА ---
    phone = Column(String, nullable=True)
    whatsapp_link = Column(String, nullable=True)
    instagram_link = Column(String, nullable=True)
    map_link = Column(String, nullable=True)
    # --- КОНЕЦ НОВЫХ ПОЛЕЙ ---

    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False, index=True)
    company = relationship("Company", back_populates="locations")

    employees = relationship("Employee", back_populates="location")
    shifts = relationship("Shift", back_populates="location")
    orders = relationship("Order", back_populates="location")

# --- ИЗМЕНЕННЫЕ МОДЕЛИ: КЛИЕНТЫ И ЗАКАЗЫ ---

class Client(Base):
    __tablename__ = 'clients'
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    phone = Column(String, index=True, nullable=False) 
    client_code_prefix = Column(String, default="KB")
    client_code_num = Column(Integer, nullable=True) 
    telegram_chat_id = Column(String, nullable=True, index=True) # <-- ИЗМЕНЕНО: unique=True УБРАНО, index=True ДОБАВЛЕНО
    status = Column(String, default="Розница")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    orders = relationship("Order", back_populates="client")

    # НОВАЯ СВЯЗЬ: К какой компании принадлежит этот клиент
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False, index=True)
    company = relationship("Company", back_populates="clients")

# НОВОЕ ПРАВИЛО: Код клиента и телефон должны быть уникальны ВНУТРИ ОДНОЙ КОМПАНИИ
    __table_args__ = (
        UniqueConstraint('phone', 'company_id', name='_phone_company_uc'),
        # Уникальность по ПРЕФИКС + НОМЕР + КОМПАНИЯ
        UniqueConstraint('client_code_prefix', 'client_code_num', 'company_id', name='_client_prefix_num_company_uc'),
    )


# models.py (Полностью заменяет класс Order)

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

    # Поля для предварительного расчета
    calculated_weight_kg = Column(Float, nullable=True)
    calculated_price_per_kg_usd = Column(Float, nullable=True)
    calculated_exchange_rate_usd = Column(Float, nullable=True)
    calculated_final_cost_som = Column(Float, nullable=True)

    # Поля для "Двух чеков" (Выкуп)
    buyout_item_cost_cny = Column(Float, nullable=True)
    buyout_commission_percent = Column(Float, default=10.0)
    buyout_rate_for_client = Column(Float, nullable=True)
    buyout_actual_rate = Column(Float, nullable=True)

    # --- ИСПРАВЛЕННЫЕ И НОВЫЕ СВЯЗИ ---

    # Связь с клиентом (Дубликаты убраны)
    client_id = Column(Integer, ForeignKey('clients.id'), nullable=True) # <-- ИЗМЕНЕНО
    client = relationship("Client", back_populates="orders")

    # Связь со сменой (nullable=True для расходов Владельца)
    shift_id = Column(Integer, ForeignKey('shifts.id'), nullable=True) 
    # (relationship к Shift не добавляем, чтобы не было цикла)

    # Связь с Компанией (Multi-Tenant)
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False, index=True)
    company = relationship("Company", back_populates="orders")
    
    # Связь с Филиалом (Multi-Location)
    location_id = Column(Integer, ForeignKey('locations.id'), nullable=False, index=True)
    location = relationship("Location", back_populates="orders") 
    
    # --- НОВАЯ СВЯЗЬ (Задача 3) ---
    history_entries = relationship("OrderHistory", back_populates="order", cascade="all, delete-orphan", order_by="OrderHistory.created_at")

    # --- КОНЕЦ СВЯЗЕЙ ---

    # Правило уникальности: Трек-код + Компания
    __table_args__ = (
        UniqueConstraint('track_code', 'company_id', name='_track_code_company_uc'),
    )

# --- ИЗМЕНЕННЫЕ МОДЕЛИ: ПЕРСОНАЛ И ДОСТУП ---

class Role(Base):
    __tablename__ = 'roles'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False) 

    employees = relationship("Employee", back_populates="role")
    permissions = relationship("Permission", secondary=role_permissions_table, back_populates="roles")

    # --- ИСПРАВЛЕНИЕ: company_id МОЖЕТ БЫТЬ NULL (для роли Супер-Админа) ---
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=True, index=True)
    company = relationship("Company", back_populates="roles")

    # Правило уникальности: Либо company_id=NULL, либо (name, company_id) уникальны
    __table_args__ = (UniqueConstraint('name', 'company_id', name='_role_name_company_uc'),)


class Permission(Base):
    """
    Разрешения - ГЛОБАЛЬНЫЕ.
    """
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

    # Это поле теперь не нужно, мы будем проверять company_id is NULL
    is_company_owner = Column(Boolean, default=False)

    role_id = Column(Integer, ForeignKey('roles.id'))
    role = relationship("Role", back_populates="employees")
    shifts = relationship("Shift", back_populates="employee")

    # --- ИСПРАВЛЕНИЕ: company_id МОЖЕТ БЫТЬ NULL (для Супер-Админа) ---
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=True, index=True)
    company = relationship("Company", back_populates="employees")

    # --- ИСПРАВЛЕНИЕ: location_id МОЖЕТ БЫТЬ NULL (для Супер-Админа) ---
    location_id = Column(Integer, ForeignKey('locations.id'), nullable=True, index=True)
    location = relationship("Location", back_populates="employees")

# --- ИЗМЕНЕННЫЕ МОДЕЛИ: ФИНАНСЫ ---

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

    # Эти поля остаются nullable=False, т.к. смена не может быть "глобальной"
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False, index=True)
    company = relationship("Company", back_populates="shifts")

    location_id = Column(Integer, ForeignKey('locations.id'), nullable=False, index=True)
    location = relationship("Location", back_populates="shifts")


class ExpenseType(Base):
    __tablename__ = 'expense_types'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False) 
    expenses = relationship("Expense", back_populates="expense_type")

    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False, index=True)
    company = relationship("Company", back_populates="expense_types")

    __table_args__ = (UniqueConstraint('name', 'company_id', name='_exp_type_name_company_uc'),)


# models.py (Внутри класса Expense)

class Expense(Base):
    __tablename__ = 'expenses'
    id = Column(Integer, primary_key=True)
    amount = Column(Float, nullable=False)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # --- ВОТ ЭТУ СТРОКУ НУЖНО ИЗМЕНИТЬ ---
    # БЫЛО: shift_id = Column(Integer, ForeignKey('shifts.id'))
    # СТАЛО:
    shift_id = Column(Integer, ForeignKey('shifts.id'), nullable=True) # <-- Добавляем nullable=True
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    
    shift = relationship("Shift", back_populates="expenses")
    
    expense_type_id = Column(Integer, ForeignKey('expense_types.id'))
    expense_type = relationship("ExpenseType", back_populates="expenses")

    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False, index=True)
    company = relationship("Company", back_populates="expenses")


# --- ИЗМЕНЕННАЯ МОДЕЛЬ: НАСТРОЙКИ ---

class Setting(Base):
    __tablename__ = 'settings'
    id = Column(Integer, primary_key=True)
    key = Column(String, nullable=False) 
    value = Column(String, nullable=True)

    # --- ИСПРАВЛЕНИЕ: company_id МОЖЕТ БЫТЬ NULL (для Глобальных настроек) ---
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=True, index=True)
    company = relationship("Company", back_populates="settings")

    __table_args__ = (UniqueConstraint('key', 'company_id', name='_setting_key_company_uc'),)

    # --- НОВЫЕ МОДЕЛИ: Рассылки и Реакции (Задача 2) ---

class Broadcast(Base):
    """
    Хранит отправленные рассылки (объявления)
    """
    __tablename__ = 'broadcasts'
    id = Column(Integer, primary_key=True, index=True)
    text = Column(String, nullable=False) # Текст (HTML) рассылки
    photo_file_id = Column(String, nullable=True) # ID фото в Telegram
    sent_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # К какой компании относится рассылка
    company_id = Column(Integer, ForeignKey('companies.id'), nullable=False, index=True)
    company = relationship("Company") # Связь в одну сторону

    # Связь с реакциями (чтобы можно было легко удалить)
    reactions = relationship("BroadcastReaction", back_populates="broadcast", cascade="all, delete-orphan")

class BroadcastReaction(Base):
    """
    Хранит реакции клиентов на конкретные рассылки
    """
    __tablename__ = 'broadcast_reactions'
    id = Column(Integer, primary_key=True)
    
    reaction_type = Column(String, nullable=False, index=True) # Например: "like", "dislike"
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Связь с рассылкой
    broadcast_id = Column(Integer, ForeignKey('broadcasts.id'), nullable=False)
    broadcast = relationship("Broadcast", back_populates="reactions")
    
    # Связь с клиентом
    client_id = Column(Integer, ForeignKey('clients.id'), nullable=False)
    client = relationship("Client") # Связь в одну сторону

    # Уникальность: Один клиент - одна реакция на одну рассылку
    __table_args__ = (UniqueConstraint('broadcast_id', 'client_id', name='_broadcast_client_reaction_uc'),)

# --- НОВАЯ МОДЕЛЬ: История Статусов Заказа (Задача 3) ---

class OrderHistory(Base):
    """
    Хранит историю изменений статуса для каждого заказа.
    """
    __tablename__ = 'order_history'
    id = Column(Integer, primary_key=True)
    
    # Статус, который был установлен
    status = Column(String, nullable=False, index=True) 
    
    # Когда это произошло
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # ID сотрудника, который изменил статус (если это было сделано из админки)
    employee_id = Column(Integer, ForeignKey('employees.id'), nullable=True)
    employee = relationship("Employee") # Связь в одну сторону

    # Связь с заказом
    order_id = Column(Integer, ForeignKey('orders.id', ondelete="CASCADE"), nullable=False, index=True)
    order = relationship("Order", back_populates="history_entries")