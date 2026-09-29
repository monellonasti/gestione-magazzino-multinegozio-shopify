"""
Trasferimenti fra i due negozi fisici: bozza -> approvato -> in preparazione -> spedito -> ricevuto (o annullato).

Quando si muove la merce (vedi docs/REGOLE.md):
  bozza / approvato    niente su Shopify. Dall'approvazione i pezzi sono PRENOTATI nel programma: non vengono
                       proposti ad altri trasferimenti. Shopify non cambia.
  spedito              SCARICO all'origine della quantità spedita, su tutti i canali del negozio (principale e specchi).
  ricevuto (anche a    CARICO a destinazione della quantità ricevuta buona, su tutti i canali. I danneggiati non
  più riprese)        si caricano. I pezzi in viaggio esistono solo nel programma.
  chiusura differenze  mancanti: persi (nessun movimento), rientrati all'origine (carico all'origine) o arrivati
                       dopo (carico a destinazione). Decide il responsabile.

Ogni azione controlla la versione del trasferimento: due persone che premono insieme non fanno due movimenti.
"""
from . import operazioni
from .db import ora, registra, transazione
from .negozi import ErroreSorgente, Topologia
from .sync import apri_anomalia, chiudi_anomalia

ESITI = {"persa": "Persa o mancante: nessun movimento", "rientrata_origine": "Rientrata all'origine: si ricarica l'origine",
         "arrivata_dopo": "Arrivata dopo: si carica la destinazione", "danneggiata_scartata": "Danneggiata e scartata: nessun movimento"}


class ErroreFlusso(Exception):
    """Azione non possibile: il messaggio spiega perché e cosa fare. `tipo` e `righe` servono al lettore."""
    def __init__(self, messaggio, dettagli=None, tipo=None, righe=None):
        super().__init__(messaggio)
        self.dettagli = dettagli or []
        self.tipo = tipo
        self.righe = righe or []


def carica(c, tid: int):
    t = c.execute("SELECT * FROM trasferimenti WHERE id=?", (tid,)).fetchone()
    if not t:
        raise ErroreFlusso("Trasferimento non trovato")
    righe = [dict(r) for r in c.execute("SELECT * FROM righe WHERE trasferimento_id=? ORDER BY prodotto, variante", (tid,))]
    return dict(t), righe


def _controlla(t, versione, *stati):
    if t["stato"] not in stati:
        raise ErroreFlusso(f"Il trasferimento {t['codice']} è «{t['stato'].replace('_', ' ')}»: azione non possibile. "
                           f"Ricarica la pagina per vedere lo stato attuale.")
    if versione is not None and int(versione) != t["versione"]:
        raise ErroreFlusso(f"Il trasferimento {t['codice']} è stato modificato da qualcun altro mentre lo guardavi. "
                           f"Ricarica la pagina e ripeti l'azione.")


def _avanza(c, t, stato, utente, campo=None, **altri):
    sets = {"stato": stato, "versione": t["versione"] + 1, **altri}
    if campo:
        sets[f"{campo}_da"], sets[f"{campo}_il"] = utente, ora()
    n = c.execute(f"UPDATE trasferimenti SET {', '.join(k + '=?' for k in sets)} WHERE id=? AND versione=?",
                  (*sets.values(), t["id"], t["versione"])).rowcount
    if n != 1:
        raise ErroreFlusso("Il trasferimento è cambiato nel frattempo: ricarica la pagina.")
    registra(c, utente, f"trasferimento {stato}", t["codice"], {"stato": t["stato"]}, {"stato": stato, **altri})


def crea(c, topo: Topologia, utente: str, origine: str, destinazione: str, tipo: str, righe: list, note: str = "") -> int:
    """righe: [{chiave, quantita, coperture: [{store, riga_ordine, pezzi, data}]}]. Solo quantità maggiori di zero."""
    if origine == destinazione:
        raise ErroreFlusso("Origine e destinazione devono essere diverse")
    topo.negozio(origine), topo.negozio(destinazione)
    righe = [r for r in righe if r["quantita"] > 0]
    if not righe:
        raise ErroreFlusso("Nessuna riga con quantità da trasferire")
    with transazione(c):
        n = (c.execute("SELECT COALESCE(MAX(id), 0) FROM trasferimenti").fetchone()[0] or 0) + 1
        codice = f"TR-{n:04d}"
        tid = c.execute("INSERT INTO trasferimenti(codice, origine, destinazione, stato, tipo, note, creato_da, creato_il) "
                        "VALUES(?,?,?, 'bozza', ?,?,?,?)", (codice, origine, destinazione, tipo, note, utente, ora())).lastrowid
        for r in righe:
            a = c.execute("SELECT * FROM articoli WHERE chiave=?", (r["chiave"],)).fetchone()
            if not a:
                raise ErroreFlusso(f"Articolo {r['chiave']} non trovato: aggiorna i dati e riprova")
            rid = c.execute("INSERT INTO righe(trasferimento_id, chiave, prodotto, variante, sku, ean, immagine, nota, proposta) "
                            "VALUES(?,?,?,?,?,?,?,?,?)", (tid, a["chiave"], a["prodotto"], a["variante"], a["sku"], a["ean"],
                                                          a["immagine"], r.get("nota") or "", r["quantita"])).lastrowid
            for k in r.get("coperture") or []:
                c.execute("INSERT INTO coperture(riga_id, store, riga_ordine, pezzi, data) VALUES(?,?,?,?,?)",
                          (rid, k["store"], k["riga_ordine"], k["pezzi"], k["data"]))
        _controlla_coperture(c, {(k["store"], k["riga_ordine"]) for r in righe for k in r.get("coperture") or []})
        registra(c, utente, "trasferimento creato", codice, None,
                 {"origine": origine, "destinazione": destinazione, "tipo": tipo, "righe": len(righe),
                  "pezzi": sum(r["quantita"] for r in righe)})
    return tid


def _controlla_coperture(c, vendite: set):
    """
    Dentro la transazione di crea, con le coperture nuove già scritte: nessuna vendita risulta coperta oltre i pezzi
    venduti. Due «Crea bozza» partiti insieme da due pagine, calcolati sugli stessi numeri, altrimenti rimanderebbero
    due volte la stessa vendita.
    """
    if not vendite:
        return
    from .calcoli import coperto_per_vendita
    coperto = coperto_per_vendita(c)
    for store, riga_ordine in vendite:
        venduti = c.execute("SELECT pezzi FROM venduto WHERE store=? AND riga_ordine=?", (store, riga_ordine)).fetchone()
        if venduti is not None and coperto.get((store, riga_ordine), 0) > venduti[0]:
            raise ErroreFlusso("Nel frattempo un'altra lista ha già coperto alcune di queste vendite: ricalcola il reintegro")


def modifica_bozza(c, utente, tid, versione, quantita: dict):
    t, righe = carica(c, tid)
    _controlla(t, versione, "bozza")
    with transazione(c):
        for r in righe:
            if r["id"] in quantita:
                q = int(quantita[r["id"]])
                if q < 0:
                    raise ErroreFlusso("Le quantità non possono essere negative")
                if q == 0:
                    c.execute("DELETE FROM coperture WHERE riga_id=?", (r["id"],))
                    c.execute("DELETE FROM righe WHERE id=?", (r["id"],))
                else:
                    c.execute("UPDATE righe SET proposta=? WHERE id=?", (q, r["id"]))
        _avanza(c, t, "bozza", utente)


def _uscite_altri(c, origine, escluso_tid) -> dict:
    out = {}
    for r in c.execute("""SELECT r.chiave, t.stato, r.proposta, r.approvata FROM righe r JOIN trasferimenti t ON t.id=r.trasferimento_id
                          WHERE t.origine=? AND t.id != ? AND t.stato IN ('approvato','in_preparazione')""", (origine, escluso_tid)):
        out[r["chiave"]] = out.get(r["chiave"], 0) + (r["approvata"] or 0)
    return out


def approva(c, utente, tid, versione, quantita: dict):
    """Il responsabile fissa le quantità approvate. Non si può prenotare più di quello che l'origine ha libero."""
    t, righe = carica(c, tid)
    _controlla(t, versione, "bozza")
    altri = _uscite_altri(c, t["origine"], tid)
    problemi = []
    for r in righe:
        q = int(quantita.get(r["id"], r["proposta"]))
        if q < 0:
            raise ErroreFlusso("Le quantità non possono essere negative")
        disp = c.execute("SELECT disponibile FROM articolo_negozi WHERE chiave=? AND negozio=?", (r["chiave"], t["origine"])).fetchone()
        libero = max(0, (disp["disponibile"] if disp else 0) or 0) - altri.get(r["chiave"], 0)
        if q > libero:
            problemi.append(f"{r['prodotto']} {r['variante'] or ''}: richiesti {q}, liberi all'origine {max(0, libero)}")
        r["_q"] = q
    if problemi:
        raise ErroreFlusso("Quantità superiori a quelle libere all'origine. Riduci queste righe:", problemi)
    if not any(r["_q"] for r in righe):
        raise ErroreFlusso("Tutte le quantità sono zero: annulla il trasferimento invece di approvarlo")
    with transazione(c):
        for r in righe:
            c.execute("UPDATE righe SET approvata=? WHERE id=?", (r["_q"], r["id"]))
        _avanza(c, t, "approvato", utente, "approvato")


def inizia_preparazione(c, utente, tid, versione):
    t, _ = carica(c, tid)
    _controlla(t, versione, "approvato")
    with transazione(c):
        _avanza(c, t, "in_preparazione", utente, "preparato")


def _tocca(c, tid):
    """Ogni cambio ai prelievi cambia la versione: un'anteprima di spedizione già aperta non vale più."""
    c.execute("UPDATE trasferimenti SET versione=versione+1 WHERE id=?", (tid,))


def stato_prelievo(c, tid, riga_id=None) -> dict:
    """Situazione del prelievo, per la pagina e per il lettore (niente dati commerciali)."""
    t, righe = carica(c, tid)
    tot_p = sum(r["prelevata"] or 0 for r in righe)
    tot_a = sum(r["approvata"] or 0 for r in righe)
    out = {"versione": t["versione"], "prelevati_totale": tot_p, "approvati_totale": tot_a, "tutto": tot_a > 0 and tot_p >= tot_a}
    r = next((x for x in righe if x["id"] == riga_id), None)
    if r:
        out.update(riga=r["id"], chiave=r["chiave"], prodotto=r["prodotto"], variante=r["variante"] or "",
                   prelevata=r["prelevata"], approvata=r["approvata"] or 0, completa=r["prelevata"] >= (r["approvata"] or 0))
    return out


def preleva(c, utente, tid, riga_id: int, quantita: int) -> dict:
    t, righe = carica(c, tid)
    _controlla(t, None, "in_preparazione")
    r = next((x for x in righe if x["id"] == riga_id), None)
    if not r:
        raise ErroreFlusso("Riga non trovata")
    if not 0 <= quantita <= (r["approvata"] or 0):
        raise ErroreFlusso(f"{r['prodotto']} {r['variante'] or ''}: i prelevati vanno da 0 a {r['approvata']}", righe=[riga_id])
    if quantita != r["prelevata"]:
        with transazione(c):
            c.execute("UPDATE righe SET prelevata=? WHERE id=?", (quantita, riga_id))
            _tocca(c, tid)
    return stato_prelievo(c, tid, riga_id)


def scansiona(c, utente, tid, codice: str) -> dict:
    """Lettore barcode in preparazione: +1 alla riga con quell'EAN (o SKU). Ritorna lo stato della riga."""
    t, righe = carica(c, tid)
    _controlla(t, None, "in_preparazione")
    codice = (codice or "").strip()
    if not codice:
        raise ErroreFlusso("Nessun codice letto", tipo="vuoto")
    trovate = [r for r in righe if r["ean"] and r["ean"] == codice] or               [r for r in righe if r["sku"] and r["sku"].upper() == codice.upper()]
    if not trovate:
        raise ErroreFlusso(f"Non è in questo trasferimento: {codice}. Mettilo da parte.", tipo="estraneo")
    if len(trovate) > 1:
        raise ErroreFlusso(f"Il codice {codice} è su {len(trovate)} righe: usa + sulla riga giusta", tipo="doppio",
                           righe=[r["id"] for r in trovate])
    r = trovate[0]
    nome = f"{r['prodotto']}{' – ' + r['variante'] if r['variante'] else ''}"
    with transazione(c):
        n = c.execute("UPDATE righe SET prelevata=prelevata+1 WHERE id=? AND prelevata < COALESCE(approvata, 0)", (r["id"],)).rowcount
        if n:
            _tocca(c, tid)
    if not n:
        raise ErroreFlusso(f"Basta così: {nome}, ne servono {r['approvata']} e li hai già", tipo="completo", righe=[r["id"]])
    info = stato_prelievo(c, tid, r["id"])
    if info["tutto"]:
        info["messaggio"] = f"Tutto pronto: {info['prelevati_totale']} pezzi. Premi «Controlla e spedisci»"
    elif info["completa"]:
        info["messaggio"] = f"{nome}: completo ({info['prelevata']} di {info['approvata']})"
    else:
        info["messaggio"] = f"{nome}: {info['prelevata']} di {info['approvata']}"
    return info


def _canali(c, topo, negozio, chiave):
    """Canali del negozio su cui esiste l'articolo: [(canale, variant_id, item_id, location_id)]."""
    out, mancanti = [], []
    for ch in topo.negozio(negozio).canali:
        r = c.execute("""SELECT v.variant_id, v.item_id, s.location_id FROM articolo_canali ac
                         JOIN varianti v ON v.store=ac.store AND v.variant_id=ac.variant_id
                         JOIN sedi s ON s.store=ac.store WHERE ac.chiave=? AND ac.store=?""", (chiave, ch.store)).fetchone()
        (out if r else mancanti).append((ch, *(r or (None, None, None))))
    return out, mancanti


def movimenti(c, topo, t, quantita: dict, tipo: str, negozio: str) -> list:
    """Anteprima dei movimenti: una riga per articolo e canale."""
    out = []
    righe = {r["id"]: r for r in carica(c, t["id"])[1]}
    for rid, q in quantita.items():
        if q <= 0:
            continue
        r = righe[rid]
        presenti, mancanti = _canali(c, topo, negozio, r["chiave"])
        for ch, vid, iid, loc in presenti:
            out.append({"riga": r, "canale": ch, "store": ch.store, "variant_id": vid, "item_id": iid, "location_id": loc,
                        "delta": -q if tipo == "scarico" else q})
        for ch, *_ in mancanti:
            out.append({"riga": r, "canale": ch, "store": ch.store, "variant_id": None, "item_id": None,
                        "location_id": None, "delta": 0, "mancante": True})
    return out


def _pianifica(c, t, movs, tipo, negozio, suffisso):
    ids = []
    for m in movs:
        r = m["riga"]
        if m.get("mancante"):
            apri_anomalia(c, f"specchio:{t['codice']}:{r['id']}:{m['store']}:{suffisso}", "aggiornamento_fallito", 3, r["chiave"],
                          f"Da aggiornare a mano: {r['prodotto']} {r['variante'] or ''} su {m['store']}",
                          f"{t['codice']}: l'articolo non esiste su questo canale, il movimento ({tipo}) non è stato scritto.")
            continue
        ids.append(operazioni.pianifica(
            c, chiave=f"{t['codice']}:{r['id']}:{tipo}:{m['store']}:{suffisso}", tipo=tipo, negozio=negozio,
            store=m["store"], variant_id=m["variant_id"], item_id=m["item_id"], location_id=m["location_id"],
            delta=m["delta"], trasferimento_id=t["id"], riga_id=r["id"]))
    return ids


def spedisci(c, topo, sorgente, scritture_attive: bool, utente, tid, versione, spedite: dict, motivo: str = "") -> list:
    """
    Registra la partenza e pianifica lo scarico all'origine. Prima rilegge da Shopify il canale principale
    dell'origine: se nel frattempo è stato venduto qualcosa e i pezzi non bastano, non spedisce e lo dice.
    """
    t, righe = carica(c, tid)
    _controlla(t, versione, "in_preparazione")
    if not scritture_attive:
        raise ErroreFlusso("Spedizione bloccata: le modifiche alle giacenze su Shopify sono disattivate. Puoi preparare "
                           "e stampare la lista; per spedire dal programma le deve accendere il responsabile.")
    q = {}
    for r in righe:
        v = spedite.get(r["id"])
        v = int(v) if v not in (None, "") else (r["prelevata"] or 0)
        if not 0 <= v <= (r["approvata"] or 0):
            raise ErroreFlusso(f"{r['prodotto']}: spediti deve essere fra 0 e {r['approvata']}")
        q[r["id"]] = v
    if not any(q.values()):
        raise ErroreFlusso("Nessun pezzo da spedire: se il trasferimento non serve più, annullalo")
    parziali = [r for r in righe if q[r["id"]] < (r["approvata"] or 0)]
    if parziali and not (motivo or "").strip():
        raise ErroreFlusso("Alcune righe partono con meno pezzi di quelli approvati: scrivi il motivo",
                           [f"{r['prodotto']} {r['variante'] or ''}: {q[r['id']]} di {r['approvata']}" for r in parziali])

    principale = topo.negozio(t["origine"]).principale
    movs = movimenti(c, topo, t, q, "scarico", t["origine"])
    da_leggere = [m for m in movs if m["store"] == principale.store and not m.get("mancante")]
    try:
        livelli = sorgente.leggi_livelli(principale.store, da_leggere[0]["location_id"], [m["item_id"] for m in da_leggere]) if da_leggere else {}
    except ErroreSorgente as e:
        raise ErroreFlusso(f"Non riesco a rileggere le giacenze dell'origine ({e}). Riprova fra poco: nulla è stato scritto.")
    conflitti = []
    for m in da_leggere:
        liv = livelli.get(m["item_id"])
        disp = liv["disponibile"] if liv else None
        if disp is None or disp < -m["delta"]:
            conflitti.append(f"{m['riga']['prodotto']} {m['riga']['variante'] or ''}: da spedire {-m['delta']}, "
                             f"disponibili ora {disp if disp is not None else 'nessuno'}")
    if conflitti:
        raise ErroreFlusso("Le giacenze dell'origine sono cambiate (vendite o modifiche nel frattempo). "
                           "Riduci queste righe o ricontrolla la merce, poi riprova:", conflitti)
    with transazione(c):
        for r in righe:
            c.execute("UPDATE righe SET spedita=?, motivo_differenza=CASE WHEN ?<approvata THEN ? ELSE motivo_differenza END WHERE id=?",
                      (q[r["id"]], q[r["id"]], motivo, r["id"]))
        _avanza(c, t, "spedito", utente, "spedito")
        return _pianifica(c, t, movs, "scarico", t["origine"], "sped")


def ricevi(c, topo, scritture_attive: bool, utente, tid, ricevute: dict, nota: str = "") -> list:
    """ricevute: {riga_id: (buoni, danneggiati)}. Si può ricevere a più riprese; si caricano solo i buoni."""
    t, righe = carica(c, tid)
    _controlla(t, None, "spedito")
    if not scritture_attive:
        raise ErroreFlusso("Arrivo non registrato: le modifiche alle giacenze su Shopify sono disattivate.")
    per_riga = {r["id"]: r for r in righe}
    carichi, eventi = {}, []
    for rid, (buoni, danni) in ricevute.items():
        r = per_riga.get(rid)
        buoni, danni = int(buoni or 0), int(danni or 0)
        if not r or buoni < 0 or danni < 0:
            raise ErroreFlusso("Quantità ricevute non valide")
        attesi = (r["spedita"] or 0) - r["ricevuta"] - r["danneggiata"]
        if buoni + danni > attesi:
            raise ErroreFlusso(f"{r['prodotto']} {r['variante'] or ''}: " + ("ne manca 1" if attesi == 1 else f"ne mancano {attesi}")
                               + f", ne hai indicati {buoni + danni}")
        if buoni or danni:
            eventi.append((rid, buoni, danni))
            if buoni:
                carichi[rid] = buoni
    if not eventi:
        raise ErroreFlusso("Nessuna quantità ricevuta indicata")
    with transazione(c):
        # i controlli qui sopra valgono per le righe lette prima: un arrivo registrato un attimo fa da un altro tablet
        # li cambia. Con il blocco in scrittura già preso si rifanno sulla riga vera, prima di caricare qualcosa.
        if c.execute("SELECT stato FROM trasferimenti WHERE id=?", (tid,)).fetchone()[0] != "spedito":
            raise ErroreFlusso(f"Il trasferimento {t['codice']} è cambiato nel frattempo: ricarica la pagina")
        ids = []
        for rid, buoni, danni in eventi:
            n = c.execute("UPDATE righe SET ricevuta=ricevuta+?, danneggiata=danneggiata+? WHERE id=? AND trasferimento_id=? "
                          "AND COALESCE(spedita,0) - ricevuta - danneggiata >= ?", (buoni, danni, rid, tid, buoni + danni)).rowcount
            if n != 1:
                raise ErroreFlusso(f"{per_riga[rid]['prodotto']} {per_riga[rid]['variante'] or ''}: nel frattempo è stato registrato "
                                   f"un altro arrivo. Ricarica la pagina: i numeri sono cambiati")
            ev = c.execute("INSERT INTO ricezioni(riga_id, buoni, danneggiati, nota, da, il) VALUES(?,?,?,?,?,?)",
                           (rid, buoni, danni, nota, utente, ora())).lastrowid
            if buoni:
                ids += _pianifica(c, t, movimenti(c, topo, t, {rid: buoni}, "carico", t["destinazione"]), "carico",
                                  t["destinazione"], f"ric{ev}")
        registra(c, utente, "ricezione", t["codice"], None, {"righe": [{"riga": e[0], "buoni": e[1], "danneggiati": e[2]} for e in eventi]})
        _, dopo = carica(c, tid)
        if all((r["spedita"] or 0) == r["ricevuta"] + r["danneggiata"] for r in dopo):
            _chiudi_ricezione(c, t, dopo, utente)
    return ids


def _chiudi_ricezione(c, t, righe, utente):
    t = dict(c.execute("SELECT * FROM trasferimenti WHERE id=?", (t["id"],)).fetchone())
    _avanza(c, t, "ricevuto", utente, "ricevuto")
    for r in righe:
        mancanti = (r["spedita"] or 0) - r["ricevuta"] - r["danneggiata"]
        if mancanti or r["danneggiata"]:
            parti = ([f"{mancanti} mancanti"] if mancanti else []) + ([f"{r['danneggiata']} danneggiati"] if r["danneggiata"] else [])
            apri_anomalia(c, f"differenza:{r['id']}", "differenza_ricezione", 2, r["chiave"],
                          f"Differenza alla ricezione {t['codice']}: {r['prodotto']} {r['variante'] or ''}",
                          f"Spediti {r['spedita']}, ricevuti buoni {r['ricevuta']}, " + ", ".join(parti) +
                          ". Il responsabile deve chiudere la differenza dal trasferimento.")


def chiudi_ricezione(c, utente, tid, nota: str):
    """Il magazziniere dichiara che non arriverà altro: le righe incomplete diventano differenze da chiudere."""
    t, righe = carica(c, tid)
    _controlla(t, None, "spedito")
    if not (nota or "").strip():
        raise ErroreFlusso("Scrivi una nota: cosa manca e perché")
    with transazione(c):
        t, righe = carica(c, tid)          # dentro la transazione: un arrivo registrato un attimo prima deve contare
        _controlla(t, None, "spedito")
        c.execute("UPDATE trasferimenti SET note=TRIM(COALESCE(note,'') || char(10) || ?) WHERE id=?", (f"Ricezione chiusa: {nota}", tid))
        _chiudi_ricezione(c, t, righe, utente)


def chiudi_differenza(c, topo, scritture_attive: bool, utente, riga_id: int, esito: str, nota: str) -> list:
    r = c.execute("SELECT * FROM righe WHERE id=?", (riga_id,)).fetchone()
    if not r:
        raise ErroreFlusso("Riga non trovata")
    t, _ = carica(c, r["trasferimento_id"])
    _controlla(t, None, "ricevuto")
    if r["esito_differenza"]:
        raise ErroreFlusso("Differenza già chiusa")
    if esito not in ESITI:
        raise ErroreFlusso("Scegli come chiudere la differenza")
    if not (nota or "").strip():
        raise ErroreFlusso("Scrivi una nota: cosa hai verificato")
    mancanti = (r["spedita"] or 0) - r["ricevuta"] - r["danneggiata"]
    if esito in ("rientrata_origine", "arrivata_dopo") and not mancanti:
        raise ErroreFlusso("Non ci sono pezzi mancanti su questa riga")
    if esito in ("rientrata_origine", "arrivata_dopo") and not scritture_attive:
        raise ErroreFlusso("Le modifiche alle giacenze su Shopify sono disattivate: questa chiusura richiede un carico.")
    ids = []
    with transazione(c):
        n = c.execute("UPDATE righe SET esito_differenza=?, differenza_chiusa_da=?, differenza_chiusa_il=?, "
                      "motivo_differenza=TRIM(COALESCE(motivo_differenza,'') || ' ' || ?) WHERE id=? AND esito_differenza IS NULL",
                      (esito, utente, ora(), nota, riga_id)).rowcount
        if n != 1:                          # chiusa un attimo fa da un'altra pagina: niente secondo carico
            raise ErroreFlusso("Differenza già chiusa nel frattempo: ricarica la pagina")
        if esito == "rientrata_origine":
            ids = _pianifica(c, t, movimenti(c, topo, t, {riga_id: mancanti}, "rientro", t["origine"]), "rientro", t["origine"], "diff")
        elif esito == "arrivata_dopo":
            ids = _pianifica(c, t, movimenti(c, topo, t, {riga_id: mancanti}, "carico", t["destinazione"]), "carico",
                             t["destinazione"], "diff")
        registra(c, utente, "differenza chiusa", t["codice"], {"mancanti": mancanti, "danneggiati": r["danneggiata"]},
                 {"esito": esito, "nota": nota})
        chiudi_anomalia(c, f"differenza:{riga_id}", utente, f"{ESITI[esito]}. {nota}")
    return ids


def annulla(c, utente, tid, versione, motivo: str):
    t, _ = carica(c, tid)
    _controlla(t, versione, "bozza", "approvato", "in_preparazione")
    if not (motivo or "").strip():
        raise ErroreFlusso("Scrivi il motivo dell'annullamento")
    with transazione(c):
        c.execute("UPDATE trasferimenti SET note=TRIM(COALESCE(note,'') || char(10) || ?) WHERE id=?", (f"Annullato: {motivo}", tid))
        _avanza(c, t, "annullato", utente, "annullato")


def differenze_aperte(righe, stato) -> list:
    if stato != "ricevuto":
        return []
    return [r for r in righe if not r["esito_differenza"] and ((r["spedita"] or 0) - r["ricevuta"] - r["danneggiata"] or r["danneggiata"])]
