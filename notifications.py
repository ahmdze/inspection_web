import requests, threading, logging, json
from database import SessionLocal, SystemSetting
from urllib.parse import quote

def get_setting(key: str, default: str = "") -> str:
    with SessionLocal() as db:
        s = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        return s.value if s else default

def _send_telegram(msg: str):
    token, chat = get_setting("tg_bot_token"), get_setting("tg_chat_id")
    if not (token and chat): return
    try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": msg, "parse_mode": "HTML"}, timeout=5)
    except Exception as e: logging.error(f"Telegram: {e}")

def _send_whatsapp(msg: str):
    url, key, phone = get_setting("wa_api_url"), get_setting("wa_api_key"), get_setting("wa_phone")
    if not (url and key and phone): return
    try: requests.get(f"{url}?phone={phone}&apikey={key}&text={quote(msg)}", timeout=5)
    except Exception as e: logging.error(f"WhatsApp: {e}")

def notify_async(subject: str, body: str):
    threading.Thread(target=_send_telegram, args=(f"🔔 <b>{subject}</b>\n{body}",), daemon=True).start()
    threading.Thread(target=_send_whatsapp, args=(body,), daemon=True).start()