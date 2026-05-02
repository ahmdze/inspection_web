# ملف تجميع للمسارات
from pages.auth import router as auth_router
from pages.admin_panel import router as admin_panel_router
from pages.admin_users import router as admin_users_router
from pages.admin_sessions import router as admin_sessions_router
from pages.inspector import router as inspector_router

__all__ = [
    "auth_router",
    "admin_panel_router", 
    "admin_users_router",
    "admin_sessions_router",
    "inspector_router"
]
