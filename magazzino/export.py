"""
CSV, Excel e PDF A4. Regole comuni:
  - EAN e SKU sono testo: in Excel la cella è di tipo testo (gli zeri iniziali restano), nel CSV la stringa è intatta;
  - protezione dalla formula injection: un testo che inizia con = + - @ tab o a capo non diventa mai una formula
    (nel CSV gli si mette davanti un apice, in Excel la cella è forzata a testo);
  - CSV in UTF-8 con BOM e separatore «;» (quello che Excel in italiano apre bene, accenti compresi);
  - i documenti operativi hanno ID, data e ora, origine e destinazione, caselle di spunta, spazio per le note,
    totale righe e totale pezzi. Nessun prezzo, venduto o dato cliente.
"""
import csv, io
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

PERICOLOSI = ("=", "+", "-", "@", "\t", "\r")
TESTO = {"ean", "sku", "codice"}


def neutro(v):
    """Valore sicuro per un foglio di calcolo."""
    if isinstance(v, str) and v.startswith(PERICOLOSI):
        return "'" + v
    return v


def csv_bytes(colonne: list, righe: list, intestazione: list = ()) -> bytes:
    """colonne: [(chiave, titolo)]. intestazione: righe iniziali [(etichetta, valore)] (ID documento, data...)."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    for k, v in intestazione:
        w.writerow([neutro(k), neutro(str(v))])
    if intestazione:
        w.writerow([])
    w.writerow([t for _, t in colonne])
    for r in righe:
        w.writerow(["" if r.get(k) is None else neutro(r.get(k)) for k, _ in colonne])
    return buf.getvalue().encode("utf-8-sig")


def xlsx_bytes(titolo: str, colonne: list, righe: list, intestazione: list = (), totali: list = ()) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = titolo[:31]
    grassetto, riga = Font(bold=True), 1
    for k, v in intestazione:
        ws.cell(riga, 1, k).font = grassetto
        _testo(ws.cell(riga, 2), str(v))
        riga += 1
    if intestazione:
        riga += 1
    testa = riga
    for i, (_, t) in enumerate(colonne, 1):
        c = ws.cell(riga, i, t)
        c.font, c.fill = grassetto, PatternFill("solid", fgColor="EDE6DD")
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for r in righe:
        riga += 1
        for i, (k, _) in enumerate(colonne, 1):
            v = r.get(k)
            cella = ws.cell(riga, i)
            if k in TESTO or isinstance(v, str):
                _testo(cella, "" if v is None else str(v))
            else:
                cella.value = v
    if totali:
        riga += 2
        for k, v in totali:
            ws.cell(riga, 1, k).font = grassetto
            ws.cell(riga, 2, v)
            riga += 1
    ws.freeze_panes = ws.cell(testa + 1, 1)
    for i, (k, t) in enumerate(colonne, 1):
        larg = max([len(str(t))] + [len(str(r.get(k) or "")) for r in righe[:500]])
        ws.column_dimensions[get_column_letter(i)].width = min(60, max(8, larg + 2))
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _testo(cella, v: str):
    cella.value = v
    cella.data_type = "s"          # mai formula, mai numero: "0012345" resta "0012345"
    cella.number_format = "@"


# ------------------------------------------------------------------------------------------------------ PDF

CARATTERI = [Path(r"C:\Windows\Fonts\DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
             Path(r"C:\Windows\Fonts\arial.ttf")]


def _pdf(piede=""):
    from fpdf import FPDF

    class Documento(FPDF):
        def footer(self):
            self.set_y(-11)
            self.set_font(self.famiglia, "", 7.5)
            self.cell(0, 5, _pulisci(f"{piede}   pagina {self.page_no()}/{{nb}}", self.famiglia), align="R")

    pdf = Documento(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(10, 12, 10)
    famiglia = "Helvetica"
    for f in CARATTERI:
        if f.exists():
            grassetto = f.with_name(f.stem + ("-Bold" if "DejaVu" in f.name else "bd") + f.suffix)
            pdf.add_font("Testo", "", str(f))
            pdf.add_font("Testo", "B", str(grassetto if grassetto.exists() else f))
            famiglia = "Testo"
            break
    pdf.famiglia = famiglia
    return pdf, famiglia


def _pulisci(testo, famiglia):
    testo = "" if testo is None else str(testo)
    if famiglia == "Helvetica":            # senza un font TTF restano solo i caratteri latin-1
        testo = testo.replace("→", "->").replace("–", "-").replace("’", "'").encode("latin-1", "replace").decode("latin-1")
    return testo


def pdf_documento(titolo: str, intestazione: list, colonne: list, righe: list, totali: list, firme: list,
                  piede: str = "", codice: str = "") -> bytes:
    """
    colonne: [(chiave, titolo, larghezza_mm, allineamento)] ; chiave "_spunta" = casella vuota, "_note" = spazio note.
    """
    pdf, fam = _pdf(codice)
    p = lambda t: _pulisci(t, fam)
    pdf.set_title(p(titolo))
    pdf.add_page()
    pdf.set_font(fam, "B", 15)
    pdf.cell(0, 8, p(titolo), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(fam, "", 9.5)
    for k, v in intestazione:
        pdf.set_font(fam, "B", 9.5)
        pdf.cell(38, 5.5, p(k))
        pdf.set_font(fam, "", 9.5)
        pdf.cell(0, 5.5, p(v), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font(fam, "", 8.5)
    pdf.set_draw_color(150, 140, 130)
    from fpdf.fonts import FontFace
    allinea = {"c": "CENTER", "r": "RIGHT", "l": "LEFT"}
    with pdf.table(col_widths=[c[2] for c in colonne], text_align=[allinea[c[3]] for c in colonne],
                   line_height=5.2, padding=1.2, headings_style=FontFace(emphasis="BOLD", fill_color=(237, 230, 221)),
                   repeat_headings=1, first_row_as_headings=True, width=sum(c[2] for c in colonne)) as tabella:
        testa = tabella.row()
        for _, t, _, _ in colonne:
            testa.cell(p(t))
        for r in righe:
            riga = tabella.row()
            for k, _, _, _ in colonne:
                if k == "_spunta":
                    riga.cell("☐" if fam == "Testo" else "[ ]")
                elif k == "_note":
                    riga.cell("")
                else:
                    riga.cell(p(r.get(k)))
    pdf.ln(4)
    pdf.set_font(fam, "B", 10)
    pdf.cell(0, 6, p("   ".join(f"{k}: {v}" for k, v in totali)), new_x="LMARGIN", new_y="NEXT")
    if firme:
        pdf.ln(8)
        pdf.set_font(fam, "", 9)
        larg = (pdf.w - 20) / len(firme)
        for f in firme:
            pdf.cell(larg, 5, p(f))
        pdf.ln(12)
        for _ in firme:
            pdf.cell(larg - 8, 0, "", border="T")
            pdf.cell(8, 0, "")
        pdf.ln(2)
    if piede:
        pdf.ln(4)
        pdf.set_font(fam, "", 7.5)
        pdf.multi_cell(0, 4, p(piede))
    return bytes(pdf.output())
