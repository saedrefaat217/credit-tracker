from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import bcrypt
import sqlite3
import re
import io
from datetime import datetime
from PIL import Image
import easyocr

app = FastAPI(title="Credit Tracker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# تهيئة قارئ النصوص للغة العربية والإنجليزي (EasyOCR)
reader = easyocr.Reader(['ar', 'en'], gpu=False)

# ==================== إعداد قاعدة البيانات ====================
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            bank_name TEXT NOT NULL,
            card_number TEXT NOT NULL,
            credit_limit REAL NOT NULL,
            current_debt REAL NOT NULL,
            next_month_debt REAL DEFAULT 0.0,
            available_credit REAL NOT NULL,
            due_day INTEGER DEFAULT 25
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            card_number TEXT NOT NULL,
            type TEXT NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            date TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ==================== النماذج ====================
class UserAuth(BaseModel):
    username: str
    password: str

class CardModel(BaseModel):
    user_id: int
    bank_name: str
    card_number: str
    credit_limit: float
    current_debt: float
    due_day: int = 25

class SpendModel(BaseModel):
    user_id: int
    card_number: str
    amount: float
    spend_type: str = "spend"
    category: str = "مشتريات"

class PayModel(BaseModel):
    user_id: int
    card_number: str
    amount: float

class SmsModel(BaseModel):
    user_id: int
    sms_text: str

# ==================== المسارات الأساسية ====================

@app.get("/")
def root():
    return {"status": "online", "message": "Credit Tracker Database Ready"}

@app.post("/register")
@app.post("/api/register")
def register(user: UserAuth):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ?", (user.username,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="اسم المستخدم موجود بالفعل")

    hashed_pwd = bcrypt.hashpw(user.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (user.username, hashed_pwd))
    conn.commit()
    conn.close()
    return {"message": "تم إنشاء الحساب بنجاح!"}

@app.post("/login")
@app.post("/api/login")
def login(user: UserAuth):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (user.username,))
    db_user = cursor.fetchone()
    conn.close()

    if not db_user:
        raise HTTPException(status_code=400, detail="اسم المستخدم غير موجود، سجل حساباً جديداً أولاً")

    if not bcrypt.checkpw(user.password.encode('utf-8'), db_user["password"].encode('utf-8')):
        raise HTTPException(status_code=400, detail="كلمة المرور غير صحيحة")

    return {"message": "تم تسجيل الدخول بنجاح", "user_id": db_user["id"]}

@app.get("/get-dashboard/{user_id}")
@app.get("/api/get-dashboard/{user_id}")
def get_dashboard(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cards WHERE user_id = ?", (user_id,))
    cards = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return {"cards": cards}

@app.post("/add-card")
@app.post("/api/add-card")
def add_card(card: CardModel):
    conn = get_db()
    cursor = conn.cursor()
    avail = card.credit_limit - card.current_debt
    cursor.execute('''
        INSERT INTO cards (user_id, bank_name, card_number, credit_limit, current_debt, available_credit, due_day)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (card.user_id, card.bank_name, card.card_number, card.credit_limit, card.current_debt, avail, card.due_day))
    conn.commit()
    conn.close()
    return {"message": "تمت إضافة البطاقة بنجاح"}

@app.delete("/delete-card/{card_number}")
@app.delete("/api/delete-card/{card_number}")
def delete_card(card_number: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM cards WHERE card_number = ?", (card_number,))
    conn.commit()
    conn.close()
    return {"message": "تم إزالة البطاقة"}

@app.post("/spend")
@app.post("/api/spend")
def spend_money(data: SpendModel):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cards WHERE user_id = ? AND card_number = ?", (data.user_id, data.card_number))
    card = cursor.fetchone()
    if not card:
        conn.close()
        raise HTTPException(status_code=404, detail="البطاقة غير موجودة")

    new_debt = card["current_debt"] + data.amount
    new_avail = card["available_credit"] - data.amount

    cursor.execute("UPDATE cards SET current_debt = ?, available_credit = ? WHERE id = ?", (new_debt, new_avail, card["id"]))
    cursor.execute("INSERT INTO transactions (user_id, card_number, type, category, amount, date) VALUES (?, ?, 'spend', ?, ?, ?)",
                   (data.user_id, data.card_number, data.category, data.amount, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()
    return {"message": "تم تسجيل عملية الخصم بنجاح"}

@app.post("/pay-manual")
@app.post("/api/pay-manual")
def pay_manual(data: PayModel):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cards WHERE user_id = ? AND card_number = ?", (data.user_id, data.card_number))
    card = cursor.fetchone()
    if not card:
        conn.close()
        raise HTTPException(status_code=404, detail="البطاقة غير موجودة")

    new_debt = max(0.0, card["current_debt"] - data.amount)
    new_avail = card["available_credit"] + data.amount

    cursor.execute("UPDATE cards SET current_debt = ?, available_credit = ? WHERE id = ?", (new_debt, new_avail, card["id"]))
    cursor.execute("INSERT INTO transactions (user_id, card_number, type, category, amount, date) VALUES (?, ?, 'payment', 'سداد يدوي', ?, ?)",
                   (data.user_id, data.card_number, data.amount, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()
    return {"message": "تم تسجيل عملية السداد بنجاح"}

@app.get("/get-transactions/{user_id}")
@app.get("/api/get-transactions/{user_id}")
def get_transactions(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC", (user_id,))
    txs = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return txs

@app.post("/parse-sms")
@app.post("/api/parse-sms")
def parse_sms(data: SmsModel):
    amounts = re.findall(r'\d+(?:\.\d+)?', data.sms_text)
    if not amounts:
        raise HTTPException(status_code=400, detail="لم نتمكن من تحديد المبلغ من نص الرسالة")
    
    amt = float(amounts[0])
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cards WHERE user_id = ?", (data.user_id,))
    card = cursor.fetchone()
    
    if card:
        new_debt = card["current_debt"] + amt
        new_avail = card["available_credit"] - amt
        cursor.execute("UPDATE cards SET current_debt = ?, available_credit = ? WHERE id = ?", (new_debt, new_avail, card["id"]))
        cursor.execute("INSERT INTO transactions (user_id, card_number, type, category, amount, date) VALUES (?, ?, 'spend', 'SMS', ?, ?)",
                       (data.user_id, card["card_number"], amt, datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()

    conn.close()
    return {"message": f"تم التعرف على خصم بمبلغ {amt} ج.م وتم تحديث البطاقة"}

@app.post("/scan-instapay")
@app.post("/api/scan-instapay")
async def scan_instapay(
    user_id: int = Form(...),
    card_number: str = Form(...),
    file: UploadFile = File(...)
):
    try:
        # قراءة صورة الإيصال المرفوعة
        image_bytes = await file.read()
        image = Image.open(io.BytesIO(image_bytes))
        
        # استخراج النصوص من الصورة عبر OCR
        results = reader.readtext(image_bytes, detail=0)
        extracted_text = " ".join(results)
        
        # البحث عن أرقام المبالغ (يدعم الأرقام المسلسلة والكسور)
        clean_text = extracted_text.replace(',', '')
        numbers = re.findall(r'\d+(?:\.\d+)?', clean_text)
        
        if not numbers:
            raise HTTPException(status_code=400, detail="لم نتمكن من قراءة المبلغ من صورة الإيصال")
            
        # اختيار الرقم الأكبر غالباً ما يكون هو قيمة المعاملة في الإيصال
        extracted_amount = max([float(num) for num in numbers if float(num) > 0])
        
    except Exception as e:
        raise HTTPException(status_code=400, detail="فشل في تحليل الصورة. تأكد من وضوح الإيصال.")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cards WHERE user_id = ? AND card_number = ?", (user_id, card_number))
    card = cursor.fetchone()
    
    if not card:
        conn.close()
        raise HTTPException(status_code=404, detail="البطاقة المحددة غير موجودة")

    # معالجة المعاملة كـ (إيداع / سداد مديونية)
    new_debt = max(0.0, card["current_debt"] - extracted_amount)
    new_avail = card["available_credit"] + extracted_amount

    cursor.execute("UPDATE cards SET current_debt = ?, available_credit = ? WHERE id = ?", (new_debt, new_avail, card["id"]))
    cursor.execute("INSERT INTO transactions (user_id, card_number, type, category, amount, date) VALUES (?, ?, 'payment', 'إيداع انستا باي', ?, ?)",
                   (user_id, card_number, extracted_amount, datetime.now().strftime("%Y-%m-%d %H:%M")))
    
    conn.commit()
    conn.close()
    
    return {"message": f"تم فحص الإيصال بنجاح وتخصيم مبلغ {extracted_amount} ج.م كسداد للمديونية"}

@app.post("/rollover-month/{user_id}")
@app.post("/api/rollover-month/{user_id}")
def rollover_month(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE cards SET next_month_debt = next_month_debt + current_debt, current_debt = 0.0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return {"message": "تم ترحيل المديونيات للشهر الجديد بنجاح"}
