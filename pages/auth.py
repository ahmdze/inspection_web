from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
import hashlib

router = APIRouter()

def hash_password(pw: str) -> str: 
    return hashlib.sha256(pw.encode()).hexdigest()

def verify_password(pw: str, h: str) -> bool: 
    return hash_password(pw) == h

def get_current_user(request: Request, db: Session):
    from itsdangerous import URLSafeTimedSerializer
    serializer = URLSafeTimedSerializer("SECRET_CHANGE_ME_IN_PROD", salt="auth-session")
    token = request.cookies.get("session_token")
    if not token: 
        raise HTTPException(401, "يجب تسجيل الدخول")
    try: 
        uid = serializer.loads(token, max_age=86400)
    except: 
        raise HTTPException(401, "انتهت الجلسة")
    from database import User
    user = db.query(User).filter(User.id == uid).first()
    if not user or not user.is_active: 
        raise HTTPException(401, "حساب غير نشط")
    return user

@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return """<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-4">
    <div class="max-w-md mx-auto bg-white p-6 rounded shadow mt-10">
    <h1 class="text-2xl font-bold text-blue-800 mb-2 text-center">نظام التفتيش الذكي المتقدم</h1>
    <p class="text-gray-500 text-center mb-4">إصدار 1.0</p>
    <form action="/login" method="post" class="space-y-4">
    <input name="username" placeholder="اسم المستخدم" required class="w-full p-2 border rounded">
    <input name="password" type="password" placeholder="كلمة المرور" required class="w-full p-2 border rounded">
    <div class="flex items-center">
      <input type="checkbox" id="remember" name="remember" class="ml-2">
      <label for="remember" class="text-sm text-gray-600">حفظ معلومات الدخول</label>
    </div>
    <button class="w-full bg-blue-600 text-white py-2 rounded">دخول</button></form>
    <div class="mt-4 text-center">
      <button onclick="showAbout()" class="text-blue-600 hover:underline text-sm">ℹ️ حول</button>
    </div>
    </div>
    
    <!-- نافذة حول -->
    <div id="about-modal" class="hidden fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div class="bg-white p-6 rounded-lg max-w-md mx-4 shadow-xl">
        <h2 class="text-xl font-bold text-blue-800 mb-4 text-center">معلومات المطور</h2>
        <div class="space-y-3 text-right">
          <div class="flex items-center gap-2">
            <span class="text-gray-600 font-bold">الاسم:</span>
            <span>تقني طبي - احمد زياد رحيمه</span>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-gray-600 font-bold">الهاتف:</span>
            <a href="tel:07723064622" class="text-blue-600 hover:underline">07723064622</a>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-gray-600 font-bold">البريد:</span>
            <a href="mailto:ahmdze@gmail.com" class="text-blue-600 hover:underline">ahmdze@gmail.com</a>
          </div>
        </div>
        <button onclick="closeAbout()" class="mt-6 w-full bg-gray-600 text-white py-2 rounded">إغلاق</button>
      </div>
    </div>
    
    <script>
    function showAbout() { document.getElementById('about-modal').classList.remove('hidden'); }
    function closeAbout() { document.getElementById('about-modal').classList.add('hidden'); }
    </script>
    </body></html>"""

@router.post("/login")
async def login(request: Request, db: Session = Depends(lambda: None), username: str = Form(...), password: str = Form(...)):
    from database import User, AuditLog
    from datetime import datetime
    from itsdangerous import URLSafeTimedSerializer
    serializer = URLSafeTimedSerializer("SECRET_CHANGE_ME_IN_PROD", salt="auth-session")
    
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash): 
        raise HTTPException(401, "بيانات خاطئة")
    token = serializer.dumps(user.id)
    resp = RedirectResponse(url="/dashboard", status_code=302)
    resp.set_cookie("session_token", token, httponly=True, max_age=86400)
    
    # تسجيل الإجراء
    ip = request.headers.get("x-forwarded-for", request.client.host)
    db.add(AuditLog(user_id=user.id, action="LOGIN", details="دخول ناجح", ip_address=ip, timestamp=datetime.now()))
    db.commit()
    return resp

@router.post("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("session_token")
    return resp

@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, db: Session = Depends(lambda: None)):
    user = get_current_user(request, db)
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-gray-50 p-4">
    <div class="max-w-md mx-auto bg-white p-6 rounded shadow">
    <div class="flex justify-between mb-4">
        <h1 class="text-xl font-bold">👤 الملف الشخصي</h1>
        <a href="/dashboard" class="text-blue-600">← العودة</a>
    </div>
    <form action="/profile" method="post" class="space-y-4">
      <div>
        <label class="block font-bold mb-2">اسم المستخدم</label>
        <input name="username" value="{user.username}" required class="w-full p-2 border rounded">
      </div>
      <div>
        <label class="block font-bold mb-2">كلمة المرور الجديدة</label>
        <input name="password" type="password" placeholder="اتركه فارغاً إذا لم ترد تغييره" class="w-full p-2 border rounded">
      </div>
      <div>
        <label class="block font-bold mb-2">الدور</label>
        <input type="text" value="{user.role}" disabled class="w-full p-2 border rounded bg-gray-100">
        <p class="text-sm text-gray-500 mt-1">لا يمكن تغيير الدور من هنا، تواصل مع المدير</p>
      </div>
      <button class="w-full bg-blue-600 text-white py-2 rounded">💾 حفظ التغييرات</button>
    </form>
    </div></body></html>"""

@router.post("/profile")
async def profile_update(request: Request, db: Session = Depends(lambda: None), 
                         username: str = Form(...), password: str = Form("")):
    from database import User, AuditLog
    from datetime import datetime
    from itsdangerous import URLSafeTimedSerializer
    serializer = URLSafeTimedSerializer("SECRET_CHANGE_ME_IN_PROD", salt="auth-session")
    
    user = get_current_user(request, db)
    
    # التحقق من عدم تكرار اسم المستخدم
    if username != user.username and db.query(User).filter(User.username == username).first():
        raise HTTPException(400, "اسم المستخدم مستخدم بالفعل")
    
    user.username = username
    if password.strip():
        user.password_hash = hash_password(password)
    
    db.commit()
    
    # تسجيل الإجراء
    ip = request.headers.get("x-forwarded-for", request.client.host)
    db.add(AuditLog(user_id=user.id, action="UPDATE_PROFILE", details=f"تعديل الملف الشخصي: {username}", ip_address=ip, timestamp=datetime.now()))
    db.commit()
    
    return RedirectResponse(url="/profile", status_code=302)
