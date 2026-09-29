"""Trasferimenti fatti su Shopify senza destinazione: rilevamento, suggerimento, scelta e aggiornamento dello stock."""
import json

import pytest

from magazzino import da_shopify, operazioni, trasferimenti as T
from magazzino.db import salva_impostazione
from magazzino.negozi import DEMO
from magazzino.sync import sincronizza

from conftest import giacenza, metti_varianti


def trasferimento_shopify(c, store, numero, righe, stato="TRANSFERRED", nota="", destinazione=None, origine=None, quando="2026-09-26T10:00:00Z"):
    """Come Shopify: lo scarico c'è solo sul negozio dove si crea il trasferimento (se è partito)."""
    loc = origine or {"demo-centro": "loc-centro-cassa", "demo-web": "loc-centro-web", "demo-porto": "loc-porto"}[store]
    out = []
    for n, q in righe:
        spedita = q if stato in ("TRANSFERRED", "IN_PROGRESS") else 0
        out.append({"line_id": f"{store}/tl{numero}-{n}", "titolo": f"Articolo {n}", "item_id": f"{store}/i{n}",
                    "variant_id": f"{store}/v{n}", "sku": f"SKU-{n}", "ean": "", "totale": q, "spedita": spedita})
        if spedita:
            c.execute("UPDATE demo_varianti SET disponibile=disponibile-? WHERE store=? AND variant_id=?", (spedita, store, f"{store}/v{n}"))
    c.execute("""INSERT OR REPLACE INTO demo_trasferimenti(store, transfer_id, nome, stato, creato_il, nota, origine_location,
                 origine_nome, destinazione, righe) VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (store, f"gid://demo/InventoryTransfer/{numero}", f"#T{numero}", stato, quando, nota, loc, "Centro", destinazione, json.dumps(out)))


@pytest.fixture
def base(vuoto):
    c, sorgente, cfg = vuoto
    salva_impostazione(c, "trasferimenti_shopify_dal", "2026-09-01")
    metti_varianti(c, [("demo-centro", 1, "8000000000017", 10), ("demo-web", 1, "8000000000017", 10), ("demo-porto", 1, "8000000000017", 0),
                       ("demo-centro", 2, "8000000000024", 5), ("demo-web", 2, "8000000000024", 5), ("demo-porto", 2, "8000000000024", 3),
                       ("demo-centro", 3, "", 4), ("demo-web", 3, "", 4)])          # senza EAN: non abbinata
    trasferimento_shopify(c, "demo-centro", 1, [(1, 3), (2, 1)], nota="per il porto")
    return c, sorgente


def test_rileva_solo_quelli_senza_destinazione_partiti_dalla_nostra_sede(base):
    c, s = base
    trasferimento_shopify(c, "demo-centro", 2, [(1, 1)], destinazione="Porto")                   # con destinazione
    trasferimento_shopify(c, "demo-centro", 3, [(1, 1)], origine="loc-esterno")                      # da un'altra sede
    trasferimento_shopify(c, "demo-centro", 4, [(2, 1)], stato="READY_TO_SHIP")                  # non ancora partito
    trasferimento_shopify(c, "demo-centro", 5, [(2, 1)], stato="CANCELED")
    trasferimento_shopify(c, "demo-centro", 6, [(2, 1)], quando="2026-08-01T10:00:00Z")          # prima della data di inizio
    sincronizza(c, s, DEMO)
    esiti = {r["nome"]: r["esito"] for r in c.execute("SELECT nome, esito FROM trasferimenti_shopify")}
    assert esiti == {"#T1": "da_destinare", "#T4": "non_partito"}
    assert c.execute("SELECT COUNT(*) FROM anomalie WHERE tipo='trasferimento_senza_destinazione' AND stato='aperta'").fetchone()[0] == 1
    # quando parte, diventa da destinare
    trasferimento_shopify(c, "demo-centro", 4, [(2, 1)], stato="TRANSFERRED")
    sincronizza(c, s, DEMO)
    assert c.execute("SELECT esito, pezzi FROM trasferimenti_shopify WHERE nome='#T4'").fetchone()[:] == ("da_destinare", 1)


def test_suggerimento_con_i_motivi(base):
    c, s = base
    sincronizza(c, s, DEMO)
    ts = da_shopify.carica_ts(c, "demo-centro", "1")
    cand = da_shopify.candidati(c, DEMO, ts, da_shopify.abbina_righe(c, DEMO, ts))
    assert [x["negozio"].id for x in cand] == ["porto"]          # l'origine non è mai fra i candidati
    motivi = " ".join(cand[0]["motivi"])
    assert "nota" in motivi and "2 articoli su 2 esistono" in motivi and "sotto la scorta minima" in motivi


def test_scelta_destinazione_carica_subito_e_allinea_lo_specchio(base):
    c, s = base
    sincronizza(c, s, DEMO)
    assert (giacenza(c, "demo-centro", 1), giacenza(c, "demo-web", 1)) == (7, 10)   # Shopify ha scalato solo la cassa
    tid, ids = da_shopify.decidi(c, DEMO, True, "mario", "magazziniere", "demo-centro", "1", "porto", True, True)
    operazioni.esegui_in_attesa(c, s)
    assert (giacenza(c, "demo-centro", 1), giacenza(c, "demo-web", 1), giacenza(c, "demo-porto", 1)) == (7, 7, 3)
    assert (giacenza(c, "demo-centro", 2), giacenza(c, "demo-web", 2), giacenza(c, "demo-porto", 2)) == (4, 4, 4)
    t, righe = T.carica(c, tid)
    assert t["stato"] == "ricevuto" and t["tipo"] == "da Shopify" and sum(r["ricevuta"] for r in righe) == 4
    assert c.execute("SELECT esito, trasferimento_id FROM trasferimenti_shopify WHERE nome='#T1'").fetchone()[:] == ("destinato", tid)
    assert c.execute("SELECT COUNT(*) FROM anomalie WHERE tipo='trasferimento_senza_destinazione' AND stato='aperta'").fetchone()[0] == 0
    assert all(o[0] == "fatta" for o in c.execute("SELECT stato FROM operazioni"))
    # una seconda decisione (doppio clic, altra persona) non muove nulla
    with pytest.raises(T.ErroreFlusso, match="già stato deciso"):
        da_shopify.decidi(c, DEMO, True, "anna", "responsabile", "demo-centro", "1", "porto", True, True)
    assert giacenza(c, "demo-porto", 1) == 3
    # rileggendo i negozi non torna da destinare
    sincronizza(c, s, DEMO)
    assert c.execute("SELECT esito FROM trasferimenti_shopify WHERE nome='#T1'").fetchone()[0] == "destinato"


def test_in_viaggio_si_carica_alla_ricezione(base):
    c, s = base
    sincronizza(c, s, DEMO)
    tid, _ = da_shopify.decidi(c, DEMO, True, "mario", "magazziniere", "demo-centro", "1", "porto", True, False)
    operazioni.esegui_in_attesa(c, s)
    assert giacenza(c, "demo-porto", 1) == 0 and giacenza(c, "demo-web", 1) == 7
    t, righe = T.carica(c, tid)
    assert t["stato"] == "spedito"
    T.ricevi(c, DEMO, True, "lucia", tid, {r["id"]: (r["spedita"], 0) for r in righe})
    operazioni.esegui_in_attesa(c, s)
    assert giacenza(c, "demo-porto", 1) == 3 and T.carica(c, tid)[0]["stato"] == "ricevuto"


def test_senza_spunta_lo_specchio_non_si_tocca(base):
    c, s = base
    sincronizza(c, s, DEMO)
    da_shopify.decidi(c, DEMO, True, "mario", "magazziniere", "demo-centro", "1", "porto", False, True)
    operazioni.esegui_in_attesa(c, s)
    assert giacenza(c, "demo-web", 1) == 10 and giacenza(c, "demo-porto", 1) == 3


def test_uscita_senza_destinazione_scala_solo_gli_specchi(base):
    c, s = base
    sincronizza(c, s, DEMO)
    tid, ids = da_shopify.decidi(c, DEMO, True, "mario", "magazziniere", "demo-centro", "1", "uscita", True, True, "reso al fornitore")
    operazioni.esegui_in_attesa(c, s)
    assert tid is None and len(ids) == 2
    assert (giacenza(c, "demo-centro", 1), giacenza(c, "demo-web", 1), giacenza(c, "demo-porto", 1)) == (7, 7, 0)
    assert c.execute("SELECT documento FROM operazioni LIMIT 1").fetchone()[0] == "logistics://magazzino/shopify/demo-centro/1"


def test_gestito_fuori_solo_responsabile_e_senza_scritture(base):
    c, s = base
    sincronizza(c, s, DEMO)
    with pytest.raises(T.ErroreFlusso, match="Solo il responsabile"):
        da_shopify.decidi(c, DEMO, True, "mario", "magazziniere", "demo-centro", "1", "gestito_fuori", False, False, "script")
    with pytest.raises(T.ErroreFlusso, match="Scrivi come"):
        da_shopify.decidi(c, DEMO, True, "anna", "responsabile", "demo-centro", "1", "gestito_fuori", False, False, "")
    da_shopify.decidi(c, DEMO, True, "anna", "responsabile", "demo-centro", "1", "gestito_fuori", False, False, "caricato con lo script")
    assert c.execute("SELECT COUNT(*) FROM operazioni").fetchone()[0] == 0
    assert c.execute("SELECT esito FROM trasferimenti_shopify").fetchone()[0] == "gestito_fuori"


def test_scritture_spente_e_origine_come_destinazione(base):
    c, s = base
    sincronizza(c, s, DEMO)
    with pytest.raises(T.ErroreFlusso, match="disattivate"):
        da_shopify.decidi(c, DEMO, False, "mario", "magazziniere", "demo-centro", "1", "porto", True, True)
    with pytest.raises(T.ErroreFlusso, match="diverso"):
        da_shopify.decidi(c, DEMO, True, "mario", "magazziniere", "demo-centro", "1", "centro", True, True)
    assert c.execute("SELECT esito FROM trasferimenti_shopify").fetchone()[0] == "da_destinare"   # niente è cambiato


def test_righe_senza_corrispondenza_segnalate(base):
    c, s = base
    trasferimento_shopify(c, "demo-centro", 7, [(1, 1), (3, 2)])
    t = c.execute("SELECT righe FROM demo_trasferimenti WHERE nome='#T7'").fetchone()[0]
    righe = json.loads(t) + [{"line_id": "demo-centro/tl7-99", "titolo": "Articolo sconosciuto", "item_id": "demo-centro/i99",
                              "variant_id": "demo-centro/v99", "sku": "", "ean": "", "totale": 1, "spedita": 1}]
    c.execute("UPDATE demo_trasferimenti SET righe=? WHERE nome='#T7'", (json.dumps(righe),))
    sincronizza(c, s, DEMO)
    tid, _ = da_shopify.decidi(c, DEMO, True, "mario", "magazziniere", "demo-centro", "7", "porto", True, True)
    # la variante che il programma non conosce: fuori dal trasferimento, da sistemare a mano
    assert c.execute("SELECT COUNT(*) FROM anomalie WHERE dedup LIKE 'shopify_riga:%' AND stato='aperta'").fetchone()[0] == 1
    assert sum(r["spedita"] for r in T.carica(c, tid)[1]) == 3
    # l'articolo senza EAN non si abbina ne' al Porto ne' al sito: carico e scarico non si scrivono e vengono segnalati
    assert c.execute("SELECT COUNT(*) FROM anomalie WHERE titolo LIKE 'Da aggiornare a mano%' AND stato='aperta'").fetchone()[0] == 2
    operazioni.esegui_in_attesa(c, s)
    assert giacenza(c, "demo-porto", 1) == 1 and giacenza(c, "demo-web", 1) == 9 and giacenza(c, "demo-web", 3) == 4


def test_decisione_su_righe_cambiate_nel_frattempo(base):
    """Una lettura da Shopify arriva fra l'apertura della pagina e la conferma: la decisione non passa sui numeri vecchi."""
    c, s = base
    sincronizza(c, s, DEMO)

    class LetturaNelFrattempo:
        """Subito dopo aver letto il movimento, la lettura periodica salva altri pezzi spediti (ora 6 invece di 4)."""
        def __init__(self):
            self.fatto = False

        def execute(self, sql, *parametri):
            r = c.execute(sql, *parametri)
            if not self.fatto and sql.startswith("SELECT * FROM trasferimenti_shopify"):
                self.fatto = True
                riga = r.fetchone()
                righe = json.loads(riga["righe"]) + [{"line_id": "x", "titolo": "Articolo 2", "item_id": "demo-centro/i2",
                                                      "variant_id": "demo-centro/v2", "sku": "SKU-2", "ean": "", "totale": 2,
                                                      "spedita": 2, "quantita": 2}]
                c.execute("UPDATE trasferimenti_shopify SET righe=?, pezzi=6 WHERE nome='#T1'", (json.dumps(righe),))
                return type("Una", (), {"fetchone": lambda _: riga})()
            return r

    with pytest.raises(T.ErroreFlusso, match="è cambiato mentre decidevi"):
        da_shopify.decidi(LetturaNelFrattempo(), DEMO, True, "mario", "magazziniere", "demo-centro", "1", "porto", True, True)
    assert c.execute("SELECT esito FROM trasferimenti_shopify WHERE nome='#T1'").fetchone()[0] == "da_destinare"
    assert not c.execute("SELECT COUNT(*) FROM operazioni").fetchone()[0]


def test_anomalie_di_seguito_visibili_fra_le_anomalie(base):
    """Un movimento cambiato dopo la decisione va sistemato a mano: deve comparire fra le anomalie e nel «Da fare»."""
    from magazzino import compiti
    c, s = base
    sincronizza(c, s, DEMO)
    da_shopify.decidi(c, DEMO, True, "mario", "magazziniere", "demo-centro", "1", "porto", True, True)
    trasferimento_shopify(c, "demo-centro", 1, [(1, 3), (2, 2)], nota="per il porto")      # su Shopify un pezzo in più
    sincronizza(c, s, DEMO)
    a = c.execute("SELECT tipo, stato FROM anomalie WHERE dedup LIKE 'shopify_cambiato:%'").fetchone()
    assert tuple(a) == ("shopify_a_mano", "aperta")
    assert dict(compiti.anomalie(c)["per_tipo"]).get("shopify_a_mano") == 1
