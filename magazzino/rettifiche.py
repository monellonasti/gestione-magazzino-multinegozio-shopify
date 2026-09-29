"""
Rettifiche di giacenza: richiesta con anteprima, conferma del responsabile, registro prima/dopo.

Il programma non decide mai da solo quale numero è giusto. La fonte la indica chi chiede la rettifica:
  - "conteggio"  : pezzi contati a mano nel negozio -> tutti i canali del negozio vanno a quel numero
  - "principale" : il canale principale è giusto -> gli specchi si allineano a lui (merce condivisa)
Sui negozi veri le rettifiche sono spente finché nel .env non ci sono SCRITTURE_SHOPIFY=1 e RETTIFICHE=1:
fino ad allora il programma confronta e segnala soltanto.
"""
import json

from . import operazioni
from .db import ora, registra, transazione
from .negozi import ErroreSorgente
from .trasferimenti import ErroreFlusso, _canali

FONTI = {"conteggio": "Conteggio fisico in negozio", "principale": "Canale principale (allinea gli specchi)"}
MASSIMO_CONTATI = 100_000             # oltre, quasi sempre è uno zero di troppo battuto per sbaglio


def _leggi(c, topo, sorgente, negozio, chiave):
    presenti, _ = _canali(c, topo, negozio, chiave)
    if not presenti:
        raise ErroreFlusso("L'articolo non esiste in questo negozio")
    out = []
    for ch, vid, iid, loc in presenti:
        try:
            liv = sorgente.leggi_livelli(ch.store, loc, [iid]).get(iid)
        except ErroreSorgente as e:
            raise ErroreFlusso(f"Lettura da Shopify non riuscita ({e}): riprova fra poco")
        if liv is None:
            raise ErroreFlusso(f"L'articolo non è attivo nella sede di {topo.nomi_store[ch.store]}")
        out.append({"store": ch.store, "principale": ch.principale, "variant_id": vid, "item_id": iid, "location_id": loc,
                    "giacenza": liv["giacenza"], "disponibile": liv["disponibile"]})
    return out


def richiedi(c, topo, sorgente, attive: bool, utente, negozio, chiave, fonte, contati, motivo) -> int:
    if not attive:
        raise ErroreFlusso("Rettifiche disattivate: servono SCRITTURE_SHOPIFY=1 e RETTIFICHE=1 nel .env. "
                           "Nel frattempo usa il confronto e correggi a mano su Shopify.")
    if fonte not in FONTI:
        raise ErroreFlusso("Scegli la fonte di riferimento")
    if not (motivo or "").strip():
        raise ErroreFlusso("Scrivi il motivo")
    if negozio not in {n.id for n in topo.negozi}:
        raise ErroreFlusso("Scegli il negozio da rettificare")
    if fonte == "conteggio" and not str(contati if contati is not None else "").strip():
        raise ErroreFlusso("Scrivi quanti pezzi hai contato")
    canali = _leggi(c, topo, sorgente, negozio, chiave)
    if fonte == "principale":
        pr = next((x for x in canali if x["principale"]), None)
        if not pr or len(canali) < 2:
            raise ErroreFlusso("Servono il canale principale e almeno uno specchio")
        contati = pr["giacenza"]
    try:
        contati = int(contati)
    except (TypeError, ValueError):
        raise ErroreFlusso("I pezzi contati devono essere un numero intero") from None
    if not 0 <= contati <= MASSIMO_CONTATI:
        raise ErroreFlusso(f"I pezzi contati vanno da 0 a {MASSIMO_CONTATI:,}".replace(",", "."))
    for x in canali:
        x["dopo"] = contati
        x["delta"] = contati - (x["giacenza"] or 0)
    if not any(x["delta"] for x in canali):
        raise ErroreFlusso("Nessuna differenza: la giacenza è già quella indicata")
    with transazione(c):
        rid = c.execute("INSERT INTO rettifiche(negozio, chiave, fonte, contati, motivo, anteprima, stato, richiesta_da, richiesta_il) "
                        "VALUES(?,?,?,?,?,?, 'da_confermare', ?, ?)",
                        (negozio, chiave, fonte, contati, motivo, json.dumps(canali), utente, ora())).lastrowid
        registra(c, utente, "rettifica richiesta", f"rettifica {rid}", {x["store"]: x["giacenza"] for x in canali},
                 {x["store"]: x["dopo"] for x in canali})
    return rid


def conferma(c, topo, sorgente, attive: bool, utente, rid) -> list:
    r = c.execute("SELECT * FROM rettifiche WHERE id=?", (rid,)).fetchone()
    if not r or r["stato"] != "da_confermare":
        raise ErroreFlusso("Rettifica non trovata o già decisa")
    if not attive:
        raise ErroreFlusso("Rettifiche disattivate")
    prima = json.loads(r["anteprima"])
    ora_letti = {x["store"]: x for x in _leggi(c, topo, sorgente, r["negozio"], r["chiave"])}
    cambiati = [f"{topo.nomi_store[x['store']]}: era {x['giacenza']}, ora {ora_letti.get(x['store'], {}).get('giacenza')}"
                for x in prima if ora_letti.get(x["store"], {}).get("giacenza") != x["giacenza"]]
    if cambiati:
        with transazione(c):
            c.execute("UPDATE rettifiche SET stato='superata' WHERE id=?", (rid,))
        raise ErroreFlusso("La giacenza è cambiata dopo l'anteprima: la rettifica è stata annullata, rifalla.", cambiati)
    with transazione(c):
        c.execute("UPDATE rettifiche SET stato='confermata', confermata_da=?, confermata_il=? WHERE id=?", (utente, ora(), rid))
        ids = [operazioni.pianifica(c, chiave=f"R{rid}:{x['store']}", tipo="rettifica", negozio=r["negozio"], store=x["store"],
                                    variant_id=x["variant_id"], item_id=x["item_id"], location_id=x["location_id"],
                                    delta=x["delta"], rettifica_id=rid)
               for x in prima if x["delta"]]
        registra(c, utente, "rettifica confermata", f"rettifica {rid}", {x["store"]: x["giacenza"] for x in prima},
                 {x["store"]: x["dopo"] for x in prima})
    return ids


def rifiuta(c, utente, rid, nota):
    with transazione(c):
        n = c.execute("UPDATE rettifiche SET stato='rifiutata', confermata_da=?, confermata_il=? WHERE id=? AND stato='da_confermare'",
                      (utente, ora(), rid)).rowcount
        if n:
            registra(c, utente, "rettifica rifiutata", f"rettifica {rid}", None, {"nota": nota})
