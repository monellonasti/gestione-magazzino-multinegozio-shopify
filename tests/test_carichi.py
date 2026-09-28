"""Carichi da fornitore registrati su Shopify: rilevamento, doppioni, tutto / diviso / 1:1, regola del dispari."""
import json

import pytest

from magazzino import da_shopify, operazioni, trasferimenti as T
from magazzino.db import impostazione, salva_impostazione
from magazzino.negozi import DEMO
from magazzino.sync import sincronizza

from conftest import giacenza, metti_varianti

LOC = {"demo-centro": "loc-centro-cassa", "demo-web": "loc-centro-web", "demo-porto": "loc-porto"}


def carico(c, store, numero, righe, stato="TRANSFERRED", verso_loc=None, quando="2026-09-26T09:00:00Z", nota=""):
    """Come Shopify: il carico c'è solo sul negozio Shopify dove viene registrato, per le quantità accettate."""
    out = []
    for n, q, accettati in righe:
        out.append({"line_id": f"{store}/cl{numero}-{n}", "titolo": f"Articolo {n}", "item_id": f"{store}/i{n}",
                    "variant_id": f"{store}/v{n}", "sku": f"SKU-{n}", "ean": "", "totale": q, "spedita": q, "ricevuta": accettati})
        c.execute("UPDATE demo_varianti SET disponibile=disponibile+? WHERE store=? AND variant_id=?", (accettati, store, f"{store}/v{n}"))
    c.execute("""INSERT OR REPLACE INTO demo_trasferimenti(store, transfer_id, nome, stato, creato_il, nota, origine_location,
                 origine_nome, destinazione, destinazione_location, righe) VALUES(?,?,?,?,?,?,NULL,NULL,?,?,?)""",
              (store, f"gid://demo/InventoryTransfer/{numero}", f"#T{numero}", stato, quando, nota, "Centro",
               verso_loc or LOC[store], json.dumps(out)))


def stock(c):
    return {s: (giacenza(c, s, 1), giacenza(c, s, 2)) for s in ("demo-centro", "demo-web", "demo-porto")}


@pytest.fixture
def base(vuoto):
    c, sorgente, cfg = vuoto
    salva_impostazione(c, "trasferimenti_shopify_dal", "2026-09-01")
    metti_varianti(c, [("demo-centro", 1, "8000000000017", 2), ("demo-web", 1, "8000000000017", 2), ("demo-porto", 1, "8000000000017", 1),
                       ("demo-centro", 2, "8000000000024", 0), ("demo-web", 2, "8000000000024", 0), ("demo-porto", 2, "8000000000024", 0)])
    carico(c, "demo-centro", 10, [(1, 5, 5), (2, 4, 4)], nota="ordine 4471")      # Shopify: cassa 7 e 4, sito 2 e 0
    return c, sorgente


def decidi(c, modo, quote=None, scala=True, subito=False, ruolo="responsabile", nota="", store="demo-centro", numero="10", **kw):
    ts = da_shopify.carica_ts(c, store, numero)
    righe = da_shopify.abbina_righe(c, DEMO, ts)
    if quote is None:
        quote = da_shopify.quote_altro(righe, modo, kw.get("frazione", "1/2"), kw.get("dispari", "a"))
    return da_shopify.decidi_carico(c, DEMO, kw.get("scritture", True), "anna" if ruolo == "responsabile" else "mario", ruolo, store,
                                    numero, modo, "porto", quote, scala, subito, nota, kw.get("frazione"), kw.get("dispari"))


def test_rileva_i_carichi_senza_origine_sulla_nostra_sede(base):
    c, s = base
    carico(c, "demo-centro", 11, [(1, 3, 0)], stato="READY_TO_SHIP")               # non ancora ricevuto
    carico(c, "demo-centro", 12, [(1, 4, 2)], stato="IN_PROGRESS")                 # ricevuto in parte: contano i 2 accettati
    carico(c, "demo-centro", 13, [(1, 9, 9)], verso_loc="loc-esterno")                # verso un'altra sede
    sincronizza(c, s, DEMO)
    righe = {r["nome"]: (r["verso"], r["esito"], r["pezzi"]) for r in c.execute("SELECT nome, verso, esito, pezzi FROM trasferimenti_shopify")}
    assert righe == {"#T10": ("ingresso", "da_destinare", 9), "#T11": ("ingresso", "non_partito", 0),
                     "#T12": ("ingresso", "da_destinare", 2)}
    assert c.execute("SELECT COUNT(*) FROM anomalie WHERE tipo='carico_fornitore' AND stato='aperta'").fetchone()[0] == 2


def test_tutto_al_negozio_di_arrivo_con_duplicazione_1_1(base):
    c, s = base
    sincronizza(c, s, DEMO)
    tid, ids = decidi(c, "tutto")
    operazioni.esegui_in_attesa(c, s)
    assert tid is None and len(ids) == 2
    assert stock(c) == {"demo-centro": (7, 4), "demo-web": (7, 4), "demo-porto": (1, 0)}   # il sito duplicato, la cassa non ricaricata
    assert c.execute("SELECT esito FROM trasferimenti_shopify").fetchone()[0] == "tutto_a"
    assert c.execute("SELECT COUNT(*) FROM anomalie WHERE tipo='carico_fornitore' AND stato='aperta'").fetchone()[0] == 0
    with pytest.raises(T.ErroreFlusso, match="già stato deciso"):
        decidi(c, "tutto")
    operazioni.esegui_in_attesa(c, s)
    assert stock(c)["demo-web"] == (7, 4)


def test_doppione_fatto_a_mano_sui_due_canali(base):
    c, s = base
    carico(c, "demo-web", 20, [(1, 5, 5), (2, 4, 4)], quando="2026-09-26T09:05:00Z")   # stessa consegna registrata anche sul sito
    sincronizza(c, s, DEMO)
    ts = da_shopify.carica_ts(c, "demo-centro", "10")
    assert [d["nome"] for d in da_shopify.doppi(c, DEMO, ts)] == ["#T20"]
    decidi(c, "tutto", scala=False)                                                  # la duplicazione c'è già
    with pytest.raises(T.ErroreFlusso, match="nota"):
        decidi(c, "stessa_consegna", store="demo-web", numero="20", ruolo="magazziniere")
    decidi(c, "stessa_consegna", store="demo-web", numero="20", ruolo="magazziniere", nota="stessa consegna di #T10")
    operazioni.esegui_in_attesa(c, s)
    assert c.execute("SELECT COUNT(*) FROM operazioni").fetchone()[0] == 0
    assert stock(c) == {"demo-centro": (7, 4), "demo-web": (7, 4), "demo-porto": (1, 0)}


def test_diviso_a_meta_da_portare_segue_il_flusso_normale(base):
    c, s = base
    sincronizza(c, s, DEMO)
    tid, _ = decidi(c, "dividi", frazione="1/2", dispari="a", ruolo="magazziniere")   # 5 -> 2 al Porto, 4 -> 2
    operazioni.esegui_in_attesa(c, s)
    assert stock(c) == {"demo-centro": (7, 4), "demo-web": (7, 4), "demo-porto": (1, 0)}   # finché non parte, tutto al Centro
    t, righe = T.carica(c, tid)
    assert t["stato"] == "bozza" and t["tipo"] == "ripartizione fornitore"               # deciso dal magazziniere: serve l'approvazione
    assert sorted(r["proposta"] for r in righe) == [2, 2]
    assert impostazione(c, "regola_fornitore") == {"frazione": "1/2", "dispari": "a"}   # la regola resta proposta la volta dopo
    T.approva(c, "anna", tid, t["versione"], {})
    T.inizia_preparazione(c, "mario", tid, t["versione"] + 1)
    for r in T.carica(c, tid)[1]:
        T.preleva(c, "mario", tid, r["id"], r["approvata"])
    T.spedisci(c, DEMO, s, True, "mario", tid, T.carica(c, tid)[0]["versione"], {})   # ogni pezzo preso cambia la versione
    T.ricevi(c, DEMO, True, "lucia", tid, {r["id"]: (r["spedita"], 0) for r in T.carica(c, tid)[1]})
    operazioni.esegui_in_attesa(c, s)
    assert stock(c) == {"demo-centro": (5, 2), "demo-web": (5, 2), "demo-porto": (3, 2)}


def test_diviso_gia_arrivato_con_dispari_all_altro(base):
    c, s = base
    sincronizza(c, s, DEMO)
    tid, _ = decidi(c, "dividi", frazione="1/2", dispari="b", subito=True)           # 5 -> 3 al Porto, 4 -> 2
    operazioni.esegui_in_attesa(c, s)
    assert T.carica(c, tid)[0]["stato"] == "ricevuto"
    assert stock(c) == {"demo-centro": (4, 2), "demo-web": (4, 2), "demo-porto": (4, 2)}
    assert all(o[0] == "fatta" for o in c.execute("SELECT stato FROM operazioni"))


def test_quote_corrette_a_mano(base):
    c, s = base
    sincronizza(c, s, DEMO)
    ts = da_shopify.carica_ts(c, "demo-centro", "10")
    righe = da_shopify.abbina_righe(c, DEMO, ts)
    assert da_shopify.quote_altro(righe, "dividi", "1/2", "alterna", {1: 4}) == [2, 4]
    with pytest.raises(T.ErroreFlusso, match="da 0 a 5"):
        da_shopify.quote_altro(righe, "dividi", "1/2", "a", {0: 6})
    decidi(c, "dividi", quote=[1, 3], subito=True)
    operazioni.esegui_in_attesa(c, s)
    assert stock(c) == {"demo-centro": (6, 1), "demo-web": (6, 1), "demo-porto": (2, 3)}


def test_stesse_quantita_anche_all_altro_negozio(base):
    c, s = base
    sincronizza(c, s, DEMO)
    tid, _ = decidi(c, "uguale")
    operazioni.esegui_in_attesa(c, s)
    assert tid is None
    assert stock(c) == {"demo-centro": (7, 4), "demo-web": (7, 4), "demo-porto": (6, 4)}
    assert c.execute("SELECT esito FROM trasferimenti_shopify").fetchone()[0] == "uguale_1_1"


def test_scritture_spente_e_gestito_fuori(base):
    c, s = base
    sincronizza(c, s, DEMO)
    with pytest.raises(T.ErroreFlusso, match="disattivate"):
        decidi(c, "tutto", scritture=False)
    with pytest.raises(T.ErroreFlusso, match="Solo il responsabile"):
        decidi(c, "gestito_fuori", ruolo="magazziniere", nota="script")
    decidi(c, "gestito_fuori", nota="caricato a mano sul negozio online", scritture=False)     # senza scritture si può
    assert c.execute("SELECT COUNT(*) FROM operazioni").fetchone()[0] == 0
    assert c.execute("SELECT esito FROM trasferimenti_shopify").fetchone()[0] == "gestito_fuori"


def test_uscita_e_carico_non_si_confondono(base):
    c, s = base
    sincronizza(c, s, DEMO)
    with pytest.raises(T.ErroreFlusso, match="carico da fornitore"):
        da_shopify.decidi(c, DEMO, True, "anna", "responsabile", "demo-centro", "10", "porto", True, True)
