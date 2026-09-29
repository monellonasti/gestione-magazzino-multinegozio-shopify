"""
Reintegro con periodi sovrapposti, trasferimento completo con ricezioni parziali e differenze, conflitti.
Negozi finti (SorgenteDemo): stesso motore della modalita' reale, cambia solo la sorgente dei dati.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from magazzino import calcoli, operazioni, regole, trasferimenti as T
from magazzino.negozi import DEMO
from magazzino.sync import sincronizza

from conftest import giacenza, metti_varianti

OGGI = date.today()


def vendita(c, store, riga, n, giorni_fa, pezzi, loc="loc-porto"):
    quando = (datetime.now(timezone.utc) - timedelta(days=giorni_fa)).isoformat(timespec="seconds")
    c.execute("INSERT INTO demo_venduto VALUES(?,?,?,?,?,?)", (store, riga, f"{store}/v{n}", loc, quando, pezzi))


def crea_da_piano(c, piano, tipo="reintegro", modo="venduto"):
    righe = [{"chiave": r["chiave"], "quantita": r["quantita"],
              "coperture": regole.assegna_copertura(r["vendite"], r["quantita"]) if modo == "venduto" else []}
             for r in piano if r["quantita"] > 0]
    return T.crea(c, DEMO, "mario", "centro", "porto", tipo, righe)


@pytest.fixture
def negozi(vuoto):
    c, sorgente, cfg = vuoto
    metti_varianti(c, [("demo-centro", 1, "0800000000011", 20), ("demo-web", 1, "0800000000011", 20), ("demo-porto", 1, "0800000000011", 0),
                       ("demo-centro", 2, "0800000000028", 5), ("demo-web", 2, "0800000000028", 5), ("demo-porto", 2, "0800000000028", 1)])
    vendita(c, "demo-porto", "r1", 1, 10, 2)
    vendita(c, "demo-porto", "r2", 1, 5, 1)
    vendita(c, "demo-porto", "r3", 1, 2, 1)
    vendita(c, "demo-porto", "r4", 1, 3, 5, loc="altra-sede")          # venduto da un'altra sede: non conta
    assert sincronizza(c, sorgente, DEMO) == {}
    return c, sorgente, cfg


def piano(c, dal, al, modo="venduto"):
    return {r["ean"]: r for r in calcoli.piano_reintegro(c, DEMO, modo, "centro", "porto", OGGI - timedelta(days=dal),
                                                          OGGI - timedelta(days=al))}


def test_ean_con_zeri_iniziali_abbinati(negozi):
    c, _, _ = negozi
    r = c.execute("SELECT chiave, ean, stato FROM articoli WHERE ean='0800000000011'").fetchone()
    assert r["chiave"] == "E:0800000000011" and r["stato"] == "abbinato"


def test_periodi_sovrapposti_non_contano_due_volte(negozi):
    c, _, _ = negozi
    p1 = piano(c, 12, 4)                                   # vendite di 10 e 5 giorni fa: 2 + 1
    assert p1["0800000000011"]["quantita"] == 3
    tid = crea_da_piano(c, [p1["0800000000011"]])
    p2 = piano(c, 7, 0)                                    # 5 giorni fa (gia' coperta) + 2 giorni fa
    assert p2["0800000000011"]["venduto"] == 2
    assert p2["0800000000011"]["gia_coperto"] == 1
    assert p2["0800000000011"]["quantita"] == 1
    t, _ = T.carica(c, tid)
    T.annulla(c, "anna", tid, t["versione"], "prova")      # annullato: le vendite tornano scoperte
    assert piano(c, 7, 0)["0800000000011"]["quantita"] == 2


def test_approvazione_ridotta_libera_le_vendite_piu_recenti(negozi):
    c, _, _ = negozi
    tid = crea_da_piano(c, [piano(c, 12, 4)["0800000000011"]])
    t, righe = T.carica(c, tid)
    T.approva(c, "anna", tid, t["versione"], {righe[0]["id"]: 2})
    # coperte le 2 unita' di 10 giorni fa; quella di 5 giorni fa torna scoperta
    assert piano(c, 7, 0)["0800000000011"]["quantita"] == 2


def test_trasferimenti_aperti_riducono_il_fabbisogno(negozi):
    c, _, _ = negozi
    T.crea(c, DEMO, "mario", "centro", "porto", "bilanciamento", [{"chiave": "E:0800000000011", "quantita": 2}])
    assert piano(c, 12, 0)["0800000000011"]["quantita"] == 2      # 4 venduti - 2 gia' in arrivo
    s = piano(c, 0, 0, modo="scorta")
    assert "0800000000011" not in s                               # 0 + 2 in arrivo >= minima 1


def test_scorta_minima_origine_rispettata(negozi):
    c, _, _ = negozi
    c.execute("INSERT INTO soglie VALUES('centro', 'E:0800000000028', 4, 6, 'anna', NULL)")
    c.execute("INSERT INTO soglie VALUES('porto', 'E:0800000000028', 3, 5, 'anna', NULL)")
    r = piano(c, 0, 0, modo="scorta")["0800000000028"]
    assert r["proposta"].fabbisogno == 4 and r["quantita"] == 1      # il Centro ha 5 e deve tenerne 4


def test_flusso_completo_con_ricezione_parziale(negozi):
    c, sorgente, _ = negozi
    tid = T.crea(c, DEMO, "mario", "centro", "porto", "reintegro",
                 [{"chiave": "E:0800000000011", "quantita": 4}, {"chiave": "E:0800000000028", "quantita": 3}])
    t, righe = T.carica(c, tid)
    r1, r2 = sorted(righe, key=lambda r: r["ean"])
    assert giacenza(c, "demo-centro", 1) == 20                     # la proposta non tocca le giacenze
    T.approva(c, "anna", tid, t["versione"], {})
    assert giacenza(c, "demo-centro", 1) == 20                     # l'approvazione prenota solo nel programma
    t, _ = T.carica(c, tid)
    T.inizia_preparazione(c, "mario", tid, t["versione"])
    T.preleva(c, "mario", tid, r1["id"], 4)
    T.preleva(c, "mario", tid, r2["id"], 2)
    t, _ = T.carica(c, tid)
    with pytest.raises(T.ErroreFlusso, match="motivo"):
        T.spedisci(c, DEMO, sorgente, True, "mario", tid, t["versione"], {})
    T.spedisci(c, DEMO, sorgente, True, "mario", tid, t["versione"], {}, "uno rotto a scaffale")
    operazioni.esegui_in_attesa(c, sorgente)
    assert (giacenza(c, "demo-centro", 1), giacenza(c, "demo-web", 1)) == (16, 16)    # scarico su principale e specchio
    assert (giacenza(c, "demo-centro", 2), giacenza(c, "demo-web", 2)) == (3, 3)
    assert giacenza(c, "demo-porto", 1) == 0                        # in viaggio: la destinazione non cambia ancora
    assert calcoli.in_corso(c)["E:0800000000011"]["in_viaggio"] == 4

    T.ricevi(c, DEMO, True, "lucia", tid, {r1["id"]: (2, 0)}, "primo collo")
    T.ricevi(c, DEMO, True, "lucia", tid, {r1["id"]: (1, 1), r2["id"]: (1, 0)}, "secondo collo")
    with pytest.raises(T.ErroreFlusso, match="ne manca 1, ne hai indicati 2"):
        T.ricevi(c, DEMO, True, "lucia", tid, {r2["id"]: (2, 0)})    # piu' di quanto spedito
    operazioni.esegui_in_attesa(c, sorgente)
    assert giacenza(c, "demo-porto", 1) == 3                        # 2 + 1 buoni; il danneggiato non si carica
    assert giacenza(c, "demo-porto", 2) == 2
    assert T.carica(c, tid)[0]["stato"] == "spedito"               # manca ancora un pezzo della riga 2

    T.chiudi_ricezione(c, "lucia", tid, "il secondo pezzo non c'era nel collo")
    t, righe = T.carica(c, tid)
    assert t["stato"] == "ricevuto"
    assert len(T.differenze_aperte(righe, t["stato"])) == 2
    assert c.execute("SELECT COUNT(*) FROM anomalie WHERE tipo='differenza_ricezione' AND stato='aperta'").fetchone()[0] == 2
    T.chiudi_differenza(c, DEMO, True, "anna", r2["id"], "rientrata_origine", "ritrovato in negozio")
    T.chiudi_differenza(c, DEMO, True, "anna", r1["id"], "danneggiata_scartata", "vetro rotto")
    operazioni.esegui_in_attesa(c, sorgente)
    assert (giacenza(c, "demo-centro", 2), giacenza(c, "demo-web", 2)) == (4, 4)
    t, righe = T.carica(c, tid)
    assert T.differenze_aperte(righe, t["stato"]) == []
    assert c.execute("SELECT COUNT(*) FROM anomalie WHERE tipo='differenza_ricezione' AND stato='aperta'").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM operazioni WHERE stato!='fatta'").fetchone()[0] == 0


def test_non_si_prenota_piu_del_libero(negozi):
    c, _, _ = negozi
    a = T.crea(c, DEMO, "mario", "centro", "porto", "manuale", [{"chiave": "E:0800000000028", "quantita": 4}])
    b = T.crea(c, DEMO, "mario", "centro", "porto", "manuale", [{"chiave": "E:0800000000028", "quantita": 3}])
    T.approva(c, "anna", a, T.carica(c, a)[0]["versione"], {})
    with pytest.raises(T.ErroreFlusso, match="libere all'origine") as e:
        T.approva(c, "anna", b, T.carica(c, b)[0]["versione"], {})
    assert "richiesti 3, liberi all'origine 1" in e.value.dettagli[0]


def test_vendita_durante_la_preparazione_blocca_la_spedizione(negozi):
    c, sorgente, _ = negozi
    tid = T.crea(c, DEMO, "mario", "centro", "porto", "manuale", [{"chiave": "E:0800000000028", "quantita": 4}])
    T.approva(c, "anna", tid, T.carica(c, tid)[0]["versione"], {})
    T.inizia_preparazione(c, "mario", tid, T.carica(c, tid)[0]["versione"])
    righe = T.carica(c, tid)[1]
    T.preleva(c, "mario", tid, righe[0]["id"], 4)
    t = T.carica(c, tid)[0]
    c.execute("UPDATE demo_varianti SET disponibile=2 WHERE variant_id='demo-centro/v2'")   # venduti 3 in cassa
    with pytest.raises(T.ErroreFlusso, match="cambiate") as e:
        T.spedisci(c, DEMO, sorgente, True, "mario", tid, t["versione"], {})
    assert "disponibili ora 2" in e.value.dettagli[0]
    assert T.carica(c, tid)[0]["stato"] == "in_preparazione"
    assert c.execute("SELECT COUNT(*) FROM operazioni").fetchone()[0] == 0


def test_versione_e_stati(negozi):
    c, sorgente, _ = negozi
    tid = T.crea(c, DEMO, "mario", "centro", "porto", "manuale", [{"chiave": "E:0800000000011", "quantita": 1}])
    v = T.carica(c, tid)[0]["versione"]
    T.approva(c, "anna", tid, v, {})
    with pytest.raises(T.ErroreFlusso):
        T.approva(c, "anna", tid, v, {})                            # doppio clic: gia' approvato
    with pytest.raises(T.ErroreFlusso, match="modificato"):
        T.inizia_preparazione(c, "mario", tid, v)                   # versione vecchia
    T.inizia_preparazione(c, "mario", tid, v + 1)
    T.preleva(c, "mario", tid, T.carica(c, tid)[1][0]["id"], 1)
    with pytest.raises(T.ErroreFlusso, match="modificato"):
        T.spedisci(c, DEMO, sorgente, True, "mario", tid, v + 2, {})    # un pezzo preso dopo l'anteprima: la conferma non vale più
    T.spedisci(c, DEMO, sorgente, True, "mario", tid, v + 3, {})
    with pytest.raises(T.ErroreFlusso, match="spedito"):
        T.annulla(c, "anna", tid, T.carica(c, tid)[0]["versione"], "troppo tardi")   # dopo la spedizione non si annulla


def test_scritture_disattivate_bloccano_spedizione(negozi):
    c, sorgente, _ = negozi
    tid = T.crea(c, DEMO, "mario", "centro", "porto", "manuale", [{"chiave": "E:0800000000011", "quantita": 1}])
    T.approva(c, "anna", tid, 1, {})
    T.inizia_preparazione(c, "mario", tid, 2)
    with pytest.raises(T.ErroreFlusso, match="disattivate"):
        T.spedisci(c, DEMO, sorgente, False, "mario", tid, 3, {1: 1})


def test_due_tablet_registrano_lo_stesso_arrivo(negozi, monkeypatch):
    """Due arrivi registrati insieme su righe viste prima dell'altro: niente pezzi ricevuti oltre lo spedito."""
    c, sorgente, _ = negozi
    tid = T.crea(c, DEMO, "mario", "centro", "porto", "manuale", [{"chiave": "E:0800000000011", "quantita": 5}])
    t, _ = T.carica(c, tid)
    T.approva(c, "anna", tid, t["versione"], {})
    t, (r,) = T.carica(c, tid)
    T.inizia_preparazione(c, "mario", tid, t["versione"])
    T.preleva(c, "mario", tid, r["id"], 5)
    t, _ = T.carica(c, tid)
    T.spedisci(c, DEMO, sorgente, True, "mario", tid, t["versione"], {})
    vista_dal_secondo = T.carica(c, tid)                             # il secondo tablet ha la pagina aperta da prima
    T.ricevi(c, DEMO, True, "lucia", tid, {r["id"]: (3, 0)}, "primo tablet")
    monkeypatch.setattr(T, "carica", lambda c_, t_: vista_dal_secondo)
    with pytest.raises(T.ErroreFlusso, match="nel frattempo"):
        T.ricevi(c, DEMO, True, "paolo", tid, {r["id"]: (3, 0)}, "secondo tablet")
    monkeypatch.undo()
    riga = T.carica(c, tid)[1][0]
    assert (riga["ricevuta"], riga["spedita"]) == (3, 5)
    assert c.execute("SELECT COUNT(*) FROM operazioni WHERE tipo='carico'").fetchone()[0] == 1   # un solo carico su Porto


def test_differenza_chiusa_da_due_pagine(negozi):
    c, sorgente, _ = negozi
    tid = T.crea(c, DEMO, "mario", "centro", "porto", "manuale", [{"chiave": "E:0800000000011", "quantita": 2}])
    t, _ = T.carica(c, tid)
    T.approva(c, "anna", tid, t["versione"], {})
    t, (r,) = T.carica(c, tid)
    T.inizia_preparazione(c, "mario", tid, t["versione"])
    T.preleva(c, "mario", tid, r["id"], 2)
    t, _ = T.carica(c, tid)
    T.spedisci(c, DEMO, sorgente, True, "mario", tid, t["versione"], {})
    T.chiudi_ricezione(c, "lucia", tid, "non è arrivato niente")
    vista = c.execute("SELECT * FROM righe WHERE id=?", (r["id"],)).fetchone()        # la seconda pagina la vede aperta
    T.chiudi_differenza(c, DEMO, True, "anna", r["id"], "arrivata_dopo", "arrivato il giorno dopo")

    class ConnessioneDellAltraPagina:
        """La prima lettura della riga restituisce quella vista prima; tutto il resto è il database vero."""
        def __init__(self):
            self.prima = True

        def execute(self, sql, *parametri):
            if self.prima and sql.startswith("SELECT * FROM righe WHERE id=?"):
                self.prima = False
                return type("Una", (), {"fetchone": lambda _: vista})()
            return c.execute(sql, *parametri)

    with pytest.raises(T.ErroreFlusso, match="già chiusa nel frattempo"):
        T.chiudi_differenza(ConnessioneDellAltraPagina(), DEMO, True, "anna", r["id"], "rientrata_origine", "ritrovato")
    assert c.execute("SELECT COUNT(*) FROM operazioni WHERE tipo IN ('carico','rientro')").fetchone()[0] == 1


def test_lettura_lenta_non_cancella_una_ricezione_appena_scritta(negozi):
    """La lettura periodica fotografa Porto a 0, intanto una ricezione carica 4 pezzi: il dato salvato deve essere 4."""
    from magazzino.db import connetti
    c, sorgente, cfg = negozi
    tid = T.crea(c, DEMO, "mario", "centro", "porto", "manuale", [{"chiave": "E:0800000000011", "quantita": 4}])
    t, _ = T.carica(c, tid)
    T.approva(c, "anna", tid, t["versione"], {})
    t, (r,) = T.carica(c, tid)
    T.inizia_preparazione(c, "mario", tid, t["versione"])
    T.preleva(c, "mario", tid, r["id"], 4)
    t, _ = T.carica(c, tid)
    T.spedisci(c, DEMO, sorgente, True, "mario", tid, t["versione"], {})
    operazioni.esegui_in_attesa(c, sorgente)

    class LetturaLenta:
        """Legge Porto, poi (mentre la lettura è ancora in corso) il programma registra l'arrivo e lo scrive su Shopify."""
        def __init__(self):
            self.fatto = False

        def leggi_varianti(self, store, loc, avanzamento=None):
            righe = sorgente.leggi_varianti(store, loc, avanzamento)
            if store == "demo-porto" and not self.fatto:
                self.fatto = True
                altra = connetti(cfg.db_path)
                T.ricevi(altra, DEMO, True, "lucia", tid, {r["id"]: (4, 0)})
                operazioni.esegui_in_attesa(altra, sorgente)
                altra.close()
            return righe

        def __getattr__(self, nome):
            return getattr(sorgente, nome)

    assert sincronizza(c, LetturaLenta(), DEMO) == {}
    assert giacenza(c, "demo-porto", 1) == 4                                        # su "Shopify"
    assert c.execute("SELECT disponibile FROM varianti WHERE store='demo-porto' AND variant_id='demo-porto/v1'").fetchone()[0] == 4


def test_due_liste_create_insieme_non_coprono_due_volte(negozi):
    """Due pagine calcolano lo stesso reintegro e premono «Crea bozza» insieme: la seconda bozza non nasce."""
    c, _, _ = negozi
    p = list(piano(c, 14, 0).values())
    crea_da_piano(c, p)                                             # la prima pagina
    with pytest.raises(T.ErroreFlusso, match="già coperto"):
        crea_da_piano(c, p)                                         # la seconda, con i numeri calcolati prima
    assert c.execute("SELECT COUNT(*) FROM trasferimenti").fetchone()[0] == 1
