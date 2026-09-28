"""Scritture su Shopify (negozi finti con la stessa semantica): idempotenza, esiti incerti, conflitti, successi parziali."""
from datetime import datetime, timedelta, timezone

import pytest

from magazzino import operazioni, trasferimenti as T
from magazzino.db import salva_impostazione
from magazzino.negozi import DEMO
from magazzino.sync import sincronizza

from conftest import giacenza, metti_varianti


@pytest.fixture
def base(vuoto):
    c, sorgente, cfg = vuoto
    metti_varianti(c, [("demo-centro", 1, "8000000000017", 10), ("demo-web", 1, "8000000000017", 10), ("demo-porto", 1, "8000000000017", 0)])
    sincronizza(c, sorgente, DEMO)
    tid = T.crea(c, DEMO, "mario", "centro", "porto", "manuale", [{"chiave": "E:8000000000017", "quantita": 3}])
    return c, sorgente, tid


def op(c, sorgente, tid, store, delta, chiave="k1"):
    loc = {"demo-centro": "loc-centro-cassa", "demo-web": "loc-centro-web", "demo-porto": "loc-porto"}[store]
    return operazioni.pianifica(c, chiave=chiave, tipo="scarico" if delta < 0 else "carico", negozio="centro", store=store,
                                variant_id=f"{store}/v1", item_id=f"{store}/i1", location_id=loc, delta=delta, trasferimento_id=tid)


def test_stessa_chiave_una_sola_operazione_e_un_solo_movimento(base):
    c, s, tid = base
    a = op(c, s, tid, "demo-centro", -3)
    b = op(c, s, tid, "demo-centro", -3)              # stesso movimento pianificato due volte (doppio clic, retry)
    assert a == b and c.execute("SELECT COUNT(*) FROM operazioni").fetchone()[0] == 1
    assert operazioni.esegui(c, s, a) == "fatta"
    assert operazioni.esegui(c, s, a) == "fatta"      # rieseguirla non muove altro
    operazioni.esegui_in_attesa(c, s)
    assert giacenza(c, "demo-centro", 1) == 7


def test_esito_incerto_si_ripete_con_la_stessa_chiave(base):
    c, s, tid = base
    salva_impostazione(c, "demo_incerte", 1)          # la scrittura passa ma la risposta si perde
    i = op(c, s, tid, "demo-centro", -3)
    assert operazioni.esegui(c, s, i) == "incerta"
    assert giacenza(c, "demo-centro", 1) == 7         # su "Shopify" il movimento c'e'
    chiave = c.execute("SELECT chiave_shopify FROM operazioni WHERE id=?", (i,)).fetchone()[0]
    assert c.execute("SELECT COUNT(*) FROM anomalie WHERE tipo='aggiornamento_fallito' AND stato='aperta'").fetchone()[0] == 1
    assert operazioni.esegui(c, s, i) == "fatta"      # ripetuta con la stessa chiave: nessun secondo scarico
    assert giacenza(c, "demo-centro", 1) == 7
    assert c.execute("SELECT chiave_shopify FROM operazioni WHERE id=?", (i,)).fetchone()[0] == chiave
    assert c.execute("SELECT COUNT(*) FROM anomalie WHERE tipo='aggiornamento_fallito' AND stato='aperta'").fetchone()[0] == 0


def test_interruzione_durante_la_scrittura(base):
    c, s, tid = base
    i = op(c, s, tid, "demo-centro", -3)
    c.execute("UPDATE operazioni SET stato='in_corso', chiave_shopify='k1-t1', change_from=10, tentativi=1 WHERE id=?", (i,))
    operazioni.esegui_in_attesa(c, s)                  # al riavvio: incerta -> stessa chiamata -> non era passata -> fatta
    assert c.execute("SELECT stato FROM operazioni WHERE id=?", (i,)).fetchone()[0] == "fatta"
    assert giacenza(c, "demo-centro", 1) == 7


def test_incerta_oltre_la_finestra_va_verificata_a_mano(base):
    c, s, tid = base
    i = op(c, s, tid, "demo-centro", -3)
    vecchio = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat(timespec="seconds")
    c.execute("UPDATE operazioni SET stato='incerta', chiave_shopify='k1-t1', change_from=10, aggiornata_il=? WHERE id=?", (vecchio, i))
    assert operazioni.esegui(c, s, i) == "da_verificare"
    assert giacenza(c, "demo-centro", 1) == 10        # nessuna scrittura alla cieca
    with pytest.raises(ValueError):
        operazioni.decidi(c, "anna", i, "applicata", "")        # serve la nota
    operazioni.decidi(c, "anna", i, "non_applicata", "nello storico Shopify non c'e'")
    assert operazioni.esegui(c, s, i) == "fatta"
    assert giacenza(c, "demo-centro", 1) == 7


def test_vendita_durante_la_scrittura_rilegge_e_riprova(base):
    c, s, tid = base
    salva_impostazione(c, "demo_vendita", True)       # fra lettura e scrittura qualcuno vende un pezzo
    i = op(c, s, tid, "demo-centro", -3)
    assert operazioni.esegui(c, s, i) == "fatta"
    assert giacenza(c, "demo-centro", 1) == 6         # 10 - 1 venduto - 3 scaricati: niente perso, niente doppio
    assert c.execute("SELECT tentativi FROM operazioni WHERE id=?", (i,)).fetchone()[0] == 2


def test_scarico_oltre_il_disponibile_non_parte(base):
    c, s, tid = base
    i = op(c, s, tid, "demo-centro", -30)
    assert operazioni.esegui(c, s, i) == "conflitto"
    assert giacenza(c, "demo-centro", 1) == 10


def test_successo_parziale_su_due_negozi(base):
    c, s, tid = base
    c.execute("UPDATE demo_varianti SET in_sede=0 WHERE store='demo-web'")      # sullo specchio la sede non e' attiva
    principale, specchio = op(c, s, tid, "demo-centro", -3, "a"), op(c, s, tid, "demo-web", -3, "b")
    esiti = operazioni.esegui_in_attesa(c, s)
    assert esiti == {principale: "fatta", specchio: "errore"}
    assert giacenza(c, "demo-centro", 1) == 7
    assert c.execute("SELECT COUNT(*) FROM anomalie WHERE dedup=? AND stato='aperta'", (f"operazione:{specchio}",)).fetchone()[0] == 1
    c.execute("UPDATE demo_varianti SET in_sede=1 WHERE store='demo-web'")
    operazioni.decidi(c, "anna", specchio, "riprova", "sede attivata")
    assert operazioni.esegui_in_attesa(c, s) == {specchio: "fatta"}
    assert giacenza(c, "demo-web", 1) == 7
    assert giacenza(c, "demo-centro", 1) == 7          # il principale non e' stato toccato una seconda volta


def test_chiave_riusata_con_dati_diversi_rifiutata(base):
    c, s, _ = base
    s.aggiusta("demo-centro", "loc-centro-cassa", "demo-centro/i1", -1, 10, "movement_created", "doc", "stessa")
    from magazzino.negozi import Rifiutata
    with pytest.raises(Rifiutata, match="IDEMPOTENCY"):
        s.aggiusta("demo-centro", "loc-centro-cassa", "demo-centro/i1", -2, 9, "movement_created", "doc", "stessa")
    assert giacenza(c, "demo-centro", 1) == 9
