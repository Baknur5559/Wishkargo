# client_api.py (ПОЛНАЯ ВЕРСИЯ)

import os
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session, joinedload
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import Optional

from models import Client, Order

# --- Pydantic модель для создания заказа ---
class OrderCreatePayload(BaseModel):
    track_code: str
    comment: Optional[str] = None

# --- НАСТРОЙКА ---
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
app = FastAPI(title="Client Portal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- DEPENDENCY ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- API-ЭНДПОИНТ ДЛЯ ПОЛУЧЕНИЯ ДАННЫХ КЛИЕНТА ---
@app.get("/api/client/data")
def get_client_data(token: str, db: Session = Depends(get_db)):
    try:
        client_id_str = token.split('-')[1]
        client_id = int(client_id_str)
    except (IndexError, ValueError):
        raise HTTPException(status_code=400, detail="Неверный формат токена.")

    client = db.query(Client).options(joinedload(Client.orders)).filter(Client.id == client_id).first()

    if not client:
        raise HTTPException(status_code=404, detail="Клиент по этому токену не найден.")
        
    return {"full_name": client.full_name, "orders": client.orders}

# --- НОВЫЙ ЭНДПОИНТ ДЛЯ ДОБАВЛЕНИЯ ЗАКАЗА КЛИЕНТОМ ---
@app.post("/api/client/orders")
def client_add_order(token: str, payload: OrderCreatePayload, db: Session = Depends(get_db)):
    try:
        client_id_str = token.split('-')[1]
        client_id = int(client_id_str)
    except (IndexError, ValueError):
        raise HTTPException(status_code=400, detail="Неверный формат токена.")

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден.")

    new_order = Order(
        track_code=payload.track_code,
        comment=payload.comment,
        client_id=client.id,
        purchase_type="Доставка",
        status="В обработке"
    )
    db.add(new_order)
    db.commit()

    return {"status": "ok", "message": "Ваш заказ успешно добавлен!"}