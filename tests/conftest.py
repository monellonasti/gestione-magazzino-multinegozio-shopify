import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from magazzino.avvio import prepara, ripristina_demo, sorgente_per  # noqa: E402
from magazzino.config import Config  # noqa: E402
from magazzino.db import connetti, salva_impostazione  # noqa: E402
from magazzino.negozi import DEMO  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    return Config(modo="demo", db_file=tmp_path / "prova.db", sync_minuti=60)


@pytest.fixture
def demo(cfg):
    """Database demo completo (negozi finti, utenti, trasferimenti di esempio) in una cartella temporanea."""
    sorgente, _ = sorgente_per(cfg)
    ripristina_demo(cfg, sorgente)
    c = connetti(cfg.db_path)
    yield c, sorgente, cfg
    c.close()


@pytest.fixture
def vuoto(cfg):
    """Database migrato ma senza dati: per costruire casi su misura."""
    c = connetti(cfg.db_path)
    prepara(c, cfg)
    sorgente, _ = sorgente_per(cfg)
    salva_impostazione(c, "demo_errore_sync", False)
    yield c, sorgente, cfg
    c.close()


def metti_varianti(c, righe):
    """righe: (store, n, ean, disponibile[, impegnata]) -> demo_varianti. n e' il numero della variante."""
    for r in righe:
        store, n, ean, disp = r[:4]
        imp = r[4] if len(r) > 4 else 0
        c.execute("""INSERT INTO demo_varianti(store, variant_id, product_id, item_id, prodotto, variante, sku, ean, immagine,
                     stato, tracciata, in_sede, disponibile, impegnata) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (store, f"{store}/v{n}", f"{store}/p{n}", f"{store}/i{n}", f"Articolo {n}", "", f"SKU-{n}", ean, "",
                   "ACTIVE", 1, 1, disp, imp))


def giacenza(c, store, n):
    r = c.execute("SELECT disponibile FROM demo_varianti WHERE store=? AND variant_id=?", (store, f"{store}/v{n}")).fetchone()
    return r[0] if r else None


TOPO = DEMO
