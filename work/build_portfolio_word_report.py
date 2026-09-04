from __future__ import annotations

import csv
import math
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "Portafoglio_3_ETF_vs_SWDA_Analisi.docx"
ASSETS = ROOT / "work" / "portfolio_word_assets"
MONTHLY = ROOT / "outputs" / "four_sleeve_v1" / "monthly_returns.csv"
ANNUAL = ROOT / "outputs" / "three_sleeve_efficiency" / "annual_comparison.csv"

NAVY = "17365D"
BLUE = "2E74B5"
DARK_BLUE = "1F4D78"
LIGHT_BLUE = "E8EEF5"
LIGHT_GRAY = "F2F4F7"
CALLOUT = "F4F6F9"
GREEN = "1F6D4A"
GOLD = "8A6400"
RED = "9B1C1C"
TEXT = "222222"
MUTED = "666666"
WHITE = "FFFFFF"

CONTENT_DXA = 9360
TABLE_INDENT_DXA = 120


def rgb(value: str) -> RGBColor:
    return RGBColor.from_string(value)


def set_run_font(run, name="Calibri", size=None, color=None, bold=None, italic=None):
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.rFonts
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)
    if size is not None:
        run.font.size = Pt(size)
    if color is not None:
        run.font.color.rgb = rgb(color)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic


def set_cell_shading(cell, fill: str):
    tcpr = cell._tc.get_or_add_tcPr()
    shd = tcpr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tcpr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=80, start=120, bottom=80, end=120):
    tcpr = cell._tc.get_or_add_tcPr()
    tc_mar = tcpr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tcpr.append(tc_mar)
    for edge, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_table_geometry(table, widths_dxa):
    if sum(widths_dxa) != CONTENT_DXA:
        raise ValueError(f"table widths must total {CONTENT_DXA}: {widths_dxa}")
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    tbl = table._tbl
    tblpr = tbl.tblPr
    tblw = tblpr.find(qn("w:tblW"))
    if tblw is None:
        tblw = OxmlElement("w:tblW")
        tblpr.append(tblw)
    tblw.set(qn("w:w"), str(CONTENT_DXA))
    tblw.set(qn("w:type"), "dxa")
    tblind = tblpr.find(qn("w:tblInd"))
    if tblind is None:
        tblind = OxmlElement("w:tblInd")
        tblpr.append(tblind)
    tblind.set(qn("w:w"), str(TABLE_INDENT_DXA))
    tblind.set(qn("w:type"), "dxa")
    grid = tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for width in widths_dxa:
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(width))
        grid.append(col)
    for row in table.rows:
        for idx, cell in enumerate(row.cells):
            cell.width = Inches(widths_dxa[idx] / 1440)
            tcpr = cell._tc.get_or_add_tcPr()
            tcw = tcpr.find(qn("w:tcW"))
            if tcw is None:
                tcw = OxmlElement("w:tcW")
                tcpr.append(tcw)
            tcw.set(qn("w:w"), str(widths_dxa[idx]))
            tcw.set(qn("w:type"), "dxa")
            set_cell_margins(cell)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def repeat_header(row):
    trpr = row._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    trpr.append(header)


def set_table_borders(table, color="D0D7DE", size="6"):
    tblpr = table._tbl.tblPr
    borders = tblpr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tblpr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = borders.find(qn(f"w:{edge}"))
        if el is None:
            el = OxmlElement(f"w:{edge}")
            borders.append(el)
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), size)
        el.set(qn("w:color"), color)


def format_table(table, header=True, header_fill=LIGHT_GRAY, font_size=9.2, number_cols=()):
    set_table_borders(table)
    if header:
        repeat_header(table.rows[0])
    for ridx, row in enumerate(table.rows):
        for cidx, cell in enumerate(row.cells):
            if header and ridx == 0:
                set_cell_shading(cell, header_fill)
            for p in cell.paragraphs:
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = 1.05
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER if cidx in number_cols else WD_ALIGN_PARAGRAPH.LEFT
                for run in p.runs:
                    set_run_font(run, size=font_size, color=TEXT, bold=(header and ridx == 0))


def add_table(doc, headers, rows, widths, number_cols=(), font_size=9.2, header_fill=LIGHT_GRAY):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for idx, value in enumerate(headers):
        table.rows[0].cells[idx].text = str(value)
    for values in rows:
        cells = table.add_row().cells
        for idx, value in enumerate(values):
            cells[idx].text = str(value)
    set_table_geometry(table, widths)
    format_table(table, number_cols=number_cols, font_size=font_size, header_fill=header_fill)
    after = doc.add_paragraph()
    after.paragraph_format.space_before = Pt(2)
    after.paragraph_format.space_after = Pt(2)
    return table


def add_callout(doc, label, text, fill=CALLOUT, accent=BLUE):
    table = doc.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    set_table_geometry(table, [CONTENT_DXA])
    set_table_borders(table, color=accent, size="10")
    set_cell_shading(cell, fill)
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(3)
    p.paragraph_format.space_after = Pt(3)
    r = p.add_run(f"{label}. ")
    set_run_font(r, size=10.5, color=accent, bold=True)
    r = p.add_run(text)
    set_run_font(r, size=10.5, color=TEXT)
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(3)
    return table


def add_body(doc, text, bold_lead=None):
    p = doc.add_paragraph()
    if bold_lead and text.startswith(bold_lead):
        r = p.add_run(bold_lead)
        set_run_font(r, bold=True, color=TEXT)
        r = p.add_run(text[len(bold_lead):])
        set_run_font(r, color=TEXT)
    else:
        r = p.add_run(text)
        set_run_font(r, color=TEXT)
    return p


def add_bullet(doc, text, level=0):
    p = doc.add_paragraph(style="List Bullet" if level == 0 else "List Bullet 2")
    p.paragraph_format.left_indent = Inches(0.5 if level == 0 else 0.75)
    p.paragraph_format.first_line_indent = Inches(-0.25)
    p.paragraph_format.space_after = Pt(5)
    p.paragraph_format.line_spacing = 1.10
    r = p.add_run(text)
    set_run_font(r, color=TEXT)
    return p


def add_numbered(doc, text):
    p = doc.add_paragraph(style="List Number")
    p.paragraph_format.left_indent = Inches(0.5)
    p.paragraph_format.first_line_indent = Inches(-0.25)
    p.paragraph_format.space_after = Pt(5)
    p.paragraph_format.line_spacing = 1.10
    r = p.add_run(text)
    set_run_font(r, color=TEXT)
    return p


def page_number_field(paragraph):
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = paragraph.add_run("Pagina ")
    set_run_font(run, size=9, color=MUTED)
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    paragraph._p.append(fld)


def add_heading(doc, text, level=1):
    p = doc.add_paragraph(text, style=f"Heading {level}")
    p.paragraph_format.keep_with_next = True
    return p


def pct(value, digits=2):
    return f"{value * 100:.{digits}f}%".replace(".", ",")


def eur(value, digits=0):
    text = f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"EUR {text}"


def read_monthly():
    rows = []
    with MONTHLY.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append({
                "date": datetime.strptime(row["month"], "%Y-%m-%d"),
                "world": float(row["WORLD_100"]),
                "portfolio": float(row["THREE_EQUITY_FIXED"]),
            })
    return rows


def load_font(size, bold=False):
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def make_nav_chart(rows, out_path):
    width, height = 1500, 760
    margin = {"left": 125, "right": 70, "top": 105, "bottom": 105}
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font, label_font, small_font = load_font(34, True), load_font(23), load_font(19)
    world_nav = portfolio_nav = 800000.0
    world, portfolio = [], []
    for row in rows:
        world_nav *= 1 + row["world"]
        portfolio_nav *= 1 + row["portfolio"]
        world.append(world_nav)
        portfolio.append(portfolio_nav)
    max_nav = math.ceil(max(max(world), max(portfolio)) / 1_000_000) * 1_000_000
    x0, x1 = margin["left"], width - margin["right"]
    y0, y1 = margin["top"], height - margin["bottom"]
    draw.text((x0, 28), "Crescita di EUR 800.000: SWDA/World vs portafoglio a tre ETF", fill="#17365D", font=title_font)
    for step in range(0, int(max_nav / 1_000_000) + 1):
        value = step * 1_000_000
        y = y1 - (value / max_nav) * (y1 - y0)
        draw.line((x0, y, x1, y), fill="#DDE3EA", width=2)
        draw.text((20, y - 12), f"{step} M", fill="#666666", font=small_font)
    for idx in range(0, len(rows), 24):
        x = x0 + idx / (len(rows) - 1) * (x1 - x0)
        draw.line((x, y0, x, y1), fill="#EEF1F4", width=1)
        draw.text((x - 22, y1 + 18), str(rows[idx]["date"].year), fill="#666666", font=small_font)
    def points(values):
        return [
            (x0 + idx / (len(values) - 1) * (x1 - x0), y1 - value / max_nav * (y1 - y0))
            for idx, value in enumerate(values)
        ]
    draw.line(points(world), fill="#6B7785", width=5)
    draw.line(points(portfolio), fill="#2E74B5", width=6)
    draw.rectangle((x0 + 25, y0 + 18, x0 + 55, y0 + 30), fill="#6B7785")
    draw.text((x0 + 68, y0 + 7), "SWDA / World", fill="#333333", font=label_font)
    draw.rectangle((x0 + 280, y0 + 18, x0 + 310, y0 + 30), fill="#2E74B5")
    draw.text((x0 + 323, y0 + 7), "Portafoglio 3 ETF", fill="#333333", font=label_font)
    draw.text((x0, height - 46), "Fonte: outputs/four_sleeve_v1/monthly_returns.csv; dopo costi di trading simulati, prima di TER ETF e fiscalita.", fill="#666666", font=small_font)
    image.save(out_path)


def make_metrics_chart(out_path):
    width, height = 1500, 720
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font, label_font, value_font, note_font = load_font(34, True), load_font(24, True), load_font(23), load_font(18)
    draw.text((70, 30), "Confronto sintetico: rendimento e rischio (2006-06 / 2026-05)", fill="#17365D", font=title_font)
    panels = [
        ("CAGR", 9.411, 10.490, 12, False),
        ("Volatilita", 13.390, 13.154, 15, True),
        ("Max drawdown", 48.214, 43.826, 55, True),
    ]
    for idx, (label, world, erc, scale, lower_better) in enumerate(panels):
        x = 70 + idx * 480
        y = 135
        draw.rounded_rectangle((x, y, x + 420, y + 455), radius=18, fill="#F7F9FB", outline="#D5DCE4", width=2)
        draw.text((x + 26, y + 26), label, fill="#17365D", font=label_font)
        for j, (name, value, color) in enumerate((("SWDA / World", world, "#6B7785"), ("Portafoglio 3 ETF", erc, "#2E74B5"))):
            yy = y + 115 + j * 135
            draw.text((x + 26, yy - 35), name, fill="#333333", font=note_font)
            draw.rounded_rectangle((x + 26, yy, x + 370, yy + 34), radius=10, fill="#E1E6EC")
            bar_end = x + 26 + 344 * value / scale
            draw.rounded_rectangle((x + 26, yy, bar_end, yy + 34), radius=10, fill=color)
            draw.text((x + 26, yy + 46), f"{value:.2f}%".replace(".", ","), fill="#333333", font=value_font)
        direction = "Piu basso e migliore" if lower_better else "Piu alto e migliore"
        draw.text((x + 26, y + 403), direction, fill="#666666", font=note_font)
    draw.text((70, 645), "Il mix fattoriale ha migliorato il rendimento storico e moderato il drawdown, senza eliminare il rischio azionario.", fill="#666666", font=note_font)
    image.save(out_path)


def set_picture_alt(inline_shape, title, description):
    docpr = inline_shape._inline.docPr
    docpr.set("title", title)
    docpr.set("descr", description)


def configure_styles(doc):
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(1)
    section.bottom_margin = Inches(1)
    section.left_margin = Inches(1)
    section.right_margin = Inches(1)
    section.header_distance = Inches(0.492)
    section.footer_distance = Inches(0.492)

    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal._element.rPr.rFonts.set(qn("w:ascii"), "Calibri")
    normal._element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
    normal.font.size = Pt(11)
    normal.font.color.rgb = rgb(TEXT)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.10

    heading_tokens = {
        "Heading 1": (16, BLUE, 16, 8),
        "Heading 2": (13, BLUE, 12, 6),
        "Heading 3": (12, DARK_BLUE, 8, 4),
    }
    for name, (size, color, before, after) in heading_tokens.items():
        style = doc.styles[name]
        style.font.name = "Calibri"
        style._element.rPr.rFonts.set(qn("w:ascii"), "Calibri")
        style._element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = rgb(color)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True

    for list_name in ("List Bullet", "List Bullet 2", "List Number"):
        style = doc.styles[list_name]
        style.font.name = "Calibri"
        style.font.size = Pt(11)
        style.paragraph_format.space_after = Pt(5)
        style.paragraph_format.line_spacing = 1.10

    header = section.header
    hp = header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.LEFT
    r = hp.add_run("PORTAFOGLIO STRUTTURALE | ANALISI E VALIDAZIONE")
    set_run_font(r, size=8.5, color=MUTED, bold=True)
    footer = section.footer
    fp = footer.paragraphs[0]
    page_number_field(fp)


def annual_returns(rows):
    years = {}
    for row in rows:
        year = row["date"].year
        values = years.setdefault(year, [1.0, 1.0, 0])
        values[0] *= 1 + row["world"]
        values[1] *= 1 + row["erc"]
        values[2] += 1
    return [(year, values[0] - 1, values[1] - 1, values[2]) for year, values in sorted(years.items())]


def read_annual_comparison():
    with ANNUAL.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def build_document():
    ASSETS.mkdir(parents=True, exist_ok=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    rows = read_monthly()
    nav_chart = ASSETS / "nav_comparison.png"
    metrics_chart = ASSETS / "metrics_comparison.png"
    make_nav_chart(rows, nav_chart)
    make_metrics_chart(metrics_chart)

    doc = Document()
    configure_styles(doc)

    # Editorial cover override on top of standard_business_brief.
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(86)
    p.paragraph_format.space_after = Pt(18)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("REPORT DI ARCHITETTURA E VALIDAZIONE")
    set_run_font(r, size=11, color=GOLD, bold=True)

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(10)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("Portafoglio strutturale\na tre ETF")
    set_run_font(r, size=30, color=NAVY, bold=True)

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(28)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("Caratteristiche, confronto con SWDA e priorita di miglioramento")
    set_run_font(r, size=15, color=DARK_BLUE)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("Versione ricostruita al 28 agosto 2026")
    set_run_font(r, size=10.5, color=MUTED, italic=True)

    add_callout(
        doc,
        "Conclusione centrale",
        "Il portafoglio World-Momentum-Quality ha migliorato storicamente rendimento e drawdown rispetto al proxy World, con volatilita quasi invariata. Il vantaggio non basta ancora per dichiararlo sostituto definitivo di SWDA: occorrono validazione ETF-level, analisi fiscale after-tax e prova prospettica.",
        fill="EEF4FA",
        accent=NAVY,
    )
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(20)
    r = p.add_run("Documento di ricerca - non costituisce autorita di trading o consulenza personalizzata")
    set_run_font(r, size=9, color=RED, italic=True)

    doc.add_page_break()
    add_heading(doc, "Indice", 1)
    for item in (
        "1. Sintesi decisionale",
        "2. Mandato e gerarchia del sistema",
        "3. Portafoglio strutturale a tre ETF",
        "4. Regole operative, costi e fiscalita",
        "5. Confronto approfondito con SWDA",
        "6. Perche il portafoglio ha funzionato e quando puo deludere",
        "7. Damodaran, GDI, TCE e DRO: ruoli separati",
        "8. Punti da migliorare e approfondire",
        "9. Roadmap di validazione",
        "Appendici: rendimenti annuali, parametri e fonti",
    ):
        add_body(doc, item)

    add_heading(doc, "1. Sintesi decisionale", 1)
    add_callout(
        doc,
        "Decisione di ricerca",
        "Mantenere come candidato strutturale: 33,05% World, 26,64% Momentum e 40,32% Quality. Il Trend e escluso per costo e accessibilita; TCE e DRO restano supplementi opzionali separati.",
        fill="EAF3EE",
        accent=GREEN,
    )
    add_body(doc, "Sul campione comune di 240 mesi il portafoglio a tre ETF ha prodotto un CAGR del 10,49% contro il 9,41% di World, con volatilita del 13,15% contro 13,39% e drawdown massimo del -43,83% contro -48,21%.")
    add_body(doc, "Negli ultimi dieci anni del campione il CAGR e stato 13,62% contro 12,35% di World, con volatilita quasi uguale. La tesi e un'esposizione azionaria fattoriale piu efficiente, non una protezione strutturale dalle crisi.")
    add_bullet(doc, "Punto forte: maggiore rendimento storico con rischio complessivo simile.")
    add_bullet(doc, "Punto debole: resta interamente azionario e aumenta complessita, turnover e fiscalita.")
    add_bullet(doc, "Stato corretto: candidato da validare, non sostituzione automatica del portafoglio reale.")

    add_heading(doc, "2. Mandato e gerarchia del sistema", 1)
    add_body(doc, "Il progetto nasce con un obiettivo piu ampio del semplice rendimento nominale: accrescere e poi preservare capitale reale in EUR, ridurre il sequence-of-returns risk, controllare fiscalita e costi, e rendere possibile una futura distribuzione sostenibile.")
    add_table(
        doc,
        ["Livello", "Funzione", "Stato"],
        [
            ("Portafoglio strutturale", "100% World / Momentum / Quality", "Candidato THREE_EQUITY_FIXED"),
            ("Valuation engine", "Damodaran ERP, Treasury e TIPS come lettura/veto", "Da integrare senza cambiare i pesi fissi in modo discrezionale"),
            ("GDI mensile", "Regime monetario e possibile tilt Value/Growth", "Osservativo e validativo"),
            ("TCE news-based", "Selezione tematica/fondamentale", "Prospettico; backtest decennale non validato"),
            ("DRO deterministico", "Ranking di prezzo separato", "Ricerca retrospettiva; supplemento opzionale"),
            ("Rule E + Funding G", "Distribuzione e waterfall fiscale", "Shadow monitoring; non ancora integrato nel backtest a tre ETF"),
        ],
        [1900, 5100, 2360],
        font_size=8.8,
    )
    add_callout(doc, "Principio di governance", "Gli overlay non possono alterare o finanziare automaticamente il 100% strutturale. Un eventuale supplemento TCE/DRO, fino a circa il 10%, richiede una decisione distinta su nozionale e fonte di finanziamento.", fill="FFF8E8", accent=GOLD)

    add_heading(doc, "3. Portafoglio strutturale a tre ETF", 1)
    add_table(
        doc,
        ["Sleeve", "Peso", "ETF candidato", "ISIN", "TER", "Ruolo"],
        [
            ("World", "33,05%", "SWDA", "IE00B4L5Y983", "0,20%", "Core globale developed"),
            ("Momentum", "26,64%", "IWMO", "IE00BP3QZ825", "0,25%", "Premio ai titoli con forza relativa"),
            ("Quality", "40,32%", "IWQU", "IE00BP3QZ601", "0,25%", "Profitabilita/qualita e stabilita"),
        ],
        [1150, 850, 1100, 1750, 760, 3750],
        number_cols=(1, 4),
        font_size=8.5,
        header_fill=LIGHT_BLUE,
    )
    add_body(doc, "Il TER ponderato indicativo e circa 0,23% annuo, contro 0,20% per SWDA. Questo costo ETF non e stato dedotto dal backtest a proxy: deve entrare nella validazione investibile.")
    add_bullet(doc, "SWDA, IWMO e IWQU sono ad accumulazione e quotati a Milano nella mappatura congelata al 27 agosto 2026.")
    add_bullet(doc, "I proxy storici non coincidono con gli ETF: Kenneth French BIG HiPR/HiOP non sono backfill degli indici MSCI.")

    add_heading(doc, "3.1 Ruolo economico dei tre elementi", 2)
    add_body(doc, "World mantiene l'esposizione al premio azionario globale e rende il portafoglio riconoscibile rispetto al benchmark. Momentum e Quality diversificano il modo in cui si selezionano i titoli, ma restano azionario developed e possono sovrapporsi al core. Senza Trend non bisogna attendersi una forte protezione nelle crisi.")

    add_heading(doc, "3.2 Regole congelate", 2)
    add_bullet(doc, "Pesi iniziali fissi e somma sempre pari al 100%.")
    add_bullet(doc, "Ribilanciamento all'avvio e ogni gennaio; negli altri mesi i pesi possono derivare.")
    add_bullet(doc, "Valuta di analisi EUR; componente azionaria sostanzialmente non coperta dal rischio cambio.")
    add_bullet(doc, "Nessuna ottimizzazione dei pesi dopo l'ispezione dei risultati.")
    add_bullet(doc, "Commissione simulata EUR 19 per ordine di sleeve e 10 bps di spread/slippage.")

    add_heading(doc, "4. Regole operative, costi e fiscalita", 1)
    add_table(
        doc,
        ["Voce", "Portafoglio a tre ETF", "SWDA soltanto"],
        [
            ("Ribilanciamento", "Annuale a gennaio", "Nessun ribilanciamento interno"),
            ("Commissioni nel test", "EUR 1.596", "EUR 19"),
            ("Spread/slippage nel test", "EUR 3.183,52", "EUR 800"),
            ("TER corrente indicativo", "circa 0,23% ponderato", "0,20%"),
            ("Fiscalita italiana", "Realizzazioni annuali su piu ETF", "Tax drag potenzialmente inferiore"),
            ("Complessita", "Tre strumenti + verifica pesi", "Un solo strumento"),
        ],
        [2400, 3480, 3480],
        font_size=9,
    )
    add_callout(doc, "Lacuna after-tax", "Il confronto storico e dopo costi di trading simulati ma prima di TER reali, imposte e caratteristiche dei tax lots. Per un investitore italiano questo puo ridurre il vantaggio del portafoglio multi-ETF e deve essere quantificato.", fill="FFF1F1", accent=RED)
    add_body(doc, "La policy fiscale di progetto prevede il 26% sulle plusvalenze ETF realizzate e una vendita guidata dai lotti. Il trattamento agevolato al 12,5% riguarda titoli di Stato eleggibili, non le tre sleeve attuali. La regola di funding G resta rilevante per i futuri prelievi, ma non e stata ancora collegata a questo backtest.")

    doc.add_page_break()
    add_heading(doc, "5. Confronto approfondito: SWDA vs portafoglio a tre ETF", 1)
    add_body(doc, "Il benchmark della ricerca e World 100%, cioe il proxy economico di SWDA. Il confronto ventennale non usa gli ETF reali per l'intero periodo: SWDA e partito nel 2009, IWMO e IWQU nel 2014. Usa quindi proxy Developed convertiti in EUR; i risultati non sono rendimenti storici ufficiali degli ETF MSCI.")
    shape = doc.add_picture(str(nav_chart), width=Inches(6.35))
    set_picture_alt(shape, "Crescita del capitale", "Grafico della crescita di EUR 800.000 per World e portafoglio a tre ETF dal giugno 2006 al maggio 2026.")
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph("Figura 1 - Evoluzione del capitale simulato dopo costi di negoziazione.")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(8)
    for run in p.runs:
        set_run_font(run, size=9, color=MUTED, italic=True)

    add_table(
        doc,
        ["Metrica", "SWDA / World", "Portafoglio 3 ETF", "Differenza"],
        [
            ("CAGR", "9,41%", "10,49%", "+1,08 pp"),
            ("Volatilita annua", "13,39%", "13,15%", "-0,24 pp"),
            ("Max drawdown", "-48,21%", "-43,83%", "+4,39 pp di tenuta"),
            ("CAGR / volatilita", "0,741", "0,827", "+0,086"),
            ("NAV finale da EUR 800.000", "EUR 4,834 mln", "EUR 5,883 mln", "+EUR 1,048 mln"),
        ],
        [2600, 2000, 2000, 2760],
        number_cols=(1, 2, 3),
        font_size=9,
        header_fill=LIGHT_BLUE,
    )

    shape = doc.add_picture(str(metrics_chart), width=Inches(6.35))
    set_picture_alt(shape, "Metriche di rendimento e rischio", "Confronto a barre di CAGR, volatilita e drawdown massimo per World e portafoglio a tre ETF.")
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph("Figura 2 - Il vantaggio storico principale e nel rendimento; il rischio resta vicino a World.")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(8)
    for run in p.runs:
        set_run_font(run, size=9, color=MUTED, italic=True)

    add_heading(doc, "5.1 Robustezza negli ultimi dieci anni", 2)
    add_table(
        doc,
        ["Metrica 2016-06 / 2026-05", "World", "Portafoglio 3 ETF", "Lettura"],
        [
            ("CAGR", "12,35%", "13,62%", "Rendimento superiore"),
            ("Volatilita", "13,48%", "13,48%", "Rischio sostanzialmente uguale"),
            ("Max drawdown", "-20,45%", "-17,87%", "Perdita massima piu contenuta"),
        ],
        [2900, 1500, 1500, 3460],
        number_cols=(1, 2),
        font_size=9,
    )
    add_body(doc, "Nei 19 anni solari completi 2007-2025 il portafoglio a tre ETF ha sovraperformato World in 14 anni e sottoperformato in 5. Questa frequenza descrive il campione storico, non una probabilita futura.")

    add_heading(doc, "5.2 Interpretazione corretta", 2)
    add_bullet(doc, "Nel 2008 World ha perso circa il 37,49% e il portafoglio a tre ETF circa il 35,29%: la protezione e modesta, coerentemente con un portafoglio interamente azionario.")
    add_bullet(doc, "Nel rimbalzo 2009 il portafoglio a tre ETF ha guadagnato meno di World; i fattori non vincono in ogni regime.")
    add_bullet(doc, "Nell'ultimo decennio il vantaggio storico e stato soprattutto di rendimento, con volatilita quasi identica.")

    add_heading(doc, "6. Perche il portafoglio ha funzionato e quando puo deludere", 1)
    add_table(
        doc,
        ["Meccanismo", "Beneficio atteso", "Scenario sfavorevole"],
        [
            ("World", "Crescita globale e semplicita", "Bear market azionario globale"),
            ("Momentum", "Partecipa ai trend azionari persistenti", "Reversal rapidi e rotazioni improvvise"),
            ("Quality", "Bilanci/profitti piu robusti", "Fasi speculative o value rebound aggressivi"),
            ("Ribilanciamento annuale", "Disciplina e turnover contenuto", "Timing di gennaio sfavorevole o drift eccessivo"),
        ],
        [1700, 3720, 3940],
        font_size=8.8,
    )
    add_callout(doc, "Rischio residuo", "World, Momentum e Quality restano tutti azionari developed. Il mix puo migliorare selezione e percorso, ma non sostituisce una vera sleeve diversificante nelle crisi.", fill="FFF8E8", accent=GOLD)

    add_heading(doc, "7. Damodaran, GDI, TCE e DRO: ruoli separati", 1)
    add_table(
        doc,
        ["Modulo", "Frequenza", "Cosa misura", "Uso consentito oggi", "Problema aperto"],
        [
            ("Damodaran", "Mensile", "ERP e convenienza azionaria vs risk-free", "Lettura/veto", "Non e ancora collegato ai pesi ERC annuali"),
            ("GDI", "Mensile + pulse", "Gold/SDR, excess money, repressione", "Osservazione e validazione Value/Growth", "Backtest della regola simmetrica non conclusivo"),
            ("TCE news", "Mensile + eventi", "Policy, ordini, earnings, valutazione, crowding", "Shadow/prospettico", "Punteggi storici PIT non ricostruibili"),
            ("DRO", "Mensile", "Ranking 12-1 e trend 10 mesi", "Supplemento opzionale separato", "Survivorship/proxy bias; non e TCE"),
        ],
        [1300, 1100, 2800, 2300, 1860],
        font_size=8.2,
        header_fill=LIGHT_BLUE,
    )
    add_body(doc, "Il GDI congelato combina 40% Gold/SDR, 35% Global Excess Money e 25% Financial Repression. E pensato per capire quando un tilt Value o Growth merita attenzione, non per riscrivere mensilmente i tre pesi senza una validazione separata.")
    add_body(doc, "Il DRO ha prodotto risultati retrospettivi molto forti dal 2020, ma utilizza un universo congelato nel 2026 e proxy condizionali. Non deve essere sommato al CAGR del portafoglio strutturale ne interpretato come prova del TCE originale basato sulle notizie.")

    doc.add_page_break()
    add_heading(doc, "8. Punti da migliorare e approfondire", 1)
    priorities = [
        ("P0", "After-tax comparison", "Ricostruire SWDA e portafoglio a tre ETF con tax lots italiani, TER, commissioni e spread reali.", "Decisione investibile"),
        ("P0", "ETF-level validation", "Usare ogni ETF solo dalla propria inception; nessun backfill artificiale.", "Misura del tracking gap"),
        ("P0", "Provenienza pesi", "Documentare finestra/covarianza che ha generato i coefficienti e testarli walk-forward.", "Escludere look-ahead nei coefficienti"),
        ("P1", "Ribilanciamento", "Confrontare gennaio fisso con bande tax-aware e annualita mobile senza ottimizzazione ex post.", "Ridurre tax drag"),
        ("P1", "Overlap azionario", "Misurare concentrazione per titolo/settore e correlazioni SWDA-IWMO-IWQU.", "Capire la vera diversificazione"),
        ("P1", "Perpetuity integration", "Rifare bootstrap/Monte Carlo con le tre sleeve, CPI italiano e Rule E+G.", "Validare reddito e capitale reale"),
        ("P1", "Regime overlays", "Definire se Damodaran/GDI agiscono solo su overlay o su bande strutturali predefinite.", "Evitare conflitti di governance"),
        ("P2", "Prospective ledger", "Congelare score GDI/TCE/DRO prima dei prezzi successivi.", "Prova out-of-sample"),
        ("P2", "Stress e sensitivita", "Proxy alternativi, FX, inflazione e costi 5/10/20 bps.", "Robustezza"),
    ]
    add_table(doc, ["Priorita", "Area", "Lavoro richiesto", "Perche conta"], priorities, [800, 1900, 4500, 2160], font_size=8.3, header_fill=LIGHT_BLUE)

    add_heading(doc, "8.1 Parametri economici da riconciliare", 2)
    add_callout(doc, "Dato utente non ancora codificato", "La conversazione successiva ha qualificato sia la rata sia EUR 180.000 come importi lordi fiscali. La configurazione four_sleeve_v1 non contiene ancora natura, frequenza, timing o trattamento fiscale di questi flussi: non devono essere inseriti nei calcoli per supposizione.", fill="FFF1F1", accent=RED)
    add_body(doc, "Anche la policy E+G archiviata usa ancora EUR 1.800 netti/mese nella zona protetta. Prima dell'attivazione occorre una specifica unica che distingua con precisione lordo, netto, imposta sulle plusvalenze e fabbisogno di cassa.")

    add_heading(doc, "8.2 Limiti statistici da tenere visibili", 2)
    add_bullet(doc, "Il test e retrospettivo e basato sulle versioni correnti degli archivi, non su una storia di segnali pubblicati in tempo reale.")
    add_bullet(doc, "La finestra di venti anni contiene pochi grandi regimi indipendenti; il 2008 pesa molto sulla conclusione.")
    add_bullet(doc, "La frequenza di vittoria decennale non garantisce una finestra futura positiva; il peggiore excess CAGR osservato e -1,31% annuo.")
    add_bullet(doc, "I proxy Momentum e Quality sono portafogli accademici e non repliche esatte degli indici MSCI degli ETF.")
    add_bullet(doc, "Un confronto veramente personale deve includere posizione fiscale, tax lots, orizzonte, contributi e prelievi reali.")

    add_heading(doc, "9. Roadmap di validazione", 1)
    roadmap = [
        "Congelare in una specifica unica capitale, EUR 180.000 lordi, rata lorda fiscale, contribuzioni e data di attivazione dei prelievi.",
        "Costruire il backtest ETF-level dalla data comune investibile e quantificare tracking gap rispetto ai proxy.",
        "Eseguire confronto after-tax paired: SWDA vs portafoglio a tre ETF con gli stessi flussi e gli stessi shock.",
        "Rifare Monte Carlo/block bootstrap del sistema perpetuo usando le tre sleeve e Rule E+G, senza importare i risultati legacy 60/20/20.",
        "Attivare un periodo shadow di 12-24 mesi con pesi congelati e registro prospettico di GDI/TCE/DRO.",
        "Solo dopo, decidere transizione graduale, eventuali bande di ribilanciamento e dimensione dell'overlay opzionale.",
    ]
    for item in roadmap:
        add_numbered(doc, item)

    add_heading(doc, "9.1 Criterio decisionale finale", 2)
    add_table(
        doc,
        ["Se la priorita e...", "Scelta piu coerente oggi", "Motivo"],
        [
            ("Massima semplicita e minimo errore operativo", "SWDA", "Un ETF, TER inferiore, fiscalita e governance piu semplici"),
            ("Tilt fattoriale con rendimento storico superiore", "Portafoglio 3 ETF in validazione", "CAGR storico superiore con rischio simile"),
            ("Rendita perpetua after-tax", "Nessuna scelta definitiva", "Serve integrazione tre ETF + CPI + E+G + tax lots"),
            ("Alpha tattico/tematico", "Overlay separato", "TCE/DRO non devono contaminare il benchmark strutturale"),
        ],
        [3000, 2300, 4060],
        font_size=8.8,
        header_fill=LIGHT_BLUE,
    )
    add_callout(doc, "Raccomandazione metodologica", "Usare SWDA come benchmark permanente e il portafoglio a tre ETF come challenger. Il challenger viene promosso solo se mantiene il vantaggio dopo costi, fiscalita, ETF reali e verifica prospettica; non perche il backtest storico e migliore.", fill="EAF3EE", accent=GREEN)

    doc.add_page_break()
    add_heading(doc, "Appendice A - Rendimenti e volatilita anno per anno", 1)
    add_body(doc, "Il confronto usa 2007-2025 come anni completi. Il 2026 copre gennaio-maggio: la volatilita e annualizzata da sole cinque osservazioni mensili e va letta con cautela.")
    annual = read_annual_comparison()
    return_rows, volatility_rows = [], []
    for row in annual:
        label = row["year"] + (" (gen-mag)" if row["months"] != "12" else "")
        return_rows.append((label, pct(float(row["SWDA_PROXY_return"])), pct(float(row["PORTAFOGLIO_3_return"])), pct(float(row["IWMO_PROXY_return"])), pct(float(row["IWQU_PROXY_return"]))))
        volatility_rows.append((label, pct(float(row["SWDA_PROXY_volatility"])), pct(float(row["PORTAFOGLIO_3_volatility"])), pct(float(row["IWMO_PROXY_volatility"])), pct(float(row["IWQU_PROXY_volatility"]))))
    add_heading(doc, "A.1 Rendimenti", 2)
    add_table(doc, ["Anno", "SWDA / World", "Portafoglio 3 ETF", "IWMO / Momentum", "IWQU / Quality"], return_rows, [1250, 1800, 2200, 2055, 2055], number_cols=(0, 1, 2, 3, 4), font_size=7.8)
    add_heading(doc, "A.2 Volatilita annualizzata", 2)
    add_table(doc, ["Anno", "SWDA / World", "Portafoglio 3 ETF", "IWMO / Momentum", "IWQU / Quality"], volatility_rows, [1250, 1800, 2200, 2055, 2055], number_cols=(0, 1, 2, 3, 4), font_size=7.8)
    add_body(doc, "Rendimento annuale = prodotto dei rendimenti mensili (1+r) meno 1. Volatilita annuale = deviazione standard campionaria dei rendimenti mensili dell'anno moltiplicata per radice di 12.")

    add_heading(doc, "Appendice B - Assunzioni congelate", 1)
    add_table(
        doc,
        ["Elemento", "Assunzione / stato"],
        [
            ("Finestra", "2006-06-30 / 2026-05-31, 240 mesi"),
            ("Capitale simulato", "EUR 800.000; scala di confronto, non valore corrente personale"),
            ("Valuta", "EUR, azionario unhedged; conversione mensile DEXUSEU"),
            ("Proxy World", "Kenneth French Developed Mkt-RF + RF"),
            ("Proxy Momentum", "Developed BIG HiPR"),
            ("Proxy Quality", "Developed BIG HiOP / Robust"),
            ("Costi trading", "EUR 19 per ordine + 10 bps per gamba"),
            ("TER e imposte", "Non inclusi nel backtest proxy; da integrare"),
            ("Rebalance", "Inception + ogni gennaio"),
            ("TCE/DRO", "Separati; supplemento facoltativo, non finanziamento automatico"),
        ],
        [2500, 6860],
        font_size=9,
    )

    add_heading(doc, "Appendice C - Fonti di progetto", 1)
    for source in (
        "outputs/four_sleeve_v1/validation_report.md",
        "outputs/four_sleeve_v1/summary.csv",
        "outputs/four_sleeve_v1/monthly_returns.csv",
        "config/four_sleeve_v1.json",
        "docs/superpowers/specs/2026-08-27-four-sleeve-tce-validation-v1.md",
        "work/motor_docs/perpetual_engine_v3_3.txt",
        "work/motor_docs/eg_policy_workbook_transcript.txt",
        "outputs/tce_deterministic/2026-08-27.md",
        "outputs/dro_v1/report.md",
    ):
        add_bullet(doc, source)
    add_body(doc, "Riferimenti ufficiali degli strumenti: iShares e Borsa Italiana per SWDA, IWMO e IWQU. Le caratteristiche prodotto sono quelle congelate nel report del 27 agosto 2026 e vanno ricontrollate al momento dell'esecuzione.")

    # Core properties and final save.
    props = doc.core_properties
    props.title = "Portafoglio strutturale a tre ETF - confronto con SWDA"
    props.subject = "Caratteristiche, validazione, rischi e roadmap"
    props.author = "Perpetual Engine - report di ricerca"
    props.keywords = "SWDA, IWMO, IWQU, Momentum, Quality, GDI, TCE, DRO"
    props.comments = "Ricerca retrospettiva; non autorita di trading."
    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    build_document()
