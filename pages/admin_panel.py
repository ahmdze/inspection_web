from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

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

@router.get("/admin/panel", response_class=HTMLResponse)
async def admin_panel(request: Request, db: Session = Depends(get_db)):
    from database import InspectionSession, FormTemplate
    
    user = get_current_user(request, db)
    if user.role not in ["admin", "supervisor"]:
        raise HTTPException(403, "صلاحية غير كافية")
    
    sessions = db.query(InspectionSession).order_by(InspectionSession.created_at.desc()).all()
    templates = db.query(FormTemplate).filter(FormTemplate.is_active == True).order_by(FormTemplate.name).all()
    
    nav = """<div class="flex flex-wrap gap-2 mb-4">
      <a href="/admin/users" class="bg-gray-600 text-white px-3 py-1 rounded text-sm">المستخدمين</a>
      <a href="/admin/form-builder" class="bg-gray-600 text-white px-3 py-1 rounded text-sm">باني النموذج</a>
      <a href="/admin/templates" class="bg-gray-600 text-white px-3 py-1 rounded text-sm">النماذج المحفوظة</a>
      <a href="/admin/sections" class="bg-gray-600 text-white px-3 py-1 rounded text-sm">الأقسام</a>
      <a href="/admin/recommendations" class="bg-gray-600 text-white px-3 py-1 rounded text-sm">التوصيات</a>
      <a href="/dashboard/stats" class="bg-gray-600 text-white px-3 py-1 rounded text-sm">الإحصائيات</a>
      <a href="/admin/sessions" class="bg-gray-600 text-white px-3 py-1 rounded text-sm">جميع الجولات</a>
      <a href="/admin/settings" class="bg-gray-600 text-white px-3 py-1 rounded text-sm">الإعدادات</a>
      <a href="/admin/logs" class="bg-gray-600 text-white px-3 py-1 rounded text-sm">السجل</a>
      <a href="/profile" class="bg-purple-600 text-white px-3 py-1 rounded text-sm">👤 الملف الشخصي</a>
      <form action="/logout" method="post"><button class="bg-red-500 text-white px-3 py-1 rounded text-sm">خروج</button></form></div>"""
    
    rows = "".join(f'''<li class="border p-3 rounded flex justify-between items-center bg-white mb-2">
      <div><span class="font-bold">{s.institution}</span> | {s.visit_date}
      <div class="text-sm text-gray-500 mt-1">الرمز: <code class="bg-gray-100 px-1">{s.session_code}</code></div></div>
      <div class="flex gap-2">
        <a href="/admin/session/{s.id}" class="bg-blue-600 text-white px-3 py-1 rounded text-sm">عرض وتوليد</a>
        <button onclick="copySessionLink('{request.url.scheme}://{request.headers.get("host", "")}/inspect/{s.session_code}')" class="bg-green-600 hover:bg-green-700 text-white px-3 py-1 rounded text-sm">📋 نسخ الرابط</button>
      </div></li>''' for s in sessions)
    
    template_options = "".join(f'<option value="{t.id}">{t.name}</option>' for t in templates)
    
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-4">
    <div class="max-w-4xl mx-auto"><div class="bg-white p-4 rounded shadow mb-6"><h1 class="text-xl font-bold text-blue-800">👤 لوحة {user.username}</h1>{nav}</div>
    <div class="bg-white p-4 rounded shadow mb-6"><h2 class="font-bold mb-3">➕ إنشاء جولة تفتيش</h2>
      <form action="/admin/create" method="post" class="grid grid-cols-1 md:grid-cols-4 gap-3">
        <input name="institution" placeholder="اسم المؤسسة" required class="p-2 border rounded">
        <input name="visit_date" type="date" required class="p-2 border rounded">
        <select name="template_id" class="p-2 border rounded"><option value="">-- اختر نموذجاً (اختياري) --</option>{template_options}</select>
        <button class="bg-green-600 text-white p-2 rounded">إنشاء + مشاركة الرابط</button></form></div>
    <div class="bg-white p-4 rounded shadow"><h2 class="font-bold mb-3">📋 الجولات النشطة</h2><ul class="space-y-2">{rows}</ul></div></div>
    <script>
    function copySessionLink(url) {{
      navigator.clipboard.writeText(url).then(() => {{
        alert('تم نسخ رابط الجولة!');
      }}).catch(err => {{
        prompt('انسخ الرابط يدوياً:', url);
      }});
    }}
    </script>
    </body></html>"""

@router.post("/admin/create")
async def create_session(request: Request, db: Session = Depends(get_db),
                         institution: str = Form(...), visit_date: str = Form(...), template_id: str = Form(None)):
    from database import InspectionSession, FormTemplate, AuditLog
    from datetime import datetime
    
    user = get_current_user(request, db)
    if user.role not in ["admin", "supervisor"]:
        raise HTTPException(403, "صلاحية غير كافية")
    
    # حفظ الجلسة مع ربطها بالنموذج المختار إن وُجد
    session = InspectionSession(
        institution=institution, 
        visit_date=visit_date, 
        created_by=user.id,
        template_id=int(template_id) if template_id else None
    )
    db.add(session)
    db.commit()
    
    # تسجيل الإجراء
    ip = request.headers.get("x-forwarded-for", request.client.host)
    if template_id:
        template = db.query(FormTemplate).filter(FormTemplate.id == int(template_id)).first()
        if template:
            db.add(AuditLog(user_id=user.id, action="CREATE_SESSION_FROM_TEMPLATE", details=f"{institution} (Template: {template.name})", ip_address=ip, timestamp=datetime.now()))
        else:
            db.add(AuditLog(user_id=user.id, action="CREATE_SESSION", details=institution, ip_address=ip, timestamp=datetime.now()))
    else:
        db.add(AuditLog(user_id=user.id, action="CREATE_SESSION", details=institution, ip_address=ip, timestamp=datetime.now()))
    db.commit()
    
    return RedirectResponse(url="/admin/panel", status_code=302)
