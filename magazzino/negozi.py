"""
Chi è chi: i negozi fisici (quelli fra cui si sposta la merce) e i negozi Shopify che li rappresentano.

Un negozio fisico ha un canale PRINCIPALE (il negozio Shopify e la sede da cui si leggono le giacenze) e zero o più
canali SPECCHIO: altri negozi Shopify che vendono la stessa merce fisica. Quando la merce parte o arriva, il
programma aggiorna il principale e anche gli specchi, così restano allineati.

I negozi veri stanno in negozi.json, fuori da git (modello: negozi.example.json). Per esempio:

  Negozio A  = negozio Shopify con la cassa, sede A (principale)  +  negozio Shopify online, sede A (specchio)
  Negozio B  = negozio Shopify, sede B

Le altre sedi (per esempio un magazzino esterno con quantità da fornitore) non partecipano: non ci si preleva.
"""
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Canale:
    store: str          # chiave del negozio Shopify
    sede: str           # nome della sede (location) su quel negozio
    principale: bool


@dataclass(frozen=True)
class Negozio:
    id: str
    nome: str
    canali: tuple

    @property
    def principale(self) -> Canale:
        return next(c for c in self.canali if c.principale)

    @property
    def specchi(self) -> list:
        return [c for c in self.canali if not c.principale]


@dataclass(frozen=True)
class Topologia:
    negozi: tuple
    nomi_store: dict      # chiave -> nome leggibile
    domini: dict          # chiave -> domini myshopify accettati, il primo e' quello ufficiale (controllo sul .env)

    def negozio(self, nid: str) -> Negozio:
        for n in self.negozi:
            if n.id == nid:
                return n
        raise KeyError(nid)

    def negozio_di(self, store: str) -> Negozio:
        return next(n for n in self.negozi if any(c.store == store for c in n.canali))

    @property
    def stores(self) -> list:
        return [c.store for n in self.negozi for c in n.canali]

    def canale(self, store: str) -> Canale:
        return next(c for n in self.negozi for c in n.canali if c.store == store)


def da_file(percorso) -> Topologia:
    """I negozi veri, dal file negozi.json. Due negozi fisici, ciascuno con un solo canale principale."""
    p = Path(percorso)
    if not p.exists():
        raise ValueError(f"Manca {p.name}: copia negozi.example.json in {p.name} e metti i negozi veri (vedi docs/TECNICO.md)")
    d = json.loads(p.read_text(encoding="utf-8"))
    negozi, nomi, domini = [], {}, {}
    for n in d["negozi"]:
        canali = tuple(Canale(ch["store"], ch["sede"], bool(ch.get("principale"))) for ch in n["canali"])
        if sum(ch.principale for ch in canali) != 1:
            raise ValueError(f"{p.name}: il negozio {n['nome']} deve avere un solo canale principale")
        for ch in n["canali"]:
            nomi[ch["store"]], domini[ch["store"]] = ch["nome"], tuple(ch["domini"])
        negozi.append(Negozio(n["id"], n["nome"], canali))
    if len(negozi) != 2:
        raise ValueError(f"{p.name}: servono esattamente due negozi fisici")
    return Topologia(tuple(negozi), nomi, domini)

DEMO = Topologia(
    negozi=(
        Negozio("centro", "Negozio Centro", (Canale("demo-centro", "Centro", True), Canale("demo-web", "Centro", False))),
        Negozio("porto", "Negozio Porto", (Canale("demo-porto", "Porto", True),)),
    ),
    nomi_store={"demo-centro": "Centro (cassa)", "demo-web": "Centro online", "demo-porto": "Porto"},
    domini={"demo-centro": ("demo-centro.example",), "demo-web": ("demo-web.example",), "demo-porto": ("demo-porto.example",)},
)


class ErroreSorgente(Exception):
    """Lettura o scrittura non riuscita, con un messaggio da mostrare."""


class Conflitto(ErroreSorgente):
    """La quantità è cambiata fra la lettura e la scrittura (changeFromQuantity): il movimento NON è stato applicato."""


class Rifiutata(ErroreSorgente):
    """Shopify ha risposto con un errore esplicito: il movimento NON è stato applicato."""


class Incerta(ErroreSorgente):
    """Nessuna risposta (rete, timeout, errore 5xx): non si sa se il movimento è stato applicato."""
