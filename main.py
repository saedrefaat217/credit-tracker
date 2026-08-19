from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3
import bcrypt
import re
from datetime import datetime

app = FastAPI(title="Credit Tracker - Full System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ocr_reader = None

def get_ocr_reader():
    global ocr_reader
    if ocr_reader is None:
        import easyocr
        print("جاري تحميل قارئ النصوص OCR...")
        ocr_reader = easyocr.Reader(['ar', 'en'], gpu=False)
    return ocr_reader

def hash_password(password: str) -> str:
    pwd_bytes = password.encode('utf-8')[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    pwd_bytes = plain_password.encode('utf-8')[:72]
    hashed_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(pwd_bytes, hashed_bytes)

def init_db():
    conn = sqlite3.connect("credit_tracker.db")
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
            user_id INTEGER,
            bank_name TEXT NOT NULL,
            card_number TEXT UNIQUE NOT NULL,
            credit_limit REAL NOT NULL,
            available_credit REAL NOT NULL,
            current_debt REAL DEFAULT 0.0,
            next_month_debt REAL DEFAULT 0.0,
            due_day INTEGER DEFAULT 25,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            card_number TEXT,
            amount REAL,
            type TEXT,
            category TEXT,
            reference_number TEXT,
            date TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# نماذج Pydantic
class UserRegister(BaseModel):
    username: str
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

class AddCard(BaseModel):
    user_id: int
    bank_name: str
    card_number: str
    credit_limit: float
    current_debt: float
    due_day: int

class SpendMoney(BaseModel):
    user_id: int
    card_number: str
    amount: float
    spend_type: str
    category: str

class ManualPay(BaseModel):
    user_id: int
    card_number: str
    amount: float

class ParseSMS(BaseModel):
    user_id: int
    sms_text: str

@app.post("/api/register")
def register(user: UserRegister):
    conn = sqlite3.connect("credit_tracker.db")
    cursor = conn.cursor()
    try:
        hashed_pwd = hash_password(user.password)
        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (user.username, hashed_pwd))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="اسم المستخدم موجود بالفعل")
    conn.close()
    return {"message": "تم إنشاء الحساب بنجاح"}

@app.post("/api/login")
def login(user: UserLogin):
    conn = sqlite3.connect("credit_tracker.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, password FROM users WHERE username = ?", (user.username,))
    db_user = cursor.fetchone()
    conn.close()
    if not db_user or not verify_password(user.password, db_user[1]):
        raise HTTPException(status_code=401, detail="اسم المستخدم أو كلمة السر غير صحيحة")
    return {"user_id": db_user[0], "username": user.username}

@app.post("/api/add-card")
def add_card(data: AddCard):
    conn = sqlite3.connect("credit_tracker.db")
    cursor = conn.cursor()
    try:
        avail = max(0.0, data.credit_limit - data.current_debt)
        cursor.execute('''
            INSERT INTO cards (user_id, bank_name, card_number, credit_limit, available_credit, current_debt, next_month_debt, due_day)
            VALUES (?, ?, ?, ?, ?, ?, 0.0, ?)
        ''', (data.user_id, data.bank_name, data.card_number, data.credit_limit, avail, data.current_debt, data.due_day))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="رقم الكارت مسجل بالفعل")
    conn.close()
    return {"message": "تمت إضافة البطاقة بنجاح"}

@app.delete("/api/delete-card/{card_number}")
def delete_card(card_number: str):
    conn = sqlite3.connect("credit_tracker.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM cards WHERE card_number = ?", (card_number,))
    conn.commit()
    conn.close()
    return {"message": "تم حذف البطاقة بنجاح"}

@app.get("/api/get-dashboard/{user_id}")
def get_dashboard(user_id: int):
    conn = sqlite3.connect("credit_tracker.db")
    cursor = conn.cursor()
    cursor.execute("SELECT bank_name, card_number, credit_limit, available_credit, current_debt, next_month_debt, due_day FROM cards WHERE user_id = ?", (user_id,))
    cards_db = cursor.fetchall()
    
    cards_list = []
    today_day = datetime.now().day
    urgent_card = None
    min_days_left = 999

    for c in cards_db:
        bank, num, limit, avail, curr_debt, next_debt, due = c
        cards_list.append({
            "bank_name": bank,
            "card_number": num,
            "credit_limit": limit,
            "available_credit": avail,
            "current_debt": curr_debt,
            "next_month_debt": next_debt,
            "due_day": due
        })
        days_left = due - today_day
        if curr_debt > 0:
            effective_days = days_left if days_left >= 0 else days_left + 30
            if effective_days < min_days_left:
                min_days_left = effective_days
                urgent_card = {
                    "bank_name": bank,
                    "card_number": f"**** **** **** {num[-4:]}",
                    "debt": curr_debt,
                    "due_day": due,
                    "days_left": effective_days
                }

    conn.close()
    return {"cards": cards_list, "urgent_due": urgent_card}

@app.post("/api/spend")
def spend(data: SpendMoney):
    conn = sqlite3.connect("credit_tracker.db")
    cursor = conn.cursor()
    cursor.execute("SELECT available_credit, next_month_debt FROM cards WHERE card_number = ? AND user_id = ?", (data.card_number, data.user_id))
    card = cursor.fetchone()
    if not card:
        conn.close()
        raise HTTPException(status_code=404, detail="البطاقة غير موجودة")
        
    avail, next_debt = card
    if data.amount > avail:
        conn.close()
        raise HTTPException(status_code=400, detail="الرصيد المتاح لا يكفي لهذه العملية")
        
    new_avail = avail - data.amount
    new_next_debt = next_debt + data.amount
    cursor.execute("UPDATE cards SET available_credit = ?, next_month_debt = ? WHERE card_number = ?", (new_avail, new_next_debt, data.card_number))
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute("INSERT INTO transactions (user_id, card_number, amount, type, category, date) VALUES (?, ?, ?, 'spend', ?, ?)",
                   (data.user_id, data.card_number, data.amount, data.category, now))
    conn.commit()
    conn.close()
    return {"message": "تم خصم المبلغ وتسجيل المشتريات بنجاح"}

@app.post("/api/pay-manual")
def pay_manual(data: ManualPay):
    conn = sqlite3.connect("credit_tracker.db")
    cursor = conn.cursor()
    cursor.execute("SELECT available_credit, current_debt, credit_limit FROM cards WHERE card_number = ? AND user_id = ?", (data.card_number, data.user_id))
    card = cursor.fetchone()
    if not card:
        conn.close()
        raise HTTPException(status_code=404, detail="البطاقة غير موجودة")
        
    avail, curr_debt, limit = card
    new_curr_debt = max(0.0, curr_debt - data.amount)
    new_avail = min(limit, avail + data.amount)
    cursor.execute("UPDATE cards SET available_credit = ?, current_debt = ? WHERE card_number = ?", (new_avail, new_curr_debt, data.card_number))
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute("INSERT INTO transactions (user_id, card_number, amount, type, category, date) VALUES (?, ?, ?, 'payment', 'سداد يدوي', ?)",
                   (data.user_id, data.card_number, data.amount, now))
    conn.commit()
    conn.close()
    return {"message": "تم تسجيل السداد بنجاح وتحديث المديونية"}

@app.get("/api/get-transactions/{user_id}")
def get_transactions(user_id: int):
    conn = sqlite3.connect("credit_tracker.db")
    cursor = conn.cursor()
    cursor.execute("SELECT card_number, amount, type, category, reference_number, date FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 20", (user_id,))
    txs = cursor.fetchall()
    conn.close()
    
    res = []
    for t in txs:
        res.append({
            "card_number": t[0],
            "amount": t[1],
            "type": t[2],
            "category": t[3],
            "reference_number": t[4],
            "date": t[5]
        })
    return res

@app.post("/api/rollover-month/{user_id}")
def rollover_month(user_id: int):
    conn = sqlite3.connect("credit_tracker.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE cards SET current_debt = next_month_debt, next_month_debt = 0.0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return {"message": "تم ترحيل المديونيات للشهر الجديد بنجاح!"}

@app.post("/api/scan-instapay")
async def scan_instapay(file: UploadFile = File(...), user_id: int = Form(...)):
    try:
        reader = get_ocr_reader()
        image_bytes = await file.read()
        results = reader.readtext(image_bytes, detail=0)
        extracted_text = " ".join(results)
        
        amount = 0.0
        amount_match = re.search(r'([\d,]+(?:\.\d{1,2})?)\s*(?:EGP|LE|ج\.م|جنيه)', extracted_text, re.IGNORECASE)
        if amount_match:
            amount = float(amount_match.group(1).replace(',', ''))

        ref_match = re.search(r'\b\d{10,18}\b', extracted_text)
        reference_number = ref_match.group(0) if ref_match else "غير معروف"

        conn = sqlite3.connect("credit_tracker.db")
        cursor = conn.cursor()
        cursor.execute("SELECT card_number, available_credit, current_debt, credit_limit FROM cards WHERE user_id = ?", (user_id,))
        user_cards = cursor.fetchall()
        
        updated_card = None
        for card in user_cards:
            card_num, avail, curr_debt, limit = card
            if card_num in extracted_text or card_num[-8:] in extracted_text:
                new_curr_debt = max(0.0, curr_debt - amount)
                new_avail = min(limit, avail + amount)
                cursor.execute("UPDATE cards SET available_credit = ?, current_debt = ? WHERE card_number = ?", (new_avail, new_curr_debt, card_num))
                updated_card = card_num
                break
                
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        cursor.execute("INSERT INTO transactions (user_id, card_number, amount, type, category, reference_number, date) VALUES (?, ?, ?, 'payment', 'سداد انستا باي', ?, ?)",
                       (user_id, updated_card if updated_card else "غير محدد", amount, reference_number, now))
                       
        conn.commit()
        conn.close()
        return {"status": "success", "amount_paid": amount, "card_deducted": updated_card, "ref_num": reference_number}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# ميزة تحليل رسائل البنوك النصية SMS Parsing
# ==========================================
@app.post("/api/parse-sms")
def parse_sms(data: ParseSMS):
    text = data.sms_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="يرجى إدخال نص الرسالة")

    clean_text = text.replace(',', '')

    # 1. استخراج مبلغ العملية الرئيسية
    amount_match = re.search(r'(?:بمبلغ|خصم|خصم مبلغ|مبلغ|debited|spent|amount|credited)\s*([\d\.]+)', clean_text, re.IGNORECASE)
    if not amount_match:
        amount_match = re.search(r'([\d\.]+)\s*(?:EGP|LE|ج\.م|جم|جنيه|\$|USD)', clean_text, re.IGNORECASE)

    amount = 0.0
    if amount_match:
        try:
            amount = float(amount_match.group(1))
        except ValueError:
            pass

    if amount <= 0:
        raise HTTPException(status_code=400, detail="لم يتم التعرف على مبلغ العملية في الرسالة")

    # 2. استخراج الرصيد المتاح المباشر المكتوب بالرسالة (Available Balance) إن وجد
    avail_match = re.search(r'(?:المتاح|الرصيد المتاح|متاح|avail|available|avail bal|bal)\D*([\d\.]+)', clean_text, re.IGNORECASE)
    explicit_available = float(avail_match.group(1)) if avail_match else None

    # 3. استخراج أخر 4 أرقام من الكارت
    card_match = re.search(r'(?:card|الكارت|بطاقة|بطاقتكم|حساب|ending in|ending with|\*+)\D*(\d{4})', clean_text, re.IGNORECASE)
    card_last4 = card_match.group(1) if card_match else None

    # 4. تحديد نوع العملية (سداد/إيداع/تحويل لحظي أم خصم/مشتريات)
    is_payment = bool(re.search(r'(سداد|إضافة|تحويل لحظي|إيداع|تم إيداع|تم استلام|وارد|تم إضافة|payment|credited|received|deposit)', clean_text, re.IGNORECASE))

    # 5. استخراج اسم التاجر/الجهة (إن وجد)
    merchant = "رسالة بنكية"
    merchant_match = re.search(r'(?:at|لدى|من)\s+([A-Za-z0-9\s\_]+)', clean_text, re.IGNORECASE)
    if merchant_match:
        merchant = merchant_match.group(1).strip()[:20]

    # 6. مطابقة الكارت مع قواعد البيانات وتحديث الرصيد
    conn = sqlite3.connect("credit_tracker.db")
    cursor = conn.cursor()
    cursor.execute("SELECT card_number, available_credit, current_debt, next_month_debt, credit_limit FROM cards WHERE user_id = ?", (data.user_id,))
    user_cards = cursor.fetchall()

    matched_card = None
    if card_last4:
        for c in user_cards:
            if c[0].endswith(card_last4):
                matched_card = c
                break

    # إذا لم يجد كارت محدد بواسطة أخر 4 أرقام وكان لدى المستخدم كارت واحد فقط يتم اختياره تلقائياً
    if not matched_card and len(user_cards) == 1:
        matched_card = user_cards[0]

    if not matched_card:
        conn.close()
        return {
            "status": "parsed_only",
            "amount": amount,
            "type": "payment" if is_payment else "spend",
            "card_last4": card_last4,
            "available_balance": explicit_available,
            "message": f"تم التعرف على المبلغ ({amount} ج.م) ولكن لم يتم العثور على كارت ينتهي بـ ({card_last4 or 'غير معروف'})"
        }

    card_num, avail, curr_debt, next_debt, limit = matched_card
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    if is_payment:
        # استخدام الرصيد المتاح المباشر إن أمكن أو احتسابه
        new_avail = explicit_available if explicit_available is not None else min(limit, avail + amount)
        new_curr_debt = max(0.0, curr_debt - amount)

        cursor.execute("UPDATE cards SET available_credit = ?, current_debt = ? WHERE card_number = ?", (new_avail, new_curr_debt, card_num))
        cursor.execute("INSERT INTO transactions (user_id, card_number, amount, type, category, date) VALUES (?, ?, ?, 'payment', 'سداد / تحويل لحظي', ?)",
                       (data.user_id, card_num, amount, now))
        msg = f"تم تسجيل إيداع/تحويل مبلغ {amount} ج.م وتحديث الرصيد المتاح إلى ({new_avail} ج.م) للكارت (..{card_num[-4:]}) بنجاح!"
    else:
        # استخدام الرصيد المتاح المباشر إن أمكن أو احتسابه
        new_avail = explicit_available if explicit_available is not None else max(0.0, avail - amount)
        new_next_debt = next_debt + amount

        cursor.execute("UPDATE cards SET available_credit = ?, next_month_debt = ? WHERE card_number = ?", (new_avail, new_next_debt, card_num))
        cursor.execute("INSERT INTO transactions (user_id, card_number, amount, type, category, date) VALUES (?, ?, ?, 'spend', ?, ?)",
                       (data.user_id, card_num, amount, f"شراء ({merchant})", now))
        msg = f"تم تسجيل خصم/شراء بمبلغ {amount} ج.م وتحديث الرصيد المتاح إلى ({new_avail} ج.م) للكارت (..{card_num[-4:]}) بنجاح!"

    conn.commit()
    conn.close()
    return {
        "status": "success",
        "amount": amount,
        "type": "payment" if is_payment else "spend",
        "card_number": card_num,
        "available_credit": new_avail,
        "message": msg
    }
