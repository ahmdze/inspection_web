"""
سكريبت ترحيل لإصلاح قاعدة البيانات على Render
يضيف عمود recommendation_categories إذا كان غير موجود
"""
import os
from sqlalchemy import create_engine, text
from database import DB_PATH

print(f"Using database: {DB_PATH}")

# إنشاء المحرك
if DB_PATH.startswith("sqlite:///"):
    engine = create_engine(DB_PATH, connect_args={"check_same_thread": False})
elif DB_PATH.startswith("postgresql://") or DB_PATH.startswith("postgres://"):
    engine = create_engine(DB_PATH)
else:
    if not DB_PATH.startswith("/"):
        import os.path
        DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), DB_PATH)
    engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})

def migrate():
    with engine.connect() as conn:
        # التحقق من وجود العمود في جدول form_fields
        if DB_PATH.startswith("sqlite"):
            cols = [row[1] for row in conn.execute(text("PRAGMA table_info(form_fields)"))]
            if "recommendation_categories" not in cols:
                print("Adding recommendation_categories column to form_fields...")
                conn.execute(text("ALTER TABLE form_fields ADD COLUMN recommendation_categories TEXT"))
                conn.commit()
                print("✓ Column added successfully!")
            else:
                print("✓ Column recommendation_categories already exists in form_fields")
        elif DB_PATH.startswith("postgresql://") or DB_PATH.startswith("postgres://"):
            # لـ PostgreSQL، نتحقق من وجود العمود
            result = conn.execute(text("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = 'form_fields' AND column_name = 'recommendation_categories'
            """)).fetchall()
            if not result:
                print("Adding recommendation_categories column to form_fields...")
                conn.execute(text("ALTER TABLE form_fields ADD COLUMN recommendation_categories TEXT"))
                conn.commit()
                print("✓ Column added successfully!")
            else:
                print("✓ Column recommendation_categories already exists in form_fields")
        
        print("\nMigration completed!")

if __name__ == "__main__":
    migrate()
