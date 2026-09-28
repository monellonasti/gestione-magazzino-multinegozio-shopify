"""
Database SQLite del programma. Un file per modalità: dati/magazzino.db (negozi veri) e dati/demo.db (demo),
così la demo non può toccare i dati reali nemmeno per sbaglio.

Le migrazioni sono la lista MIGRAZIONI: ognuna si applica una volta sola (PRAGMA user_version).
Gli orari si salvano in UTC (ISO 8601) e si mostrano nel fuso Europe/Rome.
"""
import json, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import FUSO

ROMA = ZoneInfo(FUSO)

MIGRAZIONI = [
    # 1: schema iniziale
    """
    CREATE TABLE utenti(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, nome TEXT NOT NULL,
      ruolo TEXT NOT NULL CHECK(ruolo IN ('magazziniere','responsabile')), hash TEXT NOT NULL, attivo INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE sessioni(token TEXT PRIMARY KEY, utente_id INTEGER NOT NULL REFERENCES utenti(id), scade TEXT NOT NULL);

    -- copia locale delle varianti dei negozi Shopify, con la giacenza della sede usata (una per negozio Shopify)
    CREATE TABLE varianti(store TEXT NOT NULL, variant_id TEXT NOT NULL, product_id TEXT, item_id TEXT NOT NULL,
      prodotto TEXT, variante TEXT, sku TEXT, ean TEXT, immagine TEXT, stato TEXT, tracciata INTEGER NOT NULL DEFAULT 1,
      in_sede INTEGER NOT NULL DEFAULT 0, disponibile INTEGER, giacenza INTEGER, impegnata INTEGER, letto_il TEXT,
      PRIMARY KEY(store, variant_id));
    CREATE INDEX varianti_ean ON varianti(store, ean);
    CREATE TABLE sedi(store TEXT PRIMARY KEY, location_id TEXT NOT NULL, nome TEXT NOT NULL);
    CREATE TABLE sincronizzazioni(id INTEGER PRIMARY KEY, store TEXT NOT NULL, tipo TEXT NOT NULL, inizio TEXT NOT NULL,
      fine TEXT, esito TEXT NOT NULL, messaggio TEXT, righe INTEGER NOT NULL DEFAULT 0);

    -- consumo per riga d'ordine: solo quantità, data e sede. Niente clienti, prezzi o importi.
    CREATE TABLE venduto(store TEXT NOT NULL, riga_ordine TEXT NOT NULL, variant_id TEXT NOT NULL, location_id TEXT,
      data TEXT NOT NULL, pezzi INTEGER NOT NULL, aggiornato_il TEXT, PRIMARY KEY(store, riga_ordine));
    CREATE INDEX venduto_variante ON venduto(store, variant_id, data);

    -- abbinamento fra negozi: ricalcolato a ogni lettura; le associazioni manuali sono decisioni del responsabile
    CREATE TABLE associazioni(store TEXT NOT NULL, variant_id TEXT NOT NULL, chiave TEXT NOT NULL, motivo TEXT,
      creata_da TEXT, creata_il TEXT, PRIMARY KEY(store, variant_id));
    CREATE TABLE articoli(chiave TEXT PRIMARY KEY, prodotto TEXT, variante TEXT, sku TEXT, ean TEXT, immagine TEXT,
      stato TEXT NOT NULL, cerca TEXT, aggiornato_il TEXT);
    CREATE TABLE articolo_canali(chiave TEXT NOT NULL, store TEXT NOT NULL, variant_id TEXT NOT NULL, PRIMARY KEY(chiave, store));
    CREATE INDEX articolo_canali_variante ON articolo_canali(store, variant_id);
    CREATE TABLE articolo_negozi(chiave TEXT NOT NULL, negozio TEXT NOT NULL, disponibile INTEGER, giacenza INTEGER,
      PRIMARY KEY(chiave, negozio));

    CREATE TABLE soglie(negozio TEXT NOT NULL, chiave TEXT NOT NULL, minima INTEGER NOT NULL, obiettivo INTEGER NOT NULL,
      modificata_da TEXT, modificata_il TEXT, PRIMARY KEY(negozio, chiave));
    CREATE TABLE ubicazioni(negozio TEXT NOT NULL, chiave TEXT NOT NULL, ubicazione TEXT NOT NULL, PRIMARY KEY(negozio, chiave));

    CREATE TABLE trasferimenti(id INTEGER PRIMARY KEY, codice TEXT UNIQUE NOT NULL, origine TEXT NOT NULL,
      destinazione TEXT NOT NULL, stato TEXT NOT NULL, tipo TEXT NOT NULL, note TEXT, versione INTEGER NOT NULL DEFAULT 1,
      creato_da TEXT, creato_il TEXT, approvato_da TEXT, approvato_il TEXT, preparato_da TEXT, preparato_il TEXT,
      spedito_da TEXT, spedito_il TEXT, ricevuto_da TEXT, ricevuto_il TEXT, annullato_da TEXT, annullato_il TEXT);
    CREATE TABLE righe(id INTEGER PRIMARY KEY, trasferimento_id INTEGER NOT NULL REFERENCES trasferimenti(id),
      chiave TEXT NOT NULL, prodotto TEXT, variante TEXT, sku TEXT, ean TEXT, immagine TEXT, nota TEXT,
      proposta INTEGER NOT NULL, approvata INTEGER, prelevata INTEGER NOT NULL DEFAULT 0, spedita INTEGER,
      ricevuta INTEGER NOT NULL DEFAULT 0, danneggiata INTEGER NOT NULL DEFAULT 0,
      motivo_differenza TEXT, esito_differenza TEXT, differenza_chiusa_da TEXT, differenza_chiusa_il TEXT,
      UNIQUE(trasferimento_id, chiave));
    CREATE TABLE ricezioni(id INTEGER PRIMARY KEY, riga_id INTEGER NOT NULL REFERENCES righe(id), buoni INTEGER NOT NULL,
      danneggiati INTEGER NOT NULL, nota TEXT, da TEXT, il TEXT);
    -- quali unità vendute (righe d'ordine) un reintegro ha già coperto: evita di rimandarle con periodi sovrapposti
    CREATE TABLE coperture(riga_id INTEGER NOT NULL REFERENCES righe(id), store TEXT NOT NULL, riga_ordine TEXT NOT NULL,
      pezzi INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(riga_id, store, riga_ordine));

    -- ogni scrittura su Shopify: una riga, una chiave, uno stato. È il registro che impedisce i doppi movimenti.
    CREATE TABLE operazioni(id INTEGER PRIMARY KEY, chiave TEXT UNIQUE NOT NULL, trasferimento_id INTEGER, riga_id INTEGER,
      rettifica_id INTEGER, tipo TEXT NOT NULL, negozio TEXT NOT NULL, store TEXT NOT NULL, variant_id TEXT, item_id TEXT,
      location_id TEXT, delta INTEGER NOT NULL, stato TEXT NOT NULL, tentativi INTEGER NOT NULL DEFAULT 0,
      chiave_shopify TEXT, change_from INTEGER, dopo INTEGER, errore TEXT, creata_il TEXT, aggiornata_il TEXT);
    CREATE TABLE rettifiche(id INTEGER PRIMARY KEY, negozio TEXT NOT NULL, chiave TEXT NOT NULL, fonte TEXT NOT NULL,
      contati INTEGER NOT NULL, motivo TEXT NOT NULL, anteprima TEXT, stato TEXT NOT NULL,
      richiesta_da TEXT, richiesta_il TEXT, confermata_da TEXT, confermata_il TEXT);

    CREATE TABLE anomalie(id INTEGER PRIMARY KEY, dedup TEXT UNIQUE NOT NULL, tipo TEXT NOT NULL, gravita INTEGER NOT NULL,
      chiave TEXT, titolo TEXT NOT NULL, dettaglio TEXT, stato TEXT NOT NULL DEFAULT 'aperta', creata_il TEXT,
      vista_il TEXT, chiusa_da TEXT, chiusa_il TEXT, nota TEXT);
    CREATE TABLE registro(id INTEGER PRIMARY KEY, il TEXT NOT NULL, utente TEXT, azione TEXT NOT NULL, oggetto TEXT,
      prima TEXT, dopo TEXT);
    CREATE TABLE moduli_usati(token TEXT PRIMARY KEY, il TEXT NOT NULL);
    CREATE TABLE impostazioni(chiave TEXT PRIMARY KEY, valore TEXT);

    -- solo demo: i negozi finti. In modalità reale restano vuote.
    CREATE TABLE demo_varianti(store TEXT NOT NULL, variant_id TEXT NOT NULL, product_id TEXT, item_id TEXT NOT NULL,
      prodotto TEXT, variante TEXT, sku TEXT, ean TEXT, immagine TEXT, stato TEXT, tracciata INTEGER NOT NULL DEFAULT 1,
      in_sede INTEGER NOT NULL DEFAULT 1, disponibile INTEGER NOT NULL DEFAULT 0, impegnata INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(store, variant_id));
    CREATE TABLE demo_venduto(store TEXT NOT NULL, riga_ordine TEXT NOT NULL, variant_id TEXT NOT NULL, location_id TEXT,
      data TEXT NOT NULL, pezzi INTEGER NOT NULL, PRIMARY KEY(store, riga_ordine));
    CREATE TABLE demo_idempotenza(store TEXT NOT NULL, chiave TEXT NOT NULL, richiesta TEXT NOT NULL, risposta TEXT NOT NULL,
      PRIMARY KEY(store, chiave));
    """,
    # 2: trasferimenti fatti su Shopify senza destinazione (merce uscita da una sede, destinazione da scegliere)
    """
    CREATE TABLE trasferimenti_shopify(store TEXT NOT NULL, transfer_id TEXT NOT NULL, nome TEXT, stato_shopify TEXT,
      creato_il TEXT, nota TEXT, origine_location TEXT, origine_nome TEXT, pezzi INTEGER NOT NULL DEFAULT 0,
      righe TEXT NOT NULL, letto_il TEXT, esito TEXT NOT NULL, trasferimento_id INTEGER REFERENCES trasferimenti(id),
      deciso_da TEXT, deciso_il TEXT, nota_decisione TEXT, PRIMARY KEY(store, transfer_id));
    ALTER TABLE operazioni ADD COLUMN documento TEXT;
    CREATE TABLE demo_trasferimenti(store TEXT NOT NULL, transfer_id TEXT NOT NULL, nome TEXT, stato TEXT, creato_il TEXT,
      nota TEXT, origine_location TEXT, origine_nome TEXT, destinazione TEXT, righe TEXT NOT NULL, PRIMARY KEY(store, transfer_id));
    """,
    # 3: carichi da fornitore (trasferimenti Shopify senza origine verso una nostra sede)
    """
    ALTER TABLE trasferimenti_shopify ADD COLUMN verso TEXT NOT NULL DEFAULT 'uscita';
    ALTER TABLE trasferimenti_shopify ADD COLUMN destinazione_location TEXT;
    ALTER TABLE trasferimenti_shopify ADD COLUMN destinazione_nome TEXT;
    ALTER TABLE demo_trasferimenti ADD COLUMN destinazione_location TEXT;
    """,
]


def ora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utc_iso(testo: str) -> str:
    """Qualsiasi data ISO (con Z, con offset o senza) -> 'AAAA-MM-GGTHH:MM:SSZ' in UTC, confrontabile come testo."""
    d = datetime.fromisoformat(testo.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def inizio_giorno_utc(giorno) -> str:
    """Mezzanotte di un giorno (date) a Roma, in UTC."""
    return datetime(giorno.year, giorno.month, giorno.day, tzinfo=ROMA).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def locale(iso: str | None, fmt: str = "%d/%m/%Y %H:%M") -> str:
    if not iso:
        return ""
    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(ROMA).strftime(fmt)


def connetti(path: Path | str) -> sqlite3.Connection:
    c = sqlite3.connect(str(path), timeout=30, check_same_thread=False, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=30000")
    return c


def migra(c: sqlite3.Connection) -> int:
    v = c.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRAZIONI[v:], start=v + 1):
        c.executescript("BEGIN;" + sql + f"; PRAGMA user_version={i}; COMMIT;")
    return len(MIGRAZIONI)


class transazione:
    """with transazione(c): ...  -> BEGIN IMMEDIATE / COMMIT, ROLLBACK se c'è un'eccezione."""
    def __init__(self, c):
        self.c = c

    def __enter__(self):
        self.c.execute("BEGIN IMMEDIATE")
        return self.c

    def __exit__(self, tipo, *_):
        self.c.execute("ROLLBACK" if tipo else "COMMIT")
        return False


def registra(c, utente: str | None, azione: str, oggetto: str | None = None, prima=None, dopo=None):
    """Registro delle azioni (audit): chi, quando, cosa, com'era prima e com'è dopo."""
    j = lambda x: None if x is None else json.dumps(x, ensure_ascii=False, default=str)
    c.execute("INSERT INTO registro(il, utente, azione, oggetto, prima, dopo) VALUES(?,?,?,?,?,?)",
              (ora(), utente, azione, oggetto, j(prima), j(dopo)))


def impostazione(c, chiave: str, default=None):
    r = c.execute("SELECT valore FROM impostazioni WHERE chiave=?", (chiave,)).fetchone()
    return json.loads(r[0]) if r else default


def salva_impostazione(c, chiave: str, valore):
    c.execute("INSERT INTO impostazioni(chiave, valore) VALUES(?,?) ON CONFLICT(chiave) DO UPDATE SET valore=excluded.valore",
              (chiave, json.dumps(valore, ensure_ascii=False)))
