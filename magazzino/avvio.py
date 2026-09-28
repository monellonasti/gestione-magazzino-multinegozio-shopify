"""Preparazione del database, scelta della sorgente (Shopify o demo) e ripristino della demo."""
from datetime import date, timedelta

from . import auth, calcoli, operazioni, trasferimenti as T
from .config import Config
from .db import connetti, impostazione, migra, salva_impostazione, transazione
from .negozi import DEMO, ErroreSorgente, da_file
from .sync import sincronizza

UTENTI_DEMO = [("magazziniere", "Marco (magazzino)", "magazziniere"), ("responsabile", "Giulia (responsabile)", "responsabile")]
PASSWORD_DEMO = "demo1234"


def topologia(cfg: Config):
    if cfg.demo:
        return DEMO
    try:
        return da_file(cfg.file_negozi)
    except ValueError as e:
        raise SystemExit(str(e))


def sorgente_per(cfg: Config):
    """Ritorna (sorgente, errore). In demo non si leggono MAI le credenziali dei negozi veri."""
    if cfg.demo:
        from .demo import SorgenteDemo
        return SorgenteDemo(lambda: connetti(cfg.db_path)), None
    try:
        from .shopify import SorgenteShopify
        return SorgenteShopify(topologia(cfg), cfg.env_negozi), None
    except ErroreSorgente as e:
        return None, str(e)


def prepara(c, cfg: Config):
    migra(c)
    topo = topologia(cfg)
    with transazione(c):
        for n in topo.negozi:
            c.execute("INSERT INTO soglie(negozio, chiave, minima, obiettivo, modificata_da, modificata_il) "
                      "VALUES(?, '*', 1, 2, 'predefinito', NULL) ON CONFLICT DO NOTHING", (n.id,))
        if impostazione(c, "priorita_pari") is None:
            salva_impostazione(c, "priorita_pari", topo.negozi[0].id)
    # operazioni rimaste "in corso" da un arresto improvviso: esito incerto, si ripetono con la stessa chiave
    c.execute("UPDATE operazioni SET stato='incerta', errore='programma interrotto durante la scrittura' WHERE stato='in_corso'")


def ripristina_demo(cfg: Config, sorgente):
    """Cancella tutto il database demo e ricrea negozi finti, utenti e alcuni trasferimenti di esempio."""
    from . import demo
    assert cfg.demo, "il ripristino demo si fa solo in modalità demo"
    c = connetti(cfg.db_path)
    try:
        migra(c)
        tabelle = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        with transazione(c):
            c.execute("PRAGMA defer_foreign_keys=ON")
            for t in tabelle:
                c.execute(f"DELETE FROM {t}")
        prepara(c, cfg)
        for u, nome, ruolo in UTENTI_DEMO:
            auth.crea_utente(c, u, nome, ruolo, PASSWORD_DEMO)
        demo.popola(c)
        salva_impostazione(c, "trasferimenti_shopify_dal", "2026-01-01")
        salva_impostazione(c, "demo_errore_sync", False)      # prima lettura completa
        sincronizza(c, sorgente, DEMO)
        _ubicazioni(c)
        _scenario(c, sorgente)
        salva_impostazione(c, "demo_errore_sync", True)       # da qui "Centro online" non risponde: dati vecchi
        sincronizza(c, sorgente, DEMO)
    finally:
        c.close()


def _ubicazioni(c):
    """Scaffali inventati: al Centro corsie A-D, al Porto scaffali P1-P6."""
    with transazione(c):
        for i, (chiave, negozio) in enumerate(c.execute("SELECT chiave, negozio FROM articolo_negozi ORDER BY chiave, negozio").fetchall()):
            ubi = f"{'ABCD'[i % 4]}{i % 7 + 1}-{i % 3 + 1}" if negozio == "centro" else f"P{i % 6 + 1}.{i % 4 + 1}"
            c.execute("INSERT OR REPLACE INTO ubicazioni VALUES(?,?,?)", (negozio, chiave, ubi))


def _scenario(c, sorgente):
    """Trasferimenti in stati diversi, fatti con le stesse funzioni che usano le pagine."""
    oggi = date.today()
    esegui = lambda: operazioni.esegui_in_attesa(c, sorgente)

    def da_piano(piano, n, tipo, origine, destinazione, coperture=False):
        righe = [{"chiave": r["chiave"], "quantita": r["quantita"],
                  "coperture": _coperture(r) if coperture else []} for r in piano if r["quantita"] > 0][:n]
        return T.crea(c, DEMO, "magazziniere", origine, destinazione, tipo, righe) if righe else None

    # 1. reintegro del venduto delle ultime due settimane, già ricevuto
    t1 = da_piano(calcoli.piano_reintegro(c, DEMO, "venduto", "centro", "porto", oggi - timedelta(days=21), oggi - timedelta(days=8)),
                  5, "reintegro", "centro", "porto", coperture=True)
    if t1:
        _porta_a(c, sorgente, t1, "ricevuto")
    # 2. bilanciamento spedito e ricevuto in parte: un pezzo manca, uno è arrivato rotto
    t2 = da_piano([r for r in calcoli.piano_bilanciamento(c, DEMO) if r["origine"] == "centro"], 4, "bilanciamento", "centro", "porto")
    if t2:
        _porta_a(c, sorgente, t2, "spedito")
        _, righe = T.carica(c, t2)
        ricevute = {r["id"]: (r["spedita"], 0) for r in righe}
        if len(righe) >= 2:
            ricevute[righe[0]["id"]] = (max(0, righe[0]["spedita"] - 1), 0)
            ricevute[righe[1]["id"]] = (max(0, righe[1]["spedita"] - 1), 1 if righe[1]["spedita"] else 0)
        T.ricevi(c, DEMO, True, "magazziniere", t2, {k: v for k, v in ricevute.items() if sum(v)}, "primo collo")
        esegui()
    # 3. reintegro del venduto recente, approvato e da preparare
    t3 = da_piano(calcoli.piano_reintegro(c, DEMO, "venduto", "centro", "porto", oggi - timedelta(days=7), oggi),
                  6, "reintegro", "centro", "porto", coperture=True)
    if t3:
        _porta_a(c, sorgente, t3, "approvato")
    # 4. scorta minima al Centro, ancora in bozza
    da_piano(calcoli.piano_reintegro(c, DEMO, "scorta", "porto", "centro", oggi, oggi), 4, "reintegro", "porto", "centro")


def _coperture(riga):
    from .regole import assegna_copertura
    return assegna_copertura(riga.get("vendite") or [], riga["quantita"])


def _porta_a(c, sorgente, tid, stato):
    t, righe = T.carica(c, tid)
    T.approva(c, "responsabile", tid, t["versione"], {r["id"]: r["proposta"] for r in righe})
    if stato == "approvato":
        return
    t, _ = T.carica(c, tid)
    T.inizia_preparazione(c, "magazziniere", tid, t["versione"])
    t, righe = T.carica(c, tid)
    for r in righe:
        T.preleva(c, "magazziniere", tid, r["id"], r["approvata"])
    t, _ = T.carica(c, tid)                      # ogni prelievo cambia la versione
    T.spedisci(c, DEMO, sorgente, True, "magazziniere", tid, t["versione"], {})
    operazioni.esegui_in_attesa(c, sorgente)
    if stato == "spedito":
        return
    _, righe = T.carica(c, tid)
    T.ricevi(c, DEMO, True, "magazziniere", tid, {r["id"]: (r["spedita"], 0) for r in righe})
    operazioni.esegui_in_attesa(c, sorgente)
