#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 README.md 渲染为美观专业的 PDF 文档。

依赖：
    pip install reportlab mistune

用法：
    python scripts/readme_to_pdf.py
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path

import mistune
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    KeepTogether,
    ListItem,
    ListFlowable,
    HRFlowable,
)
from reportlab.platypus.doctemplate import PageTemplate, BaseDocTemplate
from reportlab.platypus.frames import Frame
from reportlab.platypus.tableofcontents import TableOfContents


# ---------------------------------------------------------------------------
# 字体注册（优先使用微软雅黑，回退用系统默认中文字体）
# ---------------------------------------------------------------------------
FONT_CANDIDATES = [
    ("C:/Windows/Fonts/msyh.ttc", "MicrosoftYaHei"),
    ("C:/Windows/Fonts/simhei.ttf", "SimHei"),
    ("C:/Windows/Fonts/simsun.ttc", "SimSun"),
]

CHINESE_FONT = None
for font_path, font_name in FONT_CANDIDATES:
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont(font_name, font_path))
            CHINESE_FONT = font_name
            break
        except Exception:
            continue

if not CHINESE_FONT:
    raise RuntimeError("未找到可用中文字体，请确保系统安装了微软雅黑或黑体/宋体。")

# 注册同一字体的粗体变体
CHINESE_FONT_BOLD = CHINESE_FONT
if CHINESE_FONT == "MicrosoftYaHei" and os.path.exists("C:/Windows/Fonts/msyhbd.ttc"):
    try:
        pdfmetrics.registerFont(TTFont("MicrosoftYaHeiBold", "C:/Windows/Fonts/msyhbd.ttc"))
        CHINESE_FONT_BOLD = "MicrosoftYaHeiBold"
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 主题色与样式
# ---------------------------------------------------------------------------
PRIMARY = colors.HexColor("#1F6FEB")      # 品牌蓝
DARK = colors.HexColor("#1F2328")           # 正文黑
GRAY = colors.HexColor("#6E7781")           # 次要灰
LIGHT_BG = colors.HexColor("#F6F8FA")      # 代码/表头背景
BORDER = colors.HexColor("#D0D7DE")        # 边框
WHITE = colors.white

PAGE_SIZE = A4
MARGIN = 2.2 * cm


def make_style(name, **kwargs):
    defaults = {
        "fontName": CHINESE_FONT,
        "fontSize": 10.5,
        "leading": 16,
        "textColor": DARK,
        "alignment": TA_JUSTIFY,
        "spaceAfter": 6,
    }
    defaults.update(kwargs)
    return ParagraphStyle(name, **defaults)


STYLES = {
    "cover_title": make_style(
        "CoverTitle",
        fontName=CHINESE_FONT_BOLD,
        fontSize=26,
        leading=36,
        textColor=PRIMARY,
        alignment=TA_CENTER,
        spaceAfter=18,
    ),
    "cover_subtitle": make_style(
        "CoverSubtitle",
        fontSize=14,
        leading=22,
        textColor=GRAY,
        alignment=TA_CENTER,
        spaceAfter=60,
    ),
    "cover_meta": make_style(
        "CoverMeta",
        fontSize=11,
        leading=18,
        textColor=GRAY,
        alignment=TA_CENTER,
        spaceAfter=8,
    ),
    "toc_title": make_style(
        "TocTitle",
        fontName=CHINESE_FONT_BOLD,
        fontSize=18,
        leading=28,
        textColor=PRIMARY,
        alignment=TA_LEFT,
        spaceAfter=20,
    ),
    "toc_item": make_style(
        "TocItem",
        fontSize=11,
        leading=20,
        textColor=DARK,
        alignment=TA_LEFT,
        spaceAfter=2,
    ),
    "h1": make_style(
        "Heading1",
        fontName=CHINESE_FONT_BOLD,
        fontSize=20,
        leading=30,
        textColor=PRIMARY,
        alignment=TA_LEFT,
        spaceBefore=24,
        spaceAfter=12,
    ),
    "h2": make_style(
        "Heading2",
        fontName=CHINESE_FONT_BOLD,
        fontSize=15,
        leading=24,
        textColor=DARK,
        alignment=TA_LEFT,
        spaceBefore=18,
        spaceAfter=8,
    ),
    "h3": make_style(
        "Heading3",
        fontName=CHINESE_FONT_BOLD,
        fontSize=12.5,
        leading=20,
        textColor=DARK,
        alignment=TA_LEFT,
        spaceBefore=12,
        spaceAfter=6,
    ),
    "body": make_style(
        "BodyText",
        fontSize=10.5,
        leading=17,
        textColor=DARK,
        alignment=TA_JUSTIFY,
        spaceAfter=8,
    ),
    "quote": make_style(
        "Quote",
        fontSize=10,
        leading=16,
        textColor=GRAY,
        leftIndent=12,
        rightIndent=12,
        spaceBefore=8,
        spaceAfter=8,
    ),
    "code": make_style(
        "Code",
        fontName="Courier",
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#24292F"),
        alignment=TA_LEFT,
        spaceAfter=0,
    ),
    "list_item": make_style(
        "ListItem",
        fontSize=10.5,
        leading=17,
        textColor=DARK,
        leftIndent=18,
        bulletIndent=8,
        spaceAfter=4,
    ),
    "table_header": make_style(
        "TableHeader",
        fontName=CHINESE_FONT_BOLD,
        fontSize=9.5,
        leading=14,
        textColor=WHITE,
        alignment=TA_CENTER,
    ),
    "table_cell": make_style(
        "TableCell",
        fontSize=9.5,
        leading=14,
        textColor=DARK,
        alignment=TA_LEFT,
    ),
    "page_header": make_style(
        "PageHeader",
        fontSize=9,
        leading=12,
        textColor=GRAY,
        alignment=TA_LEFT,
    ),
    "page_footer": make_style(
        "PageFooter",
        fontSize=9,
        leading=12,
        textColor=GRAY,
        alignment=TA_CENTER,
    ),
}


# ---------------------------------------------------------------------------
# Markdown AST 转 reportlab Flowables
# ---------------------------------------------------------------------------
class MarkdownToFlowables:
    def __init__(self):
        self.toc_entries = []

    def convert(self, ast_nodes):
        flowables = []
        i = 0
        while i < len(ast_nodes):
            node = ast_nodes[i]
            rendered = self._render_block(node)
            if rendered:
                if isinstance(rendered, list):
                    flowables.extend(rendered)
                else:
                    flowables.append(rendered)
            i += 1
        return flowables

    def _render_block(self, node):
        node_type = node.get("type")
        if node_type == "heading":
            return self._render_heading(node)
        if node_type == "paragraph":
            return self._render_paragraph(node)
        if node_type == "list":
            return self._render_list(node)
        if node_type == "block_quote":
            return self._render_quote(node)
        if node_type == "block_code":
            return self._render_code(node)
        if node_type == "table":
            return self._render_table(node)
        if node_type == "thematic_break":
            return HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceBefore=12, spaceAfter=12)
        if node_type == "blank_line":
            return Spacer(1, 6)
        return None

    def _text_value(self, node):
        """兼容 mistune v3 的 text/codespan 节点，优先使用 raw 字段。"""
        if not isinstance(node, dict):
            return self._escape(str(node))
        return self._escape(node.get("raw") or node.get("text", ""))

    def _inline_to_html(self, children):
        if not isinstance(children, list):
            return self._escape(str(children))
        parts = []
        for child in children:
            if isinstance(child, str):
                parts.append(self._escape(child))
            elif isinstance(child, dict):
                ctype = child.get("type")
                if ctype == "text":
                    parts.append(self._text_value(child))
                elif ctype == "strong":
                    inner = self._inline_to_html(child.get("children", []))
                    parts.append(f"<b>{inner}</b>")
                elif ctype == "emphasis":
                    inner = self._inline_to_html(child.get("children", []))
                    parts.append(f"<i>{inner}</i>")
                elif ctype == "codespan":
                    text = self._text_value(child)
                    parts.append(f'<font name="{CHINESE_FONT}" size="9" color="#24292F">{text}</font>')
                elif ctype == "link":
                    text = self._inline_to_html(child.get("children", []))
                    link = child.get("attrs", {}).get("url", "#")
                    # PDF 内部锚点链接忽略，避免目标不存在的错误
                    if link.startswith("#"):
                        parts.append(text)
                    else:
                        parts.append(f'<a href="{link}" color="#{PRIMARY.hexval()[2:].upper()}">{text}</a>')
                elif ctype == "linebreak":
                    parts.append("<br/>")
                elif ctype == "softbreak":
                    parts.append(" ")
                elif ctype == "block_text":
                    # 列表项内的文本容器
                    parts.append(self._inline_to_html(child.get("children", [])))
                else:
                    parts.append(self._inline_to_html(child.get("children", [])))
        return "".join(parts)

    def _escape(self, text):
        if text is None:
            return ""
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _render_heading(self, node):
        level = node.get("attrs", {}).get("level", 1)
        text = self._inline_to_html(node.get("children", []))
        clean_text = re.sub(r"<[^>]+>", "", text)
        style_name = f"h{min(level, 3)}"
        if level > 3:
            style_name = "h3"

        para = Paragraph(text, STYLES[style_name])
        para._bookmark = clean_text
        para._toc_level = level
        return para

    def _render_paragraph(self, node):
        text = self._inline_to_html(node.get("children", []))
        return Paragraph(text, STYLES["body"])

    def _render_list(self, node):
        flowables = []
        ordered = node.get("ordered", False)
        start = node.get("start", 1)

        for idx, child in enumerate(node.get("children", [])):
            if child.get("type") != "list_item":
                continue
            prefix = f"{start + idx}." if ordered else "•"
            sub_flowables = self._list_item_content(child, prefix)
            flowables.extend(sub_flowables)

        return flowables

    def _list_item_content(self, node, prefix="•"):
        flowables = []
        children = node.get("children", [])
        if not children:
            return flowables

        def is_text_block(n):
            return n.get("type") in ("paragraph", "block_text")

        # 列表项内的第一段作为带 bullet 的段落
        first = children[0]
        if is_text_block(first):
            text = self._inline_to_html(first.get("children", []))
            text = f'<font color="#{PRIMARY.hexval()[2:].upper()}">{prefix}</font>  {text}'
            flowables.append(Paragraph(text, STYLES["list_item"]))

        # 后续子块整体缩进
        for child in children[1:]:
            rendered = self._render_block(child)
            if not rendered:
                continue
            # 对后续段落增加缩进，保持层级感
            if is_text_block(child):
                text = self._inline_to_html(child.get("children", []))
                flowables.append(Paragraph(text, STYLES["list_item"]))
            elif isinstance(rendered, list):
                flowables.extend(rendered)
            else:
                flowables.append(rendered)

        return flowables

    def _render_quote(self, node):
        flowables = []
        for child in node.get("children", []):
            rendered = self._render_block(child)
            if rendered:
                if isinstance(rendered, list):
                    flowables.extend(rendered)
                else:
                    flowables.append(rendered)

        if not flowables:
            return None

        # 用带左边框和浅背景的表格模拟引用块
        table = Table([[flowables]], colWidths=[PAGE_SIZE[0] - 2 * MARGIN - 24])
        table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F6F8FA")),
                ("LEFTPADDING", (0, 0), (-1, -1), 14),
                ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LINEBELOW", (0, 0), (-1, -1), 0, BORDER),
                ("LINEABOVE", (0, 0), (-1, -1), 0, BORDER),
                ("LINERIGHT", (0, 0), (-1, -1), 0, BORDER),
                ("LINELEFT", (0, 0), (0, -1), 3, PRIMARY),
            ])
        )
        return table

    def _render_code(self, node):
        text = self._escape(node.get("raw") or node.get("text", ""))
        # 保留行结构；代码块使用支持中文的字体
        lines = text.splitlines()
        if not lines:
            lines = [""]
        inner = "<br/>".join(lines)
        para = Paragraph(f'<font name="{CHINESE_FONT}" size="9">{inner}</font>', STYLES["code"])
        table = Table([[para]], colWidths=[PAGE_SIZE[0] - 2 * MARGIN])
        table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
            ])
        )
        return KeepTogether([table, Spacer(1, 8)])

    def _render_table(self, node):
        # mistune table 插件结构：table_head + table_body
        header_cells = []
        body_rows = []

        for child in node.get("children", []):
            ctype = child.get("type")
            if ctype == "table_head":
                for cell in child.get("children", []):
                    text = self._inline_to_html(cell.get("children", []))
                    header_cells.append(Paragraph(text, STYLES["table_header"]))
            elif ctype == "table_body":
                for row in child.get("children", []):
                    cells = []
                    for cell in row.get("children", []):
                        text = self._inline_to_html(cell.get("children", []))
                        cells.append(Paragraph(text, STYLES["table_cell"]))
                    body_rows.append(cells)

        if not header_cells:
            return None

        data = [header_cells] + body_rows
        col_width = (PAGE_SIZE[0] - 2 * MARGIN) / len(header_cells)
        table = Table(data, colWidths=[col_width] * len(header_cells), repeatRows=1)

        style = TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("FONTNAME", (0, 0), (-1, 0), CHINESE_FONT_BOLD),
            ("FONTSIZE", (0, 0), (-1, -1), 9.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, colors.HexColor("#FAFBFC")]),
        ])
        table.setStyle(style)
        return KeepTogether([table, Spacer(1, 8)])


# ---------------------------------------------------------------------------
# 页眉页脚与目录模板
# ---------------------------------------------------------------------------
class DocTemplateWithToc(BaseDocTemplate):
    def __init__(self, filename, **kwargs):
        BaseDocTemplate.__init__(self, filename, **kwargs)
        self.pages = 0
        self.title = "量化管理人综合能力评估助手"
        self.toc_entries = []
        self.collect_toc = True  # 控制是否收集目录条目

    def afterFlowable(self, flowable):
        if hasattr(flowable, "_bookmark"):
            toc_level = getattr(flowable, "_toc_level", 0)
            self.canv.bookmarkPage(flowable._bookmark)
            # 仅把二级标题加入目录
            if self.collect_toc and toc_level == 2 and flowable._bookmark != "目录":
                self.toc_entries.append((flowable._bookmark, self.page))


def header_footer(canvas, doc):
    canvas.saveState()
    width, height = PAGE_SIZE

    # 页眉：左侧标题，右侧蓝色细线
    canvas.setFont(CHINESE_FONT, 9)
    canvas.setFillColor(GRAY)
    canvas.drawString(MARGIN, height - 1.2 * cm, doc.title)
    canvas.setStrokeColor(PRIMARY)
    canvas.setLineWidth(1)
    canvas.line(MARGIN, height - 1.5 * cm, width - MARGIN, height - 1.5 * cm)

    # 页脚：居中页码
    canvas.setFont(CHINESE_FONT, 9)
    canvas.setFillColor(GRAY)
    page_num = canvas.getPageNumber()
    canvas.drawCentredString(width / 2, 0.8 * cm, f"第 {page_num} 页")

    canvas.restoreState()


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def _create_doc(output_path: str):
    doc = DocTemplateWithToc(
        output_path,
        pagesize=PAGE_SIZE,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=2.2 * cm,
        bottomMargin=2.0 * cm,
        title="量化管理人综合能力评估助手",
        author="quant-alpha-sustainability",
    )

    frame = Frame(
        MARGIN,
        2.0 * cm,
        PAGE_SIZE[0] - 2 * MARGIN,
        PAGE_SIZE[1] - 3.6 * cm,
        id="normal",
    )
    template = PageTemplate(id="content", frames=frame, onPage=header_footer)
    doc.addPageTemplates([template])
    return doc


def _cover_story():
    story = []
    story.append(Spacer(1, 6 * cm))
    story.append(Paragraph("量化管理人综合能力评估助手", STYLES["cover_title"]))
    story.append(Paragraph("README 技术说明文档", STYLES["cover_subtitle"]))
    story.append(Spacer(1, 2 * cm))
    story.append(Paragraph("版本：v2.0", STYLES["cover_meta"]))
    story.append(Paragraph(f"生成日期：{datetime.now().strftime('%Y-%m-%d')}", STYLES["cover_meta"]))
    story.append(Paragraph("项目仓库：https://github.com/ericlaoxu/quant-alpha-sustainability", STYLES["cover_meta"]))
    story.append(PageBreak())
    return story


def _toc_story(toc_entries):
    story = []
    story.append(Paragraph("目录", STYLES["toc_title"]))

    rows = []
    for title, page in toc_entries:
        title_para = Paragraph(title, STYLES["toc_item"])
        page_para = Paragraph(str(page), STYLES["toc_item"])
        rows.append([title_para, page_para])

    if rows:
        table = Table(rows, colWidths=[PAGE_SIZE[0] - 2 * MARGIN - 3 * cm, 2 * cm])
        table.setStyle(
            TableStyle([
                ("ALIGN", (0, 0), (0, -1), "LEFT"),
                ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -1), 0, colors.white),
            ])
        )
        story.append(table)

    story.append(PageBreak())
    return story


def build_pdf(readme_path: Path, output_path: Path):
    with open(readme_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    # 解析 markdown AST（启用表格插件）
    md = mistune.create_markdown(renderer="ast", plugins=["table"])
    ast = md(md_text)

    def make_body():
        return MarkdownToFlowables().convert(ast)

    # 第一次构建：仅封面 + 正文，收集二级标题页码
    temp_output = str(output_path) + ".tmp"
    doc1 = _create_doc(temp_output)
    doc1.collect_toc = True
    story1 = _cover_story() + make_body()
    doc1.multiBuild(story1)

    # 第二次构建比第一次多了一个目录页，因此正文页码整体 +1
    toc_entries = [(title, page + 1) for title, page in doc1.toc_entries]

    # 第二次构建：封面 + 目录 + 正文（不再重复收集目录条目）
    doc2 = _create_doc(str(output_path))
    doc2.collect_toc = False
    story2 = _cover_story() + _toc_story(toc_entries) + make_body()
    doc2.multiBuild(story2)

    # 清理临时文件
    try:
        os.remove(temp_output)
    except FileNotFoundError:
        pass

    print(f"PDF 已生成：{output_path}")


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    readme = root / "README.md"
    output = root / "reports" / "README_量化管理人综合能力评估助手.pdf"
    output.parent.mkdir(parents=True, exist_ok=True)
    build_pdf(readme, output)
