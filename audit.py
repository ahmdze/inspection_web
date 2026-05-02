from database import SessionLocal, AuditLog
from datetime import datetime

def log_action(user_id: int, action: str, details: str, ip: str):
    with SessionLocal() as db:
        db.add(AuditLog(user_id=user_id, action=action, details=details, ip_address=ip, timestamp=datetime.utcnow()))
        db.commit()