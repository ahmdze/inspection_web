from fastapi import APIRouter, Request, Depends, Form, HTTPException, Query, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session
import io
import pandas as pd
from password_utils import encrypt_password, export_password

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
    pending_users = [u for u in users if not u.is_active]
    rows = "".join(f'''<tr class="border-b"><td class="p-2">{u.username}</td><td class="p-2">{u.job_title or ''}</td><td class="p-2">{u.email or ''}</td><td class="p-2">{u.phone or ''}</td><td class="p-2">{u.role}</td>
    <td class="p-2"><span class="px-2 py-1 rounded text-xs {'bg-yellow-100 text-yellow-700' if not u.is_active else 'bg-green-100 text-green-700'}">{'معلق' if not u.is_active else 'مفعل'}</span></td>
    <td class="p-2">{('<form action="/admin/users/'+str(u.id)+'/approve" method="post" class="inline"><button class="bg-blue-600 text-white px-2 py-1 rounded text-xs">✅ موافقة</button></form>' if not u.is_active else '<form action="/admin/users/'+str(u.id)+'/toggle" method="post" class="inline"><button class="px-2 py-1 rounded text-xs bg-green-500 text-white">✅</button></form>')}
      <a href="/admin/users/{u.id}/edit" class="bg-yellow-500 text-white px-2 py-1 rounded text-xs mr-1">✏️ تعديل</a>
      {'<form action="/admin/users/'+str(u.id)+'/delete" method="post" class="inline" onsubmit="return confirm(\'حذف؟\')">' if u.id!=user.id else ''}
      {'<button class="bg-red-600 text-white px-2 py-1 rounded text-xs">حذف</button>' if u.id!=user.id else ''}
    {'</form>' if u.id!=user.id else ''}</td></tr>''' for u in users)
    
    pending_section = ""
    if pending_users:
        pending_rows = "".join(f'''<tr class="border-b"><td class="p-2">{u.username}</td><td class="p-2">{u.job_title or ''}</td><td class="p-2">{u.email or ''}</td><td class="p-2">{u.phone or ''}</td><td class="p-2">{u.role}</td><td class="p-2">{u.id}</td><td class="p-2"><form action="/admin/users/{u.id}/approve" method="post"><button class="bg-blue-600 text-white px-2 py-1 rounded text-xs">✅ موافقة</button></form></td></tr>''' for u in pending_users)
        pending_section = f"""
        <div class=\"mb-6\">\n          <h2 class=\"text-lg font-bold mb-3\">📥 طلبات التسجيل المعلقة</h2>\n          <div class=\"overflow-x-auto rounded border bg-yellow-50 p-3\">\n            <table class=\"w-full text-right\"><thead class=\"bg-yellow-100\"><tr><th class=\"p-2\">الاسم</th><th class=\"p-2\">العنوان الوظيفي</th><th class=\"p-2\">الدور</th><th class=\"p-2\">معرف</th><th class=\"p-2\">إجراء</th></tr></thead><tbody>{pending_rows}</tbody></table>\n          </div>\n        </div>\n        """

    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-4">
    <div class="max-w-6xl mx-auto bg-white p-6 rounded shadow"><div class="flex justify-between mb-4"><h1 class="text-xl font-bold">👥 المستخدمون</h1><a href="/admin/panel" class="text-blue-600">← العودة</a></div>
    {pending_section}
    <!-- أزرار الإكسل -->
    <div class="mb-4 flex gap-2 flex-wrap">
      <form action="/admin/users/export" method="get" class="inline">
        <button class="bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded">📤 تصدير Excel</button>
      </form>
      <form action="/admin/users/import" method="post" enctype="multipart/form-data" class="inline flex gap-2">
        <input type="file" name="file" accept=".xlsx,.xls" required class="p-2 border rounded">
        <button class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded">📥 استيراد Excel</button>
      </form>
    </div>
    
    <form action="/admin/users" method="post" class="bg-gray-50 p-4 rounded mb-6 grid grid-cols-1 md:grid-cols-6 gap-2">
      <input name="username" placeholder="اسم المستخدم" required class="p-2 border rounded">
      <input name="email" type="email" placeholder="البريد الإلكتروني" class="p-2 border rounded">
      <input name="phone" placeholder="رقم الهاتف" class="p-2 border rounded">
      <input name="job_title" placeholder="العنوان الوظيفي" class="p-2 border rounded">
      <input name="password" type="password" placeholder="كلمة المرور" required class="p-2 border rounded">
      <select name="role" class="p-2 border rounded"><option value="inspector">مفتش</option><option value="admin">مدير</option></select>
      <button class="bg-green-600 text-white p-2 rounded">➕ إضافة</button></form>
    <table class="w-full text-right"><thead class="bg-gray-100"><tr><th class="p-2">الاسم</th><th class="p-2">العنوان الوظيفي</th><th class="p-2">البريد الإلكتروني</th><th class="p-2">رقم الهاتف</th><th>الدور</th><th>الحالة</th><th>إجراء</th></tr></thead><tbody>{rows}</tbody></table></div></body></html>"""

@router.post("/admin/users")
async def create_user(request: Request, db: Session = Depends(get_db),
                      username: str = Form(...), email: str = Form(""), phone: str = Form(""), job_title: str = Form(""), password: str = Form(...), role: str = Form("inspector")):
    from database import User, AuditLog
    from datetime import datetime
    
    user = get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(403, "صلاحية غير كافية")
    
    if db.query(User).filter(User.username == username).first(): 
        raise HTTPException(400, "موجود")
    
    def hash_password(pw): return encrypt_password(pw)
    
    db.add(User(username=username, password_hash=hash_password(password), role=role, job_title=job_title.strip() or None, email=email.strip() or None, phone=phone.strip() or None))
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

@router.post("/admin/users/{uid}/approve")
async def approve_user(uid: int, request: Request, db: Session = Depends(get_db)):
    from database import User, AuditLog
    from datetime import datetime
    
    user = get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(403, "صلاحية غير كافية")
    
    u = db.query(User).filter(User.id == uid).first()
    if u and not u.is_active:
        u.is_active = True
        db.commit()
        ip = request.headers.get("x-forwarded-for", request.client.host)
        db.add(AuditLog(user_id=user.id, action="APPROVE_USER", details=f"موافقة على {u.username}", ip_address=ip, timestamp=datetime.now()))
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
      <input name="email" type="email" value="{u.email or ''}" placeholder="البريد الإلكتروني" class="w-full p-2 border rounded">
      <input name="phone" value="{u.phone or ''}" placeholder="رقم الهاتف" class="w-full p-2 border rounded">
      <input name="job_title" value="{u.job_title or ''}" placeholder="العنوان الوظيفي" class="w-full p-2 border rounded">
      <input name="password" type="password" placeholder="اتركه فارغاً إذا لم ترد تغييره" class="w-full p-2 border rounded">
      <select name="role" class="w-full p-2 border rounded"><option value="inspector" {'selected' if u.role=='inspector' else ''}>مفتش</option><option value="admin" {'selected' if u.role=='admin' else ''}>مدير</option></select>
      <button class="w-full bg-blue-600 text-white py-2 rounded">💾 حفظ</button></form><a href="/admin/users" class="block text-center mt-4 text-gray-500">← إلغاء</a></div></body></html>"""

@router.post("/admin/users/{uid}/edit")
async def edit_user_submit(uid: int, request: Request, db: Session = Depends(get_db),
                           username: str = Form(...), email: str = Form(""), phone: str = Form(""), job_title: str = Form(""), password: str = Form(""), role: str = Form("inspector")):
    from database import User, AuditLog
    from datetime import datetime
    
    user = get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(403, "صلاحية غير كافية")
    
    def hash_password(pw): return encrypt_password(pw)
    
    u = db.query(User).filter(User.id == uid).first()
    if not u: 
        raise HTTPException(404)
    
    if u.id != user.id and username != u.username and db.query(User).filter(User.username == username).first(): 
        raise HTTPException(400, "اسم المستخدم مستخدم")
    
    u.username = username
    u.email = email.strip()
    u.phone = phone.strip()
    u.job_title = job_title.strip()
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
            "job_title": u.job_title or "",
            "email": u.email or "",
            "phone": u.phone or "",
            "password": export_password(u.password_hash),
            "role": u.role, 
            "is_active": u.is_active
        })
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="المستخدمون")
    output.seek(0)
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=users.xlsx"})

@router.post("/admin/users/import", response_class=HTMLResponse)
async def import_users(request: Request, db: Session = Depends(get_db), file: UploadFile = File(...)):
    from database import User, AuditLog
    from datetime import datetime
    
    user = get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(403, "صلاحية غير كافية")
    
    def hash_password(pw): return encrypt_password(pw)
    
    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))
        
        # التحقق من الأعمدة المطلوبة
        required_cols = ["username", "role"]
        if not all(col in df.columns for col in required_cols):
            raise HTTPException(400, "يجب أن يحتوي الملف على أعمدة: username, role, password (اختياري), is_active (اختياري), job_title (اختياري), email (اختياري), phone (اختياري)")
        
        count_created = 0
        count_updated = 0
        
        for _, row in df.iterrows():
            username = str(row["username"]).strip()
            role = str(row.get("role", "inspector")).strip()
            email = "" if pd.isna(row.get("email", "")) else str(row.get("email", "")).strip()
            phone = "" if pd.isna(row.get("phone", "")) else str(row.get("phone", "")).strip()
            job_title = "" if pd.isna(row.get("job_title", "")) else str(row.get("job_title", "")).strip()
            is_active = row.get("is_active", True)
            password_value = row.get("password", "")
            password = "" if pd.isna(password_value) else str(password_value).strip()
            
            # التحقق من صحة الدور - حذف المشرف
            if role not in ["admin", "inspector"]:
                role = "inspector"
            
            existing = db.query(User).filter(User.username == username).first()
            if existing:
                # تحديث المستخدم الموجود
                existing.role = role
                existing.job_title = job_title
                if pd.notna(is_active):
                    existing.is_active = bool(is_active)
                # تحديث كلمة المرور إذا كانت موجودة في الملف (تشفيرها قبل الحفظ)
                if password and password != "LEGACY_HASH_NOT_DECRYPTABLE":
                    existing.password_hash = hash_password(password)
                count_updated += 1
            else:
                # إنشاء مستخدم جديد بكلمة مرور من الملف أو افتراضية (مع التشفير)
                new_password = password if password and password != "LEGACY_HASH_NOT_DECRYPTABLE" else "123456"
                db.add(User(username=username, password_hash=hash_password(new_password), role=role, job_title=job_title or None, email=email or None, phone=phone or None))
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
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"خطأ في الاستيراد: {str(e)}")
