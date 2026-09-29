"""
Prova completa nel browser (Chromium headless con Playwright), sulla demo ripristinata:
magazziniere e responsabile fanno reintegro -> approvazione -> preparazione con scanner -> spedizione ->
ricezione parziale -> chiusura differenza, poi bilanciamento, anomalie, export.

Salva le schermate in docs/schermate/ e gli export in docs/esempi/.

  python tests/e2e_browser.py          (serve Playwright: pip install playwright && playwright install chromium)
"""
import json, re, sqlite3, subprocess, sys, time
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
PY = BASE_DIR / ".venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = Path(sys.executable)
PORTA = 8766
URL = f"http://127.0.0.1:{PORTA}"
SCHERMATE, ESEMPI = BASE_DIR / "docs" / "schermate", BASE_DIR / "docs" / "esempi"
DB = BASE_DIR / "dati" / "demo.db"
esiti = []


def ok(cosa, condizione=True):
    esiti.append((cosa, bool(condizione)))
    print(("  OK  " if condizione else "  NO  ") + cosa, flush=True)
    return condizione


def db(sql, *p):
    c = sqlite3.connect(DB)
    try:
        return c.execute(sql, p).fetchall()
    finally:
        c.close()


def invia(page, azione):
    """Esegue un'azione che invia un modulo e aspetta la pagina nuova."""
    with page.expect_navigation():
        azione()


def foto(page, nome, pagina_intera=True):
    page.screenshot(path=str(SCHERMATE / f"{nome}.png"), full_page=pagina_intera)


def entra(browser, utente, **kw):
    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, color_scheme="light", locale="it-IT", **kw)
    page = ctx.new_page()
    page.on("dialog", lambda d: d.accept())
    page.goto(URL + "/login")
    page.fill("input[name=username]", utente)
    page.fill("input[name=password]", "demo1234")
    page.click("button:has-text('Entra')")
    page.wait_for_url(URL + "/")
    return ctx, page


def attendi(condizione, secondi=10):
    for _ in range(secondi * 10):
        if condizione():
            return True
        time.sleep(0.1)
    return False


def attendi_operazioni(tid, secondi=20):
    for _ in range(secondi * 5):
        if not db("SELECT COUNT(*) FROM operazioni WHERE trasferimento_id=? AND stato IN ('da_fare','in_corso')", tid)[0][0]:
            return True
        time.sleep(0.2)
    return False


def main():
    SCHERMATE.mkdir(parents=True, exist_ok=True)
    ESEMPI.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(PY), str(BASE_DIR / "magazzino.py"), "demo-ripristina"], check=True, cwd=BASE_DIR)
    log = open(BASE_DIR / "dati" / "e2e_server.log", "w", encoding="utf-8")
    server = subprocess.Popen([str(PY), str(BASE_DIR / "magazzino.py"), "--demo", "--porta", str(PORTA)], cwd=BASE_DIR,
                              stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            try:
                import urllib.request
                urllib.request.urlopen(URL + "/login", timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            prova(browser)
            browser.close()
    finally:
        server.terminate()
    falliti = [c for c, e in esiti if not e]
    print(f"\n{len(esiti) - len(falliti)} controlli superati, {len(falliti)} falliti")
    sys.exit(1 if falliti else 0)


def prova(browser):
    # ---------------------------------------------------------------- accesso e panoramica
    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, color_scheme="light")
    p = ctx.new_page()
    p.goto(URL + "/inventario")
    ok("senza accesso si finisce al login", "/login" in p.url)
    foto(p, "01-accesso", False)
    ctx.close()

    cm, mag = entra(browser, "magazziniere")
    ok("Da fare: un trasferimento da preparare", mag.locator("#da-preparare li").count() == 1)
    ok("Da fare: errore di lettura visibile nella barra", "errore" in mag.locator(".barra-sync").inner_text().lower())
    ok("magazziniere senza Anomalie, Storico e Impostazioni",
       all(mag.locator(f"nav.menu a[href='{u}']").count() == 0 for u in ("/anomalie", "/storico", "/impostazioni")))
    numero = mag.locator("nav.menu a[href='/'] .conteggio").inner_text()
    ok(f"il numero sul menu ({numero}) è quello della pagina", mag.locator(".testata .nota").inner_text().startswith(numero + " "))
    foto(mag, "02-panoramica")
    mag.fill(".cerca-rapida input[name=q]", "TR-3")
    invia(mag, lambda: mag.press(".cerca-rapida input[name=q]", "Enter"))
    ok("ricerca dalla barra: «TR-3» apre il trasferimento", mag.url.endswith("/trasferimenti/3"))

    # ---------------------------------------------------------------- inventario e lettore barcode
    mag.goto(URL + "/inventario")
    righe = mag.locator("table.dati tbody tr").count()
    ok(f"inventario paginato lato server (50 righe su {db('SELECT COUNT(*) FROM articoli')[0][0]})", righe == 50)
    foto(mag, "03-inventario", False)
    ean_zero = db("SELECT ean FROM articoli WHERE ean LIKE '00%' AND stato='abbinato' LIMIT 1")[0][0]
    mag.locator("#form-cerca input[name=q]").fill(ean_zero)
    invia(mag, lambda: mag.keyboard.press("Enter"))     # il lettore scrive il codice e preme Invio
    ok(f"ricerca per EAN con zeri iniziali {ean_zero}", mag.locator("table.dati tbody tr").count() == 1
       and ean_zero in mag.locator("table.dati").inner_text())
    foto(mag, "04-inventario-lettore", False)
    mag.goto(URL + "/inventario?filtro=anomalie")
    ok("filtro articoli con anomalie", mag.locator("table.dati tbody tr").count() > 0)

    # ---------------------------------------------------------------- reintegro
    dal, al = (date.today() - timedelta(days=30)).isoformat(), date.today().isoformat()
    q = f"/reintegro?calcola=1&origine=centro&destinazione=porto&modo=venduto&dal={dal}&al={al}"
    mag.goto(URL + q)
    testo = mag.locator("main").inner_text()
    ok("reintegro calcolato", "Da prelevare" in testo)
    ok("magazziniere: nessun dato di vendita", "Venduto" not in testo and "rimandato" not in testo)
    foto(mag, "05-reintegro-magazziniere")
    cr, resp = entra(browser, "responsabile")
    resp.goto(URL + q)
    ok("responsabile: vede venduto e vincoli", "Venduto" in resp.locator("main").inner_text())
    foto(resp, "06-reintegro-responsabile")

    prima = db("SELECT COUNT(*) FROM trasferimenti")[0][0]
    bottone = mag.locator("button:has-text('Crea bozza')")
    ok("il pulsante conta righe e pezzi selezionati", re.search(r"Crea bozza · \d+ righ[ae] · \d+ pezz[oi]", bottone.inner_text()) is not None)
    bottone.dblclick()                                  # doppio clic apposta
    mag.wait_for_url(re.compile(r"/trasferimenti/\d+"))
    tid = int(re.search(r"/trasferimenti/(\d+)", mag.url).group(1))
    time.sleep(0.5)
    ok("doppio clic: una sola bozza creata", db("SELECT COUNT(*) FROM trasferimenti")[0][0] == prima + 1)

    # ---------------------------------------------------------------- approvazione
    resp.goto(f"{URL}/trasferimenti/{tid}")
    foto(resp, "07-bozza-da-approvare")
    prima_riga = db("SELECT id, proposta FROM righe WHERE trasferimento_id=? ORDER BY id LIMIT 1", tid)[0]
    if prima_riga[1] > 1:
        resp.fill(f"input[name=q_{prima_riga[0]}]", str(prima_riga[1] - 1))    # il responsabile riduce una riga
    invia(resp, lambda: resp.get_by_role("button", name=re.compile(r"^Approva")).click())
    ok("approvato", db("SELECT stato FROM trasferimenti WHERE id=?", tid)[0][0] == "approvato")

    # ---------------------------------------------------------------- preparazione con scanner
    mag.goto(f"{URL}/trasferimenti/{tid}")
    invia(mag, lambda: mag.get_by_role("button", name="Inizia a preparare").click())
    url_prima = mag.url
    campo = mag.locator("input[name=codice]")
    for rid, ean, approvata in db("SELECT id, ean, approvata FROM righe WHERE trasferimento_id=? AND approvata>0", tid):
        for _ in range(approvata):
            campo.fill(ean)
            campo.press("Enter")                        # letture una dopo l'altra: il lettore non aspetta
    ok("scanner: tutte le righe prelevate", attendi(lambda: all(a == b for a, b in db(
        "SELECT prelevata, approvata FROM righe WHERE trasferimento_id=?", tid))))
    ok("letture senza ricaricare la pagina", mag.url == url_prima)
    ok("esito della lettura accanto al campo", attendi(lambda: "Tutto pronto" in mag.locator("[data-esito-testo]").inner_text()))
    ok("pulsante principale: «Controlla e spedisci»", mag.locator("[data-spedisci]").inner_text() == "Controlla e spedisci")
    foto(mag, "08-preparazione-scanner")

    # ---------------------------------------------------------------- spedizione: anteprima e conferma
    centro_prima = {r[0]: r[1] for r in db("SELECT variant_id, disponibile FROM demo_varianti WHERE store='demo-centro'")}
    invia(mag, lambda: mag.get_by_role("button", name="Controlla e spedisci").click())
    ok("anteprima dei movimenti prima di scrivere", mag.locator("#conferma").count() == 1)
    ok("anteprima: scarico anche sul negozio online", "Centro online" in mag.locator("#conferma").inner_text())
    ok("anteprima: pagina pulita, senza la lista di lavoro", mag.locator("#prelievo").count() == 0)
    foto(mag, "09-conferma-spedizione")
    invia(mag, lambda: mag.get_by_role("button", name="Conferma e aggiorna Shopify").first.click())
    ok("spedito", db("SELECT stato FROM trasferimenti WHERE id=?", tid)[0][0] == "spedito")
    ok("scritture completate", attendi_operazioni(tid))
    spedite = db("""SELECT v.variant_id, r.spedita FROM righe r JOIN articolo_canali ac ON ac.chiave=r.chiave AND ac.store='demo-centro'
                    JOIN varianti v ON v.store=ac.store AND v.variant_id=ac.variant_id WHERE r.trasferimento_id=?""", tid)
    ok("giacenze del Centro scaricate della quantità spedita",
       all(db("SELECT disponibile FROM demo_varianti WHERE variant_id=?", vid)[0][0] == centro_prima[vid] - q for vid, q in spedite))
    mag.goto(f"{URL}/trasferimenti/{tid}")
    foto(mag, "10-scritture-shopify")

    # ---------------------------------------------------------------- ricezione parziale
    righe = db("SELECT id, spedita FROM righe WHERE trasferimento_id=? AND spedita>0 ORDER BY id", tid)
    manca = righe[0]
    mag.fill(f"input[name=b_{manca[0]}]", str(manca[1] - 1))
    invia(mag, lambda: mag.get_by_role("button", name="Controlla e registra").click())
    foto(mag, "11-conferma-ricezione")
    invia(mag, lambda: mag.get_by_role("button", name="Conferma e aggiorna Shopify").first.click())
    attendi_operazioni(tid)
    ok("ricezione parziale: resta spedito", db("SELECT stato FROM trasferimenti WHERE id=?", tid)[0][0] == "spedito")
    mag.goto(f"{URL}/trasferimenti/{tid}")
    mag.locator("summary:has-text('Chiudi la ricezione')").click()
    mag.fill("textarea[name=nota]", "nel collo mancava un pezzo")
    invia(mag, lambda: mag.get_by_role("button", name="Chiudi ricezione").click())
    ok("ricezione chiusa con differenza", db("SELECT stato FROM trasferimenti WHERE id=?", tid)[0][0] == "ricevuto")

    resp.goto(f"{URL}/trasferimenti/{tid}")
    foto(resp, "12-differenza-da-chiudere")
    resp.select_option("select[name=esito]", "persa")
    resp.fill("#differenze input[name=nota]", "non ritrovato in nessuno dei due negozi")
    invia(resp, lambda: resp.get_by_role("button", name="Chiudi la differenza").click())
    ok("differenza chiusa dal responsabile", db("SELECT esito_differenza FROM righe WHERE id=?", manca[0])[0][0] == "persa")

    # ---------------------------------------------------------------- export del trasferimento
    for fmt in ("pdf", "xlsx", "csv"):
        r = mag.request.get(f"{URL}/trasferimenti/{tid}/export.{fmt}")
        (ESEMPI / f"trasferimento_{tid:04d}.{fmt}").write_bytes(r.body())
        ok(f"export {fmt} del trasferimento", r.ok and len(r.body()) > 300)
    t3 = db("SELECT id FROM trasferimenti WHERE codice='TR-0003'")[0][0]
    for fmt in ("pdf", "xlsx"):
        (ESEMPI / f"lista_di_prelievo_TR-0003.{fmt}").write_bytes(mag.request.get(f"{URL}/trasferimenti/{t3}/export.{fmt}").body())
    r = mag.request.get(URL + q.replace("/reintegro?", "/reintegro/export.xlsx?"))
    (ESEMPI / "lista_di_reintegro_magazziniere.xlsx").write_bytes(r.body())
    r = mag.request.get(URL + "/inventario/export.csv")
    (ESEMPI / "inventario.csv").write_bytes(r.body())
    mag.goto(f"{URL}/trasferimenti/{t3}/stampa")
    foto(mag, "18-vista-di-stampa")

    # ---------------------------------------------------------------- bilanciamento, anomalie, storico, impostazioni
    resp.goto(URL + "/bilanciamento?calcola=1")
    ok("bilanciamento: proposte calcolate", resp.locator("table.dati").count() >= 1 or "Nessuna proposta" in resp.content())
    foto(resp, "13-bilanciamento")
    r = resp.request.get(URL + "/bilanciamento/export.pdf")
    (ESEMPI / "proposta_di_bilanciamento.pdf").write_bytes(r.body())
    resp.goto(URL + "/trasferimenti")
    ok("trasferimenti: si apre sugli aperti, con il prossimo passo", "Prossimo passo" in resp.content()
       and resp.locator("table.dati tbody tr").count() < db("SELECT COUNT(*) FROM trasferimenti")[0][0])
    resp.goto(URL + "/trasferimenti?stato=tutti")
    ok("elenco di tutti i trasferimenti", resp.locator("table.dati tbody tr").count() >= 5)
    resp.goto(URL + "/anomalie")
    ok("anomalie per gruppi: lettura, EAN mancanti e doppi", all(x in resp.content() for x in ("EAN mancante", "EAN doppio", "Lettura non riuscita"))
       and resp.locator(".gruppo-anomalie").count() >= 3)
    foto(resp, "14-anomalie")
    resp.goto(URL + "/anomalie?tipo=solo_un_negozio")
    resp.locator("[data-seleziona-tutte]").click()
    n_sel = resp.locator("input[name=ids]:checked").count()
    ok(f"anomalie: selezione in blocco ({n_sel})", n_sel > 0 and f"({n_sel})" in resp.locator("[data-ignora-selezionate]").inner_text())
    resp.fill("#blocco input[name=nota]", "assortimento diverso fra i negozi")
    invia(resp, lambda: resp.locator("[data-ignora-selezionate]").click())
    ok("anomalie ignorate in blocco con un solo motivo",
       db("SELECT COUNT(*) FROM anomalie WHERE tipo='solo_un_negozio' AND stato='ignorata' AND nota='assortimento diverso fra i negozi'")[0][0] == n_sel)
    resp.goto(URL + "/storico?op=tutte")
    foto(resp, "15-storico")
    resp.goto(URL + "/impostazioni")
    foto(resp, "16-impostazioni")
    art = db("SELECT chiave FROM articoli WHERE stato='abbinato' LIMIT 1")[0][0]
    resp.goto(URL + "/articolo?chiave=" + art)
    foto(resp, "19-articolo")

    # ---------------------------------------------------------------- trasferimento Shopify senza destinazione
    mag.goto(URL + "/")
    ok("Da fare: movimenti fatti su Shopify da sistemare", "Movimenti fatti su Shopify da sistemare" in mag.content())
    mag.goto(URL + "/trasferimenti/shopify")
    ok("elenco Shopify: #T0101 da destinare, suggerito il Porto", "#T0101" in mag.content() and "Negozio Porto" in mag.content())
    foto(mag, "20-shopify-senza-destinazione")
    mag.goto(URL + "/trasferimenti/shopify/demo-centro/101")
    ok("suggerimento preselezionato con i motivi", mag.locator("input[name=scelta][value=porto]").is_checked()
       and "nota del trasferimento" in mag.content())
    foto(mag, "21-shopify-scelta-destinazione")
    righe_ts = db("SELECT righe FROM trasferimenti_shopify WHERE nome='#T0101'")[0][0]
    righe_ts = json.loads(righe_ts)
    porto_prima = {r["variant_id"]: db("SELECT disponibile FROM demo_varianti WHERE store='demo-porto' AND ean=?", r["ean"])[0][0] for r in righe_ts}
    ok("«È già arrivata?» visibile solo con un negozio scelto", mag.locator("[data-se-negozio]").is_visible())
    mag.check("input[name=scelta][value=uscita]")
    ok("uscita definitiva: la domanda sparisce", not mag.locator("[data-se-negozio]").is_visible())
    mag.check("input[name=scelta][value=porto]")
    invia(mag, lambda: mag.get_by_role("button", name="Controlla prima di confermare").click())
    testo = mag.locator("#conferma").inner_text()
    ok("anteprima: carico al Porto e scarico sul sito", "Entrano" in testo and "Escono" in testo and "Centro online" in testo)
    foto(mag, "22-shopify-conferma")
    invia(mag, lambda: mag.get_by_role("button", name="Conferma e aggiorna Shopify").click())
    tid_s = int(re.search(r"/trasferimenti/(\d+)", mag.url).group(1))
    ok("scritture completate", attendi_operazioni(tid_s))
    ok("Porto caricato dei pezzi del trasferimento Shopify",
       all(db("SELECT disponibile FROM demo_varianti WHERE store='demo-porto' AND ean=?", r["ean"])[0][0] == porto_prima[r["variant_id"]] + r["quantita"]
           for r in righe_ts))
    ok("trasferimento Shopify segnato come destinato", db("SELECT esito FROM trasferimenti_shopify WHERE nome='#T0101'")[0][0] == "destinato")

    # ---------------------------------------------------------------- carico da fornitore: doppione e divisione
    mag.goto(URL + "/trasferimenti/shopify")
    ok("elenco: arrivi dal fornitore e doppione segnalato", "Arrivo dal fornitore" in mag.content() and "possibile doppione" in mag.content())
    foto(mag, "23-shopify-da-sistemare")
    mag.goto(URL + "/trasferimenti/shopify/demo-web/106")
    ok("sul sito il doppione è proposto come «stessa consegna»", mag.locator("input[name=modo][value=stessa_consegna]").is_checked())
    invia(mag, lambda: mag.get_by_role("button", name="Controlla prima di confermare").click())
    invia(mag, lambda: mag.get_by_role("button", name="Conferma", exact=True).click())
    ok("doppione chiuso senza scritture", db("SELECT esito FROM trasferimenti_shopify WHERE nome='#T0106'")[0][0] == "stessa_consegna")

    resp.goto(URL + "/trasferimenti/shopify/demo-centro/104")
    ok("sulla cassa: duplicazione 1:1 non spuntata (c'è il doppione)", not resp.locator("input[name=scala_specchi]").is_checked())
    ok("regola di divisione nascosta finché non si sceglie «Dividi»", not resp.locator("select[name=frazione]").is_visible())
    invia(resp, lambda: resp.check("input[name=modo][value=dividi]"))       # la tabella si ricalcola da sola
    ok("scelto «Dividi»: compare la regola", resp.locator("select[name=frazione]").is_visible())
    if resp.input_value("select[name=frazione]") != "1/2":
        invia(resp, lambda: resp.select_option("select[name=frazione]", "1/2"))
    invia(resp, lambda: resp.check("input[name=dispari][value=alterna]"))
    arrivati = [r["quantita"] for r in json.loads(db("SELECT righe FROM trasferimenti_shopify WHERE nome='#T0104'")[0][0])]
    quote = [int(resp.input_value(f"input[name=b_{i}]")) for i in range(len(arrivati))]
    ok(f"metà con il dispari alternato: {arrivati} -> {quote}", sum(quote) <= sum(arrivati) and all(abs(2 * k - q) <= 1 for k, q in zip(quote, arrivati)))
    foto(resp, "24-carico-ripartizione")
    invia(resp, lambda: resp.get_by_role("button", name="Controlla prima di confermare").click())
    foto(resp, "25-carico-conferma")
    invia(resp, lambda: resp.get_by_role("button", name="Conferma", exact=True).click())
    tid_c = int(re.search(r"/trasferimenti/(\d+)", resp.url).group(1))
    t_c = db("SELECT stato, tipo FROM trasferimenti WHERE id=?", tid_c)[0]
    ok("quota del Porto: trasferimento approvato da preparare", t_c == ("approvato", "ripartizione fornitore"))
    ok("quote del trasferimento come la ripartizione", sorted(r[0] for r in db("SELECT approvata FROM righe WHERE trasferimento_id=?", tid_c))
       == sorted(k for k in quote if k))
    ok("carico segnato come diviso", db("SELECT esito FROM trasferimenti_shopify WHERE nome='#T0104'")[0][0] == "diviso")

    # ---------------------------------------------------------------- tablet
    ct = browser.new_context(viewport={"width": 1024, "height": 768}, color_scheme="light", storage_state=cm.storage_state())
    tab = ct.new_page()
    tab.goto(URL + "/inventario")
    ok("tablet: nessuno scorrimento orizzontale della pagina", tab.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
    ok("tablet: la tabella dell'inventario ci sta intera",
       tab.evaluate("(() => { const t = document.querySelector('.tabella-scorre'); return t.scrollWidth <= t.clientWidth + 1; })()"))
    foto(tab, "17-tablet-inventario", False)
    t3 = db("SELECT id FROM trasferimenti WHERE codice='TR-0003'")[0][0]
    tab.goto(f"{URL}/trasferimenti/{t3}")
    invia(tab, lambda: tab.get_by_role("button", name="Inizia a preparare").click())
    ean = db("SELECT ean FROM righe WHERE trasferimento_id=? ORDER BY id LIMIT 1", t3)[0][0]
    tab.locator("input[name=codice]").fill(ean)
    tab.locator("input[name=codice]").press("Enter")
    attendi(lambda: "di" in tab.locator("[data-esito-testo]").inner_text())
    foto(tab, "26-tablet-preparazione", False)
    ct.close()

    # ---------------------------------------------------------------- telefono
    cp = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="light", has_touch=True, storage_state=cm.storage_state())
    tel = cp.new_page()
    tel.goto(URL + "/")
    ok("telefono: menu in basso, nessuno scorrimento orizzontale", tel.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
    foto(tel, "27-telefono-da-fare", False)
    tel.goto(URL + "/trasferimenti")
    foto(tel, "28-telefono-trasferimenti", False)
    cp.close()
    cm.close()
    cr.close()


if __name__ == "__main__":
    main()
