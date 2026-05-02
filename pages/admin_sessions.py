from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

router = APIRouter()

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

@router.get("/admin/sessions", response_class=HTMLResponse)
async def admin_sessions(request: Request, db: Session = Depends(lambda: None)):
    from database import InspectionSession
    
    user = get_current_user(request, db)
    if user.role not in ["admin", "supervisor"]:
        raise HTTPException(403, "صلاحية غير كافية")
    
    sessions = db.query(InspectionSession).order_by(InspectionSession.created_at.desc()).all()
    rows = "".join(f'''<tr class="border-b"><td class="p-2">{s.institution}</td><td class="p-2">{s.visit_date}</td>
    <td class="p-2"><code class="bg-gray-100 px-1">{s.session_code}</code></td>
    <td class="p-2"><span class="px-2 py-1 rounded text-xs {'bg-green-100 text-green-800' if s.status=='open' else 'bg-red-100 text-red-800'}">{'✅ نشطة' if s.status=='open' else '❌ مغلقة'}</span></td>
    <td class="p-2">
      <form action="/admin/sessions/{s.id}/toggle" method="post" class="inline">
        <button class="px-2 py-1 rounded text-xs {'bg-yellow-500 text-white' if s.status=='open' else 'bg-green-500 text-white'}">{'🔒 إغلاق' if s.status=='open' else '🔓 فتح'}</button>
      </form>
      <a href="/admin/session/{s.id}" class="bg-blue-600 text-white px-2 py-1 rounded text-xs mr-1">عرض</a>
    </td></tr>''' for s in sessions)
    
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-gray-50 p-4"><div class="max-w-6xl mx-auto bg-white p-6 rounded shadow"><div class="flex justify-between mb-4"><h1 class="text-xl font-bold">📋 جميع الجولات</h1><a href="/admin/panel" class="text-blue-600">← العودة</a></div>
    <p class="text-sm text-gray-600 mb-4">يمكنك إغلاق الجولة لمنع المفتشين من إضافة ردود جديدة، أو فتحها للسماح بالإضافة.</p>
    <div class="overflow-x-auto"><table class="w-full text-right"><thead class="bg-gray-100"><tr><th class="p-2">المؤسسة</th><th>تاريخ الزيارة</th><th>رمز الجولة</th><th>الحالة</th><th>إجراء</th></tr></thead><tbody>{rows}</tbody></table></div></div></body></html>"""

@router.post("/admin/sessions/{sid}/toggle")
async def toggle_session_status(sid: int, request: Request, db: Session = Depends(lambda: None)):
    from database import InspectionSession, AuditLog
    from datetime import datetime
    
    user = get_current_user(request, db)
    if user.role not in ["admin", "supervisor"]:
        raise HTTPException(403, "صلاحية غير كافية")
    
    s = db.query(InspectionSession).filter(InspectionSession.id == sid).first()
    if s:
        s.status = "closed" if s.status == "open" else "open"
        db.commit()
        
        ip = request.headers.get("x-forwarded-for", request.client.host)
        db.add(AuditLog(user_id=user.id, action="TOGGLE_SESSION", details=f"تغيير حالة الجولة {s.institution} إلى {s.status}", ip_address=ip, timestamp=datetime.now()))
        db.commit()
    
    return RedirectResponse(url="/admin/sessions", status_code=302)

@router.get("/admin/session/{sid}", response_class=HTMLResponse)
async def view_session(sid: int, request: Request, db: Session = Depends(lambda: None)):
    from database import InspectionSession, Submission, FormField, Section, User
    import json
    
    user = get_current_user(request, db)
    if user.role not in ["admin", "supervisor"]:
        raise HTTPException(403, "صلاحية غير كافية")
    
    s = db.query(InspectionSession).filter(InspectionSession.id == sid).first()
    if not s: 
        raise HTTPException(404)
    
    subs = db.query(Submission).filter(Submission.session_id == sid).all()
    
    # جلب جميع الحقول لمعرفة الأقسام
    all_fields = db.query(FormField).all()
    field_map = {f.field_key: f for f in all_fields}
    all_sections = db.query(Section).all()
    section_map = {sec.id: sec for sec in all_sections}
    
    rows = ""
    for sub in subs:
        inspector = db.query(User).filter(User.id == sub.user_id).first()
        inspector_name = inspector.username if inspector else "مفتش مجهول"
        
        # تجميع الأقسام التي تم الإجابة عنها
        ans = json.loads(sub.answers_json)
        sections_answered = set()
        for k in ans.keys():
            if k.startswith("rec_enable_"):
                continue
            field = field_map.get(k)
            if field and field.section_id:
                section = section_map.get(field.section_id)
                if section:
                    # الحصول على القسم الرئيسي (بدون الآباء الفرعية)
                    parent_section = section
                    while parent_section.parent_id:
                        parent_section = section_map.get(parent_section.parent_id)
                        if not parent_section:
                            break
                    sections_answered.add(parent_section.name if parent_section else section.name)
        
        sections_list = "، ".join(sections_answered) if sections_answered else "لا يوجد"
        rows += f'<div class="border p-3 rounded mb-2 bg-gray-50"><div class="font-bold text-blue-700 mb-2">👤 {inspector_name}</div><div class="text-sm text-gray-600">📋 الأقسام المجابة: <span class="font-medium">{sections_list}</span></div></div>'
    
    # رابط الجولة
    session_url = request.url.scheme + "://" + request.headers.get("host", "") + "/inspect/" + s.session_code
    
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-gray-50 p-4"><div class="max-w-4xl mx-auto bg-white p-6 rounded shadow"><h1 class="text-xl font-bold mb-4">{s.institution} | {s.visit_date}</h1>
    <div class="mb-4 p-3 bg-blue-50 rounded border border-blue-200 flex justify-between items-center">
      <div class="flex-1 mr-3 overflow-hidden">
        <p class="text-sm text-gray-600 mb-1">رابط الجولة:</p>
        <code id="session-url" class="text-blue-700 break-all">{session_url}</code>
      </div>
      <button onclick="copyLink()" class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm whitespace-nowrap">📋 نسخ الرابط</button>
    </div>
    <script>
    function copyLink() {{
      const url = document.getElementById('session-url').textContent;
      navigator.clipboard.writeText(url).then(() => {{
        alert('تم نسخ الرابط!');
      }}).catch(err => {{
        prompt('انسخ الرابط يدوياً:', url);
      }});
    }}
    </script>
    <div class="mb-4 max-h-96 overflow-y-auto p-2 border rounded">{rows if rows else '<p class="text-gray-500">لا توجد إجابات بعد</p>'}</div>
    <form action="/generate/{sid}" method="post"><button class="w-full bg-indigo-600 text-white py-3 rounded font-bold">📅 توليد تقرير Word</button></form></div></body></html>"""
