#!/usr/bin/env python
"""إنشاء المستخدم الافتراضي للتطبيق"""
from database import SessionLocal, User, engine, Base
from password_utils import encrypt_password

# إنشاء الجداول
Base.metadata.create_all(engine)

db = SessionLocal()

# التحقق من عدم وجود مستخدمين
if db.query(User).count() == 0:
    # استخدام SHA256 بدلاً من bcrypt لتجنب المشاكل
    password = "Admin@123"
    password_hash = encrypt_password(password)
    
    admin_user = User(
        username="admin",
        password_hash=password_hash,
        role="admin",
        is_active=True
    )
    db.add(admin_user)
    db.commit()
    print("✅ تم إنشاء المستخدم: admin")
    print("كلمة المرور الافتراضية: Admin@123")
else:
    print("المستخدمون موجودون بالفعل")
    users = db.query(User).all()
    for u in users:
        print(f"  - {u.username} ({u.role})")

db.close()
