"""CSV, Excel e PDF: EAN come testo, formula injection, accenti, totali coerenti."""
import csv, io

from openpyxl import load_workbook

from magazzino import export

COL = [("prodotto", "Prodotto"), ("ean", "EAN"), ("quantita", "Quantità")]
RIGHE = [{"prodotto": "Perché sì – crème brûlée", "ean": "0012345678905", "quantita": 2},
         {"prodotto": "=HYPERLINK(\"http://x\")", "ean": "+39123", "quantita": 3},
         {"prodotto": "@SUM(A1)", "ean": "-5", "quantita": 1}]


def test_csv_accenti_zeri_e_formule():
    b = export.csv_bytes(COL, RIGHE, [("Documento", "TR-0001")])
    assert b.startswith(b"\xef\xbb\xbf")                     # BOM: Excel riconosce l'UTF-8
    righe = list(csv.reader(io.StringIO(b.decode("utf-8-sig")), delimiter=";"))
    assert righe[0] == ["Documento", "TR-0001"]
    dati = righe[3:]
    assert dati[0] == ["Perché sì – crème brûlée", "0012345678905", "2"]
    assert dati[1][0].startswith("'=") and dati[1][1] == "'+39123"
    assert dati[2][0] == "'@SUM(A1)" and dati[2][1] == "'-5"


def test_xlsx_ean_testo_e_niente_formule():
    b = export.xlsx_bytes("Prova", COL, RIGHE, [("Documento", "TR-0001")], [("Righe", 3), ("Pezzi", 6)])
    ws = load_workbook(io.BytesIO(b)).active
    celle = {c.value: c for riga in ws.iter_rows() for c in riga if c.value is not None}
    ean = celle["0012345678905"]
    assert ean.data_type == "s" and ean.number_format == "@"
    formula = next(c for v, c in celle.items() if isinstance(v, str) and v.startswith("=HYPERLINK"))
    assert formula.data_type == "s"                           # testo, non formula
    assert celle["@SUM(A1)"].data_type == "s"
    assert "Perché sì – crème brûlée" in celle
    valori = [c.value for riga in ws.iter_rows() for c in riga]
    assert "Pezzi" in valori and 6 in valori


def test_pdf_a4_con_totali():
    b = export.pdf_documento("Lista di prelievo", [("Documento", "TR-0001"), ("Origine", "Negozio Centro")],
                             [("_spunta", "✓", 8, "c"), ("prodotto", "Prodotto", 80, "l"), ("ean", "EAN", 40, "l"),
                              ("quantita", "Qtà", 20, "c"), ("_note", "Note", 42, "l")],
                             RIGHE, [("Righe", 3), ("Pezzi", 6)], ["Preparato da", "Ricevuto da"], codice="TR-0001")
    assert b.startswith(b"%PDF") and len(b) > 1500


def test_export_trasferimento_totali_coerenti(demo):
    from fastapi.testclient import TestClient
    from magazzino.web import crea_app
    c, _, cfg = demo
    cl = TestClient(crea_app(cfg, sfondo=False), follow_redirects=False)
    cl.post("/login", data={"username": "magazziniere", "password": "demo1234"})
    tid = c.execute("SELECT id FROM trasferimenti WHERE stato='approvato'").fetchone()[0]
    atteso = c.execute("SELECT COUNT(*), SUM(approvata) FROM righe WHERE trasferimento_id=?", (tid,)).fetchone()
    testo = cl.get(f"/trasferimenti/{tid}/export.csv").content.decode("utf-8-sig")
    righe = list(csv.reader(io.StringIO(testo), delimiter=";"))
    assert righe[0][1].startswith("Lista di prelievo TR-")
    assert any(r[:1] == ["Origine"] for r in righe) and any(r[:1] == ["Data e ora"] for r in righe)
    testa = next(i for i, r in enumerate(righe) if r and r[0] == "Prodotto")
    corpo = righe[testa + 1:]
    assert len(corpo) == atteso[0] and sum(int(r[5]) for r in corpo) == atteso[1]
    assert "Controllato (✓)" in righe[testa] and "Annotazioni" in righe[testa]
    ws = load_workbook(io.BytesIO(cl.get(f"/trasferimenti/{tid}/export.xlsx").content)).active
    valori = [x.value for riga in ws.iter_rows() for x in riga]
    assert valori[valori.index("Pezzi") + 1] == atteso[1] and valori[valori.index("Righe") + 1] == atteso[0]
    assert cl.get(f"/trasferimenti/{tid}/export.pdf").content.startswith(b"%PDF")
    for campo in ("prezzo", "Venduto", "cliente"):
        assert campo not in testo
