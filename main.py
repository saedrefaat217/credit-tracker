from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import bcrypt
import re
import requests
from datetime import datetime

app = FastAPI(
    title="Credit Tracker Engine",
    description="Backend API with Free OCR Integration",
    version="2.1.0"
)

# 1. إعدادات CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# قواعد البيانات المباشرة في الذاكرة
users_db: Dict[str, Dict[str, Any]] = {}
cards_db: List[Dict[str, Any]] = []
transactions_db: List[Dict[str, Any]] = []
user_id_counter = 1


# ==================== النماذج (Schemas) ====================

class UserRegisterSchema(BaseModel):
    username: str
    password: str

class UserLoginSchema(BaseModel):
    username: str
    password: str

class CardCreateSchema(BaseModel):
    user_id: int
    bank_name: str
    card_number: str
    credit_limit: float
    current_debt: float = 0.0
    due_day: int = 25

class SpendSchema(BaseModel):
    user_id: int
    card_number: str
    amount: float
    spend_type: str = "spend"
    category: str = "مشتريات"

class PaySchema(BaseModel):
    user_id: int
    card_number: str
    amount: float

class SmsParseSchema(BaseModel):
    user_id: int
    sms_text: str


# ==================== الأدوات المساعدة ====================

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def extract_amount_from_text(text: str) -> Optional[float]:
    """دالة استخراج المبلغ المالي من النص"""
    patterns = [
        r'(?:EGP|LE|LE\.|جنيه|مبلغ)\s*([\d,]+(?:\.\d+)?)',
        r'([\d,]+(?:\.\d+)?)\s*(?:EGP|LE|جنيه)',
        r'([\d,]+(?:\.\d+)?)'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            clean_str = match.group(1).replace(',', '')
            try:
                val = float(clean_str)
                if val > 0:
                    return val
            except ValueError:
                continue
    return None


# ==================== المسارات والخدمات ====================

@app.get("/")
def home():
    return {"status": "online", "message": "Credit Tracker Backend with OCR is Running"}


# --------------- 1. المصادقة ---------------

@app.post("/register")
@app.post("/api/register")
def register_user(payload: UserRegisterSchema):
    global user_id_counter
    if payload.username in users_db:
        raise HTTPException(status_code=400, detail="اسم المستخدم موجود بالفعل")
    
    users_db[payload.username] = {
        "id": user_id_counter,
        "username": payload.username,
        "password": hash_password(payload.password)
    }
    user_id_counter += 1
    return {"status": "success", "message": "تم حسابك بنجاح!"}

@app.post("/login")
@app.post("/api/login")
def login_user(payload: UserLoginSchema):
    user = users_db.get(payload.username)
    if not user or not verify_password(payload.password, user["password"]):
        raise HTTPException(status_code=400, detail="اسم المستخدم أو كلمة السر غير صحيحة")
    
    return {"status": "success", "message": "تم تسجيل الدخول بنجاح", "user_id": user["id"]}


# --------------- 2. البطاقات واللوحة ---------------

@app.get("/get-dashboard/{user_id}")
@app.get("/api/get-dashboard/{user_id}")
def get_user_dashboard(user_id: int):
    user_cards = [c for c in cards_db if c["user_id"] == user_id]
    return {"cards": user_cards}

@app.post("/add-card")
@app.post("/api/add-card")
def add_new_card(card: CardCreateSchema):
    avail = card.credit_limit - card.current_debt
    card_entry = {
        "user_id": card.user_id,
        "bank_name": card.bank_name,
        "card_number": card.card_number,
        "credit_limit": card.credit_limit,
        "current_debt": card.current_debt,
        "next_month_debt": 0.0,
        "available_credit": avail,
        "due_day": card.due_day
    }
    cards_db.append(card_entry)
    return {"status": "success", "message": "تم إضافة البطاقة بنجاح"}

@app.delete("/delete-card/{card_number}")
@app.delete("/api/delete-card/{card_number}")
def remove_card(card_number: str):
    global cards_db
    cards_db = [c for c in cards_db if c["card_number"] != card_number]
    return {"status": "success", "message": "تمت إزالة البطاقة"}


# --------------- 3. المعاملات والسداد ---------------

@app.post("/spend")
@app.post("/api/spend")
def record_expense(data: SpendSchema):
    for card in cards_db:
        if card["user_id"] == data.user_id and card["card_number"] == data.card_number:
            card["current_debt"] += data.amount
            card["available_credit"] -= data.amount
            transactions_db.append({
                "user_id": data.user_id,
                "card_number": data.card_number,
                "type": "spend",
                "category": data.category,
                "amount": data.amount,
                "date": datetime.now().strftime("%Y-%m-%d %H:%M")
            })
            return {"status": "success", "message": "تم خصم المبلغ وتحديث اللوحة"}

    raise HTTPException(status_code=404, detail="البطاقة غير موجودة")

@app.post("/pay-manual")
@app.post("/api/pay-manual")
def record_payment(data: PaySchema):
    for card in cards_db:
        if card["user_id"] == data.user_id and card["card_number"] == data.card_number:
            card["current_debt"] = max(0.0, card["current_debt"] - data.amount)
            card["available_credit"] += data.amount
            transactions_db.append({
                "user_id": data.user_id,
                "card_number": data.card_number,
                "type": "payment",
                "category": "سداد مديونية",
                "amount": data.amount,
                "date": datetime.now().strftime("%Y-%m-%d %H:%M")
            })
            return {"status": "success", "message": "تم تسجيل عملية السداد بنجاح"}

    raise HTTPException(status_code=404, detail="البطاقة غير موجودة")

@app.get("/get-transactions/{user_id}")
@app.get("/api/get-transactions/{user_id}")
def fetch_transaction_history(user_id: int):
    return [t for t in transactions_db if t["user_id"] == user_id][::-1]


# --------------- 4. فحص الإيصالات أونلاين (OCR) ورسائل SMS ---------------

@app.post("/scan-instapay")
@app.post("/api/scan-instapay")
async def process_instapay_receipt(user_id: int = Form(...), file: UploadFile = File(...)):
    """فحص صورة إيصال انستا باي عبر API مجاني خفيف جداً"""
    try:
        file_bytes = await file.read()
        
        # إرسال الصورة لخدمة OCR.space المجانية
        response = requests.post(
            'https://api.ocr.space/parse/image',
            files={'filename': (file.filename, file_bytes, file.content_type)},
            data={'apikey': 'helloworld', 'language': 'eng'}  # مفتاح مجاني
        )
        
        result = response.json()
        
        if result.get("IsErroredOnProcessing"):
            raise HTTPException(status_code=400, detail="تعذر قراءة الصورة، يرجى رفع صورة أوضح.")

        # استخراج النص المقروء من الصورة
        parsed_text = result["ParsedResults"][0]["ParsedText"]
        amount = extract_amount_from_text(parsed_text)

        if not amount:
            raise HTTPException(status_code=400, detail="لم نتمكن من تحديد قيمة المبلغ المالي في الإيصال.")

        # تطبيق السداد على أول بطاقة للمستخدم تلقائياً
        user_cards = [c for c in cards_db if c["user_id"] == user_id]
        if user_cards:
            target_card = user_cards[0]
            target_card["current_debt"] = max(0.0, target_card["current_debt"] - amount)
            target_card["available_credit"] += amount

            transactions_db.append({
                "user_id": user_id,
                "card_number": target_card["card_number"],
                "type": "payment",
                "category": "إيصال انستا باي (OCR)",
                "amount": amount,
                "date": datetime.now().strftime("%Y-%m-%d %H:%M")
            })

        return {
            "status": "success",
            "message": f"تم التعرف على إيصال بمبلغ {amount} ج.م وتم سداده بنجاح!",
            "amount_paid": amount
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"خطأ أثناء معالجة الصورة: {str(e)}")

@app.post("/parse-sms")
@app.post("/api/parse-sms")
def parse_bank_sms(data: SmsParseSchema):
    amount = extract_amount_from_text(data.sms_text)
    if not amount:
        raise HTTPException(status_code=400, detail="لم يتم العثور على أرقام مبالغ في الرسالة.")

    user_cards = [c for c in cards_db if c["user_id"] == data.user_id]
    if not user_cards:
        raise HTTPException(status_code=404, detail="قم بإضافة بطاقة أولاً لخصم المعاملة منها.")

    target_card = user_cards[0]
    target_card["current_debt"] += amount
    target_card["available_credit"] -= amount

    transactions_db.append({
        "user_id": data.user_id,
        "card_number": target_card["card_number"],
        "type": "spend",
        "category": "رسالة بنكية (SMS)",
        "amount": amount,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M")
    })

    return {"status": "success", "message": f"تم خصم {amount} ج.م بناءً على رسالة البنك."}

@app.post("/rollover-month/{user_id}")
@app.post("/api/rollover-month/{user_id}")
def rollover_monthly_debts(user_id: int):
    for c in cards_db:
        if c["user_id"] == user_id:
            c["current_debt"] += c["next_month_debt"]
            c["next_month_debt"] = 0.0
    return {"status": "success", "message": "تم ترحيل المديونيات للشهر الجديد بنجاح."}
