import os, json, io, html
from enum import Enum
from fastapi import FastAPI, Request, Depends, Form, HTTPException, BackgroundTasks, Query, UploadFile, File
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
from password_utils import encrypt_password, export_password, verify_password
from itsdangerous import URLSafeTimedSerializer
from database import SessionLocal, User, InspectionSession, Submission, FormField, SystemSetting, AuditLog, RecommendationCategory, Section, FormTemplate
from notifications import notify_async
from audit import log_action
from report_generator import build_web_report
from integrations import EmailNotifier, SlackIntegration, TelegramIntegration, GoogleSheetsIntegration, PDFGenerator, setup_email_from_settings, setup_telegram_from_settings
import pandas as pd
from pages import auth_router, admin_panel_router, admin_users_router, admin_sessions_router, inspector_router

app = FastAPI(title="نظام التفتيش الذكي المتقدم", default_response_class=HTMLResponse)
serializer = URLSafeTimedSerializer("SECRET_CHANGE_ME_IN_PROD", salt="auth-session")

# تضمين المسارات من الملفات المنفصلة
app.include_router(auth_router)
app.include_router(admin_panel_router)
app.include_router(admin_users_router)
app.include_router(admin_sessions_router)
app.include_router(inspector_router)

def hash_password(pw: str) -> str: return encrypt_password(pw)

def message_page(title: str, message: str, status_code: int = 200, back_url: str = "/dashboard", kind: str = "info"):
    colors = {
        "success": ("green", "✅"),
        "error": ("red", "⚠️"),
        "warning": ("yellow", "تنبيه"),
        "info": ("blue", "ℹ️"),
    }
    color, icon = colors.get(kind, colors["info"])
    return HTMLResponse(f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 min-h-screen flex items-center justify-center p-4">
    <div class="bg-white max-w-md w-full p-6 rounded-lg shadow text-center border-t-4 border-{color}-500">
      <div class="text-4xl mb-3">{icon}</div>
      <h1 class="text-2xl font-bold text-{color}-700 mb-3">{html.escape(str(title))}</h1>
      <p class="text-gray-700 leading-7 mb-6">{html.escape(str(message))}</p>
      <a href="{html.escape(back_url)}" class="inline-block bg-{color}-600 hover:bg-{color}-700 text-white px-6 py-2 rounded">العودة</a>
    </div></body></html>""", status_code=status_code)

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    kind = "error" if exc.status_code >= 400 else "info"
    title = "حدث خطأ" if exc.status_code >= 400 else "رسالة"
    # استخدام referer من الطلب إذا كان متاحًا
    referer = request.headers.get("referer") or request.headers.get("Referer")
    back_url = referer if referer else "/dashboard"
    return message_page(title, exc.detail or "تعذر تنفيذ الطلب", exc.status_code, back_url, kind)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    referer = request.headers.get("referer") or request.headers.get("Referer")
    back_url = referer if referer else "/dashboard"
    return message_page("بيانات غير مكتملة", "تأكد من تعبئة الحقول المطلوبة ثم حاول مرة أخرى.", 422, back_url, "warning")
def format_date(date_str):
    """تحويل التاريخ من YYYY-MM-DD إلى DD/MM/YYYY"""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%d/%m/%Y")
    except:
        return date_str

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

class Role(Enum):
    ADMIN = "admin"
    INSPECTOR = "inspector"

def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("session_token")
    if not token: raise HTTPException(401, "يجب تسجيل الدخول")
    try: uid = serializer.loads(token, max_age=86400)
    except: raise HTTPException(401, "انتهت الجلسة")
    user = db.query(User).filter(User.id == uid).first()
    if not user or not user.is_active: raise HTTPException(401, "حساب غير نشط")
    return user

def require_role(*roles: str):
    def dep(user=Depends(get_current_user)):
        if user.role not in roles: raise HTTPException(403, "صلاحية غير كافية")
        return user
    return dep

# ================== المصادقة ==================
@app.get("/", response_class=RedirectResponse)
async def root(): return RedirectResponse(url="/login", status_code=302)

@app.get("/dashboard")
async def dashboard(user=Depends(get_current_user)):
    return RedirectResponse("/admin/panel" if user.role == Role.ADMIN.value else "/inspect/dashboard", status_code=302)

# ================== لوحة المدير ==================

# ================== إدارة المستخدمين ==================

# ================== باني النموذج ==================
@app.get("/admin/form-builder", response_class=HTMLResponse)
async def form_builder(user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db), edit_template_id: str = Query(default=None)):
    # جلب الأقسام من قاعدة البيانات
    root_sections = db.query(Section).filter(Section.parent_id == None).order_by(Section.order).all()
    
    # معالجة edit_template_id بشكل آمن
    try:
        edit_tid = int(edit_template_id) if edit_template_id and edit_template_id.strip() else None
    except (ValueError, TypeError):
        edit_tid = None
    
    def build_section_tree(parent_section, level=0):
        html = ""
        fields = db.query(FormField).filter(FormField.section_id == parent_section.id, FormField.is_active == True).order_by(FormField.order).all()
        
        # عنوان القسم
        indent = "&nbsp;" * (level * 4)
        html += f'<div class="mb-4 border rounded p-3 bg-gray-50 ml-{level * 4}"><h3 class="font-bold text-blue-700 mb-2">{indent}{parent_section.name}</h3>'
        
        # الحقول
        if fields:
            items = "".join(f'''<li class="flex justify-between items-center bg-white p-2 mb-1 rounded border">
              <div><span class="font-bold text-blue-700">[{f.order}]</span> {f.label} <code class="text-xs bg-gray-100 px-1 rounded">{f.field_key}</code>
              <span class="text-xs text-gray-500 ml-2">{'✓ توصيات' if f.has_recommendations else ''}</span></div>
              <a href="/admin/form-field/edit/{f.id}?edit_template_id={edit_tid or ''}" class="text-sm text-blue-600 hover:underline">✏️ تعديل</a></li>''' for f in fields)
            html += f'<ul class="space-y-1">{items}</ul>'
        
        html += f'<a href="/admin/form-field/new?section_id={parent_section.id}&edit_template_id={edit_tid or ""}" class="inline-block mt-2 bg-blue-600 text-white text-xs px-3 py-1 rounded">➕ إضافة حقل</a></div>'
        
        # الأقسام الفرعية
        child_sections = db.query(Section).filter(Section.parent_id == parent_section.id).order_by(Section.order).all()
        for child in child_sections:
            html += build_section_tree(child, level + 1)
        
        return html
    
    sections_html = ""
    for section in root_sections:
        sections_html += build_section_tree(section)
    edit_template = db.query(FormTemplate).filter(FormTemplate.id == edit_tid).first() if edit_tid else None
    edit_save_bar = f'<form action="/admin/templates/{edit_template.id}/update-from-builder" method="post" class="mb-4 p-3 bg-green-50 border border-green-200 rounded flex justify-between items-center"><span class="font-bold text-green-800">تعديل النموذج: {edit_template.name}</span><button class="bg-green-600 text-white px-4 py-2 rounded">حفظ التعديل على نفس النموذج</button></form>' if edit_template else ""
    
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-gray-50 p-4"><div class="max-w-4xl mx-auto bg-white p-6 rounded shadow">
      <div class="flex justify-between items-center mb-4"><h1 class="text-xl font-bold">🛠️ باني الاستمارة</h1>
      <div class="flex gap-2"><a href="/admin/form-fields/export" class="bg-purple-600 text-white px-4 py-2 rounded text-sm">📤 تصدير الحقول</a><button onclick="document.getElementById('importFile').click()" class="bg-indigo-600 text-white px-4 py-2 rounded text-sm">📥 استيراد حقول</button><button onclick="showSaveModal()" class="bg-green-600 text-white px-4 py-2 rounded text-sm">💾 حفظ كنموذج</button><a href="/admin/panel" class="text-blue-600">← العودة</a></div></div>
      <input type="file" id="importFile" accept=".xlsx,.xls" style="display:none" onchange="handleImport(this)">
      {edit_save_bar}
      {sections_html}</div></div>
    <div id="saveModal" class="hidden fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div class="bg-white p-6 rounded shadow-lg max-w-md w-full"><h2 class="text-xl font-bold mb-4">💾 حفظ النموذج الحالي</h2>
      <form action="/admin/templates/save" method="post" class="space-y-3">
        <div><label class="block font-bold mb-1">اسم النموذج</label><input name="name" placeholder="مثلاً: نموذج الفحص الشامل" class="w-full p-2 border rounded" required></div>
        <div><label class="block font-bold mb-1">الوصف (اختياري)</label><textarea name="description" placeholder="وصف موجز للنموذج" class="w-full p-2 border rounded h-20"></textarea></div>
        <div class="flex gap-2"><button type="submit" class="flex-1 bg-green-600 text-white py-2 rounded font-bold">حفظ</button>
        <button type="button" onclick="closeSaveModal()" class="flex-1 bg-gray-400 text-white py-2 rounded">إلغاء</button></div>
      </form></div></div>
    <script>
      function showSaveModal() {{ document.getElementById('saveModal').classList.remove('hidden'); }}
      function closeSaveModal() {{ document.getElementById('saveModal').classList.add('hidden'); }}
      async function handleImport(input) {{
        const file = input.files[0];
        if (!file) return;
        const formData = new FormData();
        formData.append('file', file);
        try {{
          const res = await fetch('/admin/form-fields/import', {{ method: 'POST', body: formData }});
          const data = await res.json();
          if (data.success) {{
            alert('✅ تم استيراد ' + data.count + ' حقل بنجاح!');
            window.location.reload();
          }} else {{
            alert('❌ خطأ: ' + data.error);
          }}
        }} catch(err) {{
          alert('❌ خطأ في الاتصال: ' + err.message);
        }}
      }}
    </script></body></html>"""

@app.get("/admin/form-field/new", response_class=HTMLResponse)
async def new_field(user=Depends(require_role(Role.ADMIN.value)), section_id: int = Query(None), edit_template_id: str = Query(default=None), db: Session = Depends(get_db)):
    if not section_id:
        raise HTTPException(400, "يجب تحديد القسم")
    section = db.query(Section).filter(Section.id == section_id).first()
    if not section:
        raise HTTPException(404, "القسم غير موجود")
    # معالجة edit_template_id الفارغ أو غير الصحيح
    try:
        edit_tid = int(edit_template_id) if edit_template_id and edit_template_id.strip() else None
    except (ValueError, TypeError):
        edit_tid = None
    return _render_field_form(None, section_id, section.name, db, edit_tid)

@app.get("/admin/form-field/edit/{fid}", response_class=HTMLResponse)
async def edit_field(fid: int, user=Depends(require_role(Role.ADMIN.value)), edit_template_id: str = Query(default=None), db: Session = Depends(get_db)):
    f = db.query(FormField).filter(FormField.id == fid).first()
    if not f:
        raise HTTPException(404)
    section = db.query(Section).filter(Section.id == f.section_id).first()
    section_name = section.name if section else "غير محدد"
    # معالجة edit_template_id الفارغ أو غير الصحيح
    try:
        edit_tid = int(edit_template_id) if edit_template_id and edit_template_id.strip() else None
    except (ValueError, TypeError):
        edit_tid = None
    return _render_field_form(f, f.section_id, section_name, db, edit_tid)

def _render_field_form(field, section_id, section_name, db, edit_template_id=None):
    rec_categories = db.query(RecommendationCategory).order_by(RecommendationCategory.order).all()
    
    is_edit = field is not None
    vals = field.__dict__ if is_edit else {"field_key":"","label":"","field_type":"text","is_required":False,"options_json":"","order":1,"has_recommendations":False}
    opts_list = []
    try:
        if vals.get("options_json"): opts_list = json.loads(vals["options_json"])
        if not isinstance(opts_list, list): opts_list = []
    except: opts_list = []

    opts_html = "".join(f'<div class="flex gap-2 mb-1"><input type="text" name="opt_{i}" value="{o}" class="flex-1 p-1 border rounded"><button type="button" onclick="this.parentElement.remove()" class="text-red-500">🗑️</button></div>' for i,o in enumerate(opts_list))
    
    has_rec_checked = "checked" if vals.get("has_recommendations") else ""
    # معالجة التوصيات للحقول الجديدة
    selected_rec_cats = []
    if is_edit and hasattr(field, 'recommendation_categories'):
        try:
            selected_rec_cats = json.loads(field.recommendation_categories) if field.recommendation_categories else []
            if not isinstance(selected_rec_cats, list):
                selected_rec_cats = []
        except:
            selected_rec_cats = []
    
    rec_cats_options = "".join(f'<label class="flex gap-2 p-2 border rounded mb-1"><input type="checkbox" name="rec_cat" value="{c.key}" {"checked" if c.key in selected_rec_cats else ""}> {c.label}</label>' for c in rec_categories)
    
    # معالجة edit_template_id بشكل آمن
    edit_tid_str = str(edit_template_id) if edit_template_id is not None else ""

    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-gray-50 p-4"><div class="max-w-xl mx-auto bg-white p-6 rounded shadow"><h1 class="text-xl font-bold mb-2">{'تعديل' if is_edit else 'إضافة'} حقل</h1>
    <p class="text-sm text-gray-600 mb-4">القسم: <strong>{section_name}</strong></p>
    <form action="/admin/form-field" method="post" class="space-y-3">
      <input name="field_key" value="{vals['field_key']}" placeholder="مفتاح الحقل (مثال: doctors_count)" required class="w-full p-2 border rounded" {'readonly' if is_edit else ''}>
      <input name="label" value="{vals['label']}" placeholder="التسمية الظاهرة" required class="w-full p-2 border rounded">
      <select name="field_type" class="w-full p-2 border rounded">
        <option {'selected' if vals['field_type']=='text' else ''}>text</option><option {'selected' if vals['field_type']=='textarea' else ''}>textarea</option>
        <option {'selected' if vals['field_type']=='number' else ''}>number</option><option {'selected' if vals['field_type']=='date' else ''}>date</option>
        <option {'selected' if vals['field_type']=='select' else ''}>select</option><option {'selected' if vals['field_type']=='checkbox' else ''}>checkbox</option>
        <option {'selected' if vals['field_type']=='radio' else ''}>radio</option><option {'selected' if vals['field_type']=='email' else ''}>email</option>
        <option {'selected' if vals['field_type']=='phone' else ''}>phone</option><option {'selected' if vals['field_type']=='url' else ''}>url</option>
      </select>
      <input name="order" type="number" value="{vals['order']}" class="w-full p-2 border rounded">
      <div><label class="flex gap-2 items-center"><input type="checkbox" name="is_required" {'checked' if vals.get('is_required') else ''} class="rounded"> <span>مطلوب</span></label></div>
      <div id="opts-container" class="{'hidden' if vals['field_type']!='select' else ''} bg-blue-50 p-3 rounded border">
        <p class="text-sm font-bold mb-2">خيارات القائمة:</p>
        <div id="opts-list">{opts_html}</div>
        <button type="button" onclick="addOpt()" class="text-xs bg-blue-100 text-blue-700 px-2 py-1 rounded mt-1">➕ إضافة خيار</button>
      </div>
      <div class="border rounded p-3 bg-green-50">
        <label class="flex gap-2 items-start mb-3"><input type="checkbox" name="has_recommendations" {has_rec_checked} onchange="document.getElementById('rec-cats').classList.toggle('hidden',!this.checked)" class="mt-1 rounded"> <span class="font-bold">هل لهذا الحقل توصيات؟</span></label>
        <div id="rec-cats" class="{'hidden' if not vals.get('has_recommendations') else ''} bg-white p-2 rounded border">{rec_cats_options}</div>
      </div>
      <input type="hidden" name="section_id" value="{section_id}">
      <input type="hidden" name="id" value="{field.id if is_edit else ''}">
      <input type="hidden" name="edit_template_id" value="{edit_tid_str}">
      <input type="hidden" name="options_json" id="options_json">
      <button class="w-full bg-blue-600 text-white py-2 rounded mt-4">💾 حفظ</button></form></div>
    <script>
    document.querySelector('select[name="field_type"]').onchange=function(){{
      document.getElementById('opts-container').classList.toggle('hidden', this.value!=='select');
    }};
    function addOpt(){{document.getElementById('opts-list').insertAdjacentHTML('beforeend',`<div class="flex gap-2 mb-1"><input type="text" name="opt_${{document.getElementById('opts-list').children.length}}" class="flex-1 p-1 border rounded"><button type="button" onclick="this.parentElement.remove()" class="text-red-500">🗑️</button></div>`);}}
    document.querySelector('form').onsubmit=function(){{
      const opts=[];
      document.querySelectorAll('#opts-list input[type="text"]').forEach(i=>{{if(i.value.trim())opts.push(i.value.trim())}});
      document.getElementById('options_json').value=JSON.stringify(opts);
    }};</script></body></html>"""

@app.post("/admin/form-field")
async def save_field(request: Request, db: Session = Depends(get_db), user=Depends(require_role(Role.ADMIN.value))):
    form = await request.form()
    id = form.get("id")
    field_key = form.get("field_key", "").strip()
    label = form.get("label", "").strip()
    field_type = form.get("field_type", "text")
    section_id = int(form.get("section_id", 0))
    order = int(form.get("order", 1))
    options_json = form.get("options_json", "")
    is_required = "is_required" in form
    has_recommendations = "has_recommendations" in form

    # التحقق من الحقول المطلوبة
    if not field_key or not label:
        raise HTTPException(422, "يجب تعبئة مفتاح الحقل والتسمية")
    
    if id:
        f = db.query(FormField).filter(FormField.id == id).first()
        if f:
            f.label = label
            f.field_type = field_type
            f.order = order
            f.options_json = options_json
            f.is_required = is_required
            f.has_recommendations = has_recommendations
            log_action(user.id, "EDIT_FIELD", field_key, request.headers.get("x-forwarded-for", request.client.host))
    else:
        if db.query(FormField).filter(FormField.field_key == field_key).first():
            raise HTTPException(400, "المفتاح موجود بالفعل")
        f = FormField(
            section_id=section_id,
            field_key=field_key,
            label=label,
            field_type=field_type,
            order=order,
            options_json=options_json,
            is_required=is_required,
            has_recommendations=has_recommendations
        )
        db.add(f)
        log_action(user.id, "CREATE_FIELD", field_key, request.headers.get("x-forwarded-for", request.client.host))
    
    db.commit()
    edit_template_id = form.get("edit_template_id")
    redirect_url = f"/admin/form-builder?edit_template_id={edit_template_id}" if edit_template_id else "/admin/form-builder"
    return RedirectResponse(url=redirect_url, status_code=302)

# ================== إعدادات + سجل + تصدير + إحصائيات ==================
@app.get("/admin/settings", response_class=HTMLResponse)
async def admin_settings(user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    s = {x.key: x.value for x in db.query(SystemSetting).all()}
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-gray-50 p-4"><div class="max-w-xl mx-auto bg-white p-6 rounded shadow"><h1 class="text-xl font-bold mb-4">⚙️ الإعدادات</h1>
    <form action="/admin/settings" method="post" class="space-y-3"><input name="tg_bot_token" placeholder="Telegram Token" value="{s.get('tg_bot_token','')}" class="w-full p-2 border rounded">
    <input name="tg_chat_id" placeholder="Telegram Chat ID" value="{s.get('tg_chat_id','')}" class="w-full p-2 border rounded">
    <input name="wa_api_url" placeholder="WhatsApp URL" value="{s.get('wa_api_url','')}" class="w-full p-2 border rounded">
    <input name="wa_api_key" placeholder="WhatsApp Key" value="{s.get('wa_api_key','')}" class="w-full p-2 border rounded">
    <input name="wa_phone" placeholder="رقم المدير" value="{s.get('wa_phone','')}" class="w-full p-2 border rounded">
    <button class="w-full bg-indigo-600 text-white py-2 rounded">حفظ</button></form></div></body></html>"""

@app.post("/admin/settings")
async def save_settings(request: Request, db: Session = Depends(get_db), user=Depends(require_role(Role.ADMIN.value)), **kwargs):
    for k, v in kwargs.items():
        s = db.query(SystemSetting).filter(SystemSetting.key == k).first()
        if s: s.value = v
    db.commit(); log_action(user.id, "UPDATE_SETTINGS", "تحديث إعدادات", request.headers.get("x-forwarded-for", request.client.host))
    return RedirectResponse(url="/admin/settings", status_code=302)

# ================== إدارة النماذج المحفوظة ==================
@app.get("/admin/templates", response_class=HTMLResponse)
async def admin_templates(user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    templates = db.query(FormTemplate).order_by(FormTemplate.created_at.desc()).all()
    rows = "".join(f'''<tr class="border-b hover:bg-gray-50"><td class="p-3"><strong>{t.name}</strong><br><span class="text-xs text-gray-500">{t.description or ""}</span></td>
    <td class="p-3 text-sm text-gray-600">{t.created_at.strftime("%d/%m/%Y %H:%M")}</td>
    <td class="p-3 text-center"><span class="text-xs px-2 py-1 rounded {'bg-green-100 text-green-700' if t.is_active else 'bg-red-100 text-red-700'}">{'✓ نشط' if t.is_active else '✗ معطل'}</span></td>
    <td class="p-3 text-right"><a href="/admin/templates/{t.id}/use" class="text-blue-600 text-sm mx-1">استخدام</a><a href="/admin/templates/{t.id}/edit" class="text-blue-600 text-sm mx-1">تعديل</a><a href="/admin/templates/{t.id}/duplicate" class="text-green-600 text-sm mx-1">نسخ</a><a href="/admin/templates/{t.id}/delete" onclick="return confirm('حذف؟')" class="text-red-600 text-sm">حذف</a></td></tr>''' for t in templates)
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-4">
    <div class="max-w-5xl mx-auto bg-white p-6 rounded shadow"><div class="flex justify-between items-center mb-6"><h1 class="text-2xl font-bold">📋 النماذج المحفوظة</h1><a href="/admin/panel" class="text-blue-600">← العودة</a></div>
    <div class="overflow-x-auto"><table class="w-full text-right"><thead class="bg-gray-100 border-b-2"><tr><th class="p-3">اسم النموذج</th><th>تاريخ الإنشاء</th><th>الحالة</th><th>الإجراءات</th></tr></thead><tbody>{rows}</tbody></table></div></div></body></html>"""

@app.post("/admin/templates/save")
async def save_template(request: Request, db: Session = Depends(get_db), user=Depends(require_role(Role.ADMIN.value))):
    form = await request.form()
    name = form.get("name")
    description = form.get("description", "")
    
    # التحقق من عدم تكرار الاسم
    if db.query(FormTemplate).filter(FormTemplate.name == name).first():
        raise HTTPException(400, "اسم النموذج موجود بالفعل")
    
    # جمع بيانات الأقسام والحقول من قاعدة البيانات
    sections = db.query(Section).order_by(Section.order).all()
    sections_data = {}
    
    for section in sections:
        fields = db.query(FormField).filter(FormField.section_id == section.id, FormField.is_active == True).order_by(FormField.order).all()
        sections_data[section.id] = {
            "name": section.name,
            "parent_id": section.parent_id,
            "order": section.order,
            "fields": [{
                "field_key": f.field_key,
                "label": f.label,
                "field_type": f.field_type,
                "is_required": f.is_required,
                "options_json": f.options_json,
                "has_recommendations": f.has_recommendations,
                "order": f.order
            } for f in fields]
        }
    
    template = FormTemplate(
        name=name,
        description=description,
        sections_json=json.dumps(sections_data, ensure_ascii=False),
        created_by=user.id
    )
    db.add(template)
    db.commit()
    log_action(user.id, "CREATE_TEMPLATE", name, request.headers.get("x-forwarded-for", request.client.host))
    return RedirectResponse(url="/admin/templates", status_code=302)

@app.get("/admin/templates/{tid}/edit", response_class=HTMLResponse)
async def edit_template_page(tid: int, user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    t = db.query(FormTemplate).filter(FormTemplate.id == tid).first()
    if not t:
        raise HTTPException(404, "النموذج غير موجود")
    try:
        sections_data = json.loads(t.sections_json)
    except:
        raise HTTPException(400, "خطأ في بيانات النموذج")

    db.query(FormField).delete()
    db.query(Section).delete()
    db.commit()
    id_mapping = {}

    for old_sec_id, sec_info in sections_data.items():
        if sec_info.get("parent_id") is None:
            new_sec = Section(name=sec_info["name"], order=sec_info.get("order", 0))
            db.add(new_sec)
            db.flush()
            id_mapping[str(old_sec_id)] = new_sec.id
            for f_data in sec_info.get("fields", []):
                db.add(FormField(section_id=new_sec.id, field_key=f_data.get("field_key"), label=f_data.get("label"), field_type=f_data.get("field_type", "text"), is_required=f_data.get("is_required", False), options_json=f_data.get("options_json", ""), has_recommendations=f_data.get("has_recommendations", False), order=f_data.get("order", 0)))

    for old_sec_id, sec_info in sections_data.items():
        old_parent_id = sec_info.get("parent_id")
        if old_parent_id is not None and str(old_parent_id) in id_mapping:
            new_sec = Section(name=sec_info["name"], parent_id=id_mapping[str(old_parent_id)], order=sec_info.get("order", 0))
            db.add(new_sec)
            db.flush()
            id_mapping[str(old_sec_id)] = new_sec.id
            for f_data in sec_info.get("fields", []):
                db.add(FormField(section_id=new_sec.id, field_key=f_data.get("field_key"), label=f_data.get("label"), field_type=f_data.get("field_type", "text"), is_required=f_data.get("is_required", False), options_json=f_data.get("options_json", ""), has_recommendations=f_data.get("has_recommendations", False), order=f_data.get("order", 0)))

    db.commit()
    return RedirectResponse(url=f"/admin/form-builder?edit_template_id={tid}", status_code=302)

@app.post("/admin/templates/{tid}/edit")
async def edit_template_submit(tid: int, request: Request, user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    t = db.query(FormTemplate).filter(FormTemplate.id == tid).first()
    if not t:
        raise HTTPException(404, "النموذج غير موجود")

    form = await request.form()
    name = str(form.get("name", "")).strip()
    description = str(form.get("description", "")).strip()
    if not name:
        raise HTTPException(400, "اسم النموذج مطلوب")

    duplicate = db.query(FormTemplate).filter(FormTemplate.name == name, FormTemplate.id != tid).first()
    if duplicate:
        raise HTTPException(400, "اسم النموذج موجود بالفعل")

    t.name = name
    t.description = description
    t.is_active = form.get("is_active") == "1"
    t.updated_at = datetime.utcnow()

    if form.get("refresh_from_builder") == "1":
        sections = db.query(Section).order_by(Section.order).all()
        sections_data = {}
        for section in sections:
            fields = db.query(FormField).filter(FormField.section_id == section.id, FormField.is_active == True).order_by(FormField.order).all()
            sections_data[section.id] = {
                "name": section.name,
                "parent_id": section.parent_id,
                "order": section.order,
                "fields": [{
                    "field_key": f.field_key,
                    "label": f.label,
                    "field_type": f.field_type,
                    "is_required": f.is_required,
                    "options_json": f.options_json,
                    "has_recommendations": f.has_recommendations,
                    "order": f.order
                } for f in fields]
            }
        t.sections_json = json.dumps(sections_data, ensure_ascii=False)

    db.commit()
    log_action(user.id, "EDIT_TEMPLATE", name, request.headers.get("x-forwarded-for", request.client.host))
    return RedirectResponse(url="/admin/templates", status_code=302)

@app.post("/admin/templates/{tid}/update-from-builder")
async def update_template_from_builder(tid: int, request: Request, user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    t = db.query(FormTemplate).filter(FormTemplate.id == tid).first()
    if not t:
        raise HTTPException(404, "النموذج غير موجود")

    sections = db.query(Section).order_by(Section.order).all()
    sections_data = {}
    for section in sections:
        fields = db.query(FormField).filter(FormField.section_id == section.id, FormField.is_active == True).order_by(FormField.order).all()
        sections_data[section.id] = {
            "name": section.name,
            "parent_id": section.parent_id,
            "order": section.order,
            "fields": [{
                "field_key": f.field_key,
                "label": f.label,
                "field_type": f.field_type,
                "is_required": f.is_required,
                "options_json": f.options_json,
                "has_recommendations": f.has_recommendations,
                "order": f.order
            } for f in fields]
        }
    t.sections_json = json.dumps(sections_data, ensure_ascii=False)
    t.updated_at = datetime.utcnow()
    db.commit()
    log_action(user.id, "UPDATE_TEMPLATE_FROM_BUILDER", t.name, request.headers.get("x-forwarded-for", request.client.host))
    return RedirectResponse(url="/admin/templates", status_code=302)

@app.get("/admin/templates/{tid}/delete")
async def delete_template(tid: int, request: Request, user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    t = db.query(FormTemplate).filter(FormTemplate.id == tid).first()
    if not t:
        raise HTTPException(404)
    db.delete(t)
    db.commit()
    log_action(user.id, "DELETE_TEMPLATE", t.name, request.headers.get("x-forwarded-for", request.client.host))
    return RedirectResponse(url="/admin/templates", status_code=302)

@app.get("/admin/templates/{tid}/duplicate")
async def duplicate_template(tid: int, request: Request, user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    t = db.query(FormTemplate).filter(FormTemplate.id == tid).first()
    if not t:
        raise HTTPException(404)
    
    new_name = f"{t.name} (نسخة)"
    counter = 1
    while db.query(FormTemplate).filter(FormTemplate.name == new_name).first():
        new_name = f"{t.name} (نسخة {counter})"
        counter += 1
    
    new_template = FormTemplate(
        name=new_name,
        description=t.description,
        sections_json=t.sections_json,
        created_by=user.id
    )
    db.add(new_template)
    db.commit()
    log_action(user.id, "DUPLICATE_TEMPLATE", f"{t.name} -> {new_name}", request.headers.get("x-forwarded-for", request.client.host))
    return RedirectResponse(url="/admin/templates", status_code=302)

@app.get("/admin/templates/{tid}/use", response_class=HTMLResponse)
async def use_template(tid: int, user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    t = db.query(FormTemplate).filter(FormTemplate.id == tid).first()
    if not t:
        raise HTTPException(404, "النموذج غير موجود")
    
    try:
        sections_data = json.loads(t.sections_json)
    except:
        raise HTTPException(400, "خطأ في بيانات النموذج")
    
    # عرض تفاصيل النموذج
    sections_html = ""
    for sec_id, sec_info in sections_data.items():
        fields = sec_info.get("fields", [])
        fields_html = "".join(f'''<li class="p-2 bg-white rounded border">
          <span class="font-bold text-blue-700">{f['label']}</span> 
          <code class="text-xs bg-gray-100 px-1 rounded">{f['field_key']}</code>
          <span class="text-xs text-gray-500">({'✓ توصيات' if f.get('has_recommendations') else ''})</span></li>''' for f in fields)
        
        sections_html += f'''<div class="mb-4 border rounded p-3 bg-gray-50">
          <h3 class="font-bold text-blue-700 mb-2">{sec_info['name']}</h3>
          <ul class="space-y-1">{fields_html}</ul></div>'''
    
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-4">
    <div class="max-w-3xl mx-auto bg-white p-6 rounded shadow">
      <div class="flex justify-between items-center mb-6"><h1 class="text-2xl font-bold">{t.name}</h1><a href="/admin/templates" class="text-blue-600">← العودة</a></div>
      <div class="mb-6 p-3 bg-blue-50 rounded border border-blue-200">
        <p class="text-sm text-gray-700"><strong>الوصف:</strong> {t.description or "بدون وصف"}</p>
        <p class="text-xs text-gray-500 mt-2">تم الإنشاء: {t.created_at.strftime('%d/%m/%Y %H:%M')}</p>
      </div>
      <h2 class="text-lg font-bold mb-4">الأقسام والحقول:</h2>
      {sections_html}
      <div class="mt-6 flex gap-2">
        <a href="/admin/templates/{tid}/apply" class="flex-1 bg-green-600 text-white py-3 rounded font-bold text-center">✓ تطبيق هذا النموذج</a>
        <a href="/admin/templates" class="flex-1 bg-gray-400 text-white py-3 rounded font-bold text-center">← العودة</a>
      </div></div></body></html>"""

@app.get("/admin/templates/{tid}/apply")
async def apply_template(tid: int, user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    """تطبيق النموذج بجعله النموذج الحالي في بناء الاستمارة"""
    t = db.query(FormTemplate).filter(FormTemplate.id == tid).first()
    if not t:
        raise HTTPException(404)
    
    try:
        sections_data = json.loads(t.sections_json)
    except:
        raise HTTPException(400, "خطأ في بيانات النموذج")
    
    # 1. مسح الاستمارة الحالية (حذف الحقول ثم الأقسام)
    db.query(FormField).delete()
    db.query(Section).delete()
    db.commit()

    # 2. بناء الأقسام والحقول من القالب
    id_mapping = {}
    
    # إضافة الأقسام الرئيسية أولاً
    for old_sec_id, sec_info in sections_data.items():
        if sec_info.get("parent_id") is None:
            new_sec = Section(name=sec_info["name"], order=sec_info["order"])
            db.add(new_sec)
            db.flush() # توليد المعرف الجديد فوراً
            id_mapping[str(old_sec_id)] = new_sec.id

            # إضافة حقول القسم الرئيسي
            for f_data in sec_info.get("fields", []):
                db.add(FormField(
                    section_id=new_sec.id,
                    field_key=f_data.get("field_key"),
                    label=f_data.get("label"),
                    field_type=f_data.get("field_type", "text"),
                    is_required=f_data.get("is_required", False),
                    options_json=f_data.get("options_json", ""),
                    has_recommendations=f_data.get("has_recommendations", False),
                    order=f_data.get("order", 0)
                ))

    # إضافة الأقسام الفرعية وحقولها
    for old_sec_id, sec_info in sections_data.items():
        old_parent_id = sec_info.get("parent_id")
        if old_parent_id is not None:
            new_parent_id = id_mapping.get(str(old_parent_id))
            new_sec = Section(name=sec_info["name"], parent_id=new_parent_id, order=sec_info["order"])
            db.add(new_sec)
            db.flush()

            for f_data in sec_info.get("fields", []):
                db.add(FormField(
                    section_id=new_sec.id,
                    field_key=f_data.get("field_key"),
                    label=f_data.get("label"),
                    field_type=f_data.get("field_type", "text"),
                    is_required=f_data.get("is_required", False),
                    options_json=f_data.get("options_json", ""),
                    has_recommendations=f_data.get("has_recommendations", False),
                    order=f_data.get("order", 0)
                ))

    db.commit()
    log_action(user.id, "APPLY_TEMPLATE", t.name, "")
    
    return RedirectResponse(url="/admin/form-builder", status_code=302)

# ================== إدارة فئات التوصيات ==================
@app.get("/admin/recommendations", response_class=HTMLResponse)
async def admin_recommendations(user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    recs = db.query(RecommendationCategory).order_by(RecommendationCategory.order).all()
    rows = "".join(f'''<tr class="border-b hover:bg-gray-50"><td class="p-3 font-mono text-sm">{r.key}</td>
    <td class="p-3">{r.label}</td><td class="p-3 text-center"><span class="text-gray-500 text-sm">#{r.order}</span></td>
    <td class="p-3 text-center"><form action="/admin/recommendations/{r.id}/toggle" method="post" style="display:inline"><button class="text-sm px-2 py-1 rounded {'' if r.is_active else 'bg-red-100 text-red-700'}">{'✓ فعال' if r.is_active else '✗ معطل'}</button></form></td>
    <td class="p-3 text-right"><a href="/admin/recommendations/{r.id}/edit" class="text-blue-600 text-sm mx-1">تعديل</a><a href="/admin/recommendations/{r.id}/delete" onclick="return confirm('حذف؟')" class="text-red-600 text-sm">حذف</a></td></tr>''' for r in recs)
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-4">
    <div class="max-w-4xl mx-auto bg-white p-6 rounded shadow"><div class="flex justify-between items-center mb-6"><h1 class="text-2xl font-bold">🎯 فئات التوصيات</h1><a href="/admin/panel" class="text-blue-600">← العودة</a></div>
    <a href="/admin/recommendations/new" class="bg-green-600 text-white px-4 py-2 rounded mb-4 inline-block">+ فئة جديدة</a>
    <div class="overflow-x-auto"><table class="w-full text-right"><thead class="bg-gray-100 border-b-2"><tr><th class="p-3 text-left">المفتاح</th><th>النص</th><th>الترتيب</th><th>الحالة</th><th>الإجراءات</th></tr></thead><tbody>{rows}</tbody></table></div></div></body></html>"""

@app.get("/admin/recommendations/new", response_class=HTMLResponse)
async def new_recommendation_form(user=Depends(require_role(Role.ADMIN.value))):
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-4">
    <div class="max-w-2xl mx-auto bg-white p-6 rounded shadow"><h1 class="text-xl font-bold mb-4">فئة توصيات جديدة</h1>
    <form action="/admin/recommendations/save" method="post" class="space-y-3">
    <div><label class="block font-bold mb-1">المفتاح (key)</label><input name="key" placeholder="rec_a" class="w-full p-2 border rounded" required></div>
    <div><label class="block font-bold mb-1">النص</label><input name="label" placeholder="أ/ الإيعاز إلى..." class="w-full p-2 border rounded" required></div>
    <div><label class="block font-bold mb-1">الترتيب</label><input name="order" type="number" placeholder="1" class="w-full p-2 border rounded" required></div>
    <button class="w-full bg-green-600 text-white py-2 rounded font-bold">حفظ</button></form></div></body></html>"""

@app.get("/admin/recommendations/{rec_id}/edit", response_class=HTMLResponse)
async def edit_recommendation_form(rec_id: int, user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    r = db.query(RecommendationCategory).filter(RecommendationCategory.id == rec_id).first()
    if not r: raise HTTPException(404)
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-4">
    <div class="max-w-2xl mx-auto bg-white p-6 rounded shadow"><h1 class="text-xl font-bold mb-4">تعديل الفئة</h1>
    <form action="/admin/recommendations/save" method="post" class="space-y-3">
    <input name="id" type="hidden" value="{r.id}">
    <div><label class="block font-bold mb-1">المفتاح (key)</label><input name="key" placeholder="rec_a" value="{r.key}" class="w-full p-2 border rounded" required></div>
    <div><label class="block font-bold mb-1">النص</label><input name="label" placeholder="أ/ الإيعاز إلى..." value="{r.label}" class="w-full p-2 border rounded" required></div>
    <div><label class="block font-bold mb-1">الترتيب</label><input name="order" type="number" value="{r.order}" class="w-full p-2 border rounded" required></div>
    <button class="w-full bg-blue-600 text-white py-2 rounded font-bold">تحديث</button></form></div></body></html>"""

@app.post("/admin/recommendations/save")
async def save_recommendation(request: Request, db: Session = Depends(get_db), user=Depends(require_role(Role.ADMIN.value))):
    form = await request.form()
    rec_id = form.get("id")
    
    if rec_id:
        r = db.query(RecommendationCategory).filter(RecommendationCategory.id == rec_id).first()
        if not r: raise HTTPException(404)
        r.key = form.get("key")
        r.label = form.get("label")
        r.order = int(form.get("order", 0))
        log_action(user.id, "EDIT_REC_CAT", f"تعديل {form.get('key')}", request.headers.get("x-forwarded-for", request.client.host))
    else:
        r = RecommendationCategory(key=form.get("key"), label=form.get("label"), order=int(form.get("order", 0)))
        db.add(r)
        log_action(user.id, "CREATE_REC_CAT", f"إضافة {form.get('key')}", request.headers.get("x-forwarded-for", request.client.host))
    
    db.commit()
    return RedirectResponse(url="/admin/recommendations", status_code=302)

@app.post("/admin/recommendations/{rec_id}/toggle")
async def toggle_recommendation(rec_id: int, request: Request, user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    r = db.query(RecommendationCategory).filter(RecommendationCategory.id == rec_id).first()
    if not r: raise HTTPException(404)
    r.is_active = not r.is_active
    db.commit()
    log_action(user.id, "TOGGLE_REC_CAT", f"تفعيل/تعطيل {r.key}", request.headers.get("x-forwarded-for", request.client.host))
    return RedirectResponse(url="/admin/recommendations", status_code=302)

@app.get("/admin/recommendations/{rec_id}/delete")
async def delete_recommendation(rec_id: int, request: Request, user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    r = db.query(RecommendationCategory).filter(RecommendationCategory.id == rec_id).first()
    if not r: raise HTTPException(404)
    db.delete(r)
    db.commit()
    log_action(user.id, "DELETE_REC_CAT", f"حذف {r.key}", request.headers.get("x-forwarded-for", request.client.host))
    return RedirectResponse(url="/admin/recommendations", status_code=302)

# ================== إدارة الأقسام المتداخلة ==================
@app.get("/admin/sections", response_class=HTMLResponse)
async def admin_sections(user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    sections = db.query(Section).order_by(Section.order).all()
    
    def build_tree(parent_id=None, level=0):
        items = [s for s in sections if s.parent_id == parent_id]
        html = ""
        for s in items:
            indent = "&nbsp;" * (level * 4)
            status = '✓ فعال' if s.is_active else '✗ معطل'
            html += f'<tr class="border-b hover:bg-gray-50"><td class="p-3">{indent} {s.name}</td><td class="p-3 text-center"><span class="text-gray-500 text-sm">#{s.order}</span></td>'
            html += f'<td class="p-3 text-center"><span class="text-xs px-2 py-1 rounded bg-gray-100">{status}</span></td>'
            html += f'<td class="p-3 text-right"><a href="/admin/sections/{s.id}/edit" class="text-blue-600 text-sm mx-1">تعديل</a><a href="/admin/sections/{s.id}/delete" onclick="return confirm(\'حذف؟\')" class="text-red-600 text-sm">حذف</a></td></tr>'
            html += build_tree(s.id, level + 1)
        return html
    
    rows = build_tree()
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-4">
    <div class="max-w-4xl mx-auto bg-white p-6 rounded shadow"><div class="flex justify-between items-center mb-6"><h1 class="text-2xl font-bold">📑 الأقسام</h1><a href="/admin/panel" class="text-blue-600">← العودة</a></div>
    <a href="/admin/sections/new" class="bg-green-600 text-white px-4 py-2 rounded mb-4 inline-block">+ قسم جديد</a>
    <div class="overflow-x-auto"><table class="w-full text-right"><thead class="bg-gray-100 border-b-2"><tr><th class="p-3">اسم القسم</th><th>الترتيب</th><th>الحالة</th><th>الإجراءات</th></tr></thead><tbody>{rows}</tbody></table></div></div></body></html>"""

@app.get("/admin/sections/new", response_class=HTMLResponse)
async def new_section_form(user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    sections = db.query(Section).order_by(Section.order).all()
    opts = "".join(f'<option value="{s.id}">{s.name}</option>' for s in sections)
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-4">
    <div class="max-w-2xl mx-auto bg-white p-6 rounded shadow"><h1 class="text-xl font-bold mb-4">قسم جديد</h1>
    <form action="/admin/sections/save" method="post" class="space-y-3">
    <div><label class="block font-bold mb-1">اسم القسم</label><input name="name" placeholder="مثلاً: السجلات" class="w-full p-2 border rounded" required></div>
    <div><label class="block font-bold mb-1">القسم الأب (اختياري)</label><select name="parent_id" class="w-full p-2 border rounded"><option value="">بدون (قسم رئيسي)</option>{opts}</select></div>
    <div><label class="block font-bold mb-1">الترتيب</label><input name="order" type="number" placeholder="0" class="w-full p-2 border rounded" required></div>
    <button class="w-full bg-green-600 text-white py-2 rounded font-bold">حفظ</button></form></div></body></html>"""

@app.get("/admin/sections/{sec_id}/edit", response_class=HTMLResponse)
async def edit_section_form(sec_id: int, user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    s = db.query(Section).filter(Section.id == sec_id).first()
    if not s: raise HTTPException(404)
    sections = db.query(Section).filter(Section.id != sec_id).order_by(Section.order).all()
    opts = "".join(f'<option value="{sect.id}" {"selected" if sect.id == s.parent_id else ""}>{sect.name}</option>' for sect in sections)
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-4">
    <div class="max-w-2xl mx-auto bg-white p-6 rounded shadow"><h1 class="text-xl font-bold mb-4">تعديل القسم</h1>
    <form action="/admin/sections/save" method="post" class="space-y-3">
    <input name="id" type="hidden" value="{s.id}">
    <div><label class="block font-bold mb-1">اسم القسم</label><input name="name" value="{s.name}" class="w-full p-2 border rounded" required></div>
    <div><label class="block font-bold mb-1">القسم الأب</label><select name="parent_id" class="w-full p-2 border rounded"><option value="">بدون (قسم رئيسي)</option>{opts}</select></div>
    <div><label class="block font-bold mb-1">الترتيب</label><input name="order" type="number" value="{s.order}" class="w-full p-2 border rounded" required></div>
    <button class="w-full bg-blue-600 text-white py-2 rounded font-bold">تحديث</button></form></div></body></html>"""

@app.post("/admin/sections/save")
async def save_section(request: Request, db: Session = Depends(get_db), user=Depends(require_role(Role.ADMIN.value))):
    form = await request.form()
    sec_id = form.get("id")
    parent_id = form.get("parent_id")
    parent_id = int(parent_id) if parent_id else None
    
    if sec_id:
        s = db.query(Section).filter(Section.id == sec_id).first()
        if not s: raise HTTPException(404)
        s.name = form.get("name")
        s.parent_id = parent_id
        s.order = int(form.get("order", 0))
        log_action(user.id, "EDIT_SECTION", f"تعديل {form.get('name')}", request.headers.get("x-forwarded-for", request.client.host))
    else:
        s = Section(name=form.get("name"), parent_id=parent_id, order=int(form.get("order", 0)))
        db.add(s)
        log_action(user.id, "CREATE_SECTION", f"إضافة {form.get('name')}", request.headers.get("x-forwarded-for", request.client.host))
    
    db.commit()
    return RedirectResponse(url="/admin/sections", status_code=302)

@app.get("/admin/sections/{sec_id}/delete")
async def delete_section(sec_id: int, request: Request, user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    s = db.query(Section).filter(Section.id == sec_id).first()
    if not s: raise HTTPException(404)
    
    # التحقق من وجود أقسام أو حقول فرعية
    children = db.query(Section).filter(Section.parent_id == sec_id).count()
    fields = db.query(FormField).filter(FormField.section_id == sec_id).count()
    
    if children > 0 or fields > 0:
        raise HTTPException(400, "لا يمكن حذف قسم يحتوي على عناصر فرعية")
    
    db.delete(s)
    db.commit()
    log_action(user.id, "DELETE_SECTION", f"حذف {s.name}", request.headers.get("x-forwarded-for", request.client.host))
    return RedirectResponse(url="/admin/sections", status_code=302)

@app.get("/admin/logs", response_class=HTMLResponse)
async def admin_logs(user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    logs = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(100).all()
    rows = "".join(f'''<tr class="border-b"><td class="p-2 text-sm">{l.timestamp.strftime('%d/%m %H:%M')}</td>
    <td class="p-2 text-sm">{db.query(User).filter(User.id==l.user_id).first().username if l.user_id else 'نظام'}</td>
    <td class="p-2 text-sm"><span class="px-2 py-1 rounded bg-blue-100 text-xs">{l.action}</span></td>
    <td class="p-2 text-xs text-gray-500">{l.details or '-'}</td></tr>''' for l in logs)
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-gray-50 p-4"><div class="max-w-5xl mx-auto bg-white p-6 rounded shadow"><div class="flex justify-between mb-4"><h1 class="text-xl font-bold">📜 السجل</h1><div><a href="/admin/logs/export" class="bg-green-600 text-white px-3 py-1 rounded text-sm mr-2">📥 Excel</a><a href="/admin/panel" class="text-blue-600">← العودة</a></div></div>
    <div class="overflow-x-auto"><table class="w-full text-right"><thead class="bg-gray-100"><tr><th class="p-2">الوقت</th><th>المستخدم</th><th>الإجراء</th><th>التفاصيل</th></tr></thead><tbody>{rows}</tbody></table></div></div></body></html>"""

@app.get("/admin/logs/export")
async def export_logs(user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    logs = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).all()
    data = [{"الوقت": l.timestamp.strftime("%Y-%m-%d %H:%M"), "المستخدم": db.query(User).filter(User.id==l.user_id).first().username if l.user_id else "نظام", "الإجراء": l.action, "التفاصيل": l.details, "IP": l.ip_address} for l in logs]
    df = pd.DataFrame(data)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w: df.to_excel(w, index=False, sheet_name="Audit")
    buf.seek(0)
    return StreamingResponse(iter([buf.read()]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=audit_log.xlsx"})

@app.get("/dashboard/stats", response_class=HTMLResponse)
async def stats_dash(user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    w = datetime.utcnow() - timedelta(days=7)
    total_s = db.query(InspectionSession).count()
    total_sub = db.query(Submission).count()
    week_sub = db.query(Submission).filter(Submission.submitted_at >= w).count()
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script><script src="https://cdn.jsdelivr.net/npm/chart.js"></script></head>
    <body class="bg-gray-50 p-4"><div class="max-w-4xl mx-auto"><div class="flex justify-between mb-6 bg-white p-4 rounded shadow"><h1 class="text-xl font-bold">📊 الإحصائيات</h1><a href="/admin/panel" class="text-blue-600">← العودة</a></div>
    <div class="grid grid-cols-3 gap-4 mb-6"><div class="bg-white p-4 rounded shadow text-center"><div class="text-3xl font-bold text-blue-600">{total_s}</div><div>جولات</div></div>
    <div class="bg-white p-4 rounded shadow text-center"><div class="text-3xl font-bold text-green-600">{total_sub}</div><div>إجابات</div></div>
    <div class="bg-white p-4 rounded shadow text-center"><div class="text-3xl font-bold text-purple-600">{week_sub}</div><div>هذا الأسبوع</div></div></div></div></body></html>"""

# ================== واجهة المفتش (الهيكل الجديد المطلوب) ==================

# 🔴 التعديل الأول: رفع مسار /success للأعلى قبل /inspect/{code} حتى لا يتم اعتباره كود جولة
@app.get("/inspect/success", response_class=HTMLResponse)
async def success():
    return """<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-4 flex items-center justify-center h-screen"><div class="bg-white p-8 rounded shadow text-center"><h1 class="text-2xl font-bold text-green-600 mb-2">✅ تم الحفظ بنجاح</h1><p class="text-gray-600 mb-4">سيتم دمج البيانات في التقرير النهائي.</p><a href="/dashboard" class="inline-block bg-blue-600 text-white px-6 py-2 rounded">العودة للوحة</a></div></body></html>"""

@app.get("/inspect/{code}", response_class=HTMLResponse)
async def inspect_form(code: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
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
                    opts = json.loads(f.options_json)
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

    # 🔴 التعديل الثاني: إضافة novalidate لوسم form حتى لا يعلق المتصفح بسبب الحقول المطلوبة المخفية
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-gray-50 p-4">
    <div id="sync-status" class="fixed top-2 right-2 bg-blue-600 text-white px-4 py-2 rounded shadow-lg z-50 text-sm font-bold">🔄 جاري التحميل...</div>
    <div class="max-w-2xl mx-auto bg-white p-6 rounded shadow">
      <div class="bg-blue-600 text-white p-4 rounded-t -mx-6 -mt-6 mb-6">
        <h1 class="text-xl font-bold">{sess.institution} | {sess.visit_date}</h1>
        <p class="text-sm opacity-90 mt-1">المفتش: {user.username} | مفتش</p>
      </div>
      
      <div class="mb-6">
        <label class="block font-bold mb-2">اختر المحور الذي قمت بالتفتيش عنه:</label>
        <select id="axis-selector" class="w-full p-3 border rounded text-lg focus:ring-2 focus:ring-blue-500" onchange="showSection(this.value)">{section_opts}</select>
      </div>
      
      <form action="/inspect/submit" method="post" class="space-y-4" novalidate id="inspectionForm">
        <input type="hidden" name="session_id" value="{sess.id}">
        <input type="hidden" name="unit_name" value="مفتش">
        {sections_html}
        <button type="submit" class="w-full bg-green-600 text-white py-3 rounded font-bold text-lg hover:bg-green-700">✅ حفظ الإجابات</button>
      </form>
    </div>
    <script src="/static/register-sw.js"></script>
    <script src="/static/offline.js"></script>
    <script>
      function showSection(id) {{
        document.querySelectorAll('.section-content').forEach(s => s.classList.add('hidden'));
        document.getElementById(id).classList.remove('hidden');
      }}
      if(document.getElementById('axis-selector')) showSection(document.getElementById('axis-selector').value);
      
      // التعامل مع إرسال النموذج بشكل ذكي (أونلاين/أوفلاين)
      document.getElementById('inspectionForm').addEventListener('submit', async function(e) {{
        e.preventDefault();
        const btn = this.querySelector('button[type=submit]');
        const originalText = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'جاري الحفظ...';
        
        const sessionId = this.querySelector('input[name="session_id"]').value;
        const formData = new FormData(this);
        const data = {{ code: sessionId, ...Object.fromEntries(formData) }};
        data.answers_json = JSON.stringify(Object.fromEntries(formData));
        
        try {{
          if (navigator.onLine) {{
            // إرسال مباشر للسيرفر
            const response = await fetch('/inspect/submit', {{
              method: 'POST',
              body: formData,
              redirect: 'manual'
            }});
            if (response.ok || response.status === 302) {{
              window.location.href = '/inspect/success';
              return;
            }}
          }}
          // حفظ محلي إذا كان أوفلاين أو فشل الإرسال
          await initDB();
          await save(data);
          const statusEl = document.getElementById('sync-status');
          if (statusEl) {{
            statusEl.textContent = '💾 تم الحفظ محلياً - سيرفع عند الاتصال';
            statusEl.classList.remove('bg-blue-600');
            statusEl.classList.add('bg-green-600');
          }}
          setTimeout(() => window.location.href = '/inspect/success', 1000);
        }} catch(err) {{
          // حفظ محلي في حالة الخطأ
          try {{
            await initDB();
            await save(data);
            const statusEl = document.getElementById('sync-status');
            if (statusEl) {{
              statusEl.textContent = '💾 تم الحفظ محلياً - سيرفع عند الاتصال';
              statusEl.classList.remove('bg-blue-600');
              statusEl.classList.add('bg-green-600');
            }}
            setTimeout(() => window.location.href = '/inspect/success', 1000);
          }} catch(e) {{
            btn.disabled = false;
            btn.textContent = originalText;
            alert('❌ حدث خطأ: ' + err.message);
          }}
        }}
      }});
    </script></body></html>"""

@app.post("/inspect/submit")
async def submit_dynamic(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user), bg: BackgroundTasks = None):
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
    log_action(user.id, "SUBMIT_REPORT", f"{sess.institution}", ip)
    if bg:
        bg.add_task(notify_async, "📝 تقرير جديد", f"👤 {user.username}\n🏥 {sess.institution}")
    
    # سيعمل هذا التوجيه بشكل صحيح الآن
    return RedirectResponse(url="/inspect/success", status_code=302)

# ================== توليد التقرير الموحد ==================
@app.get("/admin/session/{sid}", response_class=HTMLResponse)
async def view_session(sid: int, request: Request, user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    s = db.query(InspectionSession).filter(InspectionSession.id == sid).first()
    if not s: raise HTTPException(404)
    subs = db.query(Submission).filter(Submission.session_id == sid).all()
    
    # جلب جميع الحقول لمعرفة الأقسام
    all_fields = db.query(FormField).order_by(FormField.order).all()
    field_map = {f.field_key: f for f in all_fields}
    all_sections = db.query(Section).order_by(Section.order).all()
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
    <div class="grid grid-cols-2 gap-2 mb-4">
      <form action="/generate/{sid}" method="post"><button class="w-full bg-indigo-600 hover:bg-indigo-700 text-white py-3 rounded font-bold">📅 تقرير Word</button></form>
      <form action="/generate-pdf/{sid}" method="post"><button class="w-full bg-red-600 hover:bg-red-700 text-white py-3 rounded font-bold">📄 تقرير PDF</button></form>
    </div>
    <div class="grid grid-cols-3 gap-2 mb-4">
      <form action="/export-session/{sid}/csv" method="get"><button class="w-full bg-green-600 hover:bg-green-700 text-white py-2 rounded text-sm">📊 CSV</button></form>
      <form action="/export-session/{sid}/json" method="get"><button class="w-full bg-yellow-600 hover:bg-yellow-700 text-white py-2 rounded text-sm">📋 JSON</button></form>
      <form action="/send-email-report/{sid}" method="post"><button class="w-full bg-purple-600 hover:bg-purple-700 text-white py-2 rounded text-sm">✉️ إرسال بالبريد</button></form>
    </div>
    </div></body></html>"""

@app.post("/generate/{sid}")
async def generate_report(sid: int, user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    from report_generator import build_web_report, build_flat_sections
 
    s = db.query(InspectionSession).filter(InspectionSession.id == sid).first()
    if not s:
        raise HTTPException(404)
 
    subs = db.query(Submission).filter(Submission.session_id == sid).all()
    if not subs:
        raise HTTPException(400, "لا توجد إجابات")
 
    # ─── جلب كل الحقول والأقسام من قاعدة البيانات ───────────────────
    from database import FormField, Section, RecommendationCategory
    import json
 
    all_fields   = db.query(FormField).all()
    field_map    = {f.field_key: f for f in all_fields}
    all_sections = db.query(Section).all()
 
    # ─── بناء القاموس المفلطح التكراري ──────────────────────────────
    flat_sections = build_flat_sections(all_sections, subs, field_map)
 
    # ─── تجميع التوصيات ──────────────────────────────────────────────
    recommendations = {"rec_a": [], "rec_b": [], "rec_c": [], "rec_d": []}
 
    for sub in subs:
        try:
            answers = json.loads(sub.answers_json)
        except Exception:
            continue
 
        for k, v in answers.items():
            if not v or k.startswith("rec_enable_"):
                continue
            # مفاتيح التوصيات تبدأ بـ rec_ وتنتهي بـ _rec_a / _rec_b / ...
            if not k.startswith("rec_"):
                continue
            cat_key = None
            for cat in ["rec_a", "rec_b", "rec_c", "rec_d"]:
                if k.endswith(f"_{cat}"):
                    cat_key = cat
                    break
            if cat_key and str(v).strip():
                recommendations[cat_key].append(str(v))
 
    # ─── تجميع المعلومات العامة ──────────────────────────────────────
    general = {
        "institution": s.institution,
        "visit_date": s.visit_date,
    }
    # الحقول التي section_id = None تُعامل كمعلومات عامة
    for sub in subs:
        try:
            answers = json.loads(sub.answers_json)
        except Exception:
            continue
        for k, v in answers.items():
            if not v or k.startswith("rec_"):
                continue
            field = field_map.get(k)
            if field and field.section_id is None:
                general[field.label] = str(v)
 
    # ─── فئات التوصيات ───────────────────────────────────────────────
    rec_cats = [
        {"key": r.key, "label": r.label, "order": r.order}
        for r in db.query(RecommendationCategory)
                   .filter(RecommendationCategory.is_active == True)
                   .order_by(RecommendationCategory.order)
                   .all()
    ]
 
    # ─── بناء data وتمريره للمولّد ───────────────────────────────────
    merged = {
        "general": general,
        "flat_sections": flat_sections,
        "recommendations": recommendations,
        "recommendation_categories": rec_cats,
    }
 
    os.makedirs("reports", exist_ok=True)
    path = build_web_report(merged, "reports")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=os.path.basename(path),
    )

# ================== تصدير واستيراد المستخدمين ==================
@app.get("/admin/users/export")
async def export_users(user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    users = db.query(User).all()
    data = [{"username": u.username, "job_title": u.job_title or "", "email": u.email or "", "phone": u.phone or "", "password": export_password(u.password_hash), "role": u.role, "is_active": u.is_active} for u in users]
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="المستخدمون")
    output.seek(0)
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=users.xlsx"})

@app.post("/admin/users/import", response_class=HTMLResponse)
async def import_users(request: Request, db: Session = Depends(get_db), user=Depends(require_role(Role.ADMIN.value)), file: UploadFile = File(...)):
    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))
        
        # التحقق من الأعمدة المطلوبة
        required_cols = ["username", "role"]
        if not all(col in df.columns for col in required_cols):
            raise HTTPException(400, "يجب أن يحتوي الملف على أعمدة: username, role")
        
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
            
            # التحقق من صحة الدور
            if role not in ["admin", "inspector"]:
                role = "inspector"
            
            existing = db.query(User).filter(User.username == username).first()
            if existing:
                # تحديث المستخدم الموجود
                existing.role = role
                existing.email = email.strip()
                existing.phone = phone.strip()
                existing.job_title = job_title
                if pd.notna(is_active):
                    existing.is_active = bool(is_active)
                # تحديث كلمة المرور إذا كانت موجودة في الملف
                if password and password != "LEGACY_HASH_NOT_DECRYPTABLE":
                    existing.password_hash = hash_password(password)
                count_updated += 1
            else:
                # إنشاء مستخدم جديد بكلمة مرور من الملف أو افتراضية
                new_password = password if password and password != "LEGACY_HASH_NOT_DECRYPTABLE" else "123456"
                db.add(User(username=username, password_hash=hash_password(new_password), role=role, job_title=job_title or None, email=email or None, phone=phone or None))
                count_created += 1
        
        db.commit()
        log_action(user.id, "IMPORT_USERS", f"تم استيراد {count_created} مستخدم جديد وتحديث {count_updated}", request.headers.get("x-forwarded-for", request.client.host))
        
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

# ================== تصدير واستيراد حقول النموذج ==================
@app.get("/admin/form-fields/export")
async def export_form_fields(user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    """تصدير جميع الحقول إلى ملف Excel"""
    try:
        fields = db.query(FormField).filter(FormField.is_active == True).order_by(FormField.section_id, FormField.order).all()
        
        data = []
        for f in fields:
            section = db.query(Section).filter(Section.id == f.section_id).first() if f.section_id else None
            section_name = section.name if section else ""
            
            # استخراج الخيارات كقائمة مفصولة بفواصل
            options = ""
            if f.options_json:
                try:
                    opts_list = json.loads(f.options_json)
                    if isinstance(opts_list, list):
                        options = " | ".join(str(opt) for opt in opts_list)
                except Exception:
                    pass
            
            # استخراج فئات التوصيات
            rec_cats = ""
            if f.has_recommendations and f.recommendation_categories:
                try:
                    rec_list = json.loads(f.recommendation_categories)
                    if isinstance(rec_list, list):
                        rec_cats = " | ".join(str(cat) for cat in rec_list)
                except Exception:
                    pass
            
            # تحويل condition_json إلى نص بشكل آمن
            condition_str = ""
            if f.condition_json:
                if isinstance(f.condition_json, str):
                    condition_str = f.condition_json
                else:
                    try:
                        condition_str = json.dumps(f.condition_json, ensure_ascii=False)
                    except Exception:
                        condition_str = str(f.condition_json)
            
            data.append({
                "section_name": section_name,
                "field_key": f.field_key,
                "label": f.label,
                "field_type": f.field_type,
                "is_required": bool(f.is_required),
                "order": f.order or 0,
                "options": options,
                "has_recommendations": bool(f.has_recommendations),
                "recommendation_categories": rec_cats,
                "subtitle": str(f.subtitle) if f.subtitle else "",
                "condition": condition_str
            })
        
        df = pd.DataFrame(data)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="حقول النموذج")
        
        # تنسيق العمود field_type ليكون قائمة منسدلة
        from openpyxl import load_workbook
        from openpyxl.worksheet.datavalidation import DataValidation
        
        output.seek(0)
        wb = load_workbook(output)
        ws = wb.active
        
        # إنشاء قائمة منسدلة لأنواع الحقول
        field_types = ["text", "textarea", "number", "date", "select", "checkbox", "radio", "email", "phone", "url"]
        dv = DataValidation(type="list", formula1=f'"{",".join(field_types)}"', allow_blank=True)
        dv.error = "يرجى اختيار نوع حقل صحيح من القائمة"
        dv.errorTitle = "نوع الحقل غير صالح"
        
        # تطبيق القائمة المنسدلة على عمود field_type (العمود D) - فقط إذا كانت هناك بيانات
        if len(data) > 0:
            col_letter = "D"
            dv.add(f"{col_letter}2:{col_letter}{len(data)+1}")
            ws.add_data_validation(dv)
        
        # تنسيق العرض
        ws.column_dimensions['A'].width = 20  # section_name
        ws.column_dimensions['B'].width = 25  # field_key
        ws.column_dimensions['C'].width = 30  # label
        ws.column_dimensions['D'].width = 15  # field_type
        ws.column_dimensions['E'].width = 12  # is_required
        ws.column_dimensions['F'].width = 10  # order
        ws.column_dimensions['G'].width = 40  # options
        ws.column_dimensions['H'].width = 15  # has_recommendations
        ws.column_dimensions['I'].width = 30  # recommendation_categories
        
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
                                headers={"Content-Disposition": "attachment; filename=form_fields.xlsx"})
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in export_form_fields: {error_details}")
        raise HTTPException(500, f"خطأ في التصدير: {str(e)}")

@app.post("/admin/form-fields/import")
async def import_form_fields(request: Request, db: Session = Depends(get_db), user=Depends(require_role(Role.ADMIN.value)), file: UploadFile = File(...)):
    """استيراد الحقول من ملف Excel"""
    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))
        
        # التحقق من الأعمدة المطلوبة
        required_cols = ["field_key", "label", "field_type"]
        if not all(col in df.columns for col in required_cols):
            return {"success": False, "error": "يجب أن يحتوي الملف على أعمدة: field_key, label, field_type"}
        
        count_created = 0
        count_updated = 0
        count_errors = 0
        errors = []
        
        valid_field_types = ["text", "textarea", "number", "date", "select", "checkbox", "radio", "email", "phone", "url"]
        
        for idx, row in df.iterrows():
            try:
                field_key = str(row["field_key"]).strip()
                label = str(row["label"]).strip()
                field_type = str(row.get("field_type", "text")).strip().lower()
                
                # التحقق من صحة نوع الحقل
                if field_type not in valid_field_types:
                    field_type = "text"
                
                section_name = str(row.get("section_name", "")).strip() if pd.notna(row.get("section_name")) else ""
                is_required = bool(row.get("is_required", False))
                order = int(row.get("order", 1)) if pd.notna(row.get("order")) else 1
                subtitle = str(row.get("subtitle", "")).strip() if pd.notna(row.get("subtitle")) else ""
                has_recommendations = bool(row.get("has_recommendations", False))
                
                # معالجة الخيارات
                options_json = None
                if pd.notna(row.get("options")) and str(row.get("options")).strip():
                    opts_str = str(row.get("options")).strip()
                    opts_list = [o.strip() for o in opts_str.split("|") if o.strip()]
                    if opts_list:
                        options_json = json.dumps(opts_list)
                
                # معالجة فئات التوصيات
                rec_categories_json = None
                if has_recommendations and pd.notna(row.get("recommendation_categories")) and str(row.get("recommendation_categories")).strip():
                    rec_str = str(row.get("recommendation_categories")).strip()
                    rec_list = [r.strip() for r in rec_str.split("|") if r.strip()]
                    if rec_list:
                        rec_categories_json = json.dumps(rec_list)
                
                # معالجة الشرط
                condition_json = None
                if pd.notna(row.get("condition")) and str(row.get("condition")).strip():
                    condition_json = str(row.get("condition")).strip()
                
                # البحث عن القسم
                section_id = None
                if section_name:
                    section = db.query(Section).filter(Section.name == section_name).first()
                    if section:
                        section_id = section.id
                
                # التحقق مما إذا كان الحقل موجوداً
                existing = db.query(FormField).filter(FormField.field_key == field_key).first()
                if existing:
                    # تحديث الحقل الموجود
                    existing.label = label
                    existing.field_type = field_type
                    existing.order = order
                    existing.options_json = options_json
                    existing.is_required = is_required
                    existing.has_recommendations = has_recommendations
                    existing.recommendation_categories = rec_categories_json
                    existing.subtitle = subtitle
                    existing.condition_json = condition_json
                    if section_id:
                        existing.section_id = section_id
                    count_updated += 1
                else:
                    # إنشاء حقل جديد
                    if not section_id:
                        # إذا لم يتم تحديد قسم، نستخدم أول قسم متاح
                        first_section = db.query(Section).order_by(Section.order).first()
                        if first_section:
                            section_id = first_section.id
                        else:
                            errors.append(f"الحقل {field_key}: لا يوجد قسم محدد")
                            count_errors += 1
                            continue
                    
                    new_field = FormField(
                        section_id=section_id,
                        field_key=field_key,
                        label=label,
                        field_type=field_type,
                        order=order,
                        options_json=options_json,
                        is_required=is_required,
                        has_recommendations=has_recommendations,
                        recommendation_categories=rec_categories_json,
                        subtitle=subtitle,
                        condition_json=condition_json
                    )
                    db.add(new_field)
                    count_created += 1
                    
            except Exception as e:
                errors.append(f"صف {idx + 2}: {str(e)}")
                count_errors += 1
                continue
        
        db.commit()
        log_action(user.id, "IMPORT_FORM_FIELDS", f"تم استيراد {count_created} حقل جديد وتحديث {count_updated}", request.headers.get("x-forwarded-for", request.client.host))
        
        error_msg = f"<br>⚠️ أخطاء: {count_errors}<br>" + "<br>".join(errors[:10]) if errors else ""
        
        return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><script src="https://cdn.tailwindcss.com"></script></head>
        <body class="bg-gray-50 p-4 flex items-center justify-center h-screen">
        <div class="bg-white p-8 rounded shadow text-center max-w-lg">
        <h1 class="text-2xl font-bold text-green-600 mb-4">✅ تم استيراد الحقول بنجاح</h1>
        <p class="mb-4">📥 حقول جديدة: {count_created}<br>🔄 تم التحديث: {count_updated}{error_msg}</p>
        <a href="/admin/form-builder" class="inline-block bg-blue-600 text-white px-6 py-2 rounded">العودة لباني النموذج</a>
        </div></body></html>"""
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "error": f"خطأ في الاستيراد: {str(e)}"}

# ================== تصدير متعدد التنسيقات ==================
@app.get("/export-session/{sid}/csv")
async def export_session_csv(sid: int, user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    """تصدير بيانات الجلسة إلى CSV"""
    s = db.query(InspectionSession).filter(InspectionSession.id == sid).first()
    if not s:
        raise HTTPException(404, "الجلسة غير موجودة")
    
    subs = db.query(Submission).filter(Submission.session_id == sid).all()
    all_fields = db.query(FormField).all()
    field_map = {f.field_key: f for f in all_fields}
    
    data = []
    for sub in subs:
        inspector = db.query(User).filter(User.id == sub.user_id).first()
        inspector_name = inspector.username if inspector else "مجهول"
        answers = json.loads(sub.answers_json)
        
        row = {
            "المفتش": inspector_name,
            "الوحدة": sub.unit_name,
            "تاريخ التقديم": sub.submitted_at.strftime("%Y-%m-%d %H:%M")
        }
        
        for field in all_fields:
            value = answers.get(field.field_key, "")
            if isinstance(value, list):
                value = ", ".join(value)
            row[field.label] = value
        
        data.append(row)
    
    df = pd.DataFrame(data)
    output = io.BytesIO()
    df.to_csv(output, index=False, encoding='utf-8-sig')
    output.seek(0)
    
    return StreamingResponse(output, media_type="text/csv", 
                            headers={"Content-Disposition": f"attachment; filename=session_{sid}.csv"})

@app.get("/export-session/{sid}/json")
async def export_session_json(sid: int, user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    """تصدير بيانات الجلسة إلى JSON"""
    s = db.query(InspectionSession).filter(InspectionSession.id == sid).first()
    if not s:
        raise HTTPException(404, "الجلسة غير موجودة")
    
    subs = db.query(Submission).filter(Submission.session_id == sid).all()
    all_fields = db.query(FormField).all()
    
    data = {
        "session": {
            "id": s.id,
            "institution": s.institution,
            "visit_date": s.visit_date,
            "session_code": s.session_code,
            "created_at": s.created_at.isoformat() if s.created_at else None
        },
        "submissions": []
    }
    
    for sub in subs:
        inspector = db.query(User).filter(User.id == sub.user_id).first()
        submission_data = {
            "inspector": inspector.username if inspector else "مجهول",
            "unit_name": sub.unit_name,
            "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
            "answers": json.loads(sub.answers_json)
        }
        data["submissions"].append(submission_data)
    
    return StreamingResponse(
        io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=session_{sid}.json"}
    )

@app.post("/generate-pdf/{sid}")
async def generate_pdf_report(sid: int, user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    """توليد تقرير PDF للجلسة"""
    from report_generator import build_flat_sections
    
    s = db.query(InspectionSession).filter(InspectionSession.id == sid).first()
    if not s:
        raise HTTPException(404, "الجلسة غير موجودة")
    
    subs = db.query(Submission).filter(Submission.session_id == sid).all()
    if not subs:
        raise HTTPException(400, "لا توجد إجابات")
    
    all_fields = db.query(FormField).all()
    field_map = {f.field_key: f for f in all_fields}
    all_sections = db.query(Section).all()
    
    flat_sections = build_flat_sections(all_sections, subs, field_map)
    
    # تجميع الإجابات
    answers = {}
    for sub in subs:
        sub_answers = json.loads(sub.answers_json)
        for key, value in sub_answers.items():
            if key not in answers and not key.startswith("rec_enable_"):
                answers[key] = value
    
    # تحويل الحقول للإجابة
    fields_data = []
    for field in all_fields:
        value = answers.get(field.field_key, "")
        if isinstance(value, list):
            value = ", ".join(value)
        fields_data.append({
            'label': field.label,
            'key': field.field_key,
            'value': str(value) if value else ''
        })
    
    try:
        pdf_gen = PDFGenerator()
        pdf_content = pdf_gen.generate_form(
            form_title=f"تقرير: {s.institution}",
            fields=fields_data,
            answers={f['key']: f['value'] for f in fields_data}
        )
        
        return StreamingResponse(
            io.BytesIO(pdf_content),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=report_{sid}.pdf"}
        )
    except ImportError as e:
        raise HTTPException(500, f"مكتبة reportlab غير متوفرة: {str(e)}")
    except Exception as e:
        raise HTTPException(500, f"خطأ في توليد PDF: {str(e)}")

@app.post("/send-email-report/{sid}")
async def send_email_report(sid: int, request: Request, user=Depends(require_role(Role.ADMIN.value)), db: Session = Depends(get_db)):
    """إرسال التقرير بالبريد الإلكتروني"""
    s = db.query(InspectionSession).filter(InspectionSession.id == sid).first()
    if not s:
        raise HTTPException(404, "الجلسة غير موجودة")
    
    email_notifier = setup_email_from_settings(db)
    if not email_notifier:
        return message_page("إعدادات البريد", "لم يتم إعداد خادم البريد الإلكتروني. يرجى الاتصال بالمسؤول.", 200, f"/admin/session/{sid}", "warning")
    
    # الحصول على عنوان البريد من الطلب أو استخدام افتراضي
    form_data = await request.form()
    recipient_email = form_data.get("email", "")
    
    if not recipient_email:
        # جلب قائمة المستخدمين admins لإرسال التقرير لهم
        admins = db.query(User).filter(User.role == "admin", User.is_active == True).all()
        if not admins or not any(u.email for u in admins):
            return message_page("بدون مستلمين", "لا يوجد عناوين بريد إلكتروني للمسؤولين.", 200, f"/admin/session/{sid}", "warning")
        recipient_email = admins[0].email
    
    # تجهيز محتوى البريد
    subject = f"تقرير تفتيش: {s.institution} - {s.visit_date}"
    
    subs_count = db.query(Submission).filter(Submission.session_id == sid).count()
    
    body_html = f"""
    <html dir="rtl" style="font-family: Arial, sans-serif;">
    <body style="background-color: #f5f5f5; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background-color: white; padding: 30px; border-radius: 10px;">
            <h2 style="color: #2c5282; text-align: center;">تقرير تفتيش جديد</h2>
            <hr style="border: none; border-top: 2px solid #e2e8f0; margin: 20px 0;">
            <p><strong>المؤسسة:</strong> {s.institution}</p>
            <p><strong>تاريخ الزيارة:</strong> {s.visit_date}</p>
            <p><strong>عدد الردود:</strong> {subs_count}</p>
            <p><strong>تم التوليد بواسطة:</strong> {user.username}</p>
            <p><strong>التاريخ:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
            <div style="text-align: center; margin-top: 30px;">
                <a href="{request.url.scheme}://{request.headers.get('host', '')}/admin/session/{sid}" 
                   style="background-color: #3182ce; color: white; padding: 12px 30px; text-decoration: none; border-radius: 5px; display: inline-block;">عرض التقرير الكامل</a>
            </div>
            <hr style="border: none; border-top: 2px solid #e2e8f0; margin: 30px 0 20px;">
            <p style="text-align: center; color: #718096; font-size: 14px;">هذا بريد تلقائي من نظام التفتيش الذكي</p>
        </div>
    </body>
    </html>
    """
    
    result = email_notifier.send_email(
        to_emails=[recipient_email],
        subject=subject,
        body_html=body_html,
        from_name="نظام التفتيش الذكي"
    )
    
    if result['success']:
        log_action(user.id, "SEND_EMAIL_REPORT", f"تم إرسال تقرير الجلسة {sid} إلى {recipient_email}", request.headers.get("x-forwarded-for", request.client.host))
        return message_page("تم الإرسال", f"تم إرسال التقرير بنجاح إلى {recipient_email}", 200, f"/admin/session/{sid}", "success")
    else:
        return message_page("فشل الإرسال", f"حدث خطأ: {result['message']}", 200, f"/admin/session/{sid}", "error")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
