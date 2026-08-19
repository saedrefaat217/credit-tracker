from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import sqlite3
import re
from datetime import datetime

app = FastAPI(title="Credit Tracker API")

# إعداد CORS للسماح بالاتصال من الواجهة الأمامية
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE = "credit_tracker.db"

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# إنشاء الجداول عند تشغيل البرنامج
def init_db():
    with get_db() as conn:
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
                current_debt REAL DEFAULT 0,
                next_month_debt REAL DEFAULT 0,
                due_day INTEGER DEFAULT 25,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                card_number TEXT NOT NULL,
                amount REAL NOT NULL,
                type TEXT NOT NULL, -- 'spend' or 'payment'
                category TEXT NOT NULL,
                date TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        conn.commit()

init_db()

# نماذج البيانات (Pydantic Models)
class UserAuth(BaseModel):
    username: str
    password: str

class CardAdd(BaseModel):
    user_id: int
    bank_name: str
    card_number: str
    credit_limit: float
    current_debt: float = 0.0
    due_day: int = 25

class SpendRequest(BaseModel):
    user_id: int
    card_number: str
    amount: float
    spend_type: str = "spend"
    category: str = "مشتريات"
    target_field: Optional[str] = "next_month_debt" # الخيار الافتراضي هو مديونية الشهر القادم

class PayRequest(BaseModel):
    user_id: int
    card_number: str
    amount: float

class SMSRequest(BaseModel):
    user_id: int
    sms_text: str

# API Auth
@app.post("/api/register")
def register(user: UserAuth):
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (user.username, user.password))
            conn.commit()
            return {"message": "تم إنشاء الحساب بنجاح"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="اسم المستخدم موجود بالفعل")

@app.post("/api/login")
def login(user: UserAuth):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = ? AND password = ?", (user.username, user.password))
        row = cursor.fetchone()
        if row:
            return {"user_id": row["id"], "message": "تم تسجيل الدخول بنجاح"}
        raise HTTPException(status_code=401, detail="اسم المستخدم أو كلمة السر غير صحيحة")

# API Cards Management
@app.post("/api/add-card")
def add_card(card: CardAdd):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO cards (user_id, bank_name, card_number, credit_limit, current_debt, next_month_debt, due_day) VALUES (?, ?, ?, ?, ?, 0, ?)",
            (card.user_id, card.bank_name, card.card_number, card.credit_limit, card.current_debt, card.due_day)
        )
        conn.commit()
        return {"message": "تمت إضافة البطاقة بنجاح"}

@app.get("/api/get-dashboard/{user_id}")
def get_dashboard(user_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cards WHERE user_id = ?", (user_id,))
        rows = cursor.fetchall()
        
        cards = []
        for r in rows:
            avail = r["credit_limit"] - (r["current_debt"] + r["next_month_debt"])
            cards.append({
                "id": r["id"],
                "bank_name": r["bank_name"],
                "card_number": r["card_number"],
                "credit_limit": r["credit_limit"],
                "current_debt": r["current_debt"],
                "next_month_debt": r["next_month_debt"],
                "available_credit": max(0, avail),
                "due_day": r["due_day"]
            })
        return {"cards": cards}

@app.delete("/api/delete-card/{card_number}")
def delete_card(card_number: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cards WHERE card_number = ?", (card_number,))
        conn.commit()
        return {"message": "تم حذف البطاقة بنجاح"}

# API Transactions
@app.post("/api/spend")
def spend(data: SpendRequest):
    if data.amount <= 0:
        raise HTTPException(status_code=400, detail="المبلغ يجب أن يكون أكبر من 0")
        
    field_to_update = "next_month_debt" if data.target_field == "next_month_debt" else "current_debt"
    
    with get_db() as conn:
        cursor = conn.cursor()
        # إضافة المعاملة على مديونية الشهر المحspecified
        cursor.execute(
            f"UPDATE cards SET {field_to_update} = {field_to_update} + ? WHERE user_id = ? AND card_number = ?",
            (data.amount, data.user_id, data.card_number)
        )
        
        # تسجيل المعاملة في السجل
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        cursor.execute(
            "INSERT INTO transactions (user_id, card_number, amount, type, category, date) VALUES (?, ?, ?, 'spend', ?, ?)",
            (data.user_id, data.card_number, data.amount, data.category, now_str)
        )
        conn.commit()
        return {"message": "تم تسجيل عملية الصرف بنجاح"}

@app.post("/api/pay-manual")
def pay_manual(data: PayRequest):
    if data.amount <= 0:
        raise HTTPException(status_code=400, detail="المبلغ يجب أن يكون أكبر من 0")
        
    with get_db() as conn:
        cursor = conn.cursor()
        # خصم المبلغ من المديونية الحالية
        cursor.execute(
            "UPDATE cards SET current_debt = MAX(0, current_debt - ?) WHERE user_id = ? AND card_number = ?",
            (data.amount, data.user_id, data.card_number)
        )
        
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        cursor.execute(
            "INSERT INTO transactions (user_id, card_number, amount, type, category, date) VALUES (?, ?, ?, 'payment', 'سداد مديونية', ?)",
            (data.user_id, data.card_number, data.amount, now_str)
        )
        conn.commit()
        return {"message": "تم تسجيل عملية السداد بنجاح"}

@app.post("/api/scan-instapay")
def scan_instapay(
    user_id: int = Form(...),
    card_number: str = Form(...),
    amount: float = Form(...),
    file: Optional[UploadFile] = File(None)
):
    if amount <= 0:
        raise HTTPException(status_code=400, detail="المبلغ يجب أن يكون أكبر من 0")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE cards SET current_debt = MAX(0, current_debt - ?) WHERE user_id = ? AND card_number = ?",
            (amount, user_id, card_number)
        )
        
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        cursor.execute(
            "INSERT INTO transactions (user_id, card_number, amount, type, category, date) VALUES (?, ?, ?, 'payment', 'سداد InstaPay', ?)",
            (user_id, card_number, amount, now_str)
        )
        conn.commit()
        return {"message": "تم تسجيل سداد InstaPay بنجاح"}

@app.post("/api/parse-sms")
def parse_sms(sms: SMSRequest):
    text = sms.sms_text
    
    # استخراج المبلغ بواسطة Regex
    amount_match = re.search(r'(?:EGP|مبلغ|بقيمة)\s*([\d,]+\.?\d*)', text, re.IGNORECASE)
    if not amount_match:
        amount_match = re.search(r'([\d,]+\.?\d*)\s*(?:EGP|جنيه)', text, re.IGNORECASE)
        
    if not amount_match:
        raise HTTPException(status_code=400, detail="لم يتم التعرف على المبلغ في الرسالة")
        
    amount = float(amount_match.group(1).replace(',', ''))
    
    # استخراج آخر 4 أرقام من الكارت
    card_match = re.search(r'(?:بـ| ending in|card|كارتك)\s*(\d{4})', text, re.IGNORECASE)
    
    with get_db() as conn:
        cursor = conn.cursor()
        card_number = None
        
        if card_match:
            last4 = card_match.group(1)
            cursor.execute("SELECT card_number FROM cards WHERE user_id = ? AND card_number LIKE ?", (sms.user_id, f"%{last4}"))
            row = cursor.fetchone()
            if row:
                card_number = row["card_number"]
                
        # إذا لم يتم تحديد الكارت من الرسالة، اختيار أول كارت للمستخدم
        if not card_number:
            cursor.execute("SELECT card_number FROM cards WHERE user_id = ? LIMIT 1", (sms.user_id,))
            row = cursor.fetchone()
            if row:
                card_number = row["card_number"]
            else:
                raise HTTPException(status_code=404, detail="لم يتم العثور على بطاقات مسجلة للحساب")

        # الخصومات الواردة في الرسائل يجرى توجيهها تلقائياً نحو مديونية الشهر القادم
        cursor.execute(
            "UPDATE cards SET next_month_debt = next_month_debt + ? WHERE user_id = ? AND card_number = ?",
            (amount, sms.user_id, card_number)
        )
        
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        cursor.execute(
            "INSERT INTO transactions (user_id, card_number, amount, type, category, date) VALUES (?, ?, ?, 'spend', 'شراء عبر SMS', ?)",
            (sms.user_id, card_number, amount, now_str)
        )
        conn.commit()
        return {"message": f"تم خصم {amount} ج.م وإضافتها لمديونية الشهر القادم بنجاح"}

@app.get("/api/get-transactions/{user_id}")
def get_transactions(user_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC", (user_id,))
        rows = cursor.fetchall()
        
        txs = []
        for r in rows:
            txs.append({
                "id": r["id"],
                "card_number": r["card_number"],
                "amount": r["amount"],
                "type": r["type"],
                "category": r["category"],
                "date": r["date"]
            })
        return txs

@app.post("/api/rollover-month/{user_id}")
def rollover_month(user_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        # نقل مديونية الشهر القادم لتضاف على المديونية الحالية وتصفير مديونية الشهر القادم
        cursor.execute(
            "UPDATE cards SET current_debt = current_debt + next_month_debt, next_month_debt = 0 WHERE user_id = ?",
            (user_id,)
        )
        conn.commit()
        return {"message": "تم ترحيل المديونيات للشهر الجديد بنجاح"}
