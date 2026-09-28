"""
Le pagine. Tutto è generato dal server: il browser riceve solo quello che il ruolo può vedere (il magazziniere
non riceve mai venduto, prezzi o dati degli ordini, nemmeno in campi nascosti).
"""
import json, re, urllib.parse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from . import auth, calcoli, compiti, da_shopify, export, operazioni, regole, rettifiche, trasferimenti as T
from .avvio import PASSWORD_DEMO, UTENTI_DEMO, prepara, ripristina_demo, sorgente_per, topologia
from .config import Config
from .db import ROMA, connetti, impostazione, locale, ora, registra, salva_impostazione, transazione
from .sync import Pianificatore, ricalcola

QUI = Path(__file__).parent
PER_PAGINA = 50
STATI = {"bozza": "Bozza", "approvato": "Approvato", "in_preparazione": "In preparazione", "spedito": "Spedito",
         "ricevuto": "Ricevuto", "annullato": "Annullato"}
# nell'ordine in cui vanno guardate (la pagina Anomalie mostra i gruppi così)
TIPI_ANOMALIA = {"sincronizzazione": "Lettura non riuscita", "aggiornamento_fallito": "Aggiornamento Shopify non riuscito",
                 "differenza_ricezione": "Differenza alla ricezione", "quantita_incoerente": "Disponibilità negativa",
                 "ean_doppio": "EAN doppio", "associazione_ambigua": "Da collegare a mano", "ean_mancante": "EAN mancante",
                 "canali_non_allineati": "Cassa e online diversi", "specchio_mancante": "Manca sul negozio gemello",
                 "solo_un_negozio": "Solo in un negozio",
                 "trasferimento_senza_destinazione": "Uscita Shopify senza destinazione", "carico_fornitore": "Arrivo dal fornitore"}
COSA_FARE = {
    "sincronizzazione": "Un negozio non ha risposto: premi «Rileggi i negozi». Se resta, controlla la connessione o le credenziali.",
    "aggiornamento_fallito": "Shopify non ha applicato un aggiornamento: apri il trasferimento e scegli «Riprova» o «Non serve più».",
    "differenza_ricezione": "Sono arrivati meno pezzi del previsto, o rotti: apri il trasferimento e chiudi la differenza.",
    "quantita_incoerente": "La disponibilità è sotto zero: conta i pezzi in negozio e fai una rettifica.",
    "ean_doppio": "Lo stesso codice a barre è su più varianti dello stesso negozio Shopify: correggilo nell'admin di Shopify.",
    "associazione_ambigua": "Il programma non sa quale variante collegare: collegala a mano dalla scheda articolo.",
    "ean_mancante": "Manca il codice a barre: aggiungilo su Shopify, oppure collega l'articolo a mano. "
                    "Quelli che non vanno abbinati (per esempio i buoni regalo) si ignorano in blocco.",
    "canali_non_allineati": "Cassa e online hanno numeri diversi: si riallineano con la regola già decisa "
                            "(vince la più bassa se giustificata da ordini, il resto a mano).",
    "specchio_mancante": "Ha merce ma non esiste sul negozio gemello: va creato nel catalogo online, oppure si ignora con un motivo.",
    "solo_un_negozio": "Di solito è un assortimento diverso: si ignora in blocco con un motivo. Se è lo stesso articolo, collegalo a mano.",
}
IN_BLOCCO = ("ean_mancante", "specchio_mancante", "solo_un_negozio")


OP_PROBLEMI = ("conflitto", "errore", "incerta", "da_verificare")
OP_STATI = {"da_fare": "In coda", "in_corso": "In corso", "fatta": "Fatto", "incerta": "In attesa di conferma",
            "conflitto": "Non scritto: giacenza cambiata", "errore": "Non riuscito", "da_verificare": "Da controllare a mano",
            "annullata": "Non serve più"}
OP_TIPI = {"scarico": "Escono", "carico": "Entrano", "rientro": "Rientrano", "rettifica": "Rettifica", "fornitore": "Carico fornitore"}


def ordine_scaffale(ubicazione: str):
    """Ordine naturale degli scaffali: A2-3 prima di A10-1; le righe senza scaffale in fondo."""
    if not ubicazione:
        return (1, [])
    return (0, [(0, int(p), "") if p.isdigit() else (1, 0, p.lower()) for p in re.findall(r"\d+|\D+", ubicazione)])


STATI_SHOPIFY = {"DRAFT": "bozza", "READY_TO_SHIP": "pronto da spedire", "IN_PROGRESS": "in corso",
                 "TRANSFERRED": "completato", "CANCELED": "annullato", "OTHER": "altro"}
TIPI_TRASF = {"reintegro": "Reintegro", "bilanciamento": "Bilanciamento", "manuale": "Manuale", "da Shopify": "Da Shopify",
              "ripartizione fornitore": "Quota fornitore"}


FILTRI_TRASF = {"aperti": "Aperti", "bozza": "Bozze", "da_preparare": "Da preparare", "spedito": "Spediti",
                "ricevuto": "Ricevuti", "annullato": "Annullati", "tutti": "Tutti"}


def prossimo_passo(t, ruolo, nomi):
    """(testo, tono) della colonna «Prossimo passo» nell'elenco dei trasferimenti."""
    if t["op_problemi"]:
        return ("Aggiornamenti Shopify da controllare" if ruolo == "responsabile"
                else "Aggiornamento di Shopify: lo controlla il responsabile"), "err"
    s = t["stato"]
    if s == "bozza":
        return ("Da approvare" if ruolo == "responsabile" else "Da approvare (responsabile)"), "att"
    if s == "approvato":
        return f"Da preparare a {nomi[t['origine']]}", "info"
    if s == "in_preparazione":
        if t["pezzi"] and t["prelevati"] >= t["pezzi"]:
            return f"Pronto da spedire da {nomi[t['origine']]}", "info"
        return f"Preparazione a {nomi[t['origine']]}: {t['prelevati']} di {t['pezzi'] or 0}", "info"
    if s == "spedito":
        return f"Da ricevere a {nomi[t['destinazione']]}", "info"
    if t["differenze"]:
        return ("Differenze da chiudere" if ruolo == "responsabile" else "Differenze da chiudere (responsabile)"), "att"
    if t["op_in_corso"]:
        return "Shopify in aggiornamento", ""
    return "", ""


def aperto(t):
    return t["stato"] not in ("ricevuto", "annullato") or t["differenze"] or t["op_problemi"] or t["op_in_corso"]


def plurale(n, singolare, plurale_):
    return f"{n} {singolare if n == 1 else plurale_}"


def voci_menu(u, conteggi, percorso):
    """Menu per ruolo: il magazziniere vede solo le sezioni del suo lavoro (i permessi restano quelli del server)."""
    def attivo(url, *altri):
        return percorso == "/" if url == "/" else any(percorso.startswith(x) for x in (url, *altri))
    voci = [
        {"url": "/", "nome": "Da fare", "icona": "panoramica", "attivo": attivo("/"), "conteggio": conteggi["da_fare"]},
        {"url": "/inventario", "nome": "Inventario", "icona": "inventario", "attivo": attivo("/inventario", "/articolo", "/cerca")},
        {"url": "/trasferimenti", "nome": "Trasferimenti", "icona": "trasferimenti", "attivo": attivo("/trasferimenti")},
        {"url": "/reintegro", "nome": "Reintegro", "icona": "reintegro", "attivo": attivo("/reintegro", "/bilanciamento")},
    ]
    extra = []
    if u["ruolo"] == "responsabile":
        voci.append({"url": "/anomalie", "nome": "Anomalie", "icona": "anomalie", "attivo": attivo("/anomalie"),
                     "conteggio": conteggi["anomalie_gravi"], "grave": True})
        extra = [{"url": "/storico", "nome": "Storico", "icona": "storico", "attivo": attivo("/storico")},
                 {"url": "/impostazioni", "nome": "Impostazioni", "icona": "impostazioni", "attivo": attivo("/impostazioni")}]
    return voci, extra


def giorno(iso):
    """Una data senza ora («2026-01-01») come si legge: 01/01/2026 (senza passare dal fuso orario)."""
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return iso or ""


def quando(iso):
    """«alle 23:38» oggi, «del 27/09 alle 23:38» gli altri giorni."""
    if not iso:
        return ""
    d = datetime.fromisoformat(iso).astimezone(ROMA)
    return f"alle {d:%H:%M}" if d.date() == datetime.now(ROMA).date() else f"del {d:%d/%m} alle {d:%H:%M}"


class Rimanda(Exception):
    def __init__(self, url):
        self.url = url


class Vietato(Exception):
    pass


class Stato:
    def __init__(self, cfg: Config):
        self.cfg, self.topo = cfg, topologia(cfg)
        self.sorgente, self.errore_config = sorgente_per(cfg)
        self.pianificatore = None


def crea_app(cfg: Config, sfondo: bool = True) -> FastAPI:
    S = Stato(cfg)
    c = connetti(cfg.db_path)
    prepara(c, cfg)
    vuoto = not c.execute("SELECT 1 FROM utenti LIMIT 1").fetchone()
    c.close()
    if cfg.demo and vuoto:
        ripristina_demo(cfg, S.sorgente)
    if sfondo and S.sorgente:
        S.pianificatore = Pianificatore(cfg.db_path, S.sorgente, S.topo, cfg.sync_minuti,
                                        dopo=lambda c: operazioni.esegui_in_attesa(c, S.sorgente) if cfg.scritture_attive else None)
        S.pianificatore.start()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.S = S
    app.mount("/static", StaticFiles(directory=QUI / "static"), name="static")
    tpl = Jinja2Templates(directory=QUI / "templates")
    tpl.env.globals.update(locale=locale, STATI=STATI, TIPI_ANOMALIA=TIPI_ANOMALIA, ESITI=T.ESITI, FONTI=rettifiche.FONTI,
                           OP_STATI=OP_STATI, OP_TIPI=OP_TIPI, STATI_SHOPIFY=STATI_SHOPIFY, TIPI_TRASF=TIPI_TRASF,
                           OP_PROBLEMI=OP_PROBLEMI, plurale=plurale, quando=quando, giorno=giorno)
    tpl.env.filters["q"] = lambda d: urllib.parse.urlencode({k: v for k, v in d.items() if v not in (None, "")})
    tpl.env.filters["fromjson"] = json.loads

    # ------------------------------------------------------------------------------------------- utilità

    @contextmanager
    def db():
        c = connetti(cfg.db_path)
        try:
            yield c
        finally:
            c.close()

    def utente(request, c, ruolo=None):
        u = auth.utente_da_token(c, request.cookies.get("sessione"))
        if not u:
            raise Rimanda("/login?" + urllib.parse.urlencode({"dopo": str(request.url.path)}))
        if ruolo and u["ruolo"] != ruolo:
            raise Vietato()
        return u

    def resp(request, c, u, nome, http=200, **kw):
        sess = request.cookies.get("sessione") or ""
        ctx = {"request": request, "u": u, "demo": cfg.demo, "topo": S.topo, "negozi": S.topo.negozi,
               "nome_negozio": {n.id: n.nome for n in S.topo.negozi}, "nomi_store": S.topo.nomi_store,
               "gettone": lambda: auth.gettone(sess), "scritture": cfg.scritture_attive, "rettifiche_attive": cfg.rettifiche_attive,
               "msg": request.query_params.get("msg"), "tipo_msg": request.query_params.get("tipo", "ok"),
               "sync": stato_sync(c), "responsabile": u is not None and u["ruolo"] == "responsabile",
               "errore_config": S.errore_config, **kw}
        if u is not None:
            ctx["conta_menu"] = compiti.conteggi_menu(c, u["ruolo"])
            ctx["menu"], ctx["menu_extra"] = voci_menu(u, ctx["conta_menu"], request.url.path)
        return tpl.TemplateResponse(request, nome, ctx, status_code=http)

    def vai(url, msg=None, tipo="ok"):
        if msg:
            url, _, frammento = url.partition("#")
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode({"msg": msg, "tipo": tipo})
            url += f"#{frammento}" if frammento else ""
        return RedirectResponse(url, status_code=303)

    def modulo_valido(request, c, form) -> bool:
        """Firma del gettone (richiesta da questa pagina) e uso singolo (niente doppio clic)."""
        g = form.get("gettone") or ""
        if not auth.gettone_valido(request.cookies.get("sessione") or "", g):
            raise Vietato()
        return auth.consuma_gettone(c, g)

    def stato_sync(c) -> dict:
        out, adesso = [], datetime.now(timezone.utc)
        limite = timedelta(minutes=max(30, 2 * cfg.sync_minuti))
        for s in S.topo.stores:
            ult = c.execute("SELECT * FROM sincronizzazioni WHERE store=? AND tipo='giacenze' AND esito!='in corso' "
                            "ORDER BY id DESC LIMIT 1", (s,)).fetchone()
            ok = c.execute("SELECT fine FROM sincronizzazioni WHERE store=? AND tipo='giacenze' AND esito='ok' "
                           "ORDER BY id DESC LIMIT 1", (s,)).fetchone()
            vecchio = not ok or adesso - datetime.fromisoformat(ok["fine"]) > limite
            out.append({"store": s, "nome": S.topo.nomi_store[s], "ultimo_ok": ok["fine"] if ok else None,
                        "errore": ult["messaggio"] if ult and ult["esito"] == "errore" else None, "vecchio": vecchio})
        p = S.pianificatore
        letti = [x["ultimo_ok"] for x in out if x["ultimo_ok"]]
        piu_vecchio = min(letti) if letti else None
        errori = [x["nome"] for x in out if x["errore"]]
        # (tono, testo, testo corto per le barre strette)
        if errori:
            riassunto = ("rosso", f"Errore di lettura: {', '.join(errori)}" + (f" (dati {quando(piu_vecchio)})" if piu_vecchio else ""),
                         f"Errore: {', '.join(errori)}")
        elif not letti:
            riassunto = ("giallo", "Negozi mai letti", "Mai letti")
        elif any(x["vecchio"] for x in out):
            riassunto = ("giallo", f"Dati vecchi: letti {quando(piu_vecchio)}", "Dati vecchi")
        else:
            riassunto = ("verde", f"Dati aggiornati {quando(piu_vecchio)}", f"Aggiornati {quando(piu_vecchio)}")
        return {"negozi": out, "problemi": any(x["errore"] or x["vecchio"] for x in out), "riassunto": riassunto,
                "in_corso": bool(p and p.in_corso), "messaggio": p.messaggio if p else ""}

    @app.exception_handler(Rimanda)
    async def _rimanda(request, e):
        return RedirectResponse(e.url, status_code=303)

    @app.exception_handler(Vietato)
    async def _vietato(request, e):
        return HTMLResponse("<!doctype html><meta charset=utf-8><title>Non permesso</title><body style='font-family:sans-serif;padding:2rem'>"
                            "<h1>Non permesso</h1><p>Questa pagina o azione è riservata al responsabile, oppure il modulo "
                            "non è valido (ricarica la pagina e riprova).</p><p><a href='/'>Torna alla panoramica</a></p>",
                            status_code=403)

    # ------------------------------------------------------------------------------------------- accesso

    @app.get("/login", response_class=HTMLResponse)
    def login(request: Request):
        with db() as c:
            return resp(request, c, None, "login.html", utenti_demo=UTENTI_DEMO if cfg.demo else [],
                        password_demo=PASSWORD_DEMO, dopo=request.query_params.get("dopo", "/"))

    @app.post("/login")
    async def login_post(request: Request):
        form = await request.form()
        with db() as c:
            token = auth.accedi(c, form.get("username"), form.get("password"))
            if not token:
                errore = ("Troppi tentativi: riprova fra 10 minuti." if auth.bloccato(form.get("username"))
                          else "Nome utente o password non corretti.")
                return resp(request, c, None, "login.html", http=401, errore=errore, utenti_demo=UTENTI_DEMO if cfg.demo else [],
                            password_demo=PASSWORD_DEMO, dopo=form.get("dopo") or "/")
        dopo = form.get("dopo") or "/"
        r = RedirectResponse(dopo if dopo.startswith("/") and not dopo.startswith("//") else "/", status_code=303)
        r.set_cookie("sessione", token, httponly=True, samesite="lax", max_age=12 * 3600)
        return r

    @app.post("/esci")
    def esci(request: Request):
        with db() as c:
            auth.esci(c, request.cookies.get("sessione"))
        r = RedirectResponse("/login", status_code=303)
        r.delete_cookie("sessione")
        return r

    # ------------------------------------------------------------------------------------------- panoramica

    @app.get("/", response_class=HTMLResponse)
    def panoramica(request: Request):
        with db() as c:
            u = utente(request, c)
            return resp(request, c, u, "panoramica.html", compiti=compiti.da_fare(c, S.topo, u["ruolo"]))

    @app.get("/cerca")
    def cerca(request: Request):
        """Ricerca e lettore da qualunque pagina: codice univoco → scheda, TR-n → trasferimento, altro → inventario."""
        q = (request.query_params.get("q") or "").strip()
        with db() as c:
            utente(request, c)
            if not q:
                return vai("/inventario")
            m = re.fullmatch(r"(?i)tr-?0*(\d+)", q)
            if m:
                r = c.execute("SELECT id FROM trasferimenti WHERE codice=?", (f"TR-{int(m.group(1)):04d}",)).fetchone()
                if r:
                    return vai(f"/trasferimenti/{r[0]}")
            trovati = c.execute("SELECT chiave FROM articoli WHERE ean=? OR UPPER(sku)=UPPER(?) LIMIT 2", (q, q)).fetchall()
            if len(trovati) == 1:
                return vai("/articolo?" + urllib.parse.urlencode({"chiave": trovati[0][0]}))
        return vai("/inventario?" + urllib.parse.urlencode({"q": q}))

    @app.post("/sincronizza")
    async def sincronizza_ora(request: Request):
        form = await request.form()
        with db() as c:
            utente(request, c)
            modulo_valido(request, c, form)
        if not S.pianificatore:
            return vai(form.get("torna") or "/", S.errore_config or "Sincronizzazione non disponibile", "errore")
        S.pianificatore.richiedi()
        return vai(form.get("torna") or "/", "Lettura avviata: i dati si aggiornano fra poco")

    @app.get("/stato-sync")
    def stato_sync_json(request: Request):
        with db() as c:
            utente(request, c)
            s = stato_sync(c)
        return JSONResponse({"in_corso": s["in_corso"], "messaggio": s["messaggio"]})

    # ------------------------------------------------------------------------------------------- inventario

    def query_inventario(c, p, limite=None, offset=0):
        a_id, b_id = (n.id for n in S.topo.negozi)
        testo = (p.get("q") or "").strip()
        where, par = [], {"A": a_id, "B": b_id}
        if testo:
            if c.execute("SELECT 1 FROM articoli WHERE ean=? LIMIT 1", (testo,)).fetchone():
                where.append("a.ean = :t")                     # lettore barcode: codice esatto
                par["t"] = testo
            else:
                where.append("(a.cerca LIKE :like OR UPPER(a.sku) = :su)")
                par.update(like=f"%{testo.lower()}%", su=testo.upper())
        neg = p.get("negozio") or ""
        col = {a_id: "na", b_id: "nb"}.get(neg)
        if col:
            where.append(f"{col}.chiave IS NOT NULL")
        filtro = p.get("filtro") or ""
        if filtro == "disponibili":
            where.append(f"{col}.disponibile > 0" if col else "(na.disponibile > 0 OR nb.disponibile > 0)")
        elif filtro == "esauriti":
            where.append(f"COALESCE({col}.disponibile, 0) <= 0" if col else
                         "((na.chiave IS NOT NULL AND COALESCE(na.disponibile,0) <= 0) OR (nb.chiave IS NOT NULL AND COALESCE(nb.disponibile,0) <= 0))")
        elif filtro == "anomalie":
            where.append("EXISTS(SELECT 1 FROM anomalie an WHERE an.chiave=a.chiave AND an.stato='aperta')")
        elif filtro == "solo_uno":
            where.append("((na.chiave IS NULL) != (nb.chiave IS NULL))")
        elif filtro in ("sotto_scorta", "in_trasferimento"):
            c.execute("CREATE TEMP TABLE IF NOT EXISTS filtro_chiavi(chiave TEXT PRIMARY KEY)")
            c.execute("DELETE FROM filtro_chiavi")
            if filtro == "in_trasferimento":
                chiavi = [k for k, x in calcoli.in_corso(c).items() if x["in_viaggio"] or x["prenotati"]]
            else:
                chiavi = chiavi_sotto_scorta(c, neg)
            c.executemany("INSERT OR IGNORE INTO filtro_chiavi VALUES(?)", [(k,) for k in chiavi])
            where.append("a.chiave IN (SELECT chiave FROM filtro_chiavi)")
        sql = f"""FROM articoli a LEFT JOIN articolo_negozi na ON na.chiave=a.chiave AND na.negozio=:A
                  LEFT JOIN articolo_negozi nb ON nb.chiave=a.chiave AND nb.negozio=:B
                  {'WHERE ' + ' AND '.join(where) if where else ''}"""
        totale = c.execute(f"SELECT COUNT(*) {sql}", par).fetchone()[0]
        lim = f" LIMIT {int(limite)} OFFSET {int(offset)}" if limite else ""
        righe = [dict(r) for r in c.execute(
            f"""SELECT a.*, na.disponibile AS disp_a, nb.disponibile AS disp_b, na.chiave IS NOT NULL AS in_a,
                nb.chiave IS NOT NULL AS in_b,
                (SELECT ubicazione FROM ubicazioni u WHERE u.chiave=a.chiave AND u.negozio=:A) AS ubi_a,
                (SELECT ubicazione FROM ubicazioni u WHERE u.chiave=a.chiave AND u.negozio=:B) AS ubi_b,
                (SELECT COUNT(*) FROM anomalie an WHERE an.chiave=a.chiave AND an.stato='aperta') AS n_anomalie
                {sql} ORDER BY a.prodotto COLLATE NOCASE, a.variante COLLATE NOCASE{lim}""", par)]
        corso = calcoli.in_corso(c)
        for r in righe:
            x = corso.get(r["chiave"])
            r["prenotati"], r["in_viaggio"] = (x["prenotati"], x["in_viaggio"]) if x else (0, 0)
        return totale, righe

    def chiavi_sotto_scorta(c, negozio=""):
        s, corso, out = calcoli.soglie(c), calcoli.in_corso(c), []
        for r in c.execute("""SELECT n.chiave, n.negozio, n.disponibile FROM articolo_negozi n WHERE EXISTS(
                              SELECT 1 FROM articolo_negozi m WHERE m.chiave=n.chiave AND m.disponibile > 0)"""):
            if negozio and r["negozio"] != negozio:
                continue
            arrivo = corso[r["chiave"]]["arrivo"][r["negozio"]] if r["chiave"] in corso else 0
            if max(0, r["disponibile"] or 0) + arrivo < regole.soglia(s, r["negozio"], r["chiave"]).minima:
                out.append(r["chiave"])
        return out

    @app.get("/inventario", response_class=HTMLResponse)
    def inventario(request: Request):
        p = dict(request.query_params)
        pagina = max(1, int(p.get("pagina") or 1))
        with db() as c:
            u = utente(request, c)
            totale, righe = query_inventario(c, p, PER_PAGINA, (pagina - 1) * PER_PAGINA)
            filtri = {k: p.get(k, "") for k in ("q", "negozio", "filtro")}
            # righe con dati più vecchi della soglia della barra in alto: il numero potrebbe non essere quello di adesso
            limite = (datetime.now(timezone.utc) - timedelta(minutes=max(30, 2 * cfg.sync_minuti))).isoformat(timespec="seconds")
            for r in righe:
                r["vecchia"] = bool(r["aggiornato_il"]) and r["aggiornato_il"] < limite
            codice = bool(re.fullmatch(r"\d{6,14}", filtri["q"].strip()))
            return resp(request, c, u, "inventario.html", righe=righe, totale=totale, pagina=pagina,
                        pagine=max(1, -(-totale // PER_PAGINA)), filtri=filtri, cercato_codice=codice)

    COL_INVENTARIO = [("prodotto", "Prodotto"), ("variante", "Variante"), ("sku", "SKU"), ("ean", "EAN"), ("disp_a", ""),
                      ("disp_b", ""), ("prenotati", "Prenotati in uscita"), ("in_viaggio", "In viaggio"),
                      ("stato", "Associazione"), ("aggiornato", "Aggiornato")]

    @app.get("/inventario/export.{formato}")
    def inventario_export(request: Request, formato: str):
        with db() as c:
            utente(request, c)
            _, righe = query_inventario(c, dict(request.query_params))
        a, b = S.topo.negozi
        col = [(k, t or f"Disponibili {a.nome if k == 'disp_a' else b.nome}") for k, t in COL_INVENTARIO]
        for r in righe:
            r["aggiornato"] = locale(r["aggiornato_il"])
        adesso = locale(ora())
        testa = [("Documento", "Inventario"), ("Data e ora", adesso), ("Filtri", _descrivi_filtri(request.query_params))]
        return _file(formato, "inventario", col, righe, testa, [("Righe", len(righe))], "Inventario")

    def _descrivi_filtri(p):
        parti = [f"{k}={v}" for k, v in p.items() if v and k in ("q", "negozio", "filtro")]
        return ", ".join(parti) or "nessuno (tutti gli articoli)"

    def _file(formato, nome, colonne, righe, testa, totali, titolo, pdf_col=None, firme=(), codice=""):
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        if formato == "csv":
            dati, tipo = export.csv_bytes(colonne, righe, testa), "text/csv; charset=utf-8"
        elif formato == "xlsx":
            dati, tipo = export.xlsx_bytes(titolo, colonne, righe, testa, totali), \
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif formato == "pdf" and pdf_col:
            dati, tipo = export.pdf_documento(titolo, testa, pdf_col, righe, totali, list(firme), codice=codice), "application/pdf"
        else:
            return Response("Formato non disponibile", status_code=404)
        return Response(dati, media_type=tipo, headers={"Content-Disposition": f'attachment; filename="{nome}_{stamp}.{formato}"'})

    # ------------------------------------------------------------------------------------------- reintegro

    def parametri_reintegro(p, ricordata=""):
        """Parametri della lista. La direzione arriva come «origine>destinazione» (o, dai vecchi link, come due campi);
        senza nessuna delle due vale l'ultima usata su questo dispositivo."""
        a, b = (n.id for n in S.topo.negozi)
        oggi = date.today()
        try:
            dal = date.fromisoformat(p.get("dal") or "")
        except ValueError:
            dal = oggi - timedelta(days=7)
        try:
            al = date.fromisoformat(p.get("al") or "")
        except ValueError:
            al = oggi
        direzione = p.get("direzione") or ("" if p.get("origine") or p.get("destinazione") else ricordata or "")
        orig, _, dest = direzione.partition(">")
        if {orig, dest} != {a, b}:
            dest = p.get("destinazione") if p.get("destinazione") in (a, b) else b
            orig = p.get("origine") if p.get("origine") in (a, b) else (a if dest == b else b)
        modo = p.get("modo") if p.get("modo") in ("venduto", "scorta") else "venduto"
        return {"origine": orig, "destinazione": dest, "direzione": f"{orig}>{dest}", "modo": modo, "dal": dal, "al": al,
                "q": p.get("q", "")}

    def controlla_periodo(par):
        oggi = date.today()
        if par["dal"] > par["al"]:
            return "La data di inizio è dopo quella di fine."
        if par["al"] > oggi:
            return "Il periodo non può finire nel futuro."
        if par["modo"] == "venduto" and par["dal"] < oggi - timedelta(days=60):
            return "Shopify fornisce solo gli ultimi 60 giorni di ordini: scegli un inizio più recente."
        if par["origine"] == par["destinazione"]:
            return "Origine e destinazione devono essere diverse."
        return None

    @app.get("/reintegro", response_class=HTMLResponse)
    def reintegro(request: Request):
        p = dict(request.query_params)
        par = parametri_reintegro(p, request.cookies.get("reintegro_direzione", ""))
        oggi = date.today()
        with db() as c:
            u = utente(request, c)
            piano, errore = None, None
            if "calcola" in p:
                errore = controlla_periodo(par)
                if not errore:
                    piano = calcoli.piano_reintegro(c, S.topo, par["modo"], par["origine"], par["destinazione"],
                                                    par["dal"], par["al"], par["q"])
            pagina = resp(request, c, u, "reintegro.html", par=par, piano=piano, errore=errore,
                          da_fare=[r for r in piano or [] if r["quantita"] > 0],
                          non_coperti=[r for r in piano or [] if r["quantita"] == 0],
                          scorciatoie=[(n, (oggi - timedelta(days=n)).isoformat(), oggi.isoformat()) for n in (7, 14, 30)])
        if "calcola" in p and not errore:
            # la direzione usata resta proposta la volta dopo, su questo dispositivo
            pagina.set_cookie("reintegro_direzione", par["direzione"], max_age=180 * 86400, httponly=True, samesite="lax")
        return pagina

    def _colonne_lista(u, con_quantita="quantita"):
        col = [("prodotto", "Prodotto"), ("variante", "Variante"), ("sku", "SKU"), ("ean", "EAN"), ("ubicazione", "Ubicazione"),
               (con_quantita, "Quantità"), ("da", "Origine"), ("a", "Destinazione"), ("note_testo", "Note")]
        if u["ruolo"] == "responsabile":
            col += [("venduto", "Venduto nel periodo"), ("gia_coperto", "Già coperto"), ("disp_dest", "Disp. destinazione"),
                    ("disp_orig", "Disp. origine"), ("arrivo", "In arrivo"), ("vincoli_testo", "Vincoli")]
        return col

    PDF_LISTA = [("_spunta", "✓", 8, "c"), ("prodotto_var", "Prodotto", 64, "l"), ("sku", "SKU", 24, "l"),
                 ("ean", "EAN", 28, "l"), ("ubicazione", "Ubic.", 20, "l"), ("quantita", "Qtà", 12, "c"),
                 ("_note", "Note", 34, "l")]

    @app.get("/reintegro/export.{formato}")
    def reintegro_export(request: Request, formato: str):
        p = dict(request.query_params)
        par = parametri_reintegro(p)
        with db() as c:
            u = utente(request, c)
            if controlla_periodo(par):
                return Response(controlla_periodo(par), status_code=400)
            piano = [r for r in calcoli.piano_reintegro(c, S.topo, par["modo"], par["origine"], par["destinazione"],
                                                        par["dal"], par["al"], par["q"]) if r["quantita"] > 0]
        nomi = {n.id: n.nome for n in S.topo.negozi}
        righe = []
        for r in piano:
            x = {"prodotto": r["prodotto"], "variante": r["variante"], "prodotto_var": _pv(r), "sku": r["sku"], "ean": r["ean"],
                 "ubicazione": r["ubicazione"], "quantita": r["quantita"], "da": nomi[par["origine"]], "a": nomi[par["destinazione"]],
                 "note_testo": "; ".join(r["proposta"].note)}
            if u["ruolo"] == "responsabile":          # i numeri di vendita solo al responsabile
                x.update(venduto=r["venduto"], gia_coperto=r["gia_coperto"], disp_dest=r["disp_dest"], disp_orig=r["disp_orig"],
                         arrivo=r["arrivo"], vincoli_testo="; ".join(r["proposta"].vincoli))
            righe.append(x)
        codice = "RI-" + datetime.now().strftime("%Y%m%d-%H%M")
        modo = "reintegro del venduto" if par["modo"] == "venduto" else "ripristino scorta obiettivo"
        testa = [("Documento", f"Lista di reintegro {codice}"), ("Data e ora", locale(ora())),
                 ("Origine", nomi[par["origine"]]), ("Destinazione", nomi[par["destinazione"]]), ("Modalità", modo)]
        if par["modo"] == "venduto":
            testa.append(("Periodo", f"{par['dal']:%d/%m/%Y} – {par['al']:%d/%m/%Y}"))
        tot = [("Righe", len(righe)), ("Pezzi", sum(r["quantita"] for r in righe))]
        return _file(formato, "reintegro", _colonne_lista(u), righe, testa, tot, "Lista di reintegro", PDF_LISTA,
                     ("Preparato da", "Controllato da", "Data"), codice)

    @app.post("/reintegro/crea")
    async def reintegro_crea(request: Request):
        form = await request.form()
        return await run_in_threadpool(_reintegro_crea, request, form)

    def _reintegro_crea(request, form):
        par = parametri_reintegro(form)
        scelti = set(form.getlist("chiave"))
        with db() as c:
            u = utente(request, c)
            if not modulo_valido(request, c, form):
                return vai("/trasferimenti", "Richiesta già eseguita (doppio clic?): controlla la lista", "info")
            errore = controlla_periodo(par)
            if errore:
                return vai("/reintegro?" + urllib.parse.urlencode({**_par_url(par), "calcola": 1}), errore, "errore")
            # si ricalcola qui: le quantità non arrivano dal browser
            piano = calcoli.piano_reintegro(c, S.topo, par["modo"], par["origine"], par["destinazione"], par["dal"], par["al"], par["q"])
            righe = [{"chiave": r["chiave"], "quantita": r["quantita"], "nota": "; ".join(r["proposta"].note),
                      "coperture": regole.assegna_copertura(r["vendite"], r["quantita"]) if par["modo"] == "venduto" else []}
                     for r in piano if r["chiave"] in scelti and r["quantita"] > 0]
            modo = "reintegro del venduto" if par["modo"] == "venduto" else "ripristino scorta"
            nota = f"{modo}" + (f", vendite dal {par['dal']:%d/%m} al {par['al']:%d/%m}" if par["modo"] == "venduto" else "")
            try:
                tid = T.crea(c, S.topo, u["username"], par["origine"], par["destinazione"], "reintegro", righe, nota)
            except T.ErroreFlusso as e:
                return vai("/reintegro?" + urllib.parse.urlencode({**_par_url(par), "calcola": 1}), str(e), "errore")
        return vai(f"/trasferimenti/{tid}", msg_bozza(u))

    def msg_bozza(u, n=1):
        if u["ruolo"] == "responsabile":
            return "Bozza creata: controlla e approva qui sotto" if n == 1 else f"Create {n} bozze, una per direzione: aprile e approvale"
        return "Bozza creata: la approva il responsabile" if n == 1 else f"Create {n} bozze, una per direzione: le approva il responsabile"

    def _par_url(par):
        return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in par.items()}

    # ------------------------------------------------------------------------------------------- bilanciamento

    @app.get("/bilanciamento", response_class=HTMLResponse)
    def bilanciamento(request: Request):
        q = request.query_params.get("q", "")
        with db() as c:
            u = utente(request, c)
            piano = calcoli.piano_bilanciamento(c, S.topo, q) if "calcola" in request.query_params else None
            gruppi = {}
            for r in piano or []:
                gruppi.setdefault((r["origine"], r["destinazione"]), []).append(r)
            return resp(request, c, u, "bilanciamento.html", gruppi=gruppi, q=q, calcolato=piano is not None,
                        soglie_neg={n.id: calcoli.soglie(c).get((n.id, "*")) for n in S.topo.negozi},
                        priorita=impostazione(c, "priorita_pari"))

    @app.get("/bilanciamento/export.{formato}")
    def bilanciamento_export(request: Request, formato: str):
        with db() as c:
            utente(request, c)
            piano = calcoli.piano_bilanciamento(c, S.topo, request.query_params.get("q", ""))
        nomi = {n.id: n.nome for n in S.topo.negozi}
        righe = [{"prodotto": r["prodotto"], "variante": r["variante"], "prodotto_var": _pv(r), "sku": r["sku"], "ean": r["ean"],
                  "ubicazione": r["ubicazione"], "quantita": r["quantita"], "da": nomi[r["origine"]], "a": nomi[r["destinazione"]],
                  "prima": f"{r['prima_o']} / {r['prima_d']}", "dopo": f"{r['dopo_o']} / {r['dopo_d']}",
                  "motivo": "; ".join(r["proposta"].vincoli)} for r in piano]
        col = [("prodotto", "Prodotto"), ("variante", "Variante"), ("sku", "SKU"), ("ean", "EAN"), ("da", "Da"), ("a", "A"),
               ("quantita", "Quantità"), ("prima", "Prima (origine / destinazione)"), ("dopo", "Dopo (origine / destinazione)"),
               ("ubicazione", "Ubicazione"), ("motivo", "Motivo")]
        codice = "BI-" + datetime.now().strftime("%Y%m%d-%H%M")
        testa = [("Documento", f"Proposta di bilanciamento {codice}"), ("Data e ora", locale(ora())),
                 ("Criterio", "solo giacenze e soglie, nessuna previsione della domanda")]
        tot = [("Righe", len(righe)), ("Pezzi", sum(r["quantita"] for r in righe))]
        pdf_col = PDF_LISTA[:5] + [("quantita", "Qtà", 12, "c"), ("da", "Da", 17, "l"), ("a", "A", 17, "l")]
        pdf_col[1] = ("prodotto_var", "Prodotto", 56, "l")
        return _file(formato, "bilanciamento", col, righe, testa, tot, "Proposta di bilanciamento", pdf_col, codice=codice)

    @app.post("/bilanciamento/crea")
    async def bilanciamento_crea(request: Request):
        form = await request.form()
        return await run_in_threadpool(_bilanciamento_crea, request, form)

    def _bilanciamento_crea(request, form):
        scelti = set(form.getlist("chiave"))
        with db() as c:
            u = utente(request, c)
            if not modulo_valido(request, c, form):
                return vai("/trasferimenti", "Richiesta già eseguita (doppio clic?)", "info")
            per_dir = {}
            for r in calcoli.piano_bilanciamento(c, S.topo, form.get("q", "")):
                if r["chiave"] in scelti:
                    per_dir.setdefault((r["origine"], r["destinazione"]), []).append(
                        {"chiave": r["chiave"], "quantita": r["quantita"], "nota": "; ".join(r["proposta"].note)})
            if not per_dir:
                return vai("/bilanciamento?calcola=1", "Nessuna proposta selezionata (o i numeri sono cambiati): ricalcola", "errore")
            creati = [T.crea(c, S.topo, u["username"], o, d, "bilanciamento", righe, "bilanciamento su soglie")
                      for (o, d), righe in per_dir.items()]
        if len(creati) == 1:
            return vai(f"/trasferimenti/{creati[0]}", msg_bozza(u))
        return vai("/trasferimenti?stato=bozza", msg_bozza(u, len(creati)))

    # ------------------------------------------------------------------------------------------- trasferimenti

    # ------------------------------------------------------------------ trasferimenti Shopify senza destinazione
    # (prima di /trasferimenti/{tid}, altrimenti "shopify" verrebbe letto come numero di trasferimento)

    VISTE_SHOPIFY = {"da_destinare": ("da_destinare",), "decisi": ("destinato", "uscita", "gestito_fuori", "tutto_a", "diviso",
                                                                 "uguale_1_1", "stessa_consegna"),
                     "altri": ("non_partito", "annullato_shopify")}

    @app.get("/trasferimenti/shopify", response_class=HTMLResponse)
    def shopify_elenco(request: Request):
        vista = request.query_params.get("vista", "da_destinare")
        vista = vista if vista in VISTE_SHOPIFY else "da_destinare"
        with db() as c:
            u = utente(request, c)
            righe = da_shopify.elenco(c, VISTE_SHOPIFY[vista])
            if vista == "da_destinare":
                for ts in righe:
                    if ts["verso"] == "uscita":
                        cand = da_shopify.candidati(c, S.topo, ts, da_shopify.abbina_righe(c, S.topo, ts))
                        ts["suggerito"] = cand[0] if cand else None
                    else:
                        ts["doppi"] = da_shopify.doppi(c, S.topo, ts)
            conteggi = {k: len(da_shopify.elenco(c, v)) for k, v in VISTE_SHOPIFY.items()}
            return resp(request, c, u, "shopify_elenco.html", righe=righe, vista=vista, conteggi=conteggi,
                        dal=da_shopify.dal(c), ESITI_SHOPIFY=da_shopify.ESITI)

    # ------------------------------------------------------------------ carichi da fornitore

    def stato_carico(c, ts, righe, form=None):
        """Scelte della pagina di ripartizione: dal modulo inviato, oppure quelle suggerite."""
        arrivo = S.topo.negozio_di(ts["store"])
        altri = [n for n in S.topo.negozi if n.id != arrivo.id]
        doppi = da_shopify.doppi(c, S.topo, ts)
        regola = da_shopify.regola_predefinita(c)
        principale = S.topo.canale(ts["store"]).principale
        if form is None:
            # doppione registrato sul canale specchio: di solito è la copia a mano di quello sul principale;
            # se il gemello è già stato deciso, questo è quasi certamente la stessa consegna
            deciso = any(d["esito"] != "da_destinare" for d in doppi)
            modo = "stessa_consegna" if doppi and (not principale or deciso) else "tutto"
            f = {"modo": modo, "altro": altri[0].id, "frazione": regola["frazione"], "dispari": regola["dispari"],
                 "scala": not doppi, "subito": False,
                 "nota": f"stessa consegna di {doppi[0]['nome']} su {S.topo.nomi_store[doppi[0]['store']]}" if modo == "stessa_consegna" else ""}
            modifiche = None
        else:
            f = {"modo": form.get("modo") or "tutto", "altro": form.get("altro") or altri[0].id,
                 "frazione": form.get("frazione") or regola["frazione"], "dispari": form.get("dispari") or regola["dispari"],
                 "scala": form.get("scala_specchi") == "1", "subito": form.get("altro_subito") == "1",
                 "nota": (form.get("nota") or "").strip()}
            modifiche = None
            if form.get("azione") != "regola":
                modifiche = {}
                for k, v in form.items():
                    if k.startswith("b_") and str(v).strip() != "":
                        try:
                            modifiche[int(k[2:])] = int(v)
                        except ValueError:
                            raise T.ErroreFlusso(f"«{v}» non è un numero intero")
        f["quote"] = da_shopify.quote_altro(righe, f["modo"], f["frazione"], f["dispari"], modifiche)
        return f, doppi, arrivo, altri

    def scheda_carico(request, c, u, ts, http=200, form=None, anteprima=False, **kw):
        righe = da_shopify.abbina_righe(c, S.topo, ts)
        try:
            f, doppi, arrivo, altri = stato_carico(c, ts, righe, form)
        except T.ErroreFlusso as e:
            f, doppi, arrivo, altri = stato_carico(c, ts, righe, None)
            kw.update(errore=str(e), dettagli=e.dettagli)
            http, anteprima = 409, False
        movs = da_shopify.movimenti_carico(c, S.topo, ts, righe, f["modo"], f["altro"], f["quote"], f["scala"], f["subito"]) \
            if anteprima else []
        collegato = T.carica(c, ts["trasferimento_id"])[0] if ts["trasferimento_id"] else None
        return resp(request, c, u, "shopify_carico.html", http=http, ts=ts, righe=righe, f=f, doppi=doppi, arrivo=arrivo,
                    altri=altri, specchi=[S.topo.nomi_store[ch.store] for ch in arrivo.canali if ch.store != ts["store"]],
                    anteprima=anteprima, movimenti=movs, collegato=collegato, ESITI_SHOPIFY=da_shopify.ESITI,
                    FRAZIONI=list(regole.FRAZIONI), REGOLE_DISPARI=regole.REGOLE_DISPARI, **kw)

    def _carico_decidi(request, c, u, ts, form):
        url = f"/trasferimenti/shopify/{ts['store']}/{ts['numero']}"
        if form.get("conferma") != "1":
            return scheda_carico(request, c, u, ts, form=form, anteprima=form.get("azione") == "anteprima")
        try:
            righe = da_shopify.abbina_righe(c, S.topo, ts)
            f, _, _, _ = stato_carico(c, ts, righe, form)
            tid, ids = da_shopify.decidi_carico(c, S.topo, cfg.scritture_attive, u["username"], u["ruolo"], ts["store"],
                                                ts["numero"], f["modo"], f["altro"], f["quote"], f["scala"], f["subito"],
                                                f["nota"], f["frazione"] if f["modo"] == "dividi" else None,
                                                f["dispari"] if f["modo"] == "dividi" else None)
        except T.ErroreFlusso as e:
            return scheda_carico(request, c, u, da_shopify.carica_ts(c, ts["store"], ts["numero"]), http=409, form=form,
                                 errore=str(e), dettagli=e.dettagli)
        if ids and S.sorgente:
            operazioni.avvia_in_sottofondo(cfg.db_path, S.sorgente)
        if tid:
            return vai(f"/trasferimenti/{tid}", "Carico ripartito: la quota per l'altro negozio è questo trasferimento")
        return vai(url, "Carico registrato" + (": duplicazione sugli altri canali in corso" if ids else ""))

    def shopify_scheda(request, c, u, store, numero, http=200, **kw):
        ts = da_shopify.carica_ts(c, store, numero)
        if ts["verso"] == "ingresso":
            return scheda_carico(request, c, u, ts, http=http, **kw)
        righe = da_shopify.abbina_righe(c, S.topo, ts)
        cand = da_shopify.candidati(c, S.topo, ts, righe)
        origine = S.topo.negozio_di(store)
        altri_canali = [ch for ch in origine.canali if ch.store != store]
        # stesso movimento fatto a mano anche sull'altro canale? stessi pezzi, entro due giorni
        doppi = [x for x in c.execute("""SELECT nome, store, creato_il, pezzi FROM trasferimenti_shopify
                     WHERE store != ? AND pezzi = ? AND ABS(julianday(creato_il) - julianday(?)) <= 2""",
                                      (store, ts["pezzi"], ts["creato_il"]))
                 if x["store"] in [ch.store for ch in altri_canali]]
        collegato = T.carica(c, ts["trasferimento_id"])[0] if ts["trasferimento_id"] else None
        return resp(request, c, u, "shopify_dettaglio.html", http=http, ts=ts, righe=righe, candidati=cand,
                    origine=origine, specchi=[S.topo.nomi_store[ch.store] for ch in altri_canali], doppi=doppi, collegato=collegato,
                    ESITI_SHOPIFY=da_shopify.ESITI, **kw)

    @app.get("/trasferimenti/shopify/{store}/{numero}", response_class=HTMLResponse)
    def shopify_dettaglio(request: Request, store: str, numero: str):
        with db() as c:
            u = utente(request, c)
            try:
                return shopify_scheda(request, c, u, store, numero)
            except T.ErroreFlusso as e:
                return vai("/trasferimenti/shopify", str(e), "errore")

    @app.post("/trasferimenti/shopify/{store}/{numero}")
    async def shopify_decidi(request: Request, store: str, numero: str):
        form = await request.form()
        return await run_in_threadpool(_shopify_decidi, request, store, numero, form)

    def _shopify_decidi(request, store, numero, form):
        url = f"/trasferimenti/shopify/{store}/{numero}"
        with db() as c:
            u = utente(request, c)
            if not modulo_valido(request, c, form):
                return vai(url, "Azione già eseguita (doppio clic?): ecco lo stato attuale", "info")
            try:
                ts = da_shopify.carica_ts(c, store, numero)
            except T.ErroreFlusso as e:
                return vai("/trasferimenti/shopify", str(e), "errore")
            if ts["verso"] == "ingresso":
                return _carico_decidi(request, c, u, ts, form)
            scelta = form.get("scelta") or ""
            scala, subito = form.get("scala_specchi") == "1", form.get("subito", "1") == "1"
            nota = (form.get("nota") or "").strip()
            try:
                if not scelta:
                    raise T.ErroreFlusso("Scegli la destinazione")
                if form.get("conferma") != "1":
                    ts = da_shopify.carica_ts(c, store, numero)
                    righe = da_shopify.abbina_righe(c, S.topo, ts)
                    movs = da_shopify.movimenti(c, S.topo, ts, righe, scelta, scala, subito)
                    return shopify_scheda(request, c, u, store, numero, anteprima={
                        "scelta": scelta, "scala": scala, "subito": subito, "nota": nota, "movimenti": movs})
                tid, ids = da_shopify.decidi(c, S.topo, cfg.scritture_attive, u["username"], u["ruolo"], store, numero,
                                             scelta, scala, subito, nota)
            except T.ErroreFlusso as e:
                return shopify_scheda(request, c, u, store, numero, http=409, errore=str(e), dettagli=e.dettagli)
        if ids and S.sorgente:
            operazioni.avvia_in_sottofondo(cfg.db_path, S.sorgente)
        if tid:
            return vai(f"/trasferimenti/{tid}", "Destinazione registrata: giacenze in aggiornamento su Shopify")
        return vai(url, "Decisione registrata" + (": altri canali in aggiornamento" if ids else ""))

    @app.get("/trasferimenti", response_class=HTMLResponse)
    def trasferimenti(request: Request):
        filtro = request.query_params.get("stato") or "aperti"
        filtro = filtro if filtro in FILTRI_TRASF or filtro in STATI else "aperti"
        problemi = ",".join("'%s'" % x for x in OP_PROBLEMI)
        with db() as c:
            u = utente(request, c)
            tutti = [dict(r) for r in c.execute(f"""SELECT t.*, COUNT(r.id) AS n_righe,
                SUM(CASE t.stato WHEN 'bozza' THEN r.proposta WHEN 'approvato' THEN r.approvata WHEN 'in_preparazione' THEN r.approvata
                    ELSE COALESCE(r.spedita, r.approvata, r.proposta) END) AS pezzi,
                COALESCE(SUM(r.prelevata), 0) AS prelevati,
                SUM(CASE WHEN t.stato='ricevuto' AND r.esito_differenza IS NULL AND (COALESCE(r.spedita,0)-r.ricevuta-r.danneggiata>0
                    OR r.danneggiata>0) THEN 1 ELSE 0 END) AS differenze,
                (SELECT COUNT(*) FROM operazioni o WHERE o.trasferimento_id=t.id AND o.stato IN ({problemi})) AS op_problemi,
                (SELECT COUNT(*) FROM operazioni o WHERE o.trasferimento_id=t.id AND o.stato IN ('da_fare','in_corso')) AS op_in_corso
                FROM trasferimenti t LEFT JOIN righe r ON r.trasferimento_id=t.id
                GROUP BY t.id ORDER BY t.id DESC""")]
            scelta = {"aperti": aperto, "tutti": lambda t: True,
                      "da_preparare": lambda t: t["stato"] in ("approvato", "in_preparazione")}
            conta = {f: sum(1 for t in tutti if scelta.get(f, lambda t, f=f: t["stato"] == f)(t)) for f in FILTRI_TRASF}
            righe = [t for t in tutti if scelta.get(filtro, lambda t: t["stato"] == filtro)(t)][:300]
            nomi = {n.id: n.nome for n in S.topo.negozi}
            for t in righe:
                t["prossimo"] = prossimo_passo(t, u["ruolo"], nomi)
            return resp(request, c, u, "trasferimenti.html", righe=righe, filtro=filtro, conta=conta, FILTRI_TRASF=FILTRI_TRASF,
                        da_shopify=da_shopify.conta_da_destinare(c), troncato=len(tutti) > 300 and filtro == "tutti")

    def dettaglio(request, c, u, tid, http=200, **kw):
        t, righe = T.carica(c, tid)
        ubi_o, ubi_d = calcoli.ubicazioni(c, t["origine"]), calcoli.ubicazioni(c, t["destinazione"])
        for r in righe:
            r["ubicazione"] = ubi_o.get(r["chiave"], "")
            r["ubicazione_arrivo"] = ubi_d.get(r["chiave"], "")
            r["mancanti"] = max(0, (r["spedita"] or 0) - r["ricevuta"] - r["danneggiata"]) if r["spedita"] is not None else 0
        # percorso di prelievo: per scaffale dell'origine; in arrivo per scaffale della destinazione
        if t["stato"] in ("approvato", "in_preparazione"):
            righe.sort(key=lambda r: (ordine_scaffale(r["ubicazione"]), r["prodotto"] or "", r["variante"] or ""))
        elif t["stato"] in ("spedito", "ricevuto"):
            righe.sort(key=lambda r: (ordine_scaffale(r["ubicazione_arrivo"]), r["prodotto"] or "", r["variante"] or ""))
        if t["stato"] == "bozza" and u["ruolo"] == "responsabile":
            # l'approvazione con i numeri davanti: quanto è libero all'origine, quanto ha già la destinazione
            altri = T._uscite_altri(c, t["origine"], tid)
            for r in righe:
                d = {x["negozio"]: x["disponibile"] for x in c.execute(
                    "SELECT negozio, disponibile FROM articolo_negozi WHERE chiave=?", (r["chiave"],))}
                r["libero"] = max(0, (d.get(t["origine"]) or 0) - altri.get(r["chiave"], 0))
                r["ha_gia"] = d.get(t["destinazione"])
        ops = [dict(o) for o in c.execute("SELECT * FROM operazioni WHERE trasferimento_id=? ORDER BY id", (tid,))]
        bloccate = [o for o in ops if o["stato"] in OP_PROBLEMI]
        ops.sort(key=lambda o: (o["stato"] not in OP_PROBLEMI, o["id"]))      # prima i problemi
        storia = c.execute("SELECT * FROM registro WHERE oggetto=? ORDER BY id", (t["codice"],)).fetchall()
        try:
            ultima = int(request.query_params.get("ultima") or 0)
        except ValueError:
            ultima = 0
        return resp(request, c, u, "trasferimento.html", http=http, t=t, righe=righe, ops=ops, storia=storia,
                    differenze=T.differenze_aperte(righe, t["stato"]), prelievo=T.stato_prelievo(c, tid),
                    op_in_corso=any(o["stato"] in ("da_fare", "in_corso") for o in ops), op_bloccate=bloccate,
                    op_fatte=sum(1 for o in ops if o["stato"] == "fatta"), ultima=ultima,
                    dove=request.query_params.get("dove", ""), totale=sum(_q_doc(t, r) for r in righe), **kw)

    def raggruppa(movs, negozio):
        """Anteprima: una riga per articolo, una colonna per negozio Shopify del negozio fisico."""
        stores = [ch.store for ch in S.topo.negozio(negozio).canali]
        gruppi = {}
        for m in movs:
            g = gruppi.setdefault(m["riga"]["id"], {"riga": m["riga"], "celle": {}, "pezzi": 0})
            g["celle"][m["store"]] = m
            g["pezzi"] = max(g["pezzi"], abs(m["delta"]))
        return {"stores": stores, "gruppi": list(gruppi.values()), "pezzi": sum(g["pezzi"] for g in gruppi.values()),
                "mancanti": sum(1 for m in movs if m.get("mancante"))}

    @app.get("/trasferimenti/{tid}", response_class=HTMLResponse)
    def trasferimento(request: Request, tid: int):
        with db() as c:
            u = utente(request, c)
            try:
                return dettaglio(request, c, u, tid)
            except T.ErroreFlusso:
                return vai("/trasferimenti", "Trasferimento non trovato", "errore")

    @app.post("/trasferimenti/{tid}/{azione}")
    async def trasferimento_azione(request: Request, tid: int, azione: str):
        form = await request.form()
        return await run_in_threadpool(_azione, request, tid, azione, form)

    def _interi(form, prefisso):
        out = {}
        for k, v in form.items():
            if k.startswith(prefisso) and str(v).strip() != "":
                try:
                    out[int(k[len(prefisso):])] = int(v)
                except ValueError:
                    raise T.ErroreFlusso(f"«{v}» non è un numero intero")
        return out

    def vuole_json(request):
        return "application/json" in (request.headers.get("accept") or "")

    def _prelievo(request, c, u, tid, azione, form):
        """Lettore e numeri scritti a mano. Con JavaScript rispondono in JSON (niente ricarica), altrimenti redirect."""
        url, json_ = f"/trasferimenti/{tid}", vuole_json(request)
        # azioni ripetibili (ogni lettura vale +1): niente gettone usa-e-getta, ma la firma sì
        if not auth.gettone_valido(request.cookies.get("sessione") or "", form.get("gettone") or ""):
            if json_:
                return JSONResponse({"ok": False, "messaggio": "Pagina scaduta: ricaricala e ripeti"}, status_code=403)
            raise Vietato()
        try:
            id_lettura = (form.get("id_lettura") or "").strip()
            if azione == "scansiona" and id_lettura and not auth.consuma_gettone(c, f"lettura:{tid}:{id_lettura[:64]}"):
                # la stessa lettura ripetuta dopo un errore di rete: già contata, non si conta di nuovo
                info = T.stato_prelievo(c, tid)
                info["messaggio"] = "Lettura già registrata"
            elif azione == "scansiona":
                info = T.scansiona(c, u["username"], tid, form.get("codice"))
            else:
                info = None
                for rid, q in _interi(form, "p_").items():
                    info = T.preleva(c, u["username"], tid, rid, q)
                info = info or T.stato_prelievo(c, tid)
                info["messaggio"] = "Numeri salvati"
        except T.ErroreFlusso as e:
            if json_:
                return JSONResponse({"ok": False, "messaggio": str(e), "tipo": e.tipo, "righe": e.righe,
                                     **T.stato_prelievo(c, tid)}, status_code=409)
            return vai(f"{url}?dove=scanner#scanner", str(e), "errore")
        if info.get("chiave"):
            t, _ = T.carica(c, tid)
            info["ubicazione"] = calcoli.ubicazioni(c, t["origine"]).get(info["chiave"], "")
            info.pop("chiave")
        if json_:
            return JSONResponse({"ok": True, **info})
        if azione == "scansiona":
            return vai(f"{url}?dove=scanner&ultima={info.get('riga', '')}#scanner", info["messaggio"])
        return vai(f"{url}#righe", "Numeri salvati")

    def _azione(request, tid, azione, form):
        solo_responsabile = {"modifica", "approva", "annulla", "differenza"}
        with db() as c:
            u = utente(request, c, "responsabile" if azione in solo_responsabile else None)
            url = f"/trasferimenti/{tid}"
            if azione in ("scansiona", "preleva"):
                return _prelievo(request, c, u, tid, azione, form)
            if not modulo_valido(request, c, form):
                return vai(url, "Azione già eseguita (doppio clic?): ecco lo stato attuale", "info")
            nome, versione = u["username"], form.get("versione")
            try:
                if azione == "modifica":
                    T.modifica_bozza(c, nome, tid, versione, _interi(form, "q_"))
                    return vai(url, "Quantità salvate, trasferimento ancora da approvare")
                if azione == "approva":
                    T.approva(c, nome, tid, versione, _interi(form, "q_"))
                    return vai(url, "Approvato: i pezzi sono prenotati. Shopify non cambia finché la merce non parte.")
                if azione == "prepara":
                    T.inizia_preparazione(c, nome, tid, versione)
                    return vai(url + "?dove=scanner#scanner", "Preparazione iniziata: spara i codici o scrivi i pezzi presi")
                if azione == "spedisci":
                    # i numeri scritti a mano e non ancora salvati si salvano prima dell'anteprima
                    for rid, q in _interi(form, "p_").items():
                        T.preleva(c, nome, tid, rid, q)
                    spedite, motivo = _interi(form, "s_"), (form.get("motivo") or "").strip()
                    if form.get("conferma") != "1":
                        t, righe = T.carica(c, tid)
                        q = {r["id"]: spedite.get(r["id"], r["prelevata"] or 0) for r in righe}
                        if not any(q.values()):
                            raise T.ErroreFlusso("Nessun pezzo preso: spara i codici o scrivi i pezzi presi prima di spedire")
                        parziali = [r for r in righe if q[r["id"]] < (r["approvata"] or 0)]
                        if parziali and not motivo:
                            mancano = sum((r["approvata"] or 0) - q[r["id"]] for r in parziali)
                            raise T.ErroreFlusso(f"Mancano {mancano} pezzi rispetto all'approvato: scrivi perché, poi ricontrolla",
                                                 [f"{r['prodotto']} {r['variante'] or ''}: {q[r['id']]} di {r['approvata']}" for r in parziali])
                        movs = T.movimenti(c, S.topo, t, q, "scarico", t["origine"])
                        # le quantità dell'anteprima viaggiano nel modulo di conferma: si spedisce esattamente questo
                        return dettaglio(request, c, u, tid, anteprima={"azione": "spedisci", "valori": q, "motivo": motivo,
                                         **raggruppa(movs, t["origine"])})
                    T.spedisci(c, S.topo, S.sorgente, cfg.scritture_attive, nome, tid, versione, spedite, motivo)
                    operazioni.avvia_in_sottofondo(cfg.db_path, S.sorgente, tid)
                    return vai(url, "Spedito. Shopify si aggiorna in pochi secondi: l'esito è qui sotto.")
                if azione == "ricevi":
                    buoni, danni = _interi(form, "b_"), _interi(form, "d_")
                    ricevute = {rid: (buoni.get(rid, 0), danni.get(rid, 0)) for rid in set(buoni) | set(danni)}
                    if form.get("conferma") != "1":
                        t, righe = T.carica(c, tid)
                        per_riga = {r["id"]: r for r in righe}
                        for rid, (b, d) in ricevute.items():         # stesso controllo del server, prima dell'anteprima
                            r = per_riga.get(rid)
                            if r and b + d > (r["spedita"] or 0) - r["ricevuta"] - r["danneggiata"]:
                                raise T.ErroreFlusso(f"{r['prodotto']} {r['variante'] or ''}: ne mancano "
                                                     f"{(r['spedita'] or 0) - r['ricevuta'] - r['danneggiata']}, ne hai indicati {b + d}")
                        if not any(b or d for b, d in ricevute.values()):
                            raise T.ErroreFlusso("Nessun pezzo indicato come arrivato")
                        movs = T.movimenti(c, S.topo, t, {k: v[0] for k, v in ricevute.items()}, "carico", t["destinazione"])
                        return dettaglio(request, c, u, tid, anteprima={
                            "azione": "ricevi", "valori_b": buoni, "valori_d": danni, "nota": form.get("nota", ""),
                            "danneggiati": sum(v[1] for v in ricevute.values()), **raggruppa(movs, t["destinazione"])})
                    T.ricevi(c, S.topo, cfg.scritture_attive, nome, tid, ricevute, form.get("nota", ""))
                    operazioni.avvia_in_sottofondo(cfg.db_path, S.sorgente, tid)
                    return vai(url, "Arrivo registrato. Shopify si aggiorna in pochi secondi.")
                if azione == "chiudi-ricezione":
                    T.chiudi_ricezione(c, nome, tid, form.get("nota", ""))
                    return vai(url, "Ricezione chiusa: le differenze passano al responsabile")
                if azione == "differenza":
                    T.chiudi_differenza(c, S.topo, cfg.scritture_attive, nome, int(form.get("riga")), form.get("esito"), form.get("nota", ""))
                    operazioni.avvia_in_sottofondo(cfg.db_path, S.sorgente, tid)
                    return vai(url + "#differenze", "Differenza chiusa")
                if azione == "annulla":
                    T.annulla(c, nome, tid, versione, form.get("motivo", ""))
                    return vai(url, "Trasferimento annullato: i pezzi prenotati tornano liberi")
                if azione == "riprendi":
                    operazioni.avvia_in_sottofondo(cfg.db_path, S.sorgente, tid)
                    return vai(url, "Aggiornamenti ripresi")
            except T.ErroreFlusso as e:
                return dettaglio(request, c, u, tid, http=409, errore=str(e), dettagli=e.dettagli)
        return Response("Azione sconosciuta", status_code=404)

    def _q_doc(t, r):
        return {"bozza": r["proposta"], "approvato": r["approvata"], "in_preparazione": r["approvata"],
                "annullato": r["proposta"]}.get(t["stato"], r["spedita"] if r["spedita"] is not None else r["approvata"]) or 0

    def _documento_trasferimento(c, tid):
        t, righe = T.carica(c, tid)
        # chi prepara gira per gli scaffali dell'origine, chi riceve per quelli della destinazione
        arrivo = t["stato"] in ("spedito", "ricevuto")
        ubi = calcoli.ubicazioni(c, t["destinazione"] if arrivo else t["origine"])
        nomi = {n.id: n.nome for n in S.topo.negozi}
        titolo = {"bozza": "Proposta di trasferimento", "approvato": "Lista di prelievo", "in_preparazione": "Lista di prelievo",
                  "annullato": "Trasferimento annullato"}.get(t["stato"], "Documento di trasferimento")
        out = [{"prodotto": r["prodotto"], "variante": r["variante"], "prodotto_var": _pv(r), "sku": r["sku"], "ean": r["ean"],
                "ubicazione": ubi.get(r["chiave"], ""), "quantita": _q_doc(t, r), "ricevuta": r["ricevuta"] if t["stato"] in ("spedito", "ricevuto") else "",
                "da": nomi[t["origine"]], "a": nomi[t["destinazione"]], "nota": r["nota"] or ""} for r in righe if _q_doc(t, r)]
        out.sort(key=lambda r: (ordine_scaffale(r["ubicazione"]), r["prodotto"] or "", r["variante"] or ""))
        testa = [("Documento", f"{titolo} {t['codice']}"), ("Data e ora", locale(ora())), ("Origine", nomi[t["origine"]]),
                 ("Destinazione", nomi[t["destinazione"]]), ("Stato", STATI[t["stato"]])]
        if t["spedito_il"]:
            testa.append(("Spedito il", locale(t["spedito_il"])))
        return t, titolo, out, testa, [("Righe", len(out)), ("Pezzi", sum(r["quantita"] for r in out))]

    @app.get("/trasferimenti/{tid}/export.{formato}")
    def trasferimento_export(request: Request, tid: int, formato: str):
        with db() as c:
            utente(request, c)
            t, titolo, righe, testa, tot = _documento_trasferimento(c, tid)
        col = [("prodotto", "Prodotto"), ("variante", "Variante"), ("sku", "SKU"), ("ean", "EAN"), ("ubicazione", "Ubicazione"),
               ("quantita", "Quantità"), ("da", "Origine"), ("a", "Destinazione"), ("nota", "Note operative"),
               ("spunta", "Controllato (✓)"), ("annotazioni", "Annotazioni")]
        pdf_col = list(PDF_LISTA)
        firme = ("Preparato da", "Spedito il", "Ricevuto da") if t["stato"] not in ("bozza",) else ("Approvato da",)
        if t["stato"] in ("spedito", "ricevuto"):
            pdf_col = PDF_LISTA[:5] + [("quantita", "Sped.", 12, "c"), ("_note", "Ric.", 12, "c"), ("_note", "Note", 22, "l")]
        return _file(formato, t["codice"], col, righe, testa, tot, titolo, pdf_col, firme, t["codice"])

    @app.get("/trasferimenti/{tid}/stampa", response_class=HTMLResponse)
    def trasferimento_stampa(request: Request, tid: int):
        with db() as c:
            u = utente(request, c)
            t, titolo, righe, testa, tot = _documento_trasferimento(c, tid)
            return resp(request, c, u, "stampa.html", titolo=titolo, righe=righe, testa=testa, tot=tot, t=t)

    # ------------------------------------------------------------------------------------------- anomalie

    @app.get("/anomalie", response_class=HTMLResponse)
    def anomalie(request: Request):
        p = request.query_params
        stato, tipo = p.get("stato", "aperta"), p.get("tipo", "")
        stato = stato if stato in ("aperta", "risolta", "ignorata") else "aperta"
        tipo = tipo if tipo in TIPI_ANOMALIA else ""
        pagina = max(1, int(p.get("pagina") or 1))
        shopify_tipi = compiti.TIPI_SHOPIFY
        with db() as c:
            u = utente(request, c)
            per_tipo = {r["tipo"]: r["n"] for r in c.execute("SELECT tipo, COUNT(*) n FROM anomalie WHERE stato='aperta' GROUP BY tipo")}
            gruppi = None
            if stato == "aperta" and not tipo:
                # vista per gruppi: i tipi in ordine di urgenza, le prime 5 di ciascuno
                gruppi = [{"tipo": t, "n": per_tipo[t], "righe": [dict(r) for r in c.execute(
                              "SELECT * FROM anomalie WHERE stato='aperta' AND tipo=? ORDER BY gravita DESC, id DESC LIMIT 5", (t,))]}
                          for t in TIPI_ANOMALIA if per_tipo.get(t) and t not in shopify_tipi]
                righe = [a for g in gruppi for a in g["righe"]]
                totale, pagine = sum(g["n"] for g in gruppi), 1
            else:
                where, par = ["stato=?"], [stato]
                if tipo:
                    where.append("tipo=?")
                    par.append(tipo)
                else:
                    where.append(f"tipo NOT IN ({','.join('?' * len(shopify_tipi))})")
                    par += list(shopify_tipi)
                w = " AND ".join(where)
                totale = c.execute(f"SELECT COUNT(*) FROM anomalie WHERE {w}", par).fetchone()[0]
                righe = [dict(r) for r in c.execute(f"SELECT * FROM anomalie WHERE {w} ORDER BY gravita DESC, id DESC LIMIT ? OFFSET ?",
                                                     (*par, PER_PAGINA, (pagina - 1) * PER_PAGINA))]
                pagine = max(1, -(-totale // PER_PAGINA))
            return resp(request, c, u, "anomalie.html", righe=righe, gruppi=gruppi, totale=totale, pagina=pagina, pagine=pagine,
                        stato=stato, tipo=tipo, per_tipo=per_tipo, shopify=sum(per_tipo.get(t, 0) for t in shopify_tipi),
                        azioni={a["id"]: azioni_anomalia(c, a) for a in righe}, COSA_FARE=COSA_FARE, IN_BLOCCO=IN_BLOCCO,
                        TIPI_SHOPIFY=shopify_tipi, torna=request.url.path + (f"?{request.url.query}" if request.url.query else ""))

    def azioni_anomalia(c, a):
        """I pulsanti della scheda: portano dove l'anomalia si risolve."""
        t, dedup = a["tipo"], a["dedup"] or ""
        out, link = [], _link_anomalia(c, a)
        if link and t == "differenza_ricezione":
            out.append(("Chiudi la differenza", link + "#differenze"))
        elif link and t == "aggiornamento_fallito":
            out.append(("Apri il movimento", link) if "/shopify/" in link else ("Apri il trasferimento", link + "#operazioni"))
        elif link and t in compiti.TIPI_SHOPIFY:
            out.append(("Sistema", link))
        if t == "ean_doppio" and dedup.count(":") >= 2:
            out.append(("Vedi le varianti", "/inventario?" + urllib.parse.urlencode({"q": dedup.split(":", 2)[2]})))
        if a["chiave"]:
            q = urllib.parse.urlencode({"chiave": a["chiave"]})
            if t == "ean_mancante":
                art = c.execute("SELECT * FROM articoli WHERE chiave=?", (a["chiave"],)).fetchone()
                sg = suggerimento_sku(c, art) if art else None
                if sg:
                    out.append(("Collega al suggerito", "/articolo?" + urllib.parse.urlencode(
                        {"chiave": sg["da"]["chiave"], "collega": sg["verso"]["chiave"]}) + "#associa"))
            if t == "quantita_incoerente":
                out.append(("Conta e rettifica", f"/articolo?{q}#rettifiche"))
            elif t == "canali_non_allineati":
                out.append(("Apri articolo", f"/articolo?{q}#rettifiche"))
            elif t == "associazione_ambigua":
                out.append(("Collega a mano", f"/articolo?{q}#associa"))
            else:
                out.append(("Apri articolo", f"/articolo?{q}"))
        return out

    @app.post("/anomalie/ignora")
    async def anomalie_ignora(request: Request):
        """Ignorare più anomalie con un solo motivo (solo il responsabile). Ciascuna resta nel registro."""
        form = await request.form()
        torna = form.get("torna") or "/anomalie"
        torna = torna if torna.startswith("/anomalie") else "/anomalie"
        with db() as c:
            u = utente(request, c, "responsabile")
            if not modulo_valido(request, c, form):
                return vai(torna, "Già fatto", "info")
            nota = (form.get("nota") or "").strip()
            try:
                ids = sorted({int(x) for x in form.getlist("ids")})
            except ValueError:
                return vai(torna, "Selezione non valida", "errore")
            if not ids:
                return vai(torna, "Seleziona almeno un'anomalia", "errore")
            if not nota:
                return vai(torna, "Scrivi perché le ignori", "errore")
            fatte = 0
            with transazione(c):
                for aid in ids:
                    a = c.execute("SELECT * FROM anomalie WHERE id=? AND stato='aperta'", (aid,)).fetchone()
                    if not a:
                        continue
                    c.execute("UPDATE anomalie SET stato='ignorata', chiusa_da=?, chiusa_il=?, nota=? WHERE id=?",
                              (u["username"], ora(), nota, aid))
                    registra(c, u["username"], "anomalia ignorata", a["dedup"], {"stato": "aperta"}, {"stato": "ignorata", "nota": nota})
                    fatte += 1
        return vai(torna, "Ignorata 1 anomalia" if fatte == 1 else f"Ignorate {fatte} anomalie")

    def _link_anomalia(c, a):
        """Il trasferimento a cui si riferisce un'anomalia di ricezione o di scrittura."""
        tipo, _, resto = a["dedup"].partition(":")
        r = None
        if tipo == "differenza":
            r = c.execute("SELECT trasferimento_id FROM righe WHERE id=?", (resto,)).fetchone()
        elif tipo == "operazione":
            r = c.execute("SELECT trasferimento_id FROM operazioni WHERE id=?", (resto,)).fetchone()
        elif tipo == "specchio":
            r = c.execute("SELECT id FROM trasferimenti WHERE codice=?", (resto.split(":")[0],)).fetchone()
        elif tipo in ("shopify_senza_dest", "shopify_cambiato", "shopify_carico"):
            store, _, tid = resto.partition(":")
            return f"/trasferimenti/shopify/{store}/{tid.rsplit('/', 1)[-1]}"
        return f"/trasferimenti/{r[0]}" if r and r[0] else None

    @app.post("/anomalie/{aid}/{azione}")
    async def anomalia_azione(request: Request, aid: int, azione: str):
        form = await request.form()
        with db() as c:
            u = utente(request, c, "responsabile")
            if not modulo_valido(request, c, form):
                return vai("/anomalie", "Già fatto", "info")
            a = c.execute("SELECT * FROM anomalie WHERE id=?", (aid,)).fetchone()
            nota = (form.get("nota") or "").strip()
            torna = form.get("torna") or "/anomalie"
            torna = torna if torna.startswith("/anomalie") else "/anomalie"
            if not a:
                return vai("/anomalie", "Anomalia non trovata", "errore")
            if azione == "ignora" and a["stato"] == "aperta":
                if not nota:
                    return vai(torna, "Scrivi perché la ignori", "errore")
                with transazione(c):
                    c.execute("UPDATE anomalie SET stato='ignorata', chiusa_da=?, chiusa_il=?, nota=? WHERE id=?", (u["username"], ora(), nota, aid))
                    registra(c, u["username"], "anomalia ignorata", a["dedup"], {"stato": a["stato"]}, {"stato": "ignorata", "nota": nota})
            elif azione == "riapri" and a["stato"] != "aperta":
                with transazione(c):
                    c.execute("UPDATE anomalie SET stato='aperta', chiusa_da=NULL, chiusa_il=NULL WHERE id=?", (aid,))
                    registra(c, u["username"], "anomalia riaperta", a["dedup"], {"stato": a["stato"]}, {"stato": "aperta"})
            return vai(torna, "Fatto")

    # ------------------------------------------------------------------------------------------- articolo

    @app.get("/articolo", response_class=HTMLResponse)
    def articolo(request: Request):
        chiave = request.query_params.get("chiave", "")
        with db() as c:
            u = utente(request, c)
            a = c.execute("SELECT * FROM articoli WHERE chiave=?", (chiave,)).fetchone()
            if not a:
                return vai("/inventario", "Articolo non trovato (forse l'abbinamento è cambiato con l'ultima lettura)", "errore")
            dati = _dati_articolo(c, chiave, a)
            if u["ruolo"] == "responsabile":
                dati["suggerito"] = suggerimento_sku(c, a)
                # «Usa questo»: il modulo di associazione arriva già compilato (resta da premere «Associa»)
                s = dati["suggerito"]
                if s and s["da"]["chiave"] == chiave and request.query_params.get("collega") == s["verso"]["chiave"]:
                    dati["precompila"] = s
            return resp(request, c, u, "articolo.html", **dati)

    def suggerimento_sku(c, a):
        """
        Un solo altro articolo con lo stesso SKU, su negozi Shopify diversi: probabilmente è lo stesso articolo con l'EAN
        mancante o diverso. Resta un suggerimento: l'associazione la fa il responsabile premendo «Associa».
        Si sposta la parte con meno varianti (di solito la sola variante dell'altro negozio).
        """
        if not a["sku"]:
            return None
        canali = lambda k: {r["store"]: r["variant_id"] for r in c.execute("SELECT store, variant_id FROM articolo_canali WHERE chiave=?", (k,))}
        miei = canali(a["chiave"])
        cand = []
        for b in c.execute("SELECT * FROM articoli WHERE UPPER(sku)=UPPER(?) AND chiave!=?", (a["sku"], a["chiave"])).fetchall():
            suoi = canali(b["chiave"])
            if miei and suoi and not set(miei) & set(suoi):
                cand.append((dict(b), suoi))
        if len(cand) != 1:
            return None
        b, suoi = cand[0]
        da, verso, varianti = (dict(a), b, miei) if len(miei) <= len(suoi) else (b, dict(a), suoi)
        store, vid = next(iter(varianti.items()))
        unico = verso["ean"] and c.execute("SELECT COUNT(*) FROM articoli WHERE ean=?", (verso["ean"],)).fetchone()[0] == 1
        return {"da": da, "verso": verso, "store": store, "variant_id": vid, "codice": verso["ean"] if unico else verso["chiave"],
                "motivo": f"stesso SKU {a['sku']}", "altre": len(varianti) - 1}

    def _dati_articolo(c, chiave, a):
        canali = c.execute("""SELECT v.*, ac.chiave, (SELECT motivo FROM associazioni x WHERE x.store=v.store AND x.variant_id=v.variant_id)
                              AS manuale FROM articolo_canali ac JOIN varianti v ON v.store=ac.store AND v.variant_id=ac.variant_id
                              WHERE ac.chiave=?""", (chiave,)).fetchall()
        s = calcoli.soglie(c)
        per_negozio = []
        for n in S.topo.negozi:
            d = c.execute("SELECT * FROM articolo_negozi WHERE chiave=? AND negozio=?", (chiave, n.id)).fetchone()
            ubi = c.execute("SELECT ubicazione FROM ubicazioni WHERE negozio=? AND chiave=?", (n.id, chiave)).fetchone()
            per_negozio.append({"negozio": n, "disp": d, "soglia": regole.soglia(s, n.id, chiave),
                                "propria": (n.id, chiave) in s, "ubicazione": ubi[0] if ubi else ""})
        return {"a": a, "canali": canali, "per_negozio": per_negozio,
                "anomalie": c.execute("SELECT * FROM anomalie WHERE chiave=? ORDER BY stato, gravita DESC", (chiave,)).fetchall(),
                "righe_aperte": c.execute("""SELECT r.*, t.codice, t.stato, t.origine, t.destinazione FROM righe r
                                             JOIN trasferimenti t ON t.id=r.trasferimento_id WHERE r.chiave=?
                                             AND t.stato NOT IN ('ricevuto','annullato') ORDER BY t.id DESC""", (chiave,)).fetchall(),
                "rettifiche": c.execute("SELECT * FROM rettifiche WHERE chiave=? ORDER BY id DESC LIMIT 10", (chiave,)).fetchall()}

    @app.post("/articolo/soglia")
    async def articolo_soglia(request: Request):
        form = await request.form()
        with db() as c:
            u = utente(request, c, "responsabile")
            chiave, negozio = form.get("chiave"), form.get("negozio")
            torna = "/articolo?" + urllib.parse.urlencode({"chiave": chiave})
            if not modulo_valido(request, c, form):
                return vai(torna, "Già salvato", "info")
            prima = c.execute("SELECT minima, obiettivo FROM soglie WHERE negozio=? AND chiave=?", (negozio, chiave)).fetchone()
            with transazione(c):
                if form.get("togli"):
                    c.execute("DELETE FROM soglie WHERE negozio=? AND chiave=?", (negozio, chiave))
                    registra(c, u["username"], "soglia articolo tolta", f"{negozio} {chiave}", dict(prima) if prima else None, None)
                else:
                    try:
                        mi, ob = int(form.get("minima")), int(form.get("obiettivo"))
                    except (TypeError, ValueError):
                        return vai(torna, "Minima e obiettivo devono essere numeri", "errore")
                    if mi < 0 or ob < mi:
                        return vai(torna, "La minima non può essere negativa e l'obiettivo non può essere sotto la minima", "errore")
                    c.execute("INSERT INTO soglie VALUES(?,?,?,?,?,?) ON CONFLICT(negozio, chiave) DO UPDATE SET minima=excluded.minima, "
                              "obiettivo=excluded.obiettivo, modificata_da=excluded.modificata_da, modificata_il=excluded.modificata_il",
                              (negozio, chiave, mi, ob, u["username"], ora()))
                    registra(c, u["username"], "soglia articolo", f"{negozio} {chiave}", dict(prima) if prima else None,
                             {"minima": mi, "obiettivo": ob})
                ubi = (form.get("ubicazione") or "").strip()
                c.execute("DELETE FROM ubicazioni WHERE negozio=? AND chiave=?", (negozio, chiave))
                if ubi:
                    c.execute("INSERT INTO ubicazioni VALUES(?,?,?)", (negozio, chiave, ubi[:40]))
        return vai(torna, "Salvato")

    @app.post("/associazioni")
    async def associa(request: Request):
        form = await request.form()
        with db() as c:
            u = utente(request, c, "responsabile")
            store, vid = form.get("store"), form.get("variant_id")
            torna = "/articolo?" + urllib.parse.urlencode({"chiave": form.get("chiave", "")})
            if not modulo_valido(request, c, form):
                return vai(torna, "Già fatto", "info")
            if form.get("togli"):
                with transazione(c):
                    c.execute("DELETE FROM associazioni WHERE store=? AND variant_id=?", (store, vid))
                    registra(c, u["username"], "associazione tolta", f"{store} {vid}", None, None)
                ricalcola(c, S.topo)
                return vai("/inventario", "Associazione manuale tolta: l'articolo torna all'abbinamento per EAN")
            codice, motivo = (form.get("destinazione") or "").strip(), (form.get("motivo") or "").strip()
            if not motivo:
                return vai(torna, "Scrivi il motivo dell'associazione", "errore")
            dest = c.execute("SELECT chiave FROM articoli WHERE chiave=? OR ean=? OR UPPER(sku)=UPPER(?)", (codice, codice, codice)).fetchall()
            if len(dest) != 1:
                return vai(torna, f"«{codice}» corrisponde a {len(dest)} articoli: indica un EAN o SKU univoco", "errore")
            dchiave = dest[0]["chiave"]
            if c.execute("SELECT 1 FROM articolo_canali WHERE chiave=? AND store=? AND variant_id!=?", (dchiave, store, vid)).fetchone():
                return vai(torna, "L'articolo di destinazione ha già una variante su quel negozio Shopify: prima va risolta quella", "errore")
            if not c.execute("SELECT 1 FROM varianti WHERE store=? AND variant_id=?", (store, vid)).fetchone():
                return vai(torna, "Variante non trovata", "errore")
            with transazione(c):
                c.execute("INSERT INTO associazioni VALUES(?,?,?,?,?,?) ON CONFLICT(store, variant_id) DO UPDATE SET chiave=excluded.chiave, "
                          "motivo=excluded.motivo, creata_da=excluded.creata_da, creata_il=excluded.creata_il",
                          (store, vid, dchiave, motivo, u["username"], ora()))
                registra(c, u["username"], "associazione manuale", f"{store} {vid}", {"chiave": form.get("chiave")},
                         {"chiave": dchiave, "motivo": motivo})
            ricalcola(c, S.topo)
        return vai("/articolo?" + urllib.parse.urlencode({"chiave": dchiave}), "Associazione salvata")

    # ------------------------------------------------------------------------------------------- rettifiche

    @app.post("/rettifiche")
    async def rettifica(request: Request):
        form = await request.form()
        return await run_in_threadpool(_rettifica, request, form)

    def _rettifica(request, form):
        with db() as c:
            u = utente(request, c, "responsabile")
            chiave = form.get("chiave", "")
            torna = "/articolo?" + urllib.parse.urlencode({"chiave": chiave})
            if not modulo_valido(request, c, form):
                return vai(torna, "Già fatto", "info")
            try:
                if form.get("rettifica_id"):
                    rid = int(form.get("rettifica_id"))
                    if form.get("decisione") == "rifiuta":
                        rettifiche.rifiuta(c, u["username"], rid, form.get("nota", ""))
                        return vai(torna, "Rettifica rifiutata")
                    rettifiche.conferma(c, S.topo, S.sorgente, cfg.rettifiche_attive, u["username"], rid)
                    operazioni.avvia_in_sottofondo(cfg.db_path, S.sorgente)
                    return vai(torna, "Rettifica confermata: scrittura in corso")
                rettifiche.richiedi(c, S.topo, S.sorgente, cfg.rettifiche_attive, u["username"], form.get("negozio"), chiave,
                                    form.get("fonte"), form.get("contati") or 0, form.get("motivo"))
                return vai(torna + "#rettifiche", "Anteprima pronta: controlla e conferma")
            except (T.ErroreFlusso, ValueError) as e:
                a = c.execute("SELECT * FROM articoli WHERE chiave=?", (chiave,)).fetchone()
                return resp(request, c, u, "articolo.html", http=409, errore=str(e), dettagli=getattr(e, "dettagli", []),
                            **_dati_articolo(c, chiave, a))

    # ------------------------------------------------------------------------------------------- storico

    @app.get("/storico", response_class=HTMLResponse)
    def storico(request: Request):
        p = request.query_params
        pagina = max(1, int(p.get("pagina") or 1))
        with db() as c:
            u = utente(request, c, "responsabile")
            totale = c.execute("SELECT COUNT(*) FROM registro").fetchone()[0]
            registro_ = c.execute("SELECT * FROM registro ORDER BY id DESC LIMIT ? OFFSET ?", (PER_PAGINA, (pagina - 1) * PER_PAGINA)).fetchall()
            filtro_op = p.get("op", "bloccate")
            w = "WHERE o.stato IN ('conflitto','errore','incerta','da_verificare','da_fare','in_corso')" if filtro_op == "bloccate" else ""
            ops = c.execute(f"""SELECT o.*, t.codice FROM operazioni o LEFT JOIN trasferimenti t ON t.id=o.trasferimento_id
                                {w} ORDER BY o.id DESC LIMIT 200""").fetchall()
            return resp(request, c, u, "storico.html", registro=registro_, ops=ops, filtro_op=filtro_op,
                        pagina=pagina, pagine=max(1, -(-totale // PER_PAGINA)))

    @app.post("/operazioni/{oid}/{decisione}")
    async def operazione_decidi(request: Request, oid: int, decisione: str):
        form = await request.form()
        with db() as c:
            u = utente(request, c, "responsabile")
            torna = form.get("torna") or "/storico"
            if not modulo_valido(request, c, form):
                return vai(torna, "Già fatto", "info")
            try:
                operazioni.decidi(c, u["username"], oid, decisione, form.get("nota", ""))
            except ValueError as e:
                return vai(torna, str(e), "errore")
        if decisione in ("riprova", "non_applicata") and S.sorgente:
            operazioni.avvia_in_sottofondo(cfg.db_path, S.sorgente)
        return vai(torna, "Decisione registrata")

    # ------------------------------------------------------------------------------------------- impostazioni

    @app.get("/impostazioni", response_class=HTMLResponse)
    def impostazioni(request: Request):
        with db() as c:
            u = utente(request, c, "responsabile")
            demo_stato = {k: impostazione(c, k) for k in ("demo_errore_sync", "demo_incerte", "demo_vendita")} if cfg.demo else {}
            return resp(request, c, u, "impostazioni.html",
                        soglie_neg={n.id: calcoli.soglie(c).get((n.id, "*")) for n in S.topo.negozi},
                        priorita=impostazione(c, "priorita_pari"), utenti=c.execute("SELECT * FROM utenti ORDER BY ruolo, username").fetchall(),
                        sedi={r["store"]: r for r in c.execute("SELECT * FROM sedi")}, demo_stato=demo_stato,
                        permessi=impostazione(c, "verifica_permessi"), cfg=cfg, shopify_dal=da_shopify.dal(c),
                        sincronizzazioni=c.execute("SELECT * FROM sincronizzazioni ORDER BY id DESC LIMIT 30").fetchall(),
                        soglie_articolo=c.execute("""SELECT s.*, a.prodotto, a.variante FROM soglie s LEFT JOIN articoli a
                                                     ON a.chiave=s.chiave WHERE s.chiave!='*' ORDER BY a.prodotto""").fetchall())

    @app.post("/impostazioni/{sezione}")
    async def impostazioni_salva(request: Request, sezione: str):
        form = await request.form()
        return await run_in_threadpool(_impostazioni_salva, request, sezione, form)

    def _impostazioni_salva(request, sezione, form):
        with db() as c:
            u = utente(request, c, "responsabile")
            if not modulo_valido(request, c, form):
                return vai("/impostazioni", "Già salvato", "info")
            try:
                if sezione == "soglie":
                    with transazione(c):
                        for n in S.topo.negozi:
                            mi, ob = int(form.get(f"minima_{n.id}")), int(form.get(f"obiettivo_{n.id}"))
                            if mi < 0 or ob < mi:
                                raise ValueError(f"{n.nome}: minima >= 0 e obiettivo >= minima")
                            prima = c.execute("SELECT minima, obiettivo FROM soglie WHERE negozio=? AND chiave='*'", (n.id,)).fetchone()
                            c.execute("UPDATE soglie SET minima=?, obiettivo=?, modificata_da=?, modificata_il=? WHERE negozio=? AND chiave='*'",
                                      (mi, ob, u["username"], ora(), n.id))
                            registra(c, u["username"], "soglie predefinite", n.nome, dict(prima), {"minima": mi, "obiettivo": ob})
                        if form.get("priorita") in [n.id for n in S.topo.negozi]:
                            salva_impostazione(c, "priorita_pari", form.get("priorita"))
                    return vai("/impostazioni", "Soglie salvate")
                if sezione == "shopify_dal":
                    try:
                        nuova = date.fromisoformat(form.get("dal") or "")
                    except ValueError:
                        raise ValueError("Data non valida")
                    prima = da_shopify.dal(c)
                    salva_impostazione(c, "trasferimenti_shopify_dal", nuova.isoformat())
                    registra(c, u["username"], "data trasferimenti Shopify", None, {"dal": prima}, {"dal": nuova.isoformat()})
                    if S.pianificatore:
                        S.pianificatore.richiedi()
                    return vai("/impostazioni#shopify", "Data salvata: i trasferimenti si rileggono con la prossima lettura")
                if sezione == "utente":
                    auth.crea_utente(c, form.get("username", ""), form.get("nome", ""), form.get("ruolo", ""), form.get("password", ""))
                    registra(c, u["username"], "utente salvato", form.get("username"), None, {"ruolo": form.get("ruolo")})
                    return vai("/impostazioni#utenti", "Utente salvato")
                if sezione == "disattiva":
                    if form.get("username") == u["username"]:
                        raise ValueError("Non puoi disattivare te stesso")
                    c.execute("UPDATE utenti SET attivo=0 WHERE username=?", (form.get("username"),))
                    c.execute("DELETE FROM sessioni WHERE utente_id=(SELECT id FROM utenti WHERE username=?)", (form.get("username"),))
                    registra(c, u["username"], "utente disattivato", form.get("username"))
                    return vai("/impostazioni#utenti", "Utente disattivato")
                if sezione == "permessi" and not cfg.demo and S.sorgente:
                    esito = S.sorgente.verifica_permessi()
                    salva_impostazione(c, "verifica_permessi", {"il": ora(), "esito": esito})
                    return vai("/impostazioni#integrazione", "Permessi verificati")
                if sezione == "demo" and cfg.demo:
                    salva_impostazione(c, "demo_errore_sync", bool(form.get("demo_errore_sync")))
                    salva_impostazione(c, "demo_incerte", max(0, int(form.get("demo_incerte") or 0)))
                    salva_impostazione(c, "demo_vendita", bool(form.get("demo_vendita")))
                    return vai("/impostazioni#demo", "Simulazioni aggiornate")
                if sezione == "ripristina" and cfg.demo:
                    c.close()
                    ripristina_demo(cfg, S.sorgente)
                    r = vai("/login", "Demo ripristinata: accedi di nuovo")
                    r.delete_cookie("sessione")
                    return r
            except ValueError as e:
                return vai("/impostazioni", str(e), "errore")
        return Response("Sezione sconosciuta", status_code=404)

    # ------------------------------------------------------------------------------------------- demo

    @app.get("/demo/img/{n}.svg")
    def demo_img(n: int):
        if not cfg.demo:
            return Response(status_code=404)
        from .demo import svg_prodotto
        with db() as c:
            svg = svg_prodotto(c, n)
        return Response(svg or "", media_type="image/svg+xml", status_code=200 if svg else 404,
                        headers={"Cache-Control": "max-age=86400"})

    return app


def _pv(r):
    return (r.get("prodotto") or "") + (f" – {r['variante']}" if r.get("variante") else "")
