"""Pagine: accesso, permessi dei ruoli, niente dati commerciali al magazziniere, doppio invio, segreti."""
import csv, io, json, re

import pytest
from fastapi.testclient import TestClient

from magazzino import auth
from magazzino.config import Config
from magazzino.db import connetti
from magazzino.web import crea_app


@pytest.fixture
def app(demo):
    _, _, cfg = demo
    return crea_app(cfg, sfondo=False)


def entra(app, utente):
    cl = TestClient(app, follow_redirects=False)
    r = cl.post("/login", data={"username": utente, "password": "demo1234", "dopo": "/"})
    assert r.status_code == 303, r.text
    return cl


def gettone(html):
    return re.search(r'name="gettone" value="([^"]+)"', html).group(1)


def versione(html, dopo=""):
    """La versione del trasferimento scritta nel modulo (dopo `dopo`, per esempio 'id="conferma"')."""
    return re.search(re.escape(dopo) + r'.*?name="versione" value="(\d+)"', html, re.S).group(1)


REINTEGRO = "/reintegro?calcola=1&origine=centro&destinazione=porto&modo=venduto"


def periodo():
    from datetime import date, timedelta
    return f"&dal={(date.today() - timedelta(days=40)).isoformat()}&al={date.today().isoformat()}"


def test_serve_accedere(app):
    cl = TestClient(app, follow_redirects=False)
    assert cl.get("/inventario").status_code == 303
    assert cl.post("/login", data={"username": "responsabile", "password": "sbagliata"}).status_code == 401


def test_magazziniere_non_entra_nelle_pagine_del_responsabile(app):
    cl = entra(app, "magazziniere")
    assert cl.get("/").status_code == 200
    for url in ("/impostazioni", "/storico"):
        assert cl.get(url).status_code == 403
    pagina = cl.get("/trasferimenti/4").text                     # bozza: niente pulsante approva per lui
    assert "/trasferimenti/4/approva" not in pagina
    r = cl.post("/trasferimenti/4/approva", data={"gettone": gettone(pagina), "versione": "1"})
    assert r.status_code == 403


def test_niente_vendite_al_magazziniere(app):
    resp = entra(app, "responsabile").get(REINTEGRO + periodo()).text
    mag = entra(app, "magazziniere").get(REINTEGRO + periodo()).text
    assert "Da prelevare" in mag and "Da prelevare" in resp
    assert "Venduto" in resp and "Già rimandato" in resp
    for parola in ("Venduto", "rimandato", "Disp. ", "prezzo", "€"):
        assert parola not in mag, parola
    csv_mag = entra(app, "magazziniere").get(REINTEGRO.replace("/reintegro?", "/reintegro/export.csv?") + periodo()).content.decode("utf-8-sig")
    assert "Venduto" not in csv_mag and "Già coperto" not in csv_mag
    csv_resp = entra(app, "responsabile").get(REINTEGRO.replace("/reintegro?", "/reintegro/export.csv?") + periodo()).content.decode("utf-8-sig")
    assert "Venduto nel periodo" in csv_resp


def test_doppio_invio_crea_un_solo_trasferimento(app, demo):
    c = demo[0]
    cl = entra(app, "magazziniere")
    pagina = cl.get(REINTEGRO + periodo()).text
    g = gettone(pagina)
    chiavi = re.findall(r'name="chiave" value="([^"]+)"', pagina)
    assert chiavi
    dati = {"gettone": g, "origine": "centro", "destinazione": "porto", "modo": "venduto", "chiave": chiavi,
            "dal": re.search(r'name="dal" value="([^"]+)"', pagina).group(1), "al": re.search(r'name="al" value="([^"]+)"', pagina).group(1)}
    prima = c.execute("SELECT COUNT(*) FROM trasferimenti").fetchone()[0]
    r1 = cl.post("/reintegro/crea", data=dati)
    r2 = cl.post("/reintegro/crea", data=dati)
    assert r1.status_code == r2.status_code == 303
    assert "gi%C3%A0+eseguita" in r2.headers["location"] or "eseguita" in r2.headers["location"]
    assert c.execute("SELECT COUNT(*) FROM trasferimenti").fetchone()[0] == prima + 1


def test_gettone_falso_rifiutato(app):
    cl = entra(app, "responsabile")
    assert cl.post("/trasferimenti/4/annulla", data={"gettone": "abc.def", "versione": "1", "motivo": "x"}).status_code == 403


def test_export_inventario_contiene_tutti_i_risultati(app, demo):
    c = demo[0]
    cl = entra(app, "magazziniere")
    totale = c.execute("SELECT COUNT(*) FROM articoli").fetchone()[0]
    assert totale > 50                                            # piu' di una pagina
    testo = cl.get("/inventario/export.csv").content.decode("utf-8-sig")
    righe = list(csv.reader(io.StringIO(testo), delimiter=";"))
    intestazione = next(i for i, r in enumerate(righe) if r and r[0] == "Prodotto")
    assert len(righe) - intestazione - 1 == totale


def test_flusso_dal_browser(app, demo):
    """Approvazione, preparazione con scanner, spedizione con conferma, ricezione: come dalle pagine."""
    c = demo[0]
    resp, mag = entra(app, "responsabile"), entra(app, "magazziniere")
    tid = 4                                                        # la bozza della demo
    pagina = resp.get(f"/trasferimenti/{tid}").text
    r = resp.post(f"/trasferimenti/{tid}/approva", data={"gettone": gettone(pagina), "versione": "1"})
    assert r.status_code == 303 and "Approvato" in resp.get(r.headers["location"]).text
    pagina = mag.get(f"/trasferimenti/{tid}").text
    assert mag.post(f"/trasferimenti/{tid}/prepara", data={"gettone": gettone(pagina), "versione": "2"}).status_code == 303
    righe = c.execute("SELECT id, ean, approvata FROM righe WHERE trasferimento_id=?", (tid,)).fetchall()
    pagina = mag.get(f"/trasferimenti/{tid}").text
    for rg in righe:
        for _ in range(rg["approvata"]):
            assert mag.post(f"/trasferimenti/{tid}/scansiona", data={"gettone": gettone(pagina), "codice": rg["ean"]}).status_code == 303
    assert all(x[0] == x[1] for x in c.execute("SELECT prelevata, approvata FROM righe WHERE trasferimento_id=?", (tid,)))
    pagina = mag.get(f"/trasferimenti/{tid}").text
    anteprima = mag.post(f"/trasferimenti/{tid}/spedisci", data={"gettone": gettone(pagina), "versione": versione(pagina)})
    assert anteprima.status_code == 200 and "Conferma e aggiorna Shopify" in anteprima.text
    assert c.execute("SELECT stato FROM trasferimenti WHERE id=?", (tid,)).fetchone()[0] == "in_preparazione"   # solo anteprima
    g = re.search(r'id="conferma".*?name="gettone" value="([^"]+)"', anteprima.text, re.S).group(1)
    dati = dict(re.findall(r'<input type="hidden" name="(s_\d+)" value="(\d+)"', anteprima.text))
    r = mag.post(f"/trasferimenti/{tid}/spedisci", data={"gettone": g, "versione": versione(anteprima.text, 'id="conferma"'),
                                                         "conferma": "1", **dati})
    assert r.status_code == 303
    assert c.execute("SELECT stato FROM trasferimenti WHERE id=?", (tid,)).fetchone()[0] == "spedito"
    import time
    for _ in range(50):                                            # scrittura in sottofondo
        if not c.execute("SELECT COUNT(*) FROM operazioni WHERE trasferimento_id=? AND stato!='fatta'", (tid,)).fetchone()[0]:
            break
        time.sleep(0.1)
    assert c.execute("SELECT COUNT(*) FROM operazioni WHERE trasferimento_id=? AND stato='fatta'", (tid,)).fetchone()[0] == len(righe)


def test_segreti_mai_nelle_pagine(tmp_path):
    """Modalita' reale (negozi dell'esempio) con credenziali finte: nessun segreto finisce nell'HTML."""
    from magazzino.config import BASE, chiave_env
    segreto, chiave = "shpss_SEGRETO_DI_PROVA_123", "id_cliente_di_prova"
    env = {}
    for store, dominio in (("a_cassa", "negozio-a"), ("a_online", "negozio-a-online"), ("b_cassa", "negozio-b")):
        f = tmp_path / f"{store}.env"
        f.write_text(f"SHOPIFY_SHOP={dominio}.myshopify.com\nSHOPIFY_CLIENT_ID={chiave}\nSHOPIFY_CLIENT_SECRET={segreto}\n", encoding="utf-8")
        env[chiave_env(store)] = str(f)
    cfg = Config(modo="reale", db_file=tmp_path / "reale.db", env_negozi=env, negozi_file=BASE / "negozi.example.json")
    app = crea_app(cfg, sfondo=False)
    c = connetti(cfg.db_path)
    auth.crea_utente(c, "capo", "Capo", "responsabile", "password-lunga")
    cl = TestClient(app, follow_redirects=False)
    cl.post("/login", data={"username": "capo", "password": "password-lunga"})
    for url in ("/", "/impostazioni", "/inventario", "/storico"):
        html = cl.get(url).text
        assert segreto not in html and chiave not in html, url
    assert "Scritture sulle giacenze Shopify: <strong>disattivate</strong>" in cl.get("/impostazioni").text


def test_tutte_le_pagine_rispondono(app, demo):
    c = demo[0]
    chiave = c.execute("SELECT chiave FROM articoli LIMIT 1").fetchone()[0]
    pagine = ["/", "/inventario", "/inventario?filtro=sotto_scorta", "/inventario?filtro=in_trasferimento",
              "/inventario?filtro=esauriti&negozio=porto", "/inventario?filtro=solo_uno", "/inventario?pagina=2",
              REINTEGRO + periodo(), "/reintegro?calcola=1&modo=scorta&origine=porto&destinazione=centro",
              "/bilanciamento?calcola=1", "/trasferimenti", "/trasferimenti?stato=aperti", "/trasferimenti?stato=ricevuto",
              "/anomalie", "/anomalie?stato=risolta", "/anomalie?tipo=ean_doppio", f"/articolo?chiave={chiave}",
              "/stato-sync", "/trasferimenti/shopify", "/trasferimenti/shopify?vista=decisi", "/trasferimenti/shopify?vista=altri",
              "/trasferimenti/shopify/demo-centro/101", "/trasferimenti/shopify/demo-web/103",
              "/trasferimenti/shopify/demo-centro/104", "/trasferimenti/shopify/demo-web/106", "/trasferimenti/shopify/demo-porto/105"] \
             + [f"/trasferimenti/{t}" for (t,) in c.execute("SELECT id FROM trasferimenti")] \
             + [f"/trasferimenti/{t}/stampa" for (t,) in c.execute("SELECT id FROM trasferimenti")]
    for ruolo in ("magazziniere", "responsabile"):
        cl = entra(app, ruolo)
        for url in pagine:
            r = cl.get(url)
            assert r.status_code == 200, (ruolo, url, r.status_code)
        for url in ("/storico", "/storico?op=tutte", "/impostazioni"):
            assert cl.get(url).status_code == (200 if ruolo == "responsabile" else 403), (ruolo, url)


def test_trasferimento_shopify_senza_destinazione_dalle_pagine(app, demo):
    c = demo[0]
    mag, resp = entra(app, "magazziniere"), entra(app, "responsabile")
    for url in ("/trasferimenti/shopify", "/trasferimenti/shopify?vista=decisi", "/trasferimenti/shopify?vista=altri"):
        assert mag.get(url).status_code == 200, url
    elenco = mag.get("/trasferimenti/shopify").text
    assert "#T0101" in elenco and "#T0102" in elenco and "#T0103" not in elenco      # il T0103 non è partito
    assert "Negozio Porto" in elenco                                                   # destinazione suggerita
    assert "Movimenti fatti su Shopify da sistemare" in mag.get("/").text               # compito nella pagina Da fare
    scheda = mag.get("/trasferimenti/shopify/demo-centro/101").text
    assert "suggerito" in scheda and "scatola per il Porto" in scheda
    assert "gestito_fuori" not in scheda                                                # opzione solo per il responsabile
    assert "gestito_fuori" in resp.get("/trasferimenti/shopify/demo-centro/101").text

    web_prima = {r[0]: r[1] for r in c.execute("SELECT variant_id, disponibile FROM demo_varianti WHERE store='demo-web'")}
    anteprima = mag.post("/trasferimenti/shopify/demo-centro/101",
                         data={"gettone": gettone(scheda), "scelta": "porto", "scala_specchi": "1", "subito": "1"})
    assert anteprima.status_code == 200 and "Conferma e aggiorna Shopify" in anteprima.text
    assert c.execute("SELECT esito FROM trasferimenti_shopify WHERE nome='#T0101'").fetchone()[0] == "da_destinare"
    g = re.search(r'id="conferma".*?name="gettone" value="([^"]+)"', anteprima.text, re.S).group(1)
    dati = {"gettone": g, "conferma": "1", "scelta": "porto", "scala_specchi": "1", "subito": "1", "nota": ""}
    r1 = mag.post("/trasferimenti/shopify/demo-centro/101", data=dati)
    r2 = mag.post("/trasferimenti/shopify/demo-centro/101", data=dati)                 # doppio clic
    assert r1.status_code == r2.status_code == 303 and "/trasferimenti/" in r1.headers["location"]
    assert c.execute("SELECT COUNT(*) FROM trasferimenti WHERE tipo='da Shopify'").fetchone()[0] == 1
    import time
    for _ in range(50):
        if not c.execute("SELECT COUNT(*) FROM operazioni WHERE stato IN ('da_fare','in_corso')").fetchone()[0]:
            break
        time.sleep(0.1)
    ts = json.loads(c.execute("SELECT righe FROM trasferimenti_shopify WHERE nome='#T0101'").fetchone()[0])
    for r in ts:                                            # il sito (specchio) scalato come la cassa
        vid = r["variant_id"].replace("demo-centro", "demo-web")
        assert c.execute("SELECT disponibile FROM demo_varianti WHERE variant_id=?", (vid,)).fetchone()[0] == web_prima[vid] - r["quantita"]
    # il magazziniere non può segnare "gestito fuori"
    scheda = mag.get("/trasferimenti/shopify/demo-porto/102").text
    r = mag.post("/trasferimenti/shopify/demo-porto/102", data={"gettone": gettone(scheda), "conferma": "1", "scelta": "gestito_fuori",
                                                                  "nota": "prova"})
    assert r.status_code == 409 and "Solo il responsabile" in r.text


def test_carico_da_fornitore_diviso_dalle_pagine(app, demo):
    c = demo[0]
    resp = entra(app, "responsabile")
    elenco = resp.get("/trasferimenti/shopify").text
    assert "Arrivo dal fornitore" in elenco and "possibile doppione di #T0104" in elenco
    scheda = resp.get("/trasferimenti/shopify/demo-web/106").text
    assert 'value="stessa_consegna" checked' in scheda                   # sul sito il doppione è suggerito come tale
    scheda = resp.get("/trasferimenti/shopify/demo-centro/104").text
    assert 'value="tutto" checked' in scheda and 'name="scala_specchi" value="1" >' in scheda   # doppione: niente 1:1 proposto
    dati = {"gettone": gettone(scheda), "modo": "dividi", "frazione": "1/2", "dispari": "b", "altro": "porto", "altro_subito": "1",
            "azione": "regola"}
    pagina = resp.post("/trasferimenti/shopify/demo-centro/104", data=dati).text
    quote = [int(q) for _, q in re.findall(r'name="b_(\d+)" value="(\d+)"', pagina)]
    arrivati = json.loads(c.execute("SELECT righe FROM trasferimenti_shopify WHERE nome='#T0104'").fetchone()[0])
    assert quote == [-(-r["quantita"] // 2) for r in arrivati]           # metà, il dispari all'altro negozio
    dati.update(gettone=gettone(pagina), azione="anteprima", b_0="0")    # la prima riga resta tutta al Centro
    for i, q in enumerate(quote[1:], 1):
        dati[f"b_{i}"] = str(q)
    anteprima = resp.post("/trasferimenti/shopify/demo-centro/104", data=dati)
    assert anteprima.status_code == 200 and "Conferma la ripartizione" in anteprima.text
    g = re.search(r'id="conferma".*?name="gettone" value="([^"]+)"', anteprima.text, re.S).group(1)
    conferma = re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', anteprima.text.split('id="conferma"')[1])
    campi = dict(conferma) | {"gettone": g}
    porto_prima = sum(r[0] for r in c.execute("SELECT disponibile FROM demo_varianti WHERE store='demo-porto'"))
    r1 = resp.post("/trasferimenti/shopify/demo-centro/104", data=campi)
    r2 = resp.post("/trasferimenti/shopify/demo-centro/104", data=campi)
    assert r1.status_code == r2.status_code == 303
    assert c.execute("SELECT COUNT(*) FROM trasferimenti WHERE tipo='ripartizione fornitore'").fetchone()[0] == 1
    import time
    for _ in range(50):
        if not c.execute("SELECT COUNT(*) FROM operazioni WHERE stato IN ('da_fare','in_corso')").fetchone()[0]:
            break
        time.sleep(0.1)
    porto_dopo = sum(r[0] for r in c.execute("SELECT disponibile FROM demo_varianti WHERE store='demo-porto'"))
    assert porto_dopo - porto_prima == sum(quote[1:])
    assert c.execute("SELECT esito FROM trasferimenti_shopify WHERE nome='#T0104'").fetchone()[0] == "diviso"
