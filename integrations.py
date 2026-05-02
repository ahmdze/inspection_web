"""
وحدات التكامل والأتمتة للنظام
- إرسال إشعارات البريد الإلكتروني
- التكامل مع Slack وGoogle Sheets وTelegram
- توليد مستندات PDF
"""

import smtplib
import json
import io
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from typing import List, Dict, Optional, Any

# محاولة استيراد المكتبات الاختيارية
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


class EmailNotifier:
    """إرسال إشعارات عبر البريد الإلكتروني"""
    
    def __init__(self, smtp_server: str, smtp_port: int, username: str, password: str, use_tls: bool = True):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.use_tls = use_tls
    
    def send_email(
        self,
        to_emails: List[str],
        subject: str,
        body_html: str,
        body_text: Optional[str] = None,
        attachments: Optional[List[Dict]] = None,
        from_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        إرسال بريد إلكتروني
        
        Args:
            to_emails: قائمة عناوين البريد للمستلمين
            subject: موضوع البريد
            body_html: محتوى HTML للبريد
            body_text: محتوى نصي بديل (اختياري)
            attachments: قائمة مرفقات [{'filename': 'name.pdf', 'content': bytes}]
            from_name: اسم المرسل
        
        Returns:
            dict: {'success': bool, 'message': str}
        """
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = f"{from_name or self.username} <{self.username}>"
            msg['To'] = ', '.join(to_emails)
            
            # إضافة المحتوى النصي والـ HTML
            if body_text:
                msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
            msg.attach(MIMEText(body_html, 'html', 'utf-8'))
            
            # إضافة المرفقات
            if attachments:
                for attachment in attachments:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(attachment['content'])
                    encoders.encode_base64(part)
                    part.add_header(
                        'Content-Disposition',
                        f"attachment; filename*=UTF-8''{attachment['filename']}"
                    )
                    msg.attach(part)
            
            # الاتصال وإرسال البريد
            if self.use_tls:
                server = smtplib.SMTP(self.smtp_server, self.smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(self.smtp_server, self.smtp_port)
            
            server.login(self.username, self.password)
            server.sendmail(self.username, to_emails, msg.as_string())
            server.quit()
            
            return {'success': True, 'message': f'تم الإرسال إلى {len(to_emails)} مستلم'}
        except Exception as e:
            return {'success': False, 'message': str(e)}


class SlackIntegration:
    """التكامل مع Slack لإرسال الإشعارات"""
    
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
    
    def send_message(
        self,
        text: str,
        channel: Optional[str] = None,
        username: Optional[str] = None,
        icon_emoji: Optional[str] = None,
        attachments: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """
        إرسال رسالة إلى Slack
        
        Args:
            text: نص الرسالة
            channel: القناة (اختياري)
            username: اسم المستخدم الظاهر
            icon_emoji: الأيقونة
            attachments: مرفقات Slack
        
        Returns:
            dict: {'success': bool, 'message': str}
        """
        if not REQUESTS_AVAILABLE:
            return {'success': False, 'message': 'مكتبة requests غير متوفرة'}
        
        try:
            payload = {'text': text}
            if channel:
                payload['channel'] = channel
            if username:
                payload['username'] = username
            if icon_emoji:
                payload['icon_emoji'] = icon_emoji
            if attachments:
                payload['attachments'] = attachments
            
            response = requests.post(self.webhook_url, json=payload, timeout=10)
            response.raise_for_status()
            
            return {'success': True, 'message': 'تم الإرسال إلى Slack'}
        except Exception as e:
            return {'success': False, 'message': str(e)}


class TelegramIntegration:
    """التكامل مع Telegram لإرسال الإشعارات"""
    
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
    
    def send_message(
        self,
        text: str,
        parse_mode: str = 'HTML',
        disable_notification: bool = False
    ) -> Dict[str, Any]:
        """
        إرسال رسالة إلى Telegram
        
        Args:
            text: نص الرسالة (يدعم HTML)
            parse_mode: وضع التحليل ('HTML' أو 'Markdown')
            disable_notification: إرسال بدون إشعار
        
        Returns:
            dict: {'success': bool, 'message': str}
        """
        if not REQUESTS_AVAILABLE:
            return {'success': False, 'message': 'مكتبة requests غير متوفرة'}
        
        try:
            url = f"{self.base_url}/sendMessage"
            payload = {
                'chat_id': self.chat_id,
                'text': text,
                'parse_mode': parse_mode,
                'disable_notification': disable_notification
            }
            
            response = requests.post(url, json=payload, timeout=10)
            result = response.json()
            
            if result.get('ok'):
                return {'success': True, 'message': 'تم الإرسال إلى Telegram'}
            else:
                return {'success': False, 'message': result.get('description', 'خطأ غير معروف')}
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    def send_document(
        self,
        document_content: bytes,
        filename: str,
        caption: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        إرسال ملف إلى Telegram
        
        Args:
            document_content: محتوى الملف كـ bytes
            filename: اسم الملف
            caption: تعليق اختياري
        
        Returns:
            dict: {'success': bool, 'message': str}
        """
        if not REQUESTS_AVAILABLE:
            return {'success': False, 'message': 'مكتبة requests غير متوفرة'}
        
        try:
            url = f"{self.base_url}/sendDocument"
            files = {'document': (filename, document_content)}
            data = {'chat_id': self.chat_id}
            if caption:
                data['caption'] = caption
            
            response = requests.post(url, files=files, data=data, timeout=30)
            result = response.json()
            
            if result.get('ok'):
                return {'success': True, 'message': 'تم إرسال الملف إلى Telegram'}
            else:
                return {'success': False, 'message': result.get('description', 'خطأ غير معروف')}
        except Exception as e:
            return {'success': False, 'message': str(e)}


class GoogleSheetsIntegration:
    """التكامل مع Google Sheets (يتطلب API Key وService Account)"""
    
    def __init__(self, credentials_json: str, spreadsheet_id: str):
        """
        Args:
            credentials_json: JSON لبيانات اعتماد حساب الخدمة
            spreadsheet_id: معرف جدول البيانات
        """
        self.credentials_json = credentials_json
        self.spreadsheet_id = spreadsheet_id
        self._service = None
    
    def _get_service(self):
        """الحصول على خدمة Google Sheets API"""
        if self._service:
            return self._service
        
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            
            SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
            creds_info = json.loads(self.credentials_json)
            creds = service_account.Credentials.from_service_account_info(
                creds_info, scopes=SCOPES
            )
            self._service = build('sheets', 'v4', credentials=creds)
            return self._service
        except ImportError:
            raise ImportError('تحتاج لتثبيت: google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client')
        except Exception as e:
            raise Exception(f'خطأ في تهيئة Google Sheets: {e}')
    
    def append_row(self, worksheet_name: str, values: List[Any]) -> Dict[str, Any]:
        """
        إضافة صف إلى جدول البيانات
        
        Args:
            worksheet_name: اسم ورقة العمل
            values: قائمة القيم للإضافة
        
        Returns:
            dict: {'success': bool, 'message': str}
        """
        try:
            service = self._get_service()
            body = {'values': [values]}
            result = service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f'{worksheet_name}!A:Z',
                valueInputOption='RAW',
                body=body
            ).execute()
            
            return {'success': True, 'message': f'تمت إضافة {result.get("updates", {}).get("updatedRows", 0)} صف'}
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    def append_multiple_rows(self, worksheet_name: str, rows: List[List[Any]]) -> Dict[str, Any]:
        """
        إضافة عدة صفوف إلى جدول البيانات
        
        Args:
            worksheet_name: اسم ورقة العمل
            rows: قائمة القوائم (كل قائمة تمثل صفًا)
        
        Returns:
            dict: {'success': bool, 'message': str}
        """
        try:
            service = self._get_service()
            body = {'values': rows}
            result = service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f'{worksheet_name}!A:Z',
                valueInputOption='RAW',
                body=body
            ).execute()
            
            return {'success': True, 'message': f'تمت إضافة {result.get("updates", {}).get("updatedRows", 0)} صف'}
        except Exception as e:
            return {'success': False, 'message': str(e)}


class PDFGenerator:
    """توليد مستندات PDF للتقارير والنماذج"""
    
    def __init__(self, font_path: Optional[str] = None):
        """
        Args:
            font_path: مسار خط عربي لدعم اللغة العربية (اختياري)
        """
        self.font_path = font_path
        self.styles = None
    
    def _setup_fonts(self):
        """إعداد الخطوط العربية"""
        if not REPORTLAB_AVAILABLE:
            return False
        
        try:
            # تسجيل الخط العربي إذا تم توفيره
            if self.font_path and os.path.exists(self.font_path):
                pdfmetrics.registerFont(TTFont('Arabic', self.font_path))
            return True
        except Exception:
            return False
    
    def generate_report(
        self,
        title: str,
        sections: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
        footer_text: Optional[str] = None
    ) -> bytes:
        """
        توليد تقرير PDF
        
        Args:
            title: عنوان التقرير
            sections: قائمة الأقسام، كل قسم هو dict يحتوي على:
                      {'heading': str, 'content': str/table_data}
            metadata: بيانات وصفية (تاريخ، مؤسسة، إلخ)
            footer_text: نص التذييل
        
        Returns:
            bytes: محتوى PDF
        """
        if not REPORTLAB_AVAILABLE:
            raise ImportError('تحتاج لتثبيت reportlab: pip install reportlab')
        
        self._setup_fonts()
        
        # إنشاء المستند
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=50,
            leftMargin=50,
            topMargin=50,
            bottomMargin=50,
            title=title
        )
        
        elements = []
        styles = getSampleStyleSheet()
        
        # تخصيص الأنماط للعربية
        arabic_style = ParagraphStyle(
            'ArabicStyle',
            parent=styles['Normal'],
            fontName='Helvetica',  # سيتم تغييره إذا كان الخط متاحًا
            fontSize=12,
            leading=16,
            alignment=1  # Right align for RTL
        )
        
        title_style = ParagraphStyle(
            'ArabicTitle',
            parent=styles['Heading1'],
            fontSize=18,
            alignment=1
        )
        
        # إضافة العنوان
        elements.append(Paragraph(title, title_style))
        elements.append(Spacer(1, 20))
        
        # إضافة البيانات الوصفية
        if metadata:
            meta_table_data = [[str(k), str(v)] for k, v in metadata.items()]
            meta_table = Table(meta_table_data, colWidths=[150, 300])
            meta_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.lightgrey),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
            ]))
            elements.append(meta_table)
            elements.append(Spacer(1, 20))
        
        # إضافة الأقسام
        for section in sections:
            heading = section.get('heading', '')
            content = section.get('content', '')
            
            elements.append(Paragraph(heading, arabic_style))
            elements.append(Spacer(1, 10))
            
            if isinstance(content, list):
                # جدول
                table = Table(content)
                table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
                ]))
                elements.append(table)
            else:
                # نص عادي
                elements.append(Paragraph(str(content), arabic_style))
            
            elements.append(Spacer(1, 20))
        
        # إضافة التذييل
        if footer_text:
            elements.append(Spacer(1, 30))
            elements.append(Paragraph(footer_text, arabic_style))
        
        # بناء المستند
        doc.build(elements)
        buffer.seek(0)
        
        return buffer.getvalue()
    
    def generate_form(
        self,
        form_title: str,
        fields: List[Dict[str, Any]],
        answers: Optional[Dict[str, Any]] = None
    ) -> bytes:
        """
        توليد نموذج PDF مع الإجابات
        
        Args:
            form_title: عنوان النموذج
            fields: قائمة الحقول [{'label': str, 'value': str, 'type': str}]
            answers: قاموس الإجابات {field_key: value}
        
        Returns:
            bytes: محتوى PDF
        """
        sections = []
        
        for field in fields:
            label = field.get('label', '')
            key = field.get('key', '')
            value = answers.get(key, '') if answers else field.get('value', '')
            
            sections.append({
                'heading': label,
                'content': str(value) if value else '---'
            })
        
        return self.generate_report(
            title=form_title,
            sections=sections,
            metadata={'تاريخ التعبئة': datetime.now().strftime('%Y-%m-%d %H:%M')}
        )


# دوال مساعدة للتكامل السريع

def setup_email_from_settings(db_session) -> Optional[EmailNotifier]:
    """
    إعداد EmailNotifier من إعدادات قاعدة البيانات
    
    Args:
        db_session: جلسة قاعدة البيانات
    
    Returns:
        EmailNotifier أو None إذا لم تكن الإعدادات موجودة
    """
    from database import SystemSetting
    
    settings = {}
    for setting in db_session.query(SystemSetting).all():
        settings[setting.key] = setting.value
    
    required = ['email_smtp_server', 'email_smtp_port', 'email_username', 'email_password']
    if not all(k in settings and settings[k] for k in required):
        return None
    
    return EmailNotifier(
        smtp_server=settings['email_smtp_server'],
        smtp_port=int(settings['email_smtp_port']),
        username=settings['email_username'],
        password=settings['email_password'],
        use_tls=settings.get('email_use_tls', 'true').lower() == 'true'
    )


def setup_telegram_from_settings(db_session) -> Optional[TelegramIntegration]:
    """إعداد TelegramIntegration من إعدادات قاعدة البيانات"""
    from database import SystemSetting
    
    settings = {}
    for setting in db_session.query(SystemSetting).all():
        settings[setting.key] = setting.value
    
    token = settings.get('tg_bot_token')
    chat_id = settings.get('tg_chat_id')
    
    if not token or not chat_id:
        return None
    
    return TelegramIntegration(bot_token=token, chat_id=chat_id)
