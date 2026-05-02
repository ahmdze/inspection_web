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

@router.get("/inspect/success", response_class=HTMLResponse)
async def success():
    return """<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-4 flex items-center justify-center h-screen"><div class="bg-white p-8 rounded shadow text-center"><h1 class="text-2xl font-bold text-green-600 mb-2">✅ تم الحفظ بنجاح</h1><p class="text-gray-600 mb-4">سيتم دمج البيانات في التقرير النهائي.</p><a href="/dashboard" class="inline-block bg-blue-600 text-white px-6 py-2 rounded">العودة للوحة</a></div></body></html>"""

@router.get("/inspect/{code}", response_class=HTMLResponse)
async def inspect_form(code: str, request: Request, db: Session = Depends(lambda: None)):
    from database import InspectionSession, RecommendationCategory, Section, FormField
    
    user = get_current_user(request, db)
    
    sess = db.query(InspectionSession).filter(InspectionSession.session_code == code, InspectionSession.status == "open").first()
    if not sess:
        raise HTTPException(404, "الجولة مغلقة أو غير موجودة")
    
    # جلب فئات التوصيات من قاعدة البيانات
    rec_cats = db.query(RecommendationCategory).filter(RecommendationCategory.is_active == True).order_by(RecommendationCategory.order).all()
    rec_cats_list = [{"key": r.key, "label": r.label} for r in rec_cats]
    
    # جلب الأقسام الرئيسية
    root_sections = db.query(Section).filter(Section.parent_id == None).order_by(Section.order).all()
    
    # بناء هيكل الأقسام المتداخلة مع الحقول
    def build_section_fields(section):
        html = ""
        fields = db.query(FormField).filter(FormField.section_id == section.id, FormField.is_active == True).order_by(FormField.order).all()
        
        for f in fields:
            if f.field_type == "textarea":
                inp = f'<textarea name="{f.field_key}" placeholder="{f.label}" {"required" if f.is_required else ""} class="w-full p-3 border rounded h-24 focus:ring-2 focus:ring-blue-500"></textarea>'
            elif f.field_type == "select" and f.options_json:
                try:
                    opts = __import__('json').loads(f.options_json)
                except:
                    opts = []
                inp = f'<select name="{f.field_key}" {"required" if f.is_required else ""} class="w-full p-3 border rounded focus:ring-2 focus:ring-blue-500"><option value="">اختر...</option>' + ''.join(f'<option value="{o}">{o}</option>' for o in opts) + '</select>'
            else:
                inp = f'<input name="{f.field_key}" type="{f.field_type}" placeholder="{f.label}" {"required" if f.is_required else ""} class="w-full p-3 border rounded focus:ring-2 focus:ring-blue-500">'
            
            html += f'<div class="mb-6 p-4 bg-white rounded border"><label class="block font-bold text-lg mb-2">{f.label}</label>{inp}'
            
            # التوصيات إذا كان الحقل يدعمها
            if f.has_recommendations and rec_cats_list:
                html += f'<div class="mt-4 pt-4 border-t bg-green-50 p-3 rounded"><p class="font-bold text-green-800 mb-2">💡 التوصيات لهذا البند</p>'
                for cat in rec_cats_list:
                    html += f'<div class="mb-3"><label class="flex items-start gap-2 cursor-pointer"><input type="checkbox" name="rec_enable_{f.field_key}_{cat["key"]}" class="mt-1 rounded" onchange="document.getElementById(\'rec_{f.field_key}_{cat["key"]}\').classList.toggle(\'hidden\',!this.checked)"> <span class="font-medium text-sm">{cat["label"]}</span></label><textarea id="rec_{f.field_key}_{cat["key"]}" name="rec_{f.field_key}_{cat["key"]}" placeholder="اكتب التفاصيل هنا..." class="w-full p-2 border rounded h-16 mt-2 hidden"></textarea></div>'
                html += '</div>'
            html += '</div>'
        
        # الأقسام الفرعية
        child_sections = db.query(Section).filter(Section.parent_id == section.id).order_by(Section.order).all()
        for child in child_sections:
            html += f'<h3 class="text-lg font-bold mt-6 pt-4 border-t text-blue-700">📌 {child.name}</h3>'
            html += build_section_fields(child)
        
        return html
    
    sections_html = ""
    for section in root_sections:
        sections_html += f'<div id="sec-{section.id}" class="section-content hidden"><h2 class="text-2xl font-bold mb-4 pb-2 border-b text-blue-800">{section.name}</h2>'
        sections_html += build_section_fields(section)
        sections_html += '</div>'
    
    # بناء قائمة الأقسام للاختيار
    section_opts = "".join(f'<option value="sec-{s.id}">{s.name}</option>' for s in root_sections)

    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-gray-50 p-4">
    <div class="max-w-2xl mx-auto bg-white p-6 rounded shadow">
      <div class="bg-blue-600 text-white p-4 rounded-t -mx-6 -mt-6 mb-6">
        <h1 class="text-xl font-bold">{sess.institution} | {sess.visit_date}</h1>
        <p class="text-sm opacity-90 mt-1">المفتش: {user.username} | مفتش</p>
      </div>
      
      <div class="mb-6">
        <label class="block font-bold mb-2">اختر المحور الذي قمت بالتفتيش عنه:</label>
        <select id="axis-selector" class="w-full p-3 border rounded text-lg focus:ring-2 focus:ring-blue-500" onchange="showSection(this.value)">{section_opts}</select>
      </div>
      
      <form action="/inspect/submit" method="post" class="space-y-4" novalidate>
        <input type="hidden" name="session_id" value="{sess.id}">
        {sections_html}
        <button type="submit" class="w-full bg-green-600 text-white py-3 rounded font-bold text-lg hover:bg-green-700">✅ حفظ الإجابات</button>
      </form>
    </div>
    <script>
      function showSection(id) {{
        document.querySelectorAll('.section-content').forEach(s => s.classList.add('hidden'));
        document.getElementById(id).classList.remove('hidden');
      }}
      if(document.getElementById('axis-selector')) showSection(document.getElementById('axis-selector').value);
    </script></body></html>"""

@router.post("/inspect/submit")
async def submit_dynamic(request: Request, db: Session = Depends(lambda: None), bg=None):
    from database import InspectionSession, Submission, AuditLog
    from datetime import datetime
    import json
    
    user = get_current_user(request, db)
    form = await request.form()
    session_id = int(form.get("session_id", 0))
    sess = db.query(InspectionSession).filter(InspectionSession.id == session_id, InspectionSession.status == "open").first()
    if not sess:
        raise HTTPException(400, "الجولة مغلقة")
    
    answers = {}
    for k, v in form.items():
        if k in ["unit_name", "session_id"]:
            continue
        if not k.startswith("rec_enable_") and v.strip():
            answers[k] = v.strip()
            
    db.add(Submission(session_id=sess.id, user_id=user.id, unit_name=form.get("unit_name","عام"), answers_json=json.dumps(answers, ensure_ascii=False)))
    db.commit()
    
    ip = request.headers.get("x-forwarded-for", request.client.host)
    db.add(AuditLog(user_id=user.id, action="SUBMIT_REPORT", details=f"{sess.institution}", ip_address=ip, timestamp=datetime.now()))
    db.commit()
    
    return RedirectResponse(url="/inspect/success", status_code=302)
