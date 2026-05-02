from fastapi import APIRouter, Request, Depends, Form, HTTPException, Query, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session
import io
import pandas as pd

router = APIRouter()

def get_db():
    from database import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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

def require_role(*roles: str):
    def dep(user=Depends(lambda r, db: get_current_user(r, db))):
        if user.role not in roles: 
            raise HTTPException(403, "صلاحية غير كافية")
        return user
    return dep

@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request, db: Session = Depends(get_db)):
    from database import User
    user = get_current_user(request, db)
    if user.role not in ["admin"]:
        raise HTTPException(403, "صلاحية غير كافية")
    
    users = db.query(User).all()
    rows = "".join(f'''<tr class="border-b"><td class="p-2">{u.username}</td><td class="p-2">{u.role}</td>
    <td class="p-2">
    <form action="/admin/users/{u.id}/toggle" method="post" class="inline">
      <button class="px-2 py-1 rounded text-xs {'bg-green-500 text-white' if u.is_active else 'bg-red-500 text-white'}">{'✅' if u.is_active else '❌'}</button></form></td>
    <td class="p-2">
      <a href="/admin/users/{u.id}/edit" class="bg-yellow-500 text-white px-2 py-1 rounded text-xs mr-1">✏️ تعديل</a>
      {'<form action="/admin/users/'+str(u.id)+'/delete" method="post" class="inline" onsubmit="return confirm(\'حذف؟\')">' if u.id!=user.id else ''}
      {'<button class="bg-red-600 text-white px-2 py-1 rounded text-xs">حذف</button>' if u.id!=user.id else ''}
    {'</form>' if u.id!=user.id else ''}</td></tr>''' for u in users)
    
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-4">
    <div class="max-w-6xl mx-auto bg-white p-6 rounded shadow"><div class="flex justify-between mb-4"><h1 class="text-xl font-bold">👥 المستخدمون</h1><a href="/admin/panel" class="text-blue-600">← العودة</a></div>
    
    <!-- أزرار الإكسل -->
    <div class="mb-4 flex gap-2">
      <form action="/admin/users/export" method="get" class="inline">
        <button class="bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded">📤 تصدير Excel</button>
      </form>
      <form action="/admin/users/import" method="post" enctype="multipart/form-data" class="inline flex gap-2">
        <input type="file" name="file" accept=".xlsx,.xls" required class="p-2 border rounded">
        <button class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded">📥 استيراد Excel</button>
      </form>
    </div>
    
    <form action="/admin/users" method="post" class="bg-gray-50 p-4 rounded mb-6 grid grid-cols-2 md:grid-cols-4 gap-2">
      <input name="username" placeholder="اسم المستخدم" required class="p-2 border rounded">
      <input name="password" type="password" placeholder="كلمة المرور" required class="p-2 border rounded">
      <select name="role" class="p-2 border rounded"><option value="inspector">مفتش</option><option value="admin">مدير</option></select>
      <button class="bg-green-600 text-white p-2 rounded">➕ إضافة</button></form>
    <table class="w-full text-right"><thead class="bg-gray-100"><tr><th class="p-2">الاسم</th><th>الدور</th><th>الحالة</th><th>إجراء</th></tr></thead><tbody>{rows}</tbody></table></div></body></html>"""

@router.post("/admin/users")
async def create_user(request: Request, db: Session = Depends(get_db),
                      username: str = Form(...), password: str = Form(...), role: str = Form("inspector")):
    from database import User, AuditLog
    from datetime import datetime
    import hashlib
    
    user = get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(403, "صلاحية غير كافية")
    
    if db.query(User).filter(User.username == username).first(): 
        raise HTTPException(400, "موجود")
    
    def hash_password(pw): return hashlib.sha256(pw.encode()).hexdigest()
    
    db.add(User(username=username, password_hash=hash_password(password), role=role))
    db.commit()
    
    ip = request.headers.get("x-forwarded-for", request.client.host)
    db.add(AuditLog(user_id=user.id, action="CREATE_USER", details=username, ip_address=ip, timestamp=datetime.now()))
    db.commit()
    
    return RedirectResponse(url="/admin/users", status_code=302)

@router.post("/admin/users/{uid}/toggle")
async def toggle_user(uid: int, request: Request, db: Session = Depends(get_db)):
    from database import User, AuditLog
    from datetime import datetime
    
    user = get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(403, "صلاحية غير كافية")
    
    u = db.query(User).filter(User.id == uid).first()
    if u and u.id != user.id: 
        u.is_active = not u.is_active
        db.commit()
    
    return RedirectResponse(url="/admin/users", status_code=302)

@router.post("/admin/users/{uid}/delete")
async def delete_user(uid: int, request: Request, db: Session = Depends(get_db)):
    from database import User, AuditLog
    from datetime import datetime
    
    user = get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(403, "صلاحية غير كافية")
    
    u = db.query(User).filter(User.id == uid).first()
    if u and u.id != user.id: 
        db.delete(u)
        db.commit()
    
    return RedirectResponse(url="/admin/users", status_code=302)

@router.get("/admin/users/{uid}/edit", response_class=HTMLResponse)
async def edit_user_page(uid: int, request: Request, db: Session = Depends(get_db)):
    from database import User
    
    user = get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(403, "صلاحية غير كافية")
    
    u = db.query(User).filter(User.id == uid).first()
    if not u: 
        raise HTTPException(404)
    
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-gray-50 p-4"><div class="max-w-md mx-auto bg-white p-6 rounded shadow"><h1 class="text-xl font-bold mb-4">✏️ تعديل: {u.username}</h1>
    <form action="/admin/users/{uid}/edit" method="post" class="space-y-3">
      <input name="username" value="{u.username}" required class="w-full p-2 border rounded">
      <input name="password" type="password" placeholder="اتركه فارغاً إذا لم ترد تغييره" class="w-full p-2 border rounded">
      <select name="role" class="w-full p-2 border rounded"><option value="inspector" {'selected' if u.role=='inspector' else ''}>مفتش</option><option value="admin" {'selected' if u.role=='admin' else ''}>مدير</option></select>
      <button class="w-full bg-blue-600 text-white py-2 rounded">💾 حفظ</button></form><a href="/admin/users" class="block text-center mt-4 text-gray-500">← إلغاء</a></div></body></html>"""

@router.post("/admin/users/{uid}/edit")
async def edit_user_submit(uid: int, request: Request, db: Session = Depends(get_db),
                           username: str = Form(...), password: str = Form(""), role: str = Form("inspector")):
    from database import User, AuditLog
    from datetime import datetime
    import hashlib
    
    user = get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(403, "صلاحية غير كافية")
    
    def hash_password(pw): return hashlib.sha256(pw.encode()).hexdigest()
    
    u = db.query(User).filter(User.id == uid).first()
    if not u: 
        raise HTTPException(404)
    
    if u.id != user.id and username != u.username and db.query(User).filter(User.username == username).first(): 
        raise HTTPException(400, "اسم المستخدم مستخدم")
    
    u.username = username
    u.role = role
    if password.strip(): 
        u.password_hash = hash_password(password)
    
    db.commit()
    
    ip = request.headers.get("x-forwarded-for", request.client.host)
    db.add(AuditLog(user_id=user.id, action="EDIT_USER", details=f"تعديل: {username}", ip_address=ip, timestamp=datetime.now()))
    db.commit()
    
    return RedirectResponse(url="/admin/users", status_code=302)

@router.get("/admin/users/export")
async def export_users(request: Request, db: Session = Depends(get_db)):
    from database import User
    
    user = get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(403, "صلاحية غير كافية")
    
    users = db.query(User).all()
    # تصدير كلمات المرور كما هي (بدون تشفير) - ستظهر في الملف
    data = []
    for u in users:
        data.append({
            "username": u.username, 
            "password": u.password_hash,  # كلمة المرور المشفرة كما هي في قاعدة البيانات
            "role": u.role, 
            "is_active": u.is_active
        })
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="المستخدمون")
    output.seek(0)
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=users.xlsx"})

@router.post("/admin/users/import")
async def import_users(request: Request, db: Session = Depends(get_db), file: UploadFile = File(...)):
    from database import User, AuditLog
    from datetime import datetime
    import hashlib
    
    user = get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(403, "صلاحية غير كافية")
    
    def hash_password(pw): return hashlib.sha256(pw.encode()).hexdigest()
    
    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))
        
        # التحقق من الأعمدة المطلوبة
        required_cols = ["username", "role"]
        if not all(col in df.columns for col in required_cols):
            raise HTTPException(400, "يجب أن يحتوي الملف على أعمدة: username, role, password (اختياري), is_active (اختياري)")
        
        count_created = 0
        count_updated = 0
        
        for _, row in df.iterrows():
            username = str(row["username"]).strip()
            role = str(row.get("role", "inspector")).strip()
            is_active = row.get("is_active", True)
            password = str(row.get("password", "")).strip()
            
            # التحقق من صحة الدور - حذف المشرف
            if role not in ["admin", "inspector"]:
                role = "inspector"
            
            existing = db.query(User).filter(User.username == username).first()
            if existing:
                # تحديث المستخدم الموجود
                existing.role = role
                if pd.notna(is_active):
                    existing.is_active = bool(is_active)
                # تحديث كلمة المرور إذا كانت موجودة في الملف (تشفيرها قبل الحفظ)
                if password:
                    existing.password_hash = hash_password(password)
                count_updated += 1
            else:
                # إنشاء مستخدم جديد بكلمة مرور من الملف أو افتراضية (مع التشفير)
                new_password = password if password else "123456"
                db.add(User(username=username, password_hash=hash_password(new_password), role=role))
                count_created += 1
        
        db.commit()
        
        ip = request.headers.get("x-forwarded-for", request.client.host)
        db.add(AuditLog(user_id=user.id, action="IMPORT_USERS", details=f"تم استيراد {count_created} مستخدم جديد وتحديث {count_updated}", ip_address=ip, timestamp=datetime.now()))
        db.commit()
        
        return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><script src="https://cdn.tailwindcss.com"></script></head>
        <body class="bg-gray-50 p-4 flex items-center justify-center h-screen">
        <div class="bg-white p-8 rounded shadow text-center">
        <h1 class="text-2xl font-bold text-green-600 mb-4">✅ تم الاستيراد بنجاح</h1>
        <p class="mb-4">📥 مستخدمين جدد: {count_created}<br>🔄 تم التحديث: {count_updated}</p>
        <p class="text-sm text-gray-500 mb-4">كلمة المرور الافتراضية للمستخدمين الجدد: <code>123456</code></p>
        <a href="/admin/users" class="inline-block bg-blue-600 text-white px-6 py-2 rounded">العودة للمستخدمين</a>
        </div></body></html>"""
    except Exception as e:
        raise HTTPException(500, f"خطأ في الاستيراد: {str(e)}")
