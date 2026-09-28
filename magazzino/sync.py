"""
Lettura dai negozi (giacenze e venduto) nella copia locale, abbinamento e anomalie.

Se un negozio non risponde si tengono i suoi dati precedenti, con la loro data: le pagine mostrano che sono
vecchi e la panoramica segnala la sincronizzazione parziale. Niente viene cancellato per un errore di rete.
"""
import threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from .abbinamento import abbina
from .db import connetti, impostazione, inizio_giorno_utc, ora, salva_impostazione, transazione, utc_iso
from .negozi import ErroreSorgente, Topologia

GIORNI_VENDUTO = 60                     # Shopify senza read_all_orders non va oltre
TIPI_DATI = ("ean_mancante", "ean_doppio", "associazione_ambigua", "solo_un_negozio", "quantita_incoerente",
             "canali_non_allineati", "specchio_mancante")

COLONNE = ("variant_id", "product_id", "item_id", "prodotto", "variante", "sku", "ean", "immagine", "stato", "tracciata",
           "in_sede", "disponibile", "giacenza", "impegnata")


def _inizio(c, store, tipo):
    return c.execute("INSERT INTO sincronizzazioni(store, tipo, inizio, esito) VALUES(?,?,?, 'in corso')",
                     (store, tipo, ora())).lastrowid


def _fine(c, sid, esito, messaggio=None, righe=0):
    c.execute("UPDATE sincronizzazioni SET fine=?, esito=?, messaggio=?, righe=? WHERE id=?", (ora(), esito, messaggio, righe, sid))


def sincronizza(c, sorgente, topo: Topologia, avanzamento=lambda testo: None) -> dict:
    """Legge tutti i negozi (in parallelo), salva, ricalcola. Ritorna {store: errore} per i negozi falliti."""
    errori = {}

    def leggi(store):
        nome, canale = topo.nomi_store[store], topo.canale(store)
        sedi = sorgente.sedi(store)
        if canale.sede not in sedi:
            raise ErroreSorgente(f"{nome}: la sede «{canale.sede}» non esiste (sedi: {', '.join(sedi)})")
        loc = sedi[canale.sede]
        righe = sorgente.leggi_varianti(store, loc, lambda n: avanzamento(f"{nome}: {n} varianti lette"))
        return loc, righe

    avvii = {s: _inizio(c, s, "giacenze") for s in topo.stores}
    with ThreadPoolExecutor(max_workers=len(avvii)) as ex:
        futuri = {s: ex.submit(leggi, s) for s in topo.stores}
        risultati = {}
        for s, f in futuri.items():
            try:
                risultati[s] = f.result()
            except Exception as e:                    # noqa: BLE001 - qualsiasi errore di un negozio non ferma gli altri
                errori[s] = str(e) or type(e).__name__
                _fine(c, avvii[s], "errore", errori[s])
    adesso = ora()
    for s, (loc, righe) in risultati.items():
        with transazione(c):
            c.execute("INSERT INTO sedi(store, location_id, nome) VALUES(?,?,?) ON CONFLICT(store) DO UPDATE SET "
                      "location_id=excluded.location_id, nome=excluded.nome", (s, loc, topo.canale(s).sede))
            c.execute("DELETE FROM varianti WHERE store=?", (s,))
            c.executemany(f"INSERT INTO varianti(store, {', '.join(COLONNE)}, letto_il) VALUES(?, {', '.join('?' * len(COLONNE))}, ?)",
                          [(s, *[r.get(k) for k in COLONNE], adesso) for r in righe])
        _fine(c, avvii[s], "ok", None, len(righe))

    for s in topo.stores:
        avanzamento(f"{topo.nomi_store[s]}: venduto")
        sid = _inizio(c, s, "venduto")
        try:
            ultimo = c.execute("SELECT inizio FROM sincronizzazioni WHERE store=? AND tipo='venduto' AND esito='ok' "
                               "ORDER BY id DESC LIMIT 1", (s,)).fetchone()
            limite = datetime.now(timezone.utc) - timedelta(days=GIORNI_VENDUTO)
            dal = max(limite, datetime.fromisoformat(ultimo[0]) - timedelta(hours=2)) if ultimo else limite
            righe = sorgente.leggi_venduto(s, dal.strftime("%Y-%m-%dT%H:%M:%SZ"))
            with transazione(c):
                c.executemany("""INSERT INTO venduto(store, riga_ordine, variant_id, location_id, data, pezzi, aggiornato_il)
                                 VALUES(?,?,?,?,?,?,?) ON CONFLICT(store, riga_ordine) DO UPDATE SET variant_id=excluded.variant_id,
                                 location_id=excluded.location_id, pezzi=excluded.pezzi, aggiornato_il=excluded.aggiornato_il""",
                              [(s, r["riga_ordine"], r["variant_id"], r["location_id"], utc_iso(r["data"]), r["pezzi"], ora()) for r in righe])
            _fine(c, sid, "ok", None, len(righe))
        except Exception as e:                        # noqa: BLE001
            errori.setdefault(s, str(e))
            _fine(c, sid, "errore", str(e))
    # trasferimenti fatti su Shopify senza destinazione (dalla data impostata: quelli prima sono già stati gestiti)
    from . import da_shopify
    dal_tr = impostazione(c, "trasferimenti_shopify_dal")
    if dal_tr is None:
        dal_tr = date.today().isoformat()
        salva_impostazione(c, "trasferimenti_shopify_dal", dal_tr)
    for s in topo.stores:
        if s in errori:
            continue
        avanzamento(f"{topo.nomi_store[s]}: trasferimenti")
        sid = _inizio(c, s, "trasferimenti")
        try:
            lista = sorgente.leggi_trasferimenti(s, inizio_giorno_utc(date.fromisoformat(dal_tr)))
            _fine(c, sid, "ok", None, da_shopify.registra_letti(c, topo, s, lista))
        except Exception as e:                        # noqa: BLE001
            errori.setdefault(s, str(e))
            _fine(c, sid, "errore", str(e))
    avanzamento("abbinamento e anomalie")
    ricalcola(c, topo)
    return errori


def ricalcola(c, topo: Topologia):
    varianti = [dict(r) for r in c.execute("SELECT * FROM varianti")]
    manuali = {(r["store"], r["variant_id"]): r["chiave"] for r in c.execute("SELECT store, variant_id, chiave FROM associazioni")}
    articoli, anomalie = abbina(varianti, manuali, topo)
    with transazione(c):
        for t in ("articoli", "articolo_canali", "articolo_negozi"):
            c.execute(f"DELETE FROM {t}")
        for a in articoli.values():
            canali = a["canali"]
            letture = [v["letto_il"] for v in canali.values() if v.get("letto_il")]
            cerca = " ".join(x for x in (a["prodotto"], a["variante"], a["sku"], a["ean"]) if x).lower()
            c.execute("INSERT INTO articoli VALUES(?,?,?,?,?,?,?,?,?)", (a["chiave"], a["prodotto"], a["variante"], a["sku"],
                      a["ean"], a["immagine"], a["stato"], cerca, min(letture) if letture else None))
            c.executemany("INSERT INTO articolo_canali VALUES(?,?,?)", [(a["chiave"], s, v["variant_id"]) for s, v in canali.items()])
            for n in topo.negozi:
                v = next((canali[ch.store] for ch in n.canali if ch.store in canali), None)
                if v is not None:
                    c.execute("INSERT INTO articolo_negozi VALUES(?,?,?,?)", (a["chiave"], n.id, v.get("disponibile"), v.get("giacenza")))
        aggiorna_anomalie(c, anomalie, TIPI_DATI)
        for s in topo.stores:
            ultimi = [c.execute("SELECT esito, messaggio FROM sincronizzazioni WHERE store=? AND tipo=? AND esito!='in corso' "
                                "ORDER BY id DESC LIMIT 1", (s, tipo)).fetchone() for tipo in ("giacenze", "venduto", "trasferimenti")]
            errori = [r["messaggio"] for r in ultimi if r and r["esito"] == "errore"]
            if errori:
                apri_anomalia(c, f"sync:{s}", "sincronizzazione", 3, None, f"Lettura non riuscita: {topo.nomi_store[s]}",
                              f"{errori[0]}. Si stanno usando i dati letti prima: controlla connessione e credenziali, "
                              f"poi premi «Rileggi i negozi».")
            else:
                chiudi_anomalia(c, f"sync:{s}", "controllo automatico", "lettura tornata regolare")


def aggiorna_anomalie(c, nuove: list, tipi: tuple):
    segna = ",".join("?" * len(tipi))
    presenti = {r["dedup"]: r for r in c.execute(f"SELECT dedup, stato FROM anomalie WHERE tipo IN ({segna})", tipi)}
    viste = set()
    for a in nuove:
        viste.add(a["dedup"])
        apri_anomalia(c, a["dedup"], a["tipo"], a["gravita"], a.get("chiave"), a["titolo"], a["dettaglio"], presenti.get(a["dedup"]))
    for dedup, r in presenti.items():
        if dedup not in viste and r["stato"] == "aperta":
            chiudi_anomalia(c, dedup, "controllo automatico", "la condizione non c'è più")


def apri_anomalia(c, dedup, tipo, gravita, chiave, titolo, dettaglio, esistente="?"):
    if esistente == "?":
        esistente = c.execute("SELECT stato FROM anomalie WHERE dedup=?", (dedup,)).fetchone()
    if esistente is None:
        c.execute("INSERT INTO anomalie(dedup, tipo, gravita, chiave, titolo, dettaglio, stato, creata_il, vista_il) "
                  "VALUES(?,?,?,?,?,?, 'aperta', ?, ?)", (dedup, tipo, gravita, chiave, titolo, dettaglio, ora(), ora()))
    else:
        riapri = ", stato='aperta', chiusa_da=NULL, chiusa_il=NULL, nota=NULL" if esistente["stato"] == "risolta" else ""
        c.execute(f"UPDATE anomalie SET gravita=?, chiave=?, titolo=?, dettaglio=?, vista_il=?{riapri} WHERE dedup=?",
                  (gravita, chiave, titolo, dettaglio, ora(), dedup))


def chiudi_anomalia(c, dedup, da, nota=None, stato="risolta"):
    c.execute("UPDATE anomalie SET stato=?, chiusa_da=?, chiusa_il=?, nota=COALESCE(?, nota) WHERE dedup=? AND stato='aperta'",
              (stato, da, ora(), nota, dedup))


class Pianificatore(threading.Thread):
    """Sincronizzazione periodica in sottofondo, più quella chiesta dal pulsante «Rileggi i negozi»."""

    def __init__(self, db_path, sorgente, topo, minuti, dopo=None):
        super().__init__(daemon=True, name="sincronizzazione")
        self.db_path, self.sorgente, self.topo, self.minuti, self.dopo = db_path, sorgente, topo, minuti, dopo
        self.richiesta = threading.Event()
        self.blocco = threading.Lock()
        self.in_corso, self.messaggio, self.fermo = False, "", False

    def richiedi(self):
        self.richiesta.set()

    def esegui_ora(self):
        with self.blocco:
            self.in_corso = True
            c = connetti(self.db_path)
            try:
                self.messaggio = "lettura in corso"
                errori = sincronizza(c, self.sorgente, self.topo, lambda t: setattr(self, "messaggio", t))
                self.messaggio = "completata" if not errori else f"parziale: {len(errori)} negozi non letti"
                if self.dopo:
                    self.dopo(c)
            except Exception as e:                    # noqa: BLE001
                self.messaggio = f"errore: {e}"
            finally:
                c.close()
                self.in_corso = False

    def run(self):
        self.richiedi()                               # prima lettura all'avvio
        while not self.fermo:
            self.richiesta.wait(timeout=self.minuti * 60)
            self.richiesta.clear()
            if not self.fermo:
                self.esegui_ora()
            time.sleep(1)
