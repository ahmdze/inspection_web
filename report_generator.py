import os, json
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from datetime import datetime

def set_rtl_and_justify(paragraph, align=WD_ALIGN_PARAGRAPH.JUSTIFY):
    paragraph.alignment = align
    pPr = paragraph._element.get_or_add_pPr()
    pPr.append(OxmlElement('w:bidi'))

def set_font_style(run, size=12, bold=False):
    run.font.name = "Times New Roman"
    run.font.size = Pt(size)
    run.bold = bold
    rPr = run._element.get_or_add_rPr()
    rFonts = OxmlElement("w:rFonts")
    for a, v in [("w:ascii", "Times New Roman"), ("w:hAnsi", "Times New Roman"), ("w:cs", "Times New Roman")]:
        rFonts.set(qn(a), v)
    rPr.extend([rFonts, OxmlElement("w:rtl"), OxmlElement("w:szCs")])
    if bold:
        rPr.append(OxmlElement("w:bCs"))

def clean_numeric_value(val):
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val)

def format_date_only(val):
    if not val:
        return ""
    try:
        if isinstance(val, datetime):
            return val.strftime("%d/%m/%Y")
        return datetime.strptime(str(val).strip(), "%Y-%m-%d").strftime("%d/%m/%Y")
    except:
        return str(val)

def build_web_report(data: dict, output_folder: str) -> str:
    inst = data.get("general",{}).get("institution") or data.get("المؤسسة") or "غير محدد"
    date = data.get("general",{}).get("visit_date") or data.get("تاريخ الزيارة") or "غير محدد"
    filename = f"تقرير_{inst.replace('/','-').replace('\\','-')}_{str(date).replace('/','-')}.docx"
    path = os.path.join(output_folder, filename)
    doc = Document()

    # الترويسة
    p_subj = doc.add_paragraph()
    set_rtl_and_justify(p_subj, WD_ALIGN_PARAGRAPH.CENTER)
    set_font_style(p_subj.add_run("م/ زيارة تفتيشية"), size=18, bold=True)

    intro_text = f"استناداً إلى الخطة السنوية لشعبة تفتيش المؤسسات الصحية الحكومية، أجرى فريق من قسم التفتيش زيارة تفتيشية الى ({inst}) بتاريخ ({date})"
    p_intro = doc.add_paragraph()
    set_rtl_and_justify(p_intro)
    p_intro.paragraph_format.line_spacing = 1.5
    set_font_style(p_intro.add_run(intro_text), size=12)

    p_note = doc.add_paragraph()
    set_rtl_and_justify(p_note)
    set_font_style(p_note.add_run("وتم ملاحظة الاتي :"), size=12, bold=True)
    doc.add_paragraph("  ")

    # 1. المعلومات العامة
    p_gen = doc.add_paragraph()
    set_rtl_and_justify(p_gen)
    set_font_style(p_gen.add_run("المعلومات العامة:"), size=15, bold=True)
    for k, v in data.get("general",{}).items():
        if k in ["institution", "visit_date"]:
            continue
        p = doc.add_paragraph()
        set_rtl_and_justify(p)
        set_font_style(p.add_run(f"{k}:  "), size=12, bold=True)
        set_font_style(p.add_run(str(v)), size=12)
    doc.add_paragraph("  ")

    # 2. البيانات حسب الأقسام المتداخلة
    sections_data = data.get("sections", {})
    
    if sections_data:
        for section_id, section_info in sections_data.items():
            section_name = section_info.get("name", "قسم")
            section_data = section_info.get("data", [])
            subsections = section_info.get("subsections", {})
            
            # عنوان القسم الرئيسي
            if section_data or subsections:
                p_sec = doc.add_paragraph()
                set_rtl_and_justify(p_sec)
                set_font_style(p_sec.add_run(section_name), size=16, bold=True)
                doc.add_paragraph("  ")
                
                # بيانات القسم الرئيسي
                for item in section_data:
                    p = doc.add_paragraph()
                    set_rtl_and_justify(p)
                    set_font_style(p.add_run(f"{item['label']}: "), size=12, bold=True)
                    set_font_style(p.add_run(f"{item['value']} "), size=12)
                
                # الأقسام الفرعية
                for subsec_id, subsec_info in subsections.items():
                    subsec_name = subsec_info.get("name", "قسم فرعي")
                    subsec_data = subsec_info.get("data", [])
                    
                    if subsec_data:
                        p_subsec = doc.add_paragraph()
                        set_rtl_and_justify(p_subsec)
                        set_font_style(p_subsec.add_run(f"📌 {subsec_name}"), size=14, bold=True)
                        
                        for item in subsec_data:
                            p = doc.add_paragraph()
                            set_rtl_and_justify(p)
                            set_font_style(p.add_run(f"▪ {item['label']}: "), size=12, bold=True)
                            set_font_style(p.add_run(f"{item['value']} "), size=12)
                
                doc.add_paragraph("  ")

    # 3. التوصيات - تجميع حسب الفئات
    recommendations_data = data.get("recommendations", {})
    
    # تعريف فئات التوصيات
    rec_categories = [
        {"key": "rec_a", "label": "أ/ الإيعاز إلى دائرة صحة بغداد الرصافة/ قسم التخطيط:"},
        {"key": "rec_b", "label": "ب/ الإيعاز إلى شعبة التحقيقات/ قسمنا، بتشكيل لجنة تحقيقية بخصوص:"},
        {"key": "rec_c", "label": "ج/ الإيعاز إلى إدارة المستشفى بخصوص:"},
        {"key": "rec_d", "label": "د/ أخرى:"}
    ]
    
    # التحقق من وجود أي توصيات
    has_any_recommendation = False
    if isinstance(recommendations_data, dict):
        for cat_key in recommendations_data:
            if recommendations_data[cat_key]:
                has_any_recommendation = True
                break
    
    # عرض التوصيات مع عناوينها
    if has_any_recommendation:
        doc.add_page_break()
        p_rec_title = doc.add_paragraph()
        set_rtl_and_justify(p_rec_title)
        set_font_style(p_rec_title.add_run("التوصيات:"), size=18, bold=True)
        doc.add_paragraph("  ")
        
        for category in rec_categories:
            cat_key = category["key"]
            cat_label = category["label"]
            cat_recs = recommendations_data.get(cat_key, []) if isinstance(recommendations_data, dict) else []
            
            if cat_recs:
                # عنوان الفئة
                p_cat = doc.add_paragraph()
                set_rtl_and_justify(p_cat)
                set_font_style(p_cat.add_run(cat_label), size=14, bold=True)
                
                # التوصيات تحت الفئة
                for rec in cat_recs:
                    if rec and str(rec).strip():
                        p_li = doc.add_paragraph()
                        set_rtl_and_justify(p_li)
                        set_font_style(p_li.add_run(f"• {str(rec).strip()} "), size=12)
                
                doc.add_paragraph("  ")

    doc.save(path)
    return path