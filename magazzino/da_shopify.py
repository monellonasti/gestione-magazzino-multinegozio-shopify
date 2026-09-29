"""
Trasferimenti fatti nell'admin di Shopify che il programma deve completare. Sono di due tipi.

USCITE SENZA DESTINAZIONE («Negozio A → nessuna destinazione»)
  Shopify ha scalato la merce solo sul negozio Shopify dove il trasferimento è stato creato. Il programma suggerisce la
  destinazione (nota, articoli presenti, sotto scorta) e alla scelta: scarico sugli altri canali del negozio di origine
  (spuntabile), carico a destinazione subito o alla ricezione. Oppure «nessuna destinazione» o «gestito fuori».

CARICHI DA FORNITORE («nessuna origine → Negozio A»)
  Shopify ha caricato la merce solo sul negozio Shopify dove è stato registrato il carico. Si sceglie come ripartirla:
    - tutto al negozio di arrivo, con la duplicazione 1:1 sugli altri suoi canali (stessa merce fisica);
    - diviso con l'altro negozio (metà, un terzo... con la regola del pezzo dispari scelta ogni volta, righe modificabili):
      la quota dell'altro negozio diventa un trasferimento, da preparare oppure già arrivato;
    - stesse quantità anche all'altro negozio (1:1): il fornitore ha consegnato la stessa merce anche lì;
    - stessa consegna già registrata sull'altro canale (i doppioni fatti a mano sui due canali): nessuna scrittura;
    - gestito fuori dal programma (responsabile).

Ogni trasferimento Shopify si decide una volta sola: la decisione e le scritture stanno nella stessa transazione.
"""
import json

from . import calcoli, operazioni, regole, trasferimenti as T
from .db import impostazione, ora, registra, salva_impostazione, transazione
from .negozi import Topologia
from .sync import apri_anomalia, chiudi_anomalia

PARTITI = ("IN_PROGRESS", "TRANSFERRED")
ESITI = {"da_destinare": "Da sistemare", "non_partito": "Non ancora partito o ricevuto", "destinato": "Destinato",
         "uscita": "Uscita senza destinazione", "gestito_fuori": "Gestito fuori dal programma",
         "annullato_shopify": "Annullato su Shopify", "tutto_a": "Tenuto tutto al negozio di arrivo",
         "diviso": "Diviso fra i negozi", "uguale_1_1": "Stesse quantità anche all'altro negozio",
         "stessa_consegna": "Stessa consegna già registrata sull'altro canale"}
MODI_CARICO = ("tutto", "dividi", "uguale", "stessa_consegna", "gestito_fuori")


def registra_letti(c, topo: Topologia, store: str, lista: list) -> int:
    """Salva uscite senza destinazione e carichi senza origine di una nostra sede. Ritorna quanti ne ha considerati."""
    sede = c.execute("SELECT location_id FROM sedi WHERE store=?", (store,)).fetchone()
    if not sede:
        return 0
    n = 0
    with transazione(c):
        for t in lista:
            if t["destinazione"] is None and t["origine_location"] == sede[0]:
                verso, righe = "uscita", [{**r, "quantita": r.get("spedita") or 0} for r in t["righe"]]
            elif t.get("origine_location") is None and t.get("origine_nome") is None and t.get("destinazione_location") == sede[0]:
                verso, righe = "ingresso", [{**r, "quantita": r.get("ricevuta") or 0} for r in t["righe"]]
            else:
                continue                      # fra sedi, o da/verso un'altra sede (magazzino esterno...): non ci riguarda
            pezzi = sum(r["quantita"] for r in righe)
            partito = t["stato"] in PARTITI and pezzi > 0
            vecchio = c.execute("SELECT esito, pezzi FROM trasferimenti_shopify WHERE store=? AND transfer_id=?",
                                (store, t["transfer_id"])).fetchone()
            if vecchio is None:
                if t["stato"] == "CANCELED":
                    continue
                esito = "da_destinare" if partito else "non_partito"
                c.execute("""INSERT INTO trasferimenti_shopify(store, transfer_id, nome, stato_shopify, creato_il, nota,
                             origine_location, origine_nome, pezzi, righe, letto_il, esito, verso, destinazione_location,
                             destinazione_nome) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (store, t["transfer_id"], t["nome"], t["stato"], t["creato_il"], t["nota"], t["origine_location"],
                           t["origine_nome"], pezzi, json.dumps(righe), ora(), esito, verso, t.get("destinazione_location"),
                           t.get("destinazione")))
            else:
                esito = vecchio["esito"]
                if esito in ("da_destinare", "non_partito"):
                    esito = "annullato_shopify" if t["stato"] == "CANCELED" else ("da_destinare" if partito else "non_partito")
                    c.execute("""UPDATE trasferimenti_shopify SET stato_shopify=?, nota=?, pezzi=?, righe=?, letto_il=?, esito=?
                                 WHERE store=? AND transfer_id=?""",
                              (t["stato"], t["nota"], pezzi, json.dumps(righe), ora(), esito, store, t["transfer_id"]))
                else:
                    c.execute("UPDATE trasferimenti_shopify SET stato_shopify=?, letto_il=? WHERE store=? AND transfer_id=?",
                              (t["stato"], ora(), store, t["transfer_id"]))
                    if pezzi > vecchio["pezzi"]:
                        apri_anomalia(c, f"shopify_cambiato:{store}:{t['transfer_id']}", "shopify_a_mano", 3,
                                      None, f"{t['nome']} su {topo.nomi_store[store]} è cambiato dopo la decisione",
                                      f"Su Shopify ora risultano {pezzi} pezzi {'spediti' if verso == 'uscita' else 'ricevuti'}, "
                                      f"al momento della decisione erano {vecchio['pezzi']}. La differenza va sistemata a mano.")
            dedup = _dedup(verso, store, t["transfer_id"])
            if esito == "da_destinare" and verso == "uscita":
                apri_anomalia(c, dedup, "trasferimento_senza_destinazione", 2, None,
                              f"Trasferimento Shopify senza destinazione: {t['nome']} da {t['origine_nome']} ({pezzi} pezzi)",
                              f"Creato su {topo.nomi_store[store]}: la merce è uscita ma non risulta arrivata da nessuna "
                              f"parte. Scegli la destinazione per aggiornare le giacenze.")
            elif esito == "da_destinare":
                n_neg = topo.negozio_di(store)
                apri_anomalia(c, dedup, "carico_fornitore", 2, None,
                              f"Carico da fornitore da ripartire: {t['nome']} a {n_neg.nome} ({pezzi} pezzi)",
                              f"Registrato solo su {topo.nomi_store[store]}: scegli se tenerlo tutto a {n_neg.nome} "
                              f"(duplicandolo sugli altri canali), dividerlo con l'altro negozio o segnarlo come doppione.")
            else:
                chiudi_anomalia(c, dedup, "controllo automatico", ESITI.get(esito))
            n += 1
    return n


def _dedup(verso, store, transfer_id):
    return f"{'shopify_senza_dest' if verso == 'uscita' else 'shopify_carico'}:{store}:{transfer_id}"


def carica_ts(c, store: str, numero: str):
    r = c.execute("SELECT * FROM trasferimenti_shopify WHERE store=? AND (transfer_id=? OR transfer_id LIKE ?)",
                  (store, numero, f"%/{numero}")).fetchone()
    if not r:
        raise T.ErroreFlusso("Trasferimento Shopify non trovato: aggiorna i dati e riprova")
    ts = dict(r)
    ts["righe_lette"] = ts["righe"]                       # il testo salvato: serve a riconoscere una rilettura nel frattempo
    ts["righe"] = json.loads(ts["righe"])
    ts["numero"] = ts["transfer_id"].rsplit("/", 1)[-1]
    return ts


def _prendi_in_decisione(c, ts, cosa):
    """
    Dentro la transazione: il movimento passa a «in decisione» solo se è ancora da destinare E ha ancora le righe che
    chi decide ha visto. Una lettura da Shopify arrivata un attimo prima (altri pezzi spediti o accettati) cambierebbe
    i numeri sotto la decisione, e il controllo «cambiato dopo la decisione» non se ne accorgerebbe più.
    """
    n = c.execute("UPDATE trasferimenti_shopify SET esito='in_decisione' WHERE store=? AND transfer_id=? "
                  "AND esito='da_destinare' AND righe=?", (ts["store"], ts["transfer_id"], ts["righe_lette"])).rowcount
    if n == 1:
        return
    ora_ = c.execute("SELECT esito, pezzi FROM trasferimenti_shopify WHERE store=? AND transfer_id=?",
                     (ts["store"], ts["transfer_id"])).fetchone()
    if ora_ and ora_["esito"] == "da_destinare":
        raise T.ErroreFlusso(f"Su Shopify {ts['nome']} è cambiato mentre decidevi (ora {ora_['pezzi']} pezzi): "
                             f"ricarica la pagina e ricontrolla prima di confermare.")
    raise T.ErroreFlusso(f"Qualcun altro ha appena deciso questo {cosa}: ricarica la pagina.")


def elenco(c, esiti=("da_destinare",)) -> list:
    segna = ",".join("?" * len(esiti))
    out = []
    for r in c.execute(f"SELECT * FROM trasferimenti_shopify WHERE esito IN ({segna}) ORDER BY creato_il DESC", esiti):
        x = dict(r)
        x["righe"] = json.loads(x["righe"])
        x["numero"] = x["transfer_id"].rsplit("/", 1)[-1]
        out.append(x)
    return out


def abbina_righe(c, topo: Topologia, ts: dict) -> list:
    """Ogni riga del trasferimento Shopify con il suo articolo nel programma (per variante del negozio Shopify)."""
    disp = calcoli.disponibilita(c)
    out = []
    for r in ts["righe"]:
        if not r["quantita"]:
            continue
        a = c.execute("""SELECT a.* FROM articolo_canali ac JOIN articoli a ON a.chiave=ac.chiave
                         WHERE ac.store=? AND ac.variant_id=?""", (ts["store"], r["variant_id"])).fetchone()
        out.append({**r, "chiave": a["chiave"] if a else None, "articolo": dict(a) if a else None,
                    "disp": disp.get(a["chiave"], {}) if a else {}})
    return out


def candidati(c, topo: Topologia, ts: dict, righe: list) -> list:
    """Negozi possibili come destinazione, dal più probabile, con i motivi in parole."""
    origine = topo.negozio_di(ts["store"])
    s, corso = calcoli.soglie(c), calcoli.in_corso(c)
    nota = (ts.get("nota") or "").lower()
    out = []
    for n in topo.negozi:
        if n.id == origine.id:
            continue
        presenti = [r for r in righe if r["chiave"] and n.id in r["disp"]]
        sotto = [r for r in presenti if max(0, r["disp"][n.id] or 0) + (corso[r["chiave"]]["arrivo"][n.id] if r["chiave"] in corso else 0)
                 < regole.soglia(s, n.id, r["chiave"]).minima]
        in_nota = n.nome.lower() in nota or any(p in nota for p in n.nome.lower().split() if len(p) > 3 and p != "negozio")
        motivi = []
        if in_nota:
            motivi.append(f"la nota del trasferimento nomina {n.nome} («{ts['nota']}»)")
        motivi.append(f"{len(presenti)} articoli su {len(righe)} esistono a {n.nome}")
        if sotto:
            motivi.append(f"{len(sotto)} sono sotto la scorta minima a {n.nome}")
        out.append({"negozio": n, "punteggio": (1000 if in_nota else 0) + 10 * len(presenti) + len(sotto),
                    "presenti": len(presenti), "sotto_scorta": len(sotto), "motivi": motivi})
    out.sort(key=lambda x: -x["punteggio"])
    return out


def movimenti(c, topo: Topologia, ts: dict, righe: list, scelta: str, scala_specchi: bool, subito: bool) -> list:
    """Anteprima: cosa verrà scritto su Shopify."""
    origine = topo.negozio_di(ts["store"])
    out = []
    for r in righe:
        if not r["chiave"]:
            continue
        if scala_specchi:
            for ch in origine.canali:
                if ch.store != ts["store"]:
                    out.append(_mov(c, r, ch, -r["quantita"], "scarico"))
        if scelta not in ("uscita", "gestito_fuori") and subito:
            for ch in topo.negozio(scelta).canali:
                out.append(_mov(c, r, ch, r["quantita"], "carico"))
    return out


def _mov(c, r, ch, delta, tipo):
    v = c.execute("""SELECT v.variant_id, v.item_id, s.location_id FROM articolo_canali ac JOIN varianti v
                     ON v.store=ac.store AND v.variant_id=ac.variant_id JOIN sedi s ON s.store=ac.store
                     WHERE ac.chiave=? AND ac.store=?""", (r["chiave"], ch.store)).fetchone()
    return {"riga": r, "canale": ch, "store": ch.store, "delta": delta, "tipo": tipo, "mancante": v is None,
            "variant_id": v["variant_id"] if v else None, "item_id": v["item_id"] if v else None,
            "location_id": v["location_id"] if v else None}


def decidi(c, topo: Topologia, scritture_attive: bool, utente: str, ruolo: str, store: str, numero: str, scelta: str,
           scala_specchi: bool, subito: bool, nota: str = "") -> tuple:
    """
    Applica la decisione. Ritorna (id del trasferimento creato o None, id delle operazioni da eseguire).
    scelta: id di un negozio fisico, "uscita" (nessuna destinazione) o "gestito_fuori" (solo responsabile).
    """
    ts = carica_ts(c, store, numero)
    if ts["esito"] != "da_destinare":
        raise T.ErroreFlusso(f"{ts['nome']} è già stato deciso ({ESITI.get(ts['esito'], ts['esito'])}): nulla da fare.")
    if ts["verso"] != "uscita":
        raise T.ErroreFlusso("Questo è un carico da fornitore: si ripartisce, non si destina")
    origine = topo.negozio_di(store)
    if scelta == "gestito_fuori":
        if ruolo != "responsabile":
            raise T.ErroreFlusso("Solo il responsabile può segnarlo come gestito fuori dal programma")
        if not (nota or "").strip():
            raise T.ErroreFlusso("Scrivi come è stato gestito (per esempio: caricato con lo script il 19/09)")
    elif scelta != "uscita":
        if scelta == origine.id or scelta not in [n.id for n in topo.negozi]:
            raise T.ErroreFlusso("Scegli un negozio di destinazione diverso da quello di origine")
    if scelta != "gestito_fuori" and not scritture_attive:
        raise T.ErroreFlusso("Le modifiche alle giacenze su Shopify sono disattivate: la destinazione si potrà confermare "
                             "quando saranno accese. Nel frattempo il movimento resta in elenco.")
    righe = abbina_righe(c, topo, ts)
    non_abbinate = [r for r in righe if not r["chiave"]]
    ids, tid = [], None
    with transazione(c):
        _prendi_in_decisione(c, ts, "trasferimento")
        documento = f"logistics://magazzino/shopify/{store}/{ts['numero']}"
        if scelta in ("uscita", "gestito_fuori"):
            esito = scelta
            if scelta == "uscita" and scala_specchi:
                for m in movimenti(c, topo, ts, righe, scelta, True, False):
                    ids += _pianifica_libera(c, topo, ts, m, origine.id, documento)
        else:
            esito = "destinato"
            tid = _crea_trasferimento(c, topo, ts, righe, origine.id, scelta, utente, subito)
            t, righe_t = T.carica(c, tid)
            q = {rt["id"]: rt["spedita"] for rt in righe_t}
            if scala_specchi:
                altri = [m for m in T.movimenti(c, topo, t, q, "scarico", origine.id) if m["store"] != store]
                ids += T._pianifica(c, t, altri, "scarico", origine.id, "shopify")
            if subito:
                for rt in righe_t:
                    ev = c.execute("INSERT INTO ricezioni(riga_id, buoni, danneggiati, nota, da, il) VALUES(?,?,0,?,?,?)",
                                   (rt["id"], rt["spedita"], "carico diretto da trasferimento Shopify", utente, ora())).lastrowid
                    c.execute("UPDATE righe SET ricevuta=? WHERE id=?", (rt["spedita"], rt["id"]))
                    ids += T._pianifica(c, t, T.movimenti(c, topo, t, {rt["id"]: rt["spedita"]}, "carico", scelta), "carico",
                                        scelta, f"ric{ev}")
                c.execute("UPDATE trasferimenti SET stato='ricevuto', ricevuto_da=?, ricevuto_il=?, versione=versione+1 WHERE id=?",
                          (utente, ora(), tid))
        for r in non_abbinate:
            apri_anomalia(c, f"shopify_riga:{store}:{r['line_id']}", "shopify_a_mano", 2, None,
                          f"Riga non abbinata in {ts['nome']}: {r['titolo']}",
                          f"EAN {r['ean'] or '-'}, SKU {r['sku'] or '-'}, {r['quantita']} pezzi: la variante non è fra gli articoli "
                          f"del programma (non tracciata o senza abbinamento). Aggiorna le giacenze a mano.")
        c.execute("""UPDATE trasferimenti_shopify SET esito=?, trasferimento_id=?, deciso_da=?, deciso_il=?, nota_decisione=?
                     WHERE store=? AND transfer_id=?""", (esito, tid, utente, ora(), nota, store, ts["transfer_id"]))
        registra(c, utente, "trasferimento Shopify deciso", f"{ts['nome']} {store}", {"esito": "da_destinare"},
                 {"esito": esito, "destinazione": scelta if esito == "destinato" else None, "scala_specchi": scala_specchi,
                  "carico_subito": subito, "nota": nota})
        chiudi_anomalia(c, f"shopify_senza_dest:{store}:{ts['transfer_id']}", utente, ESITI[esito])
    return tid, ids


def _crea_trasferimento(c, topo, ts, righe, origine, destinazione, utente, subito):
    """Il trasferimento nel programma che rappresenta quello di Shopify: nasce già spedito."""
    per_articolo = {}
    for r in righe:
        if r["chiave"]:
            per_articolo[r["chiave"]] = per_articolo.get(r["chiave"], 0) + r["quantita"]
    if not per_articolo:
        raise T.ErroreFlusso("Nessuna riga del trasferimento corrisponde ad articoli del programma: va gestito a mano")
    n = (c.execute("SELECT COALESCE(MAX(id), 0) FROM trasferimenti").fetchone()[0] or 0) + 1
    codice = f"TR-{n:04d}"
    nota = (f"Da trasferimento Shopify {ts['nome']} su {topo.nomi_store[ts['store']]} (senza destinazione), "
            f"destinazione scelta da {utente}" + (", caricato subito" if subito else ""))
    tid = c.execute("""INSERT INTO trasferimenti(codice, origine, destinazione, stato, tipo, note, creato_da, creato_il,
                       approvato_da, approvato_il, spedito_da, spedito_il) VALUES(?,?,?, 'spedito', 'da Shopify', ?,?,?,?,?,?,?)""",
                    (codice, origine, destinazione, nota, utente, ora(), "Shopify", ts["creato_il"], "Shopify", ts["creato_il"])).lastrowid
    for chiave, q in per_articolo.items():
        a = c.execute("SELECT * FROM articoli WHERE chiave=?", (chiave,)).fetchone()
        c.execute("""INSERT INTO righe(trasferimento_id, chiave, prodotto, variante, sku, ean, immagine, nota, proposta, approvata,
                     prelevata, spedita) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (tid, chiave, a["prodotto"], a["variante"], a["sku"], a["ean"], a["immagine"], f"da {ts['nome']}", q, q, q, q))
    registra(c, utente, "trasferimento creato", codice, None, {"da_shopify": ts["nome"], "origine": origine,
                                                                "destinazione": destinazione, "pezzi": sum(per_articolo.values())})
    return tid


def _pianifica_libera(c, topo, ts, m, negozio, documento, tipo="scarico"):
    """Scrittura legata a un trasferimento Shopify ma senza trasferimento del programma (specchi, stessa merce)."""
    r = m["riga"]
    if m["mancante"]:
        apri_anomalia(c, f"shopify_specchio:{ts['store']}:{r['line_id']}:{m['store']}", "aggiornamento_fallito", 3, r["chiave"],
                      f"Da aggiornare a mano: {r['titolo']} su {topo.nomi_store[m['store']]}",
                      f"{ts['nome']}: l'articolo non esiste su questo canale, il movimento ({tipo}) non è stato scritto.")
        return []
    return [operazioni.pianifica(c, chiave=f"{ts['store']}:{ts['numero']}:{r['line_id'].rsplit('/', 1)[-1]}:{tipo}:{m['store']}",
                                 tipo=tipo, negozio=negozio, store=m["store"], variant_id=m["variant_id"],
                                 item_id=m["item_id"], location_id=m["location_id"], delta=m["delta"], documento=documento)]


def conta_da_destinare(c) -> int:
    return c.execute("SELECT COUNT(*) FROM trasferimenti_shopify WHERE esito='da_destinare'").fetchone()[0]


def dal(c) -> str | None:
    return impostazione(c, "trasferimenti_shopify_dal")


# ------------------------------------------------------------------------------------------ carichi da fornitore

def doppi(c, topo: Topologia, ts: dict) -> list:
    """Lo stesso movimento registrato anche su un altro canale dello stesso negozio: stessi pezzi, entro due giorni."""
    gemelli = [ch.store for ch in topo.negozio_di(ts["store"]).canali if ch.store != ts["store"]]
    if not gemelli:
        return []
    out = []
    for x in c.execute(f"""SELECT * FROM trasferimenti_shopify WHERE store IN ({','.join('?' * len(gemelli))}) AND verso=?
                           AND pezzi=? AND ABS(julianday(creato_il) - julianday(?)) <= 2""",
                       (*gemelli, ts["verso"], ts["pezzi"], ts["creato_il"])):
        x = dict(x)
        x["numero"] = x["transfer_id"].rsplit("/", 1)[-1]
        out.append(x)
    return out


def regola_predefinita(c) -> dict:
    return impostazione(c, "regola_fornitore") or {"frazione": "1/2", "dispari": "a"}


def quote_altro(righe: list, modo: str, frazione: str = "1/2", dispari: str = "a", modifiche: dict | None = None) -> list:
    """
    Pezzi che vanno all'altro negozio, riga per riga (stesso ordine di `righe`). Le righe non abbinate restano a 0.
    modifiche: {indice riga: pezzi} scritti a mano dopo aver applicato la regola.
    """
    q = [r["quantita"] if r["chiave"] else 0 for r in righe]
    if modo == "dividi":
        if frazione not in regole.FRAZIONI:
            raise T.ErroreFlusso("Scegli quanto va all'altro negozio")
        try:
            out = regole.ripartisci(q, *regole.FRAZIONI[frazione], dispari)
        except ValueError:
            raise T.ErroreFlusso("Scegli la regola per il pezzo dispari")
    elif modo == "uguale":
        out = list(q)
    else:
        out = [0] * len(q)
    for i, v in (modifiche or {}).items():
        if not 0 <= i < len(out) or not righe[i]["chiave"]:
            continue
        # anche con «stesse quantità» all'altro negozio non va più di quanto è arrivato: altrimenti si caricherebbe
        # su Shopify merce che nessuno ha consegnato, senza passare dal responsabile
        if v < 0 or (modo in ("dividi", "uguale") and v > q[i]):
            raise T.ErroreFlusso(f"{righe[i]['titolo']}: all'altro negozio vanno da 0 a {q[i]} pezzi")
        out[i] = v
    return out


def movimenti_carico(c, topo: Topologia, ts: dict, righe: list, modo: str, altro: str, quote: list,
                     scala_specchi: bool, altro_subito: bool) -> list:
    """Anteprima delle scritture su Shopify, nell'ordine in cui verranno fatte."""
    arrivo = topo.negozio_di(ts["store"])
    out = []
    if modo in ("stessa_consegna", "gestito_fuori"):
        return out
    for r, k in zip(righe, quote):
        if not r["chiave"]:
            continue
        if scala_specchi:
            for ch in arrivo.canali:
                if ch.store != ts["store"]:
                    out.append({**_mov(c, r, ch, r["quantita"], "fornitore"), "spiega": "duplicazione 1:1 del carico"})
        if k and modo == "uguale":
            for ch in topo.negozio(altro).canali:
                out.append({**_mov(c, r, ch, k, "fornitore"), "spiega": "stessa merce anche qui"})
        if k and modo == "dividi" and altro_subito:
            for ch in arrivo.canali:
                out.append({**_mov(c, r, ch, -k, "scarico"), "spiega": f"quota per {topo.negozio(altro).nome}"})
            for ch in topo.negozio(altro).canali:
                out.append({**_mov(c, r, ch, k, "carico"), "spiega": f"quota da {arrivo.nome}"})
    return out


def decidi_carico(c, topo: Topologia, scritture_attive: bool, utente: str, ruolo: str, store: str, numero: str, modo: str,
                  altro: str, quote: list, scala_specchi: bool, altro_subito: bool, nota: str = "",
                  frazione: str | None = None, dispari: str | None = None) -> tuple:
    """Applica la ripartizione di un carico da fornitore. Ritorna (id del trasferimento creato o None, id operazioni)."""
    ts = carica_ts(c, store, numero)
    if ts["esito"] != "da_destinare":
        raise T.ErroreFlusso(f"{ts['nome']} è già stato deciso ({ESITI.get(ts['esito'], ts['esito'])}): nulla da fare.")
    if ts["verso"] != "ingresso":
        raise T.ErroreFlusso("Questo non è un carico da fornitore")
    if modo not in MODI_CARICO:
        raise T.ErroreFlusso("Scegli come ripartire il carico")
    arrivo = topo.negozio_di(store)
    if modo in ("dividi", "uguale") and (altro == arrivo.id or altro not in [n.id for n in topo.negozi]):
        raise T.ErroreFlusso("Scegli l'altro negozio")
    if modo == "gestito_fuori" and ruolo != "responsabile":
        raise T.ErroreFlusso("Solo il responsabile può segnarlo come gestito fuori dal programma")
    if modo in ("gestito_fuori", "stessa_consegna") and not (nota or "").strip():
        raise T.ErroreFlusso("Scrivi una nota: come è stato gestito, o di quale consegna è il doppione")
    righe = abbina_righe(c, topo, ts)
    if len(quote) != len(righe):
        raise T.ErroreFlusso("Le righe sono cambiate: ricarica la pagina")
    if modo in ("dividi", "uguale") and not any(quote):
        raise T.ErroreFlusso("Nessun pezzo per l'altro negozio: scegli «tutto al negozio di arrivo»")
    movs = movimenti_carico(c, topo, ts, righe, modo, altro, quote, scala_specchi, altro_subito)
    if (movs or (modo == "dividi" and any(quote))) and not scritture_attive:
        raise T.ErroreFlusso("Le modifiche alle giacenze su Shopify sono disattivate: la ripartizione si potrà confermare "
                             "quando saranno accese. Nel frattempo il carico resta in elenco.")
    esito = {"tutto": "tutto_a", "dividi": "diviso", "uguale": "uguale_1_1"}.get(modo, modo)
    documento = f"logistics://magazzino/shopify/{store}/{ts['numero']}"
    ids, tid = [], None
    with transazione(c):
        _prendi_in_decisione(c, ts, "carico")
        # 1. duplicazione 1:1 sugli altri canali del negozio di arrivo, e stessa merce all'altro negozio
        for m in movs:
            if m["tipo"] == "fornitore":
                ids += _pianifica_libera(c, topo, ts, m, topo.negozio_di(m["store"]).id, documento, "fornitore")
        # 2. quota dell'altro negozio: un trasferimento del programma
        if modo == "dividi":
            tid = _crea_ripartizione(c, topo, ts, righe, quote, arrivo.id, altro, utente, ruolo, altro_subito)
            if altro_subito:
                t, righe_t = T.carica(c, tid)
                q = {rt["id"]: rt["spedita"] for rt in righe_t}
                ids += T._pianifica(c, t, T.movimenti(c, topo, t, q, "scarico", arrivo.id), "scarico", arrivo.id, "fornitore")
                for rt in righe_t:
                    ev = c.execute("INSERT INTO ricezioni(riga_id, buoni, danneggiati, nota, da, il) VALUES(?,?,0,?,?,?)",
                                   (rt["id"], rt["spedita"], "quota di un carico da fornitore, già arrivata", utente, ora())).lastrowid
                    ids += T._pianifica(c, t, T.movimenti(c, topo, t, {rt["id"]: rt["spedita"]}, "carico", altro), "carico",
                                        altro, f"ric{ev}")
        for r in righe:
            if not r["chiave"]:
                apri_anomalia(c, f"shopify_riga:{store}:{r['line_id']}", "shopify_a_mano", 2, None,
                              f"Riga non abbinata in {ts['nome']}: {r['titolo']}",
                              f"EAN {r['ean'] or '-'}, SKU {r['sku'] or '-'}, {r['quantita']} pezzi: la variante non è fra gli "
                              f"articoli del programma. Duplicazione e ripartizione vanno fatte a mano.")
        c.execute("""UPDATE trasferimenti_shopify SET esito=?, trasferimento_id=?, deciso_da=?, deciso_il=?, nota_decisione=?
                     WHERE store=? AND transfer_id=?""", (esito, tid, utente, ora(), nota, store, ts["transfer_id"]))
        if modo == "dividi" and frazione and dispari:
            salva_impostazione(c, "regola_fornitore", {"frazione": frazione, "dispari": dispari})
        registra(c, utente, "carico da fornitore ripartito", f"{ts['nome']} {store}", {"esito": "da_destinare"},
                 {"esito": esito, "altro": altro if modo in ("dividi", "uguale") else None, "frazione": frazione,
                  "dispari": dispari, "quote": {r["titolo"]: k for r, k in zip(righe, quote) if k},
                  "duplicazione_1_1": scala_specchi, "quota_gia_arrivata": altro_subito, "nota": nota})
        chiudi_anomalia(c, _dedup("ingresso", store, ts["transfer_id"]), utente, ESITI[esito])
    return tid, ids


def _crea_ripartizione(c, topo, ts, righe, quote, arrivo, altro, utente, ruolo, subito):
    """
    La quota dell'altro negozio come trasferimento del programma, dal negozio di arrivo all'altro.
    Già arrivata: nasce ricevuto (le scritture le pianifica chi chiama). Da portare: nasce approvato se decide il
    responsabile, altrimenti bozza, e segue il flusso normale (preparazione, spedizione, ricezione).
    """
    per_articolo = {}
    for r, k in zip(righe, quote):
        if r["chiave"] and k:
            per_articolo[r["chiave"]] = per_articolo.get(r["chiave"], 0) + k
    n = (c.execute("SELECT COALESCE(MAX(id), 0) FROM trasferimenti").fetchone()[0] or 0) + 1
    codice = f"TR-{n:04d}"
    stato = "ricevuto" if subito else ("approvato" if ruolo == "responsabile" else "bozza")
    nota = f"Quota del carico da fornitore {ts['nome']} ({topo.nomi_store[ts['store']]}), ripartito da {utente}"
    campi = {"codice": codice, "origine": arrivo, "destinazione": altro, "stato": stato, "tipo": "ripartizione fornitore",
             "note": nota, "creato_da": utente, "creato_il": ora()}
    if stato in ("approvato", "ricevuto"):
        campi.update(approvato_da=utente, approvato_il=ora())
    if subito:
        campi.update(spedito_da=utente, spedito_il=ora(), ricevuto_da=utente, ricevuto_il=ora())
    tid = c.execute(f"INSERT INTO trasferimenti({', '.join(campi)}) VALUES({', '.join('?' * len(campi))})",
                    tuple(campi.values())).lastrowid
    for chiave, k in per_articolo.items():
        a = c.execute("SELECT * FROM articoli WHERE chiave=?", (chiave,)).fetchone()
        c.execute("""INSERT INTO righe(trasferimento_id, chiave, prodotto, variante, sku, ean, immagine, nota, proposta, approvata,
                     prelevata, spedita, ricevuta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (tid, chiave, a["prodotto"], a["variante"], a["sku"], a["ean"], a["immagine"], f"da {ts['nome']}", k,
                   k if stato != "bozza" else None, k if subito else 0, k if subito else None, k if subito else 0))
    registra(c, utente, "trasferimento creato", codice, None, {"da_carico_fornitore": ts["nome"], "stato": stato,
                                                                "pezzi": sum(per_articolo.values())})
    return tid
