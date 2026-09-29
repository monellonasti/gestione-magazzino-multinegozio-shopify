"""Pagine verificate nell'audit: numeri che non si perdono, anteprime ripetibili, ritorni giusti, moduli senza JavaScript."""
import re

import pytest
from fastapi.testclient import TestClient

from magazzino import trasferimenti as T
from magazzino.config import Config
from magazzino.web import crea_app


@pytest.fixture
def app(demo):
    _, _, cfg = demo
    return crea_app(cfg, sfondo=False)


def entra(app, utente):
    cl = TestClient(app, follow_redirects=False)
    assert cl.post("/login", data={"username": utente, "password": "demo1234", "dopo": "/"}).status_code == 303
    return cl


def gettone(html, dopo=""):
    return re.search(re.escape(dopo) + r'.*?name="gettone" value="([^"]+)"', html, re.S).group(1)


def spedito(c):
    """Il trasferimento della demo spedito e ricevuto in parte, con le righe che aspettano ancora pezzi."""
    tid = c.execute("SELECT id FROM trasferimenti WHERE stato='spedito'").fetchone()[0]
    righe = [r for r in T.carica(c, tid)[1] if (r["spedita"] or 0) - r["ricevuta"] - r["danneggiata"] > 0]
    return tid, righe


def test_filtro_delle_anomalie_resta_dopo_ignora(app, demo):
    # l'avviso aveva il parametro «tipo», lo stesso del filtro: si tornava al riepilogo e il modulo in blocco spariva
    c, _, _ = demo
    aid = c.execute("SELECT id FROM anomalie WHERE tipo='ean_mancante' AND stato='aperta' LIMIT 1").fetchone()[0]
    cl = entra(app, "responsabile")
    pagina = cl.get("/anomalie?tipo=ean_mancante").text
    r = cl.post(f"/anomalie/{aid}/ignora", data={"gettone": gettone(pagina), "nota": "buono regalo",
                                                "torna": "/anomalie?tipo=ean_mancante"})
    dopo = cl.get(r.headers["location"]).text
    assert "tipo=ean_mancante" in r.headers["location"] and 'id="blocco"' in dopo


def test_anteprima_ripetibile_e_numeri_che_restano(app, demo):
    c, _, _ = demo
    tid, righe = spedito(c)
    r0 = righe[0]
    cl = entra(app, "magazziniere")
    g = gettone(cl.get(f"/trasferimenti/{tid}").text, 'id="ricezione"')
    dati = {"gettone": g, f"b_{r0['id']}": "0", f"d_{r0['id']}": "1", "nota": "collo aperto"}
    prima = cl.post(f"/trasferimenti/{tid}/ricevi", data=dati)
    di_nuovo = cl.post(f"/trasferimenti/{tid}/ricevi", data=dati)        # ricaricare l'anteprima non è «già eseguita»
    assert prima.status_code == di_nuovo.status_code == 200 and 'id="t-conferma"' in di_nuovo.text
    # «Torna indietro»: il modulo con i numeri scritti, non «arrivato tutto» da capo
    indietro = cl.post(f"/trasferimenti/{tid}/ricevi", data={**dati, "conferma": "1", "indietro": "1"})
    assert indietro.status_code == 200 and 'id="t-conferma"' not in indietro.text
    assert re.search(rf'name="d_{r0["id"]}" value="1"', indietro.text) and 'value="collo aperto"' in indietro.text
    # un errore (più pezzi di quelli attesi) lascia nel modulo quello che si era scritto
    troppi = (r0["spedita"] or 0) - r0["ricevuta"] - r0["danneggiata"] + 1
    errore = cl.post(f"/trasferimenti/{tid}/ricevi", data={"gettone": g, f"b_{r0['id']}": str(troppi), "nota": "secondo collo"})
    assert errore.status_code == 409 and re.search(rf'name="b_{r0["id"]}" value="{troppi}"', errore.text)
    assert 'value="secondo collo"' in errore.text


def test_dopo_la_sessione_scaduta_si_torna_alla_pagina_giusta(app):
    from urllib.parse import parse_qs, urlsplit
    dopo = lambda r: parse_qs(urlsplit(r.headers["location"]).query)["dopo"][0]
    cl = TestClient(app, follow_redirects=False)
    r = cl.get("/articolo?chiave=E:123")                                # una pagina con i suoi parametri
    assert parse_qs(urlsplit(dopo(r)).query) == {"chiave": ["E:123"]} and dopo(r).startswith("/articolo?")
    r = cl.post("/trasferimenti/4/modifica", data={}, headers={"referer": "http://testserver/trasferimenti/4?msg=x&tono=ok"})
    assert r.headers["location"] == "/login?dopo=%2Ftrasferimenti%2F4"        # non l'indirizzo del modulo (405)
    r = cl.post("/trasferimenti/4/modifica", data={}, headers={"referer": "https://altro.example/pagina"})
    assert r.headers["location"] == "/login?dopo=%2F"


def test_collega_a_mano_senza_javascript_usa_la_variante_scelta(app, demo):
    # senza JavaScript i campi nascosti restavano sulla prima variante: si collegava quella sbagliata
    c, _, _ = demo
    chiave = c.execute("""SELECT chiave FROM articolo_canali GROUP BY chiave HAVING COUNT(*) >= 2 LIMIT 1""").fetchone()[0]
    canali = c.execute("SELECT store, variant_id FROM articolo_canali WHERE chiave=? ORDER BY store", (chiave,)).fetchall()
    scelta, altra = canali[-1], canali[0]
    dest = c.execute("""SELECT chiave FROM articoli WHERE chiave != ? AND chiave NOT IN
                        (SELECT chiave FROM articolo_canali WHERE store=?) LIMIT 1""", (chiave, scelta["store"])).fetchone()[0]
    cl = entra(app, "responsabile")
    pagina = cl.get(f"/articolo?chiave={chiave}").text
    r = cl.post("/associazioni", data={"gettone": gettone(pagina, 'action="/associazioni" class="griglia-form"'), "chiave": chiave,
                                       "store_variante": f"{scelta['store']}|{scelta['variant_id']}",
                                       "store": altra["store"], "variant_id": altra["variant_id"],
                                       "destinazione": dest, "motivo": "stesso articolo"})
    assert r.status_code == 303
    salvata = c.execute("SELECT store, variant_id FROM associazioni WHERE motivo='stesso articolo'").fetchone()
    assert (salvata["store"], salvata["variant_id"]) == (scelta["store"], scelta["variant_id"])


def test_rettifica_con_il_conteggio_vuoto(app, demo):
    # il campo vuoto valeva 0: l'anteprima proponeva di azzerare la giacenza
    c, _, _ = demo
    chiave = c.execute("SELECT chiave FROM articolo_negozi WHERE negozio='centro' AND disponibile > 0 LIMIT 1").fetchone()[0]
    cl = entra(app, "responsabile")
    pagina = cl.get(f"/articolo?chiave={chiave}").text
    r = cl.post("/rettifiche", data={"gettone": gettone(pagina), "chiave": chiave, "negozio": "centro", "fonte": "conteggio",
                                     "contati": "", "motivo": "inventario"})
    assert r.status_code == 409 and "Scrivi quanti pezzi hai contato" in r.text
    assert not c.execute("SELECT COUNT(*) FROM rettifiche").fetchone()[0]


def test_la_pagina_si_ricarica_da_sola_solo_se_serve(demo, monkeypatch):
    c, _, cfg = demo
    tid = c.execute("SELECT trasferimento_id FROM operazioni WHERE trasferimento_id IS NOT NULL LIMIT 1").fetchone()[0]
    c.execute("UPDATE operazioni SET stato='da_fare' WHERE trasferimento_id=?", (tid,))
    cl = entra(crea_app(cfg, sfondo=False), "responsabile")
    assert "data-ricarica" in cl.get(f"/trasferimenti/{tid}").text               # aggiornamenti in corso: si aggiorna
    monkeypatch.setattr(Config, "scritture_attive", property(lambda self: False))
    cl = entra(crea_app(cfg, sfondo=False), "responsabile")
    pagina = cl.get(f"/trasferimenti/{tid}").text                               # scritture spente: in coda, nessun giro
    assert "data-ricarica" not in pagina and "restano in coda" in pagina


def test_conferme_e_stampe(app):
    cl = entra(app, "responsabile")
    bozza = cl.get("/trasferimenti/4").text
    assert 'id="approvazione">' in bozza and "data-totale-approva data-conferma=" in bozza    # «Salva» non chiede «Approvare?»
    reintegro = cl.get("/reintegro?calcola=1&modo=scorta&direzione=porto%3Ecentro").text
    assert "Stampa proposta" not in reintegro
