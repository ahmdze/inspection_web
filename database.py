from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, ForeignKey, func, text
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime
import secrets, json, os

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="inspector")
    job_title = Column(String, nullable=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)

class InspectionSession(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True)
    institution = Column(String, nullable=False)
    visit_date = Column(String, nullable=False)
    session_code = Column(String, unique=True, nullable=False, default=lambda: secrets.token_urlsafe(6))
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="open")
    template_id = Column(Integer, ForeignKey("form_templates.id"), nullable=True) # <-- الحقل الجديد
    submissions = relationship("Submission", back_populates="session")
    
class Submission(Base):
    __tablename__ = "submissions"
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("sessions.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    unit_name = Column(String, nullable=False)
    answers_json = Column(Text, nullable=False, default="{}")
    submitted_at = Column(DateTime, default=datetime.utcnow)
    session = relationship("InspectionSession", back_populates="submissions")

class RecommendationCategory(Base):
    __tablename__ = "recommendation_categories"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False)  # rec_a, rec_b, ...
    label = Column(String, nullable=False)
    order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

class Section(Base):
    __tablename__ = "sections"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    parent_id = Column(Integer, ForeignKey("sections.id"), nullable=True)
    order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

class FormField(Base):
    __tablename__ = "form_fields"
    id = Column(Integer, primary_key=True)
    section_id = Column(Integer, ForeignKey("sections.id"), nullable=True)  # القسم الذي ينتمي إليه الحقل
    field_key = Column(String, unique=True, nullable=False)
    label = Column(String, nullable=False)
    field_type = Column(String, default="text")  # text, number, textarea, select, checkbox
    is_required = Column(Boolean, default=False)
    options_json = Column(Text, nullable=True)  # لخيارات select/checkbox
    condition_json = Column(Text, nullable=True)  # شروط الإظهار، مثلاً: {"field_key": "value"} يعني يظهر إذا كان field_key يساوي value
    subtitle = Column(String, nullable=True)  # نص فرعي يظهر تحت الحقل
    has_recommendations = Column(Boolean, default=False)  # هل لهذا الحقل توصيات؟
    order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

class FormTemplate(Base):
    __tablename__ = "form_templates"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)  # اسم النموذج
    description = Column(Text, nullable=True)  # وصف النموذج
    sections_json = Column(Text, nullable=False, default="{}")  # هيكل النموذج (الأقسام والحقول)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)

class SystemSetting(Base):
    __tablename__ = "system_settings"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(Text, nullable=True)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String, nullable=False)
    details = Column(Text, nullable=True)
    ip_address = Column(String, nullable=True)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inspection.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

def init_db():
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(users)"))]
        if "job_title" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN job_title VARCHAR"))
        if "email" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR"))
        if "phone" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN phone VARCHAR"))
    try:
        with SessionLocal() as db:
            from password_utils import encrypt_password

            if db.query(User).count() == 0:
                db.add(User(username="admin", password_hash=encrypt_password("123"), role="admin", job_title="مدير النظام", is_active=True))
                db.commit()
            # إضافة الإعدادات الافتراضية
            if db.query(SystemSetting).count() == 0:
                defaults = {"tg_bot_token": "", "tg_chat_id": "", "wa_api_url": "https://api.callmebot.com/whatsapp.php", "wa_api_key": "", "wa_phone": ""}
                for k, v in defaults.items(): db.add(SystemSetting(key=k, value=v))
                db.commit()
            
            # إضافة فئات التوصيات الافتراضية
            if db.query(RecommendationCategory).count() == 0:
                rec_cats = [
                    RecommendationCategory(key="rec_a", label="أ/ الإيعاز إلى دائرة صحة بغداد الرصافة/ قسم التخطيط:", order=1),
                    RecommendationCategory(key="rec_b", label="ب/ الإيعاز إلى شعبة التحقيقات/ قسمنا، بتشكيل لجنة تحقيقية بخصوص:", order=2),
                    RecommendationCategory(key="rec_c", label="ج/ الإيعاز إلى إدارة المستشفى بخصوص:", order=3),
                    RecommendationCategory(key="rec_d", label="د/ أخرى:", order=4),
                ]
                db.add_all(rec_cats)
                db.commit()
            
            # إضافة الأقسام الرئيسية الافتراضية
            if db.query(Section).count() == 0:
                sections = [
                    Section(name="المعلومات العامة", parent_id=None, order=0),
                    Section(name="المحور الفني", parent_id=None, order=1),
                    Section(name="المحور الإداري", parent_id=None, order=2),
                    Section(name="المحور الهندسي", parent_id=None, order=3),
                ]
                db.add_all(sections)
                db.commit()
    except Exception as e: print(f"DB init warning: {e}")
init_db()
