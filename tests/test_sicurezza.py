"""Regole di sicurezza verificate nell'audit: interruttore delle scritture, limiti, righe di altri trasferimenti, accessi."""
import re, time

import pytest
from fastapi.testclient import TestClient

from magazzino import auth
from magazzino.config import Config
from magazzino.web import crea_app


@pytest.fixture
def app(demo):
    _, _, cfg = demo
    return crea_app(cfg, sfondo=False)


def entra(app, utente, password="demo1234"):
    cl = TestClient(app, follow_redirects=False)
    r = cl.post("/login", data={"username": utente, "password": password, "dopo": "/"})
    assert r.status_code == 303, r.text
    return cl


def gettone(html):
    return re.search(r'name="gettone" value="([^"]+)"', html).group(1)


def test_con_le_scritture_spente_nessun_aggiornamento_parte(demo, monkeypatch):
    # SCRITTURE_SHOPIFY=0 vale per ogni pulsante: «riprendi» e «Riprova» lasciano le operazioni in coda
    c, _, cfg = demo
    monkeypatch.setattr(Config, "scritture_attive", property(lambda self: False))
    app = crea_app(cfg, sfondo=False)
    op = c.execute("SELECT * FROM operazioni WHERE stato='fatta' AND trasferimento_id IS NOT NULL LIMIT 1").fetchone()
    altra = c.execute("SELECT * FROM operazioni WHERE stato='fatta' AND id!=? LIMIT 1", (op["id"],)).fetchone()
    c.execute("UPDATE operazioni SET stato='da_fare' WHERE id=?", (op["id"],))
    c.execute("UPDATE operazioni SET stato='errore' WHERE id=?", (altra["id"],))
    tid = op["trasferimento_id"]

    mag = entra(app, "magazziniere")
    assert mag.post(f"/trasferimenti/{tid}/riprendi", data={"gettone": gettone(mag.get("/").text)}).status_code == 403

    resp = entra(app, "responsabile")
    r = resp.post(f"/trasferimenti/{tid}/riprendi", data={"gettone": gettone(resp.get("/").text)})
    assert r.status_code == 303 and "restano+in+coda" in r.headers["location"]
    r = resp.post(f"/operazioni/{altra['id']}/riprova", data={"gettone": gettone(resp.get("/").text), "nota": "riprovo"})
    assert r.status_code == 303 and "resta+in+coda" in r.headers["location"]
    time.sleep(0.5)                                         # un'esecuzione in sottofondo avrebbe già scritto
    stati = {r["id"]: r["stato"] for r in c.execute("SELECT id, stato FROM operazioni WHERE id IN (?,?)", (op["id"], altra["id"]))}
    assert stati == {op["id"]: "da_fare", altra["id"]: "da_fare"}


def test_stesse_quantita_non_oltre_l_arrivato(app):
    # «Stesse quantità anche all'altro negozio»: non si può caricare più di quanto il fornitore ha consegnato
    cl = entra(app, "magazziniere")
    scheda = cl.get("/trasferimenti/shopify/demo-centro/104").text
    r = cl.post("/trasferimenti/shopify/demo-centro/104", data={
        "gettone": gettone(scheda), "modo": "uguale", "altro": "porto", "azione": "anteprima", "b_0": "100000"})
    assert r.status_code == 409 and "vanno da 0 a" in r.text


def test_differenza_solo_sulle_righe_del_trasferimento(app, demo):
    c, _, _ = demo
    riga_altrui = c.execute("SELECT id FROM righe WHERE trasferimento_id != 1 LIMIT 1").fetchone()[0]
    cl = entra(app, "responsabile")
    r = cl.post("/trasferimenti/1/differenza", data={"gettone": gettone(cl.get("/trasferimenti/1").text),
                                                     "riga": str(riga_altrui), "esito": "perso", "nota": "prova"})
    assert r.status_code == 409 and "non è di questo trasferimento" in r.text
    assert c.execute("SELECT esito_differenza FROM righe WHERE id=?", (riga_altrui,)).fetchone()[0] is None


def test_password_cambiata_chiude_le_altre_sessioni(app):
    mag = entra(app, "magazziniere")
    resp = entra(app, "responsabile")
    pagina = resp.get("/impostazioni").text
    r = resp.post("/impostazioni/utente", data={"gettone": gettone(pagina), "username": "magazziniere", "nome": "Marco",
                                                "ruolo": "magazziniere", "password": "nuova-password"})
    assert r.status_code == 303
    assert mag.get("/").status_code == 303                  # con la password vecchia si rientra dal login
    pagina = resp.get("/impostazioni").text                 # chi cambia la password resta dentro
    r = resp.post("/impostazioni/utente", data={"gettone": gettone(pagina), "username": "responsabile", "nome": "Giulia",
                                                "ruolo": "responsabile", "password": "altra-password"})
    assert r.status_code == 303 and resp.get("/").status_code == 200


def test_blocco_degli_accessi_per_dispositivo(demo):
    c, _, _ = demo
    auth._tentativi.clear()
    try:
        for _ in range(5):
            assert auth.accedi(c, "responsabile", "sbagliata", "10.0.0.9") is None
        assert auth.accedi(c, "responsabile", "demo1234", "10.0.0.9") is None          # quel tablet aspetta 10 minuti
        assert auth.accedi(c, "responsabile", "demo1234", "10.0.0.2")                  # il PC del responsabile entra
        for i in range(15):                                                             # da più dispositivi insieme
            auth.accedi(c, "responsabile", "sbagliata", f"10.0.1.{i % 3}")
        assert auth.accedi(c, "responsabile", "demo1234", "10.0.0.3") is None          # 20 errori: si aspetta ovunque
    finally:
        auth._tentativi.clear()


def test_avvisi_e_cache(app):
    cl = entra(app, "responsabile")
    falso = cl.get("/inventario?msg=Sessione scaduta: chiama il numero 000&tono=errore")
    assert "chiama il numero" not in falso.text                         # un avviso scritto in un link non si mostra
    r = cl.get("/trasferimenti/9999/export.csv")
    assert "Trasferimento non trovato" in cl.get(r.headers["location"]).text   # quelli del programma sì
    assert falso.headers["cache-control"] == "no-store"                 # Indietro dopo «Esci» non mostra la pagina
    assert cl.get("/static/stile.css").headers.get("cache-control") != "no-store"


def test_rettifiche_e_soglie_con_dati_non_validi(app, demo):
    c, _, _ = demo
    chiave = c.execute("SELECT chiave FROM articolo_negozi WHERE negozio='centro' LIMIT 1").fetchone()[0]
    cl = entra(app, "responsabile")
    nuovo = lambda: gettone(cl.get("/articolo?chiave=" + chiave).text)      # ogni invio ha il suo gettone usa-e-getta
    base = {"chiave": chiave, "fonte": "conteggio", "motivo": "conteggio di prova"}
    r = cl.post("/rettifiche", data={**base, "gettone": nuovo(), "negozio": "inventato", "contati": "3"})
    assert r.status_code == 409 and "Scegli il negozio" in r.text
    r = cl.post("/rettifiche", data={**base, "gettone": nuovo(), "negozio": "centro", "contati": "1000000000000"})
    assert r.status_code == 409 and "vanno da 0 a" in r.text
    r = cl.post("/articolo/soglia", data={"gettone": nuovo(), "chiave": chiave, "negozio": "inventato",
                                          "minima": "1", "obiettivo": "2"})
    assert r.status_code == 303 and "non+valido" in r.headers["location"]
    assert not c.execute("SELECT 1 FROM soglie WHERE negozio='inventato'").fetchone()
