"""
Il restyling: pagine per ruolo, lettore senza ricaricare, anteprima che fissa le quantità, ricerca, anomalie in blocco.
Le regole di sicurezza restano quelle del server: qui si prova che le pagine nuove non le aggirano.
"""
import json, re

import pytest
from fastapi.testclient import TestClient

from magazzino import compiti
from magazzino.negozi import DEMO
from magazzino.web import crea_app


@pytest.fixture
def app(demo):
    return crea_app(demo[2], sfondo=False)


def entra(app, utente):
    cl = TestClient(app, follow_redirects=False)
    assert cl.post("/login", data={"username": utente, "password": "demo1234"}).status_code == 303
    return cl


def gettone(html, dopo=""):
    return re.search(re.escape(dopo) + r'.*?name="gettone" value="([^"]+)"', html, re.S).group(1)


def versione(html, dopo=""):
    return re.search(re.escape(dopo) + r'.*?name="versione" value="(\d+)"', html, re.S).group(1)


def in_preparazione(cl, tid):
    pagina = cl.get(f"/trasferimenti/{tid}").text
    assert cl.post(f"/trasferimenti/{tid}/prepara", data={"gettone": gettone(pagina), "versione": versione(pagina)}).status_code == 303
    return cl.get(f"/trasferimenti/{tid}").text


JSON = {"Accept": "application/json"}


def test_anteprima_della_spedizione_fissa_le_quantita(app, demo):
    """Un pezzo letto dopo l'anteprima rende vecchia la conferma: niente scritture diverse da quelle mostrate."""
    c = demo[0]
    resp, mag = entra(app, "responsabile"), entra(app, "magazziniere")
    tid = 4                                                                    # bozza della demo: 2 pezzi, un articolo
    pagina = resp.get(f"/trasferimenti/{tid}").text
    assert resp.post(f"/trasferimenti/{tid}/approva", data={"gettone": gettone(pagina), "versione": versione(pagina)}).status_code == 303
    pagina = in_preparazione(mag, tid)
    ean = c.execute("SELECT ean FROM righe WHERE trasferimento_id=?", (tid,)).fetchone()[0]
    assert mag.post(f"/trasferimenti/{tid}/scansiona", data={"gettone": gettone(pagina), "codice": ean}).status_code == 303

    pagina = mag.get(f"/trasferimenti/{tid}").text
    senza_motivo = mag.post(f"/trasferimenti/{tid}/spedisci", data={"gettone": gettone(pagina), "versione": versione(pagina)})
    assert senza_motivo.status_code == 409 and "scrivi perché" in senza_motivo.text     # incompleto: serve il motivo
    anteprima = mag.post(f"/trasferimenti/{tid}/spedisci",
                         data={"gettone": gettone(senza_motivo.text), "versione": versione(pagina), "motivo": "uno non trovato"})
    assert anteprima.status_code == 200 and 'id="conferma"' in anteprima.text
    fissate = dict(re.findall(r'<input type="hidden" name="(s_\d+)" value="(\d+)"', anteprima.text))
    assert list(fissate.values()) == ["1"]
    conferma = {"gettone": gettone(anteprima.text, 'id="conferma"'), "versione": versione(anteprima.text, 'id="conferma"'),
                "conferma": "1", "motivo": "uno non trovato", **fissate}

    # intanto qualcuno legge il secondo pezzo (altra scheda del browser, altro tablet)
    pagina = mag.get(f"/trasferimenti/{tid}").text
    assert mag.post(f"/trasferimenti/{tid}/scansiona", data={"gettone": gettone(pagina), "codice": ean}).status_code == 303
    vecchia = mag.post(f"/trasferimenti/{tid}/spedisci", data=conferma)
    assert vecchia.status_code == 409 and "modificato" in vecchia.text
    assert c.execute("SELECT stato FROM trasferimenti WHERE id=?", (tid,)).fetchone()[0] == "in_preparazione"
    assert c.execute("SELECT COUNT(*) FROM operazioni WHERE trasferimento_id=?", (tid,)).fetchone()[0] == 0

    # nuova anteprima, ora completa: si scrive esattamente quello che mostra
    pagina = mag.get(f"/trasferimenti/{tid}").text
    anteprima = mag.post(f"/trasferimenti/{tid}/spedisci", data={"gettone": gettone(pagina), "versione": versione(pagina)})
    fissate = dict(re.findall(r'<input type="hidden" name="(s_\d+)" value="(\d+)"', anteprima.text))
    assert list(fissate.values()) == ["2"]
    r = mag.post(f"/trasferimenti/{tid}/spedisci", data={"gettone": gettone(anteprima.text, 'id="conferma"'), "conferma": "1",
                                                         "versione": versione(anteprima.text, 'id="conferma"'), **fissate})
    assert r.status_code == 303
    origine = DEMO.negozio(c.execute("SELECT origine FROM trasferimenti WHERE id=?", (tid,)).fetchone()[0])
    principale = next(ch.store for ch in origine.canali if ch.principale)
    uscita = c.execute("SELECT -SUM(delta) FROM operazioni WHERE trasferimento_id=? AND store=?", (tid, principale)).fetchone()[0]
    assert uscita == 2


def test_lettura_senza_ricaricare_e_ripetuta_una_volta_sola(app, demo):
    c = demo[0]
    mag = entra(app, "magazziniere")
    tid = 3                                                                    # approvato nella demo
    pagina = in_preparazione(mag, tid)
    g = gettone(pagina)
    riga = c.execute("SELECT id, ean FROM righe WHERE trasferimento_id=? ORDER BY id LIMIT 1", (tid,)).fetchone()
    v0 = int(versione(pagina))

    r = mag.post(f"/trasferimenti/{tid}/scansiona", data={"gettone": g, "codice": riga["ean"], "id_lettura": "L1"}, headers=JSON)
    d = r.json()
    assert r.status_code == 200 and d["ok"] and d["riga"] == riga["id"] and d["prelevata"] == 1
    assert d["versione"] > v0                                                  # ogni lettura cambia la versione
    for parola in ("venduto", "prezzo", "ordine", "€"):                        # niente dati commerciali nella risposta
        assert parola not in r.text.lower(), parola

    # la rete cade e il browser ripete la stessa lettura: non si conta due volte
    r = mag.post(f"/trasferimenti/{tid}/scansiona", data={"gettone": g, "codice": riga["ean"], "id_lettura": "L1"}, headers=JSON)
    assert r.json()["messaggio"] == "Lettura già registrata"
    assert c.execute("SELECT prelevata FROM righe WHERE id=?", (riga["id"],)).fetchone()[0] == 1
    mag.post(f"/trasferimenti/{tid}/scansiona", data={"gettone": g, "codice": riga["ean"], "id_lettura": "L2"}, headers=JSON)
    assert c.execute("SELECT prelevata FROM righe WHERE id=?", (riga["id"],)).fetchone()[0] == 2

    r = mag.post(f"/trasferimenti/{tid}/scansiona", data={"gettone": g, "codice": "0000000000000", "id_lettura": "L3"}, headers=JSON)
    assert r.status_code == 409 and r.json()["tipo"] == "estraneo" and "Mettilo da parte" in r.json()["messaggio"]
    r = mag.post(f"/trasferimenti/{tid}/scansiona", data={"gettone": "falso.falso", "codice": riga["ean"]}, headers=JSON)
    assert r.status_code == 403 and not r.json()["ok"]

    # numero scritto a mano: si salva da solo, ma non oltre l'approvato
    approvata = c.execute("SELECT approvata FROM righe WHERE id=?", (riga["id"],)).fetchone()[0]
    r = mag.post(f"/trasferimenti/{tid}/preleva", data={"gettone": g, f"p_{riga['id']}": str(approvata + 1)}, headers=JSON)
    assert r.status_code == 409 and r.json()["righe"] == [riga["id"]]
    r = mag.post(f"/trasferimenti/{tid}/preleva", data={"gettone": g, f"p_{riga['id']}": "0"}, headers=JSON)
    assert r.json()["ok"] and r.json()["prelevata"] == 0


def test_cerca_da_qualunque_pagina(app, demo):
    c = demo[0]
    cl = entra(app, "magazziniere")
    assert cl.get("/cerca?q=TR-3").headers["location"] == "/trasferimenti/3"
    assert cl.get("/cerca?q=tr0003").headers["location"] == "/trasferimenti/3"
    ean = c.execute("SELECT ean FROM articoli WHERE ean IS NOT NULL GROUP BY ean HAVING COUNT(*) = 1 LIMIT 1").fetchone()[0]
    assert cl.get(f"/cerca?q={ean}").headers["location"].startswith("/articolo?chiave=")
    assert cl.get("/cerca?q=candela").headers["location"] == "/inventario?q=candela"


def test_menu_e_conteggi_per_ruolo(app, demo):
    c = demo[0]
    for ruolo in ("magazziniere", "responsabile"):
        home = entra(app, ruolo).get("/").text
        menu = re.search(r'<nav class="menu".*?</nav>', home, re.S).group(0)
        n = re.search(r'href="/"[^>]*>(?:(?!</a>).)*?class="conteggio[^"]*"[^>]*>(\d+)<', menu, re.S)
        totale = compiti.da_fare(c, DEMO, ruolo)["totale"]
        assert (int(n.group(1)) if n else 0) == totale, ruolo                  # il numero sul menu è quello della pagina
        for url in ("/anomalie", "/storico", "/impostazioni"):
            assert (f'href="{url}"' in menu) == (ruolo == "responsabile"), (ruolo, url)
    mag = entra(app, "magazziniere")
    inventario = mag.get("/inventario").text
    assert "Collegamento" not in inventario and 'href="/anomalie' not in inventario


def test_trasferimenti_aperti_e_prossimo_passo(app):
    cl = entra(app, "magazziniere")
    aperti = cl.get("/trasferimenti").text
    assert "TR-0004" in aperti and "Da approvare (responsabile)" in aperti and "Da ricevere a" in aperti
    tutti = cl.get("/trasferimenti?stato=tutti").text
    assert len(re.findall(r'href="/trasferimenti/\d+', tutti)) >= len(re.findall(r'href="/trasferimenti/\d+', aperti))
    assert "Fatti su Shopify" in aperti


def test_anomalie_ignorate_in_blocco(app, demo):
    c = demo[0]
    resp = entra(app, "responsabile")
    pagina = resp.get("/anomalie?tipo=solo_un_negozio").text
    ids = re.findall(r'name="ids" value="(\d+)"', pagina)[:3]
    assert len(ids) == 3
    blocco = gettone(pagina, 'id="blocco"')
    r = resp.post("/anomalie/ignora", data={"gettone": blocco, "ids": ids, "nota": "", "torna": "/anomalie?tipo=solo_un_negozio"})
    assert r.status_code == 303 and "Scrivi" in r.headers["location"]
    pagina = resp.get("/anomalie?tipo=solo_un_negozio").text
    r = resp.post("/anomalie/ignora", data={"gettone": gettone(pagina, 'id="blocco"'), "ids": ids, "nota": "assortimento diverso",
                                             "torna": "/anomalie?tipo=solo_un_negozio"})
    assert r.status_code == 303
    stati = c.execute(f"SELECT stato, nota FROM anomalie WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    assert all(s["stato"] == "ignorata" and s["nota"] == "assortimento diverso" for s in stati)
    assert c.execute("SELECT COUNT(*) FROM registro WHERE azione='anomalia ignorata'").fetchone()[0] == 3
    # il magazziniere non può
    mag = entra(app, "magazziniere")
    pagina = mag.get("/trasferimenti").text
    assert mag.post("/anomalie/ignora", data={"gettone": gettone(pagina), "ids": ids, "nota": "x"}).status_code == 403


def test_reintegro_ricorda_la_direzione(app):
    cl = entra(app, "magazziniere")
    r = cl.get("/reintegro?calcola=1&direzione=porto>centro&modo=scorta")
    assert r.status_code == 200
    assert re.search(r'value="porto&gt;centro" selected|value="porto>centro" selected', r.text)
    pagina = cl.get("/reintegro").text                                       # la volta dopo, sullo stesso dispositivo
    assert re.search(r'value="porto&gt;centro" selected|value="porto>centro" selected', pagina)
    assert "confirm(" not in pagina and "data-conferma" not in pagina       # niente finestre di conferma per creare la bozza


def test_data_senza_fuso():
    from magazzino.web import giorno
    assert giorno("2026-01-01") == "01/01/2026"
