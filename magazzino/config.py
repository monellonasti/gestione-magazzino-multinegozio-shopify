"""
Impostazioni del programma, lette dal file .env di questa cartella (vedi .env.example).

I negozi veri stanno in negozi.json (fuori da git, modello in negozi.example.json). Le credenziali Shopify NON stanno
qui: per ogni negozio Shopify il .env indica, nella riga ENV_<STORE>, il file con le credenziali dell'app (spesso quello
che usano già gli script). Il programma li legge senza stamparli.
"""
import os, re
from dataclasses import dataclass, field
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATI = BASE / "dati"
FUSO = "Europe/Rome"


def leggi_env(fp: Path) -> dict:
    out = {}
    if not fp.exists():
        return out
    for riga in fp.read_text(encoding="utf-8-sig").splitlines():
        riga = riga.strip()
        if riga and not riga.startswith("#") and "=" in riga:
            k, v = riga.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def chiave_env(store: str) -> str:
    """Il nome di un negozio Shopify come compare nelle righe ENV_<STORE> del .env (a_cassa -> A_CASSA)."""
    return re.sub(r"[^A-Z0-9]", "_", store.upper())


def _vero(v) -> bool:
    return str(v or "").strip().lower() in ("1", "si", "sì", "true", "yes", "on")


def _intero(nome: str, valore, minimo: int, massimo: int) -> int:
    """Un numero del .env: se non è valido il programma non parte, e dice quale riga correggere."""
    try:
        n = int(str(valore).strip())
    except ValueError:
        raise SystemExit(f"{nome} nel .env deve essere un numero intero, non {valore!r}") from None
    if not minimo <= n <= massimo:
        raise SystemExit(f"{nome} nel .env deve essere fra {minimo} e {massimo}, non {n}")
    return n


@dataclass
class Config:
    modo: str = "demo"                      # "demo" oppure "reale"
    host: str = "0.0.0.0"
    porta: int = 8000
    scritture: bool = False                 # scritture reali su Shopify (in demo sono sempre attive, sui negozi finti)
    rettifiche: bool = False                # rettifiche da conteggio sui negozi veri
    sync_minuti: int = 15
    env_negozi: dict = field(default_factory=dict)      # store -> file credenziali (dalle righe ENV_<STORE>)
    negozi_file: Path | None = None         # negozi veri; se manca, negozi.json nella cartella del programma
    db_file: Path | None = None             # solo per i test: un database altrove

    @property
    def demo(self) -> bool:
        return self.modo == "demo"

    @property
    def db_path(self) -> Path:
        return self.db_file or DATI / ("demo.db" if self.demo else "magazzino.db")

    @property
    def file_negozi(self) -> Path:
        return Path(self.negozi_file) if self.negozi_file else BASE / "negozi.json"

    @property
    def scritture_attive(self) -> bool:
        return self.demo or self.scritture

    @property
    def rettifiche_attive(self) -> bool:
        return self.demo or (self.scritture and self.rettifiche)


def carica(modo: str | None = None, env_file: Path | None = None) -> Config:
    e = {**leggi_env(env_file or BASE / ".env"), **{k: v for k, v in os.environ.items() if k.startswith("MAGAZZINO_")}}
    g = lambda k, d=None: e.get("MAGAZZINO_" + k, e.get(k, d))
    c = Config(
        modo=(modo or g("MODALITA", "demo")).strip().lower(),
        host=g("HOST", "0.0.0.0"),
        porta=_intero("PORTA", g("PORTA", 8000), 1, 65535),
        scritture=_vero(g("SCRITTURE_SHOPIFY")),
        rettifiche=_vero(g("RETTIFICHE")),
        # con 0 o meno la lettura periodica ripartirebbe subito, di continuo, contro i limiti di Shopify
        sync_minuti=_intero("SYNC_MINUTI", g("SYNC_MINUTI", 15), 1, 24 * 60),
        # percorsi relativi: rispetto alla cartella del programma, non a quella da cui lo si lancia
        env_negozi={chiave_env(k[4:]): (str((BASE / v).resolve()) if v and not Path(v).is_absolute() else v)
                    for k, v in e.items() if k.startswith("ENV_") and v},
        negozi_file=(BASE / g("NEGOZI_FILE")) if g("NEGOZI_FILE") else None,
    )
    if c.modo not in ("demo", "reale"):
        raise SystemExit(f"MODALITA deve essere demo o reale, non {c.modo!r}")
    DATI.mkdir(exist_ok=True)
    return c
