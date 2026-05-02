import os
from datetime import datetime

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt


def set_rtl_and_justify(paragraph, align=WD_ALIGN_PARAGRAPH.JUSTIFY):
    paragraph.alignment = align
    pPr = paragraph._element.get_or_add_pPr()
    pPr.append(OxmlElement("w:bidi"))


def set_font_style(run, size=12, bold=False):
    run.font.name = "Times New Roman"
    run.font.size = Pt(size)
    run.bold = bold
    rPr = run._element.get_or_add_rPr()
    rFonts = OxmlElement("w:rFonts")
    for attr, value in [("w:ascii", "Times New Roman"), ("w:hAnsi", "Times New Roman"), ("w:cs", "Times New Roman")]:
        rFonts.set(qn(attr), value)
    rPr.append(rFonts)
    rPr.append(OxmlElement("w:rtl"))
    if bold:
        rPr.append(OxmlElement("w:bCs"))


def clean_numeric_value(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return "" if value is None else str(value)


def format_date_only(value):
    if not value:
        return ""
    try:
        if isinstance(value, datetime):
            return value.strftime("%d/%m/%Y")
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return str(value)


def _add_title(doc, text, size, align=WD_ALIGN_PARAGRAPH.JUSTIFY):
    p = doc.add_paragraph()
    set_rtl_and_justify(p, align)
    set_font_style(p.add_run(text), size=size, bold=True)
    return p


def _add_field(doc, label, value):
    p = doc.add_paragraph()
    set_rtl_and_justify(p)
    run_label = p.add_run(f"{label}: ")
    set_font_style(run_label, size=12, bold=True)
    run_value = p.add_run(clean_numeric_value(value))
    set_font_style(run_value, size=12)
    return p


def _add_field_no_label(doc, value):
    """Add a paragraph with just the value, no label prefix"""
    p = doc.add_paragraph()
    set_rtl_and_justify(p)
    run_value = p.add_run(clean_numeric_value(value))
    set_font_style(run_value, size=12)
    return p


def build_web_report(data: dict, output_folder: str) -> str:
    general = data.get("general", {})
    inst = general.get("institution") or "غير محدد"
    date = general.get("visit_date") or "غير محدد"
    safe_name = str(inst).replace("/", "-").replace("\\", "-")
    safe_date = str(date).replace("/", "-").replace("\\", "-")
    path = os.path.join(output_folder, f"تقرير_{safe_name}_{safe_date}.docx")

    doc = Document()

    # ─── عنوان التقرير ───────────────────────────────────────────────
    p_subj = doc.add_paragraph()
    set_rtl_and_justify(p_subj, WD_ALIGN_PARAGRAPH.CENTER)
    set_font_style(p_subj.add_run("م/ زيارة تفتيشية"), size=18, bold=True)

    # ─── المقدمة ─────────────────────────────────────────────────────
    intro_text = (
        f"استناداً إلى الخطة السنوية لشعبة تفتيش المؤسسات الصحية الحكومية، "
        f"أجرى فريق من قسم التفتيش زيارة تفتيشية الى ({inst}) بتاريخ ({format_date_only(date)})"
    )
    p_intro = doc.add_paragraph()
    set_rtl_and_justify(p_intro)
    p_intro.paragraph_format.line_spacing = 1.5
    set_font_style(p_intro.add_run(intro_text), size=12)

    p_note = doc.add_paragraph()
    set_rtl_and_justify(p_note)
    set_font_style(p_note.add_run("وتم ملاحظة الاتي :"), size=12, bold=True)
    doc.add_paragraph(" ")

    # ─── المعلومات العامة ────────────────────────────────────────────
    for label, value in general.items():
        if label in ["institution", "visit_date"]:
            continue
        _add_field(doc, label, value)
    doc.add_paragraph(" ")

    # ─── الأقسام ─────────────────────────────────────────────────────
    # الهيكل المتوقع في data["sections"]:
    #   { section_id: { "name", "order", "data": [...], "subsections": { subsec_id: { "name", "order", "data": [...] } } } }
    #
    # المطلوب في التقرير:
    #   اسم القسم الرئيسي (كبير)
    #     اسم القسم الفرعي الأول (متوسط)
    #       إجابات القسم الفرعي الأول
    #     اسم القسم الفرعي الثاني (متوسط)
    #       إجابات القسم الفرعي الثاني
    #   (وهكذا)

    sections = data.get("sections", {})

    for _, section in sorted(sections.items(), key=lambda item: item[1].get("order", 0)):
        section_data = sorted(section.get("data", []), key=lambda item: item.get("order", 0))
        subsections = sorted(
            section.get("subsections", {}).items(),
            key=lambda item: item[1].get("order", 0)
        )

        # تحقق هل يوجد بيانات فعلية لهذا القسم أو أقسامه الفرعية
        has_any_data = bool(section_data) or any(
            sub.get("data") for _, sub in subsections
        )
        if not has_any_data:
            continue

        # ① اسم القسم الرئيسي
        _add_title(doc, section.get("name", "محور"), 16)

        # ② بيانات القسم الرئيسي مباشرةً (إن وُجدت)
        for item in section_data:
            _add_field(doc, item.get("label", ""), item.get("value", ""))

        # ③ الأقسام الفرعية
        for _, subsection in subsections:
            subsection_data = sorted(
                subsection.get("data", []),
                key=lambda item: item.get("order", 0)
            )
            if not subsection_data:
                continue

            # اسم القسم الفرعي
            _add_title(doc, subsection.get("name", ""), 14)

            # إجابات القسم الفرعي (القيمة فقط بدون عنوان الحقل)
            for item in subsection_data:
                _add_field_no_label(doc, item.get("value", ""))

        doc.add_paragraph(" ")

    # ─── التوصيات ────────────────────────────────────────────────────
    recommendations = data.get("recommendations", {})
    rec_categories = data.get("recommendation_categories") or [
        {"key": "rec_a", "label": "أ/ الإيعاز إلى دائرة صحة بغداد الرصافة/ قسم التخطيط:"},
        {"key": "rec_b", "label": "ب/ الإيعاز إلى شعبة التحقيقات/ قسمنا، بتشكيل لجنة تحقيقية بخصوص:"},
        {"key": "rec_c", "label": "ج/ الإيعاز إلى إدارة المستشفى بخصوص:"},
        {"key": "rec_d", "label": "د/ أخرى:"},
    ]

    has_recommendations = any(
        recommendations.get(cat["key"]) for cat in rec_categories
    )

    if has_recommendations:
        # ① صفحة جديدة
        doc.add_page_break()

        # ② عنوان "التوصيات:" في أعلى الصفحة الجديدة
        _add_title(doc, "التوصيات:", 16, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
        doc.add_paragraph(" ")

        for cat in rec_categories:
            items = [item for item in recommendations.get(cat["key"], []) if str(item).strip()]
            if not items:
                continue

            # ③ العنوان الفرعي (الحرف فقط مثل: أ/ أو ب/)
            # نعرض النص الكامل للفئة (label) دون اسم القسم التابعة له
            sub_label = cat["label"]   # مثال: "أ/ الإيعاز إلى دائرة صحة بغداد الرصافة/ قسم التخطيط:"
            _add_title(doc, sub_label, 13)

            # ④ الإجابات كقائمة مرقمة بدون اسم القسم
            for idx, item in enumerate(items, start=1):
                p = doc.add_paragraph()
                set_rtl_and_justify(p)
                set_font_style(p.add_run(f"{idx}. "), size=12, bold=True)
                set_font_style(p.add_run(str(item).strip()), size=12)

            doc.add_paragraph(" ")

    doc.save(path)
    return path