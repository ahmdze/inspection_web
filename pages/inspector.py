from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime
from password_utils import encrypt_password

router = APIRouter()

def format_date(date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%d/%m/%Y")
    except:
        return date_str

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


@router.get("/inspect/success", response_class=HTMLResponse)
async def success():
    return """<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head><body class="bg-gray-50 p-4 flex items-center justify-center h-screen"><div class="bg-white p-8 rounded shadow text-center"><h1 class="text-2xl font-bold text-green-600 mb-2">✅ تم الحفظ بنجاح</h1><p class="text-gray-600 mb-4">سيتم دمج البيانات في التقرير النهائي.</p><a href="/dashboard" class="inline-block bg-blue-600 text-white px-6 py-2 rounded">العودة للوحة</a></div></body></html>"""


@router.get("/inspect/dashboard", response_class=HTMLResponse)
async def inspector_dashboard(request: Request, db: Session = Depends(get_db)):
    from database import InspectionSession

    user = get_current_user(request, db)
    if user.role not in ["inspector"]:
        raise HTTPException(403, "صلاحية غير كافية")

    sessions = db.query(InspectionSession).filter(InspectionSession.status == "open").order_by(InspectionSession.created_at.desc()).all()

    rows = "".join(f'''<li class="border p-4 rounded bg-white mb-3 hover:shadow-lg transition">
      <div class="flex justify-between items-start">
        <div>
          <h3 class="font-bold text-lg text-blue-700">{s.institution}</h3>
          <p class="text-sm text-gray-600 mt-1">📅 تاريخ الزيارة: {format_date(s.visit_date)}</p>
          <p class="text-sm text-gray-500 mt-1">رمز الجولة: <code class="bg-gray-100 px-2 py-1 rounded">{s.session_code}</code></p>
        </div>
        <a href="/inspect/{s.session_code}" class="bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded font-bold">بدء الفحص →</a>
      </div></li>''' for s in sessions)

    if not rows:
        rows = '<li class="border p-4 rounded bg-white text-center text-gray-500">لا توجد جولات نشطة حالياً</li>'

    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-gray-50 p-4">
    <div class="max-w-3xl mx-auto">
      <div class="bg-white p-6 rounded shadow mb-6">
        <div class="flex justify-between items-center">
          <div>
            <h1 class="text-2xl font-bold text-blue-800">👨‍💼 لوحة المفتش</h1>
            <p class="text-gray-600 mt-1">مرحباً، {user.username}</p>
          </div>
          <div class="flex gap-2">
            <a href="/inspect/profile" class="bg-purple-600 hover:bg-purple-700 text-white px-4 py-2 rounded">👤 الملف الشخصي</a>
            <form action="/logout" method="post"><button class="bg-red-500 hover:bg-red-600 text-white px-4 py-2 rounded">خروج</button></form>
          </div>
        </div>
      </div>
      <div class="bg-white p-6 rounded shadow">
        <h2 class="text-xl font-bold mb-4">📋 الجولات النشطة</h2>
        <ul class="space-y-2">{rows}</ul>
      </div>
    </div></body></html>"""


@router.get("/inspect/profile", response_class=HTMLResponse)
async def inspector_profile_route(request: Request, db: Session = Depends(get_db)):
    return await inspector_profile(request, db)


@router.get("/inspect/{code}", response_class=HTMLResponse)
async def inspect_form(code: str, request: Request, db: Session = Depends(get_db)):
    from database import InspectionSession, RecommendationCategory, Section, FormField, FormTemplate
    import json

    user = get_current_user(request, db)

    sess = db.query(InspectionSession).filter(
        InspectionSession.session_code == code,
        InspectionSession.status == "open"
    ).first()
    if not sess:
        raise HTTPException(404, "الجولة مغلقة أو غير موجودة")

    # فئات التوصيات
    rec_cats = db.query(RecommendationCategory).filter(
        RecommendationCategory.is_active == True
    ).order_by(RecommendationCategory.order).all()
    rec_cats_list = [{"key": r.key, "label": r.label} for r in rec_cats]

    # ─── دالة بناء حقل واحد ────────────────────────────────────────
    def render_field(field_key, label, field_type, is_required, options_json, has_recommendations):
        if field_type == "textarea":
            inp = (
                f'<textarea name="{field_key}" placeholder="{label}" '
                f'{"required" if is_required else ""} '
                f'class="w-full p-3 border rounded h-24 focus:ring-2 focus:ring-blue-500"></textarea>'
            )
        elif field_type == "select" and options_json:
            try:
                opts = json.loads(options_json)
            except Exception:
                opts = []
            options_html = "".join(f'<option value="{o}">{o}</option>' for o in opts)
            inp = (
                f'<select name="{field_key}" {"required" if is_required else ""} '
                f'class="w-full p-3 border rounded focus:ring-2 focus:ring-blue-500">'
                f'<option value="">اختر...</option>{options_html}</select>'
            )
        else:
            inp = (
                f'<input name="{field_key}" type="{field_type}" placeholder="{label}" '
                f'{"required" if is_required else ""} '
                f'class="w-full p-3 border rounded focus:ring-2 focus:ring-blue-500">'
            )

        html = (
            f'<div class="mb-6 p-4 bg-white rounded border">'
            f'<label class="block font-bold text-lg mb-2">{label}</label>{inp}'
        )

        if has_recommendations and rec_cats_list:
            html += (
                '<div class="mt-4 pt-4 border-t bg-green-50 p-3 rounded">'
                '<p class="font-bold text-green-800 mb-2">💡 التوصيات لهذا البند</p>'
            )
            for cat in rec_cats_list:
                cat_key = cat["key"]
                cat_label = cat["label"]
                textarea_id = f'rec_{field_key}_{cat_key}'
                checkbox_name = f'rec_enable_{field_key}_{cat_key}'
                textarea_name = f'rec_{field_key}_{cat_key}'
                html += (
                    f'<div class="mb-3">'
                    f'<label class="flex items-start gap-2 cursor-pointer">'
                    f'<input type="checkbox" name="{checkbox_name}" class="mt-1 rounded" '
                    f'onchange="document.getElementById(\'{textarea_id}\').classList.toggle(\'hidden\',!this.checked)"> '
                    f'<span class="font-medium text-sm">{cat_label}</span></label>'
                    f'<textarea id="{textarea_id}" name="{textarea_name}" '
                    f'placeholder="اكتب التفاصيل هنا..." '
                    f'class="w-full p-2 border rounded h-16 mt-2 hidden"></textarea>'
                    f'</div>'
                )
            html += '</div>'

        html += '</div>'
        return html

    # ─── بناء شجرة الأقسام من قاعدة البيانات ──────────────────────
    def build_section_html(section):
        html = ""
        fields = db.query(FormField).filter(
            FormField.section_id == section.id,
            FormField.is_active == True
        ).order_by(FormField.order).all()

        for f in fields:
            html += render_field(
                f.field_key, f.label, f.field_type,
                f.is_required, f.options_json, f.has_recommendations
            )

        child_sections = db.query(Section).filter(
            Section.parent_id == section.id
        ).order_by(Section.order).all()
        for child in child_sections:
            html += f'<h3 class="text-lg font-bold mt-6 pt-4 border-t text-blue-700">📌 {child.name}</h3>'
            html += build_section_html(child)

        return html

    # ─── إذا كانت الجولة مرتبطة بنموذج محفوظ ──────────────────────
    template = None
    if sess.template_id:
        template = db.query(FormTemplate).filter(
            FormTemplate.id == sess.template_id,
            FormTemplate.is_active == True
        ).first()

    if template:
        # بناء الاستمارة من النموذج المحفوظ
        try:
            template_sections = json.loads(template.sections_json)
        except Exception:
            template_sections = {}

        # تجميع الأبناء حسب الأب
        children_by_parent = {}
        for sec_id, sec in template_sections.items():
            parent = sec.get("parent_id")
            children_by_parent.setdefault(parent, []).append((sec_id, sec))
        for lst in children_by_parent.values():
            lst.sort(key=lambda item: item[1].get("order", 0))

        def build_template_section_html(sec_id, sec):
            html = ""
            for f in sorted(sec.get("fields", []), key=lambda x: x.get("order", 0)):
                html += render_field(
                    f.get("field_key", ""),
                    f.get("label", ""),
                    f.get("field_type", "text"),
                    f.get("is_required", False),
                    f.get("options_json"),
                    f.get("has_recommendations", False),
                )
            for child_id, child in children_by_parent.get(int(sec_id), []):
                html += f'<h3 class="text-lg font-bold mt-6 pt-4 border-t text-blue-700">📌 {child.get("name", "")}</h3>'
                html += build_template_section_html(child_id, child)
            return html

        template_roots = sorted(
            children_by_parent.get(None, []),
            key=lambda item: item[1].get("order", 0)
        )
        sections_html = ""
        for sec_id, section in template_roots:
            sections_html += (
                f'<div id="sec-{sec_id}" class="section-content hidden">'
                f'<h2 class="text-2xl font-bold mb-4 pb-2 border-b text-blue-800">{section.get("name", "")}</h2>'
            )
            sections_html += build_template_section_html(sec_id, section)
            sections_html += '</div>'

        section_opts = "".join(
            f'<option value="sec-{sec_id}">{section.get("name", "")}</option>'
            for sec_id, section in template_roots
        )

    else:
        # بناء الاستمارة من الأقسام الحالية في قاعدة البيانات
        root_sections = db.query(Section).filter(
            Section.parent_id == None
        ).order_by(Section.order).all()

        sections_html = ""
        for section in root_sections:
            sections_html += (
                f'<div id="sec-{section.id}" class="section-content hidden">'
                f'<h2 class="text-2xl font-bold mb-4 pb-2 border-b text-blue-800">{section.name}</h2>'
            )
            sections_html += build_section_html(section)
            sections_html += '</div>'

        section_opts = "".join(
            f'<option value="sec-{s.id}">{s.name}</option>'
            for s in root_sections
        )

    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-gray-50 p-4">
    <div id="sync-status" class="fixed top-0 left-0 right-0 bg-green-600 text-white text-center py-2 font-bold hidden"></div>
    <div class="max-w-2xl mx-auto bg-white p-6 rounded shadow mt-8">
      <div class="bg-blue-600 text-white p-4 rounded-t -mx-6 -mt-6 mb-6">
        <h1 class="text-xl font-bold">{sess.institution} | {sess.visit_date}</h1>
        <p class="text-sm opacity-90 mt-1">المفتش: {user.username} | مفتش</p>
      </div>
      <div class="mb-6">
        <label class="block font-bold mb-2">اختر المحور الذي قمت بالتفتيش عنه:</label>
        <select id="axis-selector" class="w-full p-3 border rounded text-lg focus:ring-2 focus:ring-blue-500"
                onchange="showSection(this.value)">{section_opts}</select>
      </div>
      <form id="inspection-form" action="/inspect/submit" method="post" class="space-y-4" novalidate>
        <input type="hidden" name="session_id" value="{sess.id}">
        <input type="hidden" name="code" value="{code}">
        {sections_html}
        <button type="submit" class="w-full bg-green-600 text-white py-3 rounded font-bold text-lg hover:bg-green-700">✅ حفظ الإجابات</button>
      </form>
    </div>
    <script>
      function showSection(id) {{
        document.querySelectorAll('.section-content').forEach(s => s.classList.add('hidden'));
        document.getElementById(id).classList.remove('hidden');
      }}
      if (document.getElementById('axis-selector'))
        showSection(document.getElementById('axis-selector').value);
      
      // التعامل مع إرسال النموذج
      document.getElementById('inspection-form').addEventListener('submit', async function(e) {{
        e.preventDefault();
        const form = this;
        const btn = form.querySelector('button[type=submit]');
        const originalText = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'جاري الحفظ...';
        
        const code = form.querySelector('input[name="code"]').value;
        const sessionId = form.querySelector('input[name="session_id"]').value;
        
        try {{
          if (navigator.onLine) {{
            // متصل بالإنترنت - أرسل مباشرة
            const formData = new FormData(form);
            const response = await fetch('/inspect/submit', {{
              method: 'POST',
              body: formData,
              redirect: 'manual'
            }});
            if (response.ok || response.status === 302) {{
              window.location.href = '/inspect/success';
            }} else {{
              throw new Error('فشل الإرسال');
            }}
          }} else {{
            // غير متصل - احفظ محلياً
            if (window.handleOfflineSubmit) {{
              await window.handleOfflineSubmit(form, code);
            }} else {{
              // fallback إذا لم يتم تحميل offline.js
              const data = {{
                code: code,
                session_id: sessionId,
                ...Object.fromEntries(new FormData(form))
              }};
              localStorage.setItem('pending_submission', JSON.stringify(data));
              document.getElementById('sync-status').textContent = '💾 تم الحفظ محلياً - سيرفع عند الاتصال';
              document.getElementById('sync-status').classList.remove('hidden');
              setTimeout(() => {{
                window.location.href = '/inspect/success';
              }}, 1000);
            }}
          }}
        }} catch (error) {{
          console.error('Error:', error);
          // في حالة الخطأ، احفظ محلياً
          const data = {{
            code: code,
            session_id: sessionId,
            ...Object.fromEntries(new FormData(form))
          }};
          localStorage.setItem('pending_submission', JSON.stringify(data));
          document.getElementById('sync-status').textContent = '💾 تم الحفظ محلياً - سيرفع عند الاتصال';
          document.getElementById('sync-status').classList.remove('hidden');
          setTimeout(() => {{
            window.location.href = '/inspect/success';
          }}, 1000);
        }} finally {{
          btn.disabled = false;
          btn.textContent = originalText;
        }}
      }});
    </script>
    <script src="/static/register-sw.js"></script>
    <script src="/static/offline.js"></script>
    </body></html>"""


@router.post("/inspect/submit")
async def submit_dynamic(request: Request, db: Session = Depends(get_db), bg=None):
    from database import InspectionSession, Submission, AuditLog
    from datetime import datetime
    import json

    user = get_current_user(request, db)
    form = await request.form()
    session_id = int(form.get("session_id", 0))
    sess = db.query(InspectionSession).filter(
        InspectionSession.id == session_id,
        InspectionSession.status == "open"
    ).first()
    if not sess:
        raise HTTPException(400, "الجولة مغلقة")

    answers = {}
    for k, v in form.items():
        if k in ["unit_name", "session_id"]:
            continue
        if not k.startswith("rec_enable_") and v.strip():
            answers[k] = v.strip()

    db.add(Submission(
        session_id=sess.id,
        user_id=user.id,
        unit_name=form.get("unit_name", "عام"),
        answers_json=json.dumps(answers, ensure_ascii=False)
    ))
    db.commit()

    ip = request.headers.get("x-forwarded-for", request.client.host)
    db.add(AuditLog(
        user_id=user.id,
        action="SUBMIT_REPORT",
        details=f"{sess.institution}",
        ip_address=ip,
        timestamp=datetime.now()
    ))
    db.commit()

    return RedirectResponse(url="/inspect/success", status_code=302)


@router.get("/inspect/profile", response_class=HTMLResponse)
async def inspector_profile(request: Request, db: Session = Depends(get_db)):
    from database import User

    user = get_current_user(request, db)
    if user.role not in ["inspector"]:
        raise HTTPException(403, "صلاحية غير كافية")

    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-gray-50 p-4">
    <div class="max-w-md mx-auto bg-white p-6 rounded shadow">
      <h1 class="text-xl font-bold mb-4">👤 الملف الشخصي</h1>
      <p class="text-gray-600 mb-4">مرحباً، {user.username}</p>
      <form action="/inspect/profile" method="post" class="space-y-3">
        <div><label class="block font-bold mb-1">اسم المستخدم</label>
        <input name="username" value="{user.username}" required class="w-full p-2 border rounded"></div>
        <div><label class="block font-bold mb-1">كلمة المرور الجديدة (اتركه فارغاً إذا لم ترد تغييرها)</label>
        <input name="password" type="password" class="w-full p-2 border rounded"></div>
        <button class="w-full bg-blue-600 text-white py-2 rounded">💾 حفظ التغييرات</button>
      </form>
      <a href="/inspect/dashboard" class="block text-center mt-4 text-gray-500">← العودة للوحة</a>
    </div></body></html>"""


@router.post("/inspect/profile", response_class=HTMLResponse)
async def update_inspector_profile(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form("")
):
    from database import User, AuditLog
    from datetime import datetime

    def hash_password(pw): return encrypt_password(pw)

    current_user = get_current_user(request, db)
    if current_user.role not in ["inspector"]:
        raise HTTPException(403, "صلاحية غير كافية")

    existing = db.query(User).filter(
        User.username == username,
        User.id != current_user.id
    ).first()
    if existing:
        raise HTTPException(400, "اسم المستخدم مستخدم بالفعل")

    current_user.username = username
    if password.strip():
        current_user.password_hash = hash_password(password)

    db.commit()

    ip = request.headers.get("x-forwarded-for", request.client.host)
    db.add(AuditLog(
        user_id=current_user.id,
        action="UPDATE_PROFILE",
        details=f"تحديث الملف الشخصي: {username}",
        ip_address=ip,
        timestamp=datetime.now()
    ))
    db.commit()

    return """<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8"><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-gray-50 p-4 flex items-center justify-center h-screen">
    <div class="bg-white p-8 rounded shadow text-center">
      <h1 class="text-2xl font-bold text-green-600 mb-4">✅ تم التحديث بنجاح</h1>
      <a href="/inspect/profile" class="inline-block bg-blue-600 text-white px-6 py-2 rounded">العودة للملف الشخصي</a>
    </div></body></html>"""