"""
Scritture sulle giacenze Shopify, una per riga della tabella `operazioni`.

Le due scritture di un movimento (per esempio la cassa e il negozio online dello stesso negozio fisico) non sono una transazione unica: ognuna
ha il suo stato e si riprende da sola. Regole:
  1. prima di scrivere si rilegge la giacenza; uno scarico che la porterebbe sotto zero diventa CONFLITTO e non parte;
  2. si scrive con changeFromQuantity (se nel frattempo qualcuno ha venduto, Shopify rifiuta e si rilegge) e con
     una chiave @idempotent propria di quel tentativo;
  3. lo stato "in_corso" si salva PRIMA della chiamata: se il programma si ferma a metà, al riavvio l'operazione
     risulta incerta e non si rifà alla cieca;
  4. un esito INCERTO (nessuna risposta) si risolve ripetendo la STESSA chiamata con la STESSA chiave: se era già
     passata Shopify restituisce il risultato di allora senza applicarla di nuovo. Shopify ricorda le chiavi 24 ore:
     oltre, l'operazione passa a DA_VERIFICARE e decide una persona guardando lo storico della giacenza.
"""
import threading
from datetime import datetime, timedelta, timezone

from .db import ora, registra, transazione
from .negozi import Conflitto, ErroreSorgente, Incerta, Rifiutata
from .sync import apri_anomalia, chiudi_anomalia

MOTIVI = {"scarico": "movement_created", "carico": "movement_received", "rientro": "movement_canceled",
          "rettifica": "cycle_count_available", "fornitore": "received"}
FINALI = ("fatta", "annullata")
DA_ESEGUIRE = ("da_fare", "incerta")
FINESTRA_CHIAVE = timedelta(hours=23)
TENTATIVI = 3
_blocco = threading.Lock()


def pianifica(c, *, chiave, tipo, negozio, store, variant_id, item_id, location_id, delta,
              trasferimento_id=None, riga_id=None, rettifica_id=None, documento=None) -> int:
    """Registra l'operazione. La chiave è unica: pianificare due volte la stessa cosa non crea un secondo movimento."""
    c.execute("""INSERT INTO operazioni(chiave, trasferimento_id, riga_id, rettifica_id, tipo, negozio, store, variant_id,
                 item_id, location_id, delta, stato, creata_il, aggiornata_il, documento)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?, 'da_fare', ?, ?, ?) ON CONFLICT(chiave) DO NOTHING""",
              (chiave, trasferimento_id, riga_id, rettifica_id, tipo, negozio, store, variant_id, item_id, location_id,
               delta, ora(), ora(), documento))
    return c.execute("SELECT id FROM operazioni WHERE chiave=?", (chiave,)).fetchone()[0]


def documento(c, op) -> str:
    if op.get("documento"):
        return op["documento"]
    if op["trasferimento_id"]:
        cod = c.execute("SELECT codice FROM trasferimenti WHERE id=?", (op["trasferimento_id"],)).fetchone()[0]
        return f"logistics://magazzino/trasferimento/{cod}"
    return f"logistics://magazzino/rettifica/{op['rettifica_id']}"


def _salva(c, op_id, **campi):
    campi["aggiornata_il"] = ora()
    c.execute(f"UPDATE operazioni SET {', '.join(k + '=?' for k in campi)} WHERE id=?", (*campi.values(), op_id))


def _segnala(c, op, testo):
    apri_anomalia(c, f"operazione:{op['id']}", "aggiornamento_fallito", 3, None,
                  f"Giacenza non aggiornata: {op['tipo']} {op['delta']:+d} su {op['store']}", testo)


def _riuscita(c, topo, op, dopo):
    with transazione(c):
        _salva(c, op["id"], stato="fatta", dopo=dopo, errore=None)
        c.execute("UPDATE varianti SET disponibile=?, giacenza=COALESCE(giacenza,0)+?, letto_il=? WHERE store=? AND item_id=?",
                  (dopo, op["delta"], ora(), op["store"], op["item_id"]))
        r = c.execute("SELECT chiave FROM articolo_canali WHERE store=? AND variant_id=?", (op["store"], op["variant_id"])).fetchone()
        if r:
            # la disponibilità del negozio segue il canale principale (o lo specchio se il principale non c'è)
            for ch in topo.negozio(op["negozio"]).canali:
                v = c.execute("""SELECT v.disponibile, v.giacenza FROM articolo_canali ac JOIN varianti v
                                 ON v.store=ac.store AND v.variant_id=ac.variant_id WHERE ac.chiave=? AND ac.store=?""",
                              (r["chiave"], ch.store)).fetchone()
                if v:
                    c.execute("UPDATE articolo_negozi SET disponibile=?, giacenza=? WHERE chiave=? AND negozio=?",
                              (v["disponibile"], v["giacenza"], r["chiave"], op["negozio"]))
                    break
        chiudi_anomalia(c, f"operazione:{op['id']}", "esecuzione", "operazione riuscita")


def esegui(c, sorgente, op_id: int) -> str:
    op = dict(c.execute("SELECT * FROM operazioni WHERE id=?", (op_id,)).fetchone())
    if op["stato"] not in DA_ESEGUIRE:
        return op["stato"]
    doc = documento(c, op)
    motivo = MOTIVI[op["tipo"]]

    if op["stato"] == "incerta":
        eta = datetime.now(timezone.utc) - datetime.fromisoformat(op["aggiornata_il"])
        if not op["chiave_shopify"] or eta > FINESTRA_CHIAVE:
            _salva(c, op_id, stato="da_verificare",
                   errore="Esito incerto da più di 23 ore: controlla nello storico della giacenza su Shopify se il "
                          "movimento c'è, poi segnalo come applicato o non applicato.")
            _segnala(c, op, "Esito incerto oltre la finestra di sicurezza: serve una verifica a mano.")
            return "da_verificare"
        try:
            dopo = sorgente.aggiusta(op["store"], op["location_id"], op["item_id"], op["delta"], op["change_from"],
                                     motivo, doc, op["chiave_shopify"])
            _riuscita(c, sorgente.topo, op, dopo)
            return "fatta"
        except Conflitto:
            pass                           # la chiamata originale NON era passata: si riparte con una lettura nuova
        except Incerta as e:
            _salva(c, op_id, errore=str(e))
            return "incerta"
        except (Rifiutata, ErroreSorgente) as e:
            _salva(c, op_id, stato="errore", errore=str(e))
            _segnala(c, op, str(e))
            return "errore"

    for _ in range(TENTATIVI):
        try:
            livello = sorgente.leggi_livelli(op["store"], op["location_id"], [op["item_id"]]).get(op["item_id"])
        except ErroreSorgente as e:
            _salva(c, op_id, stato="da_fare", errore=f"lettura non riuscita, si riprova: {e}")
            return "da_fare"
        if livello is None:
            _salva(c, op_id, stato="errore", errore="articolo non attivo in questa sede")
            _segnala(c, op, "L'articolo non è attivo nella sede: attivalo su Shopify o annulla l'operazione.")
            return "errore"
        disp = livello["disponibile"]
        if op["delta"] < 0 and disp + op["delta"] < 0:
            testo = f"Servono {-op['delta']} pezzi, disponibili {disp}: forse venduti nel frattempo. Nulla è stato scritto."
            _salva(c, op_id, stato="conflitto", errore=testo, change_from=disp)
            _segnala(c, op, testo)
            return "conflitto"
        tentativo = op["tentativi"] + 1
        op.update(tentativi=tentativo, chiave_shopify=f"{op['chiave']}-t{tentativo}", change_from=disp)
        _salva(c, op_id, stato="in_corso", tentativi=tentativo, chiave_shopify=op["chiave_shopify"], change_from=disp)
        try:
            dopo = sorgente.aggiusta(op["store"], op["location_id"], op["item_id"], op["delta"], disp, motivo, doc,
                                     op["chiave_shopify"])
            _riuscita(c, sorgente.topo, op, dopo)
            return "fatta"
        except Conflitto:
            _salva(c, op_id, stato="da_fare", errore="giacenza cambiata durante la scrittura: rilettura")
            continue
        except Incerta as e:
            _salva(c, op_id, stato="incerta", errore=str(e))
            _segnala(c, op, f"{e}. Il programma ripeterà la stessa chiamata (non verrà applicata due volte).")
            return "incerta"
        except (Rifiutata, ErroreSorgente) as e:
            _salva(c, op_id, stato="errore", errore=str(e))
            _segnala(c, op, str(e))
            return "errore"
    testo = "La giacenza continua a cambiare durante la scrittura: riprova fra poco."
    _salva(c, op_id, stato="conflitto", errore=testo)
    _segnala(c, op, testo)
    return "conflitto"


def esegui_in_attesa(c, sorgente, trasferimento_id=None) -> dict:
    """Esegue le operazioni da fare o incerte. Una sola esecuzione alla volta nel processo."""
    with _blocco:
        c.execute("UPDATE operazioni SET stato='incerta', errore='programma interrotto durante la scrittura' WHERE stato='in_corso'")
        where = "stato IN ('da_fare','incerta')" + (" AND trasferimento_id=?" if trasferimento_id else "")
        ids = [r[0] for r in c.execute(f"SELECT id FROM operazioni WHERE {where} ORDER BY id",
                                       (trasferimento_id,) if trasferimento_id else ())]
        esiti = {}
        for i in ids:
            esiti[i] = esegui(c, sorgente, i)
        return esiti


def avvia_in_sottofondo(db_path, sorgente, trasferimento_id=None):
    from .db import connetti

    def lavoro():
        c = connetti(db_path)
        try:
            esegui_in_attesa(c, sorgente, trasferimento_id)
        finally:
            c.close()
    threading.Thread(target=lavoro, daemon=True, name="operazioni").start()


def decidi(c, utente, op_id: int, decisione: str, nota: str):
    """Decisione del responsabile su un'operazione bloccata. Tutto finisce nel registro."""
    op = c.execute("SELECT * FROM operazioni WHERE id=?", (op_id,)).fetchone()
    permesse = {"riprova": ("conflitto", "errore"), "applicata": ("incerta", "da_verificare"),
                "non_applicata": ("incerta", "da_verificare"), "annulla": ("conflitto", "errore", "da_verificare")}
    if not op or op["stato"] not in permesse.get(decisione, ()):
        raise ValueError("decisione non valida per lo stato dell'operazione")
    if not (nota or "").strip():
        raise ValueError("scrivi una nota: cosa hai verificato")
    nuovo = {"riprova": "da_fare", "applicata": "fatta", "non_applicata": "da_fare", "annulla": "annullata"}[decisione]
    with transazione(c):
        _salva(c, op_id, stato=nuovo, errore=f"{decisione}: {nota}")
        registra(c, utente, f"operazione {decisione}", f"operazione {op_id}", {"stato": op["stato"]}, {"stato": nuovo, "nota": nota})
        if nuovo in FINALI:
            chiudi_anomalia(c, f"operazione:{op_id}", utente, nota)
