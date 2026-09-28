"""
Modalità demo: tre negozi Shopify finti che rappresentano due negozi fisici (Centro, con cassa e sito, e Porto).

SorgenteDemo ha la stessa interfaccia di shopify.SorgenteShopify e la stessa semantica delle scritture:
changeFromQuantity (Conflitto se la giacenza è cambiata), chiave idempotente (la stessa chiave non applica due
volte) ed errori veri e propri. Tre interruttori nelle Impostazioni simulano i guasti:
  - demo_errore_sync   : la lettura di "Centro online" fallisce (errore di sincronizzazione)
  - demo_incerte       : le prossime N scritture vanno a buon fine ma la risposta "si perde" (esito incerto)
  - demo_vendita       : prima della prossima scrittura qualcuno vende un pezzo (conflitto di giacenza)

I dati stanno nelle tabelle demo_* di dati/demo.db: nessun collegamento ai negozi veri.
"""
import json, random, re, unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import impostazione, salva_impostazione, transazione
from .negozi import DEMO, Conflitto, ErroreSorgente, Incerta, Rifiutata

LOC = {"demo-centro": "loc-centro-cassa", "demo-web": "loc-centro-web", "demo-porto": "loc-porto"}


class SorgenteDemo:
    reale = False

    def __init__(self, conn_factory):
        self._conn = conn_factory      # funzione che restituisce una connessione al db demo
        self.topo = DEMO

    def sedi(self, store):
        return {DEMO.canale(store).sede: LOC[store]}

    def leggi_varianti(self, store, location_id, avanzamento=None):
        c = self._conn()
        if store == "demo-web" and impostazione(c, "demo_errore_sync", True):
            raise ErroreSorgente("Centro online: HTTP 503 (errore simulato dalla demo)")
        righe = [dict(r) for r in c.execute("SELECT * FROM demo_varianti WHERE store=? ORDER BY variant_id", (store,))]
        for r in righe:
            r.pop("store")
            r["giacenza"] = r["disponibile"] + r["impegnata"] if r["in_sede"] else None
            if not r["in_sede"]:
                r["disponibile"] = r["impegnata"] = None
        if avanzamento:
            avanzamento(len(righe))
        return righe

    def leggi_venduto(self, store, dal_iso):
        c = self._conn()
        return [dict(r) for r in c.execute(
            "SELECT riga_ordine, variant_id, location_id, data, pezzi FROM demo_venduto WHERE store=?", (store,))]

    def leggi_trasferimenti(self, store, dal_iso):
        c = self._conn()
        out = []
        for r in c.execute("SELECT * FROM demo_trasferimenti WHERE store=? AND creato_il >= ? ORDER BY creato_il DESC", (store, dal_iso)):
            x = dict(r)
            x["righe"] = json.loads(x["righe"])
            x.pop("store")
            out.append(x)
        return out

    def leggi_livelli(self, store, location_id, item_ids):
        c = self._conn()
        out = {}
        for i in item_ids:
            r = c.execute("SELECT in_sede, disponibile, impegnata FROM demo_varianti WHERE store=? AND item_id=?", (store, i)).fetchone()
            if r:
                out[i] = {"disponibile": r["disponibile"], "giacenza": r["disponibile"] + r["impegnata"]} if r["in_sede"] else None
        return out

    def aggiusta(self, store, location_id, item_id, delta, change_from, motivo, documento, chiave):
        c = self._conn()
        richiesta = json.dumps([store, location_id, item_id, delta, change_from, motivo, documento])
        if impostazione(c, "demo_vendita", False):               # vendita simulata fra lettura e scrittura
            with transazione(c):
                salva_impostazione(c, "demo_vendita", False)
                c.execute("UPDATE demo_varianti SET disponibile=disponibile-1 WHERE store=? AND item_id=? AND disponibile>0", (store, item_id))
        with transazione(c):
            vecchia = c.execute("SELECT richiesta, risposta FROM demo_idempotenza WHERE store=? AND chiave=?", (store, chiave)).fetchone()
            if vecchia:
                if vecchia["richiesta"] != richiesta:
                    raise Rifiutata("IDEMPOTENCY_KEY_PARAMETER_MISMATCH: chiave già usata con dati diversi")
                return json.loads(vecchia["risposta"])          # stessa chiave: nessun nuovo movimento
            r = c.execute("SELECT in_sede, disponibile FROM demo_varianti WHERE store=? AND item_id=?", (store, item_id)).fetchone()
            if not r or not r["in_sede"]:
                raise Rifiutata("ITEM_NOT_STOCKED_AT_LOCATION: articolo non attivo in questa sede")
            if change_from is not None and r["disponibile"] != change_from:
                raise Conflitto(f"la giacenza è {r['disponibile']}, non {change_from}")
            dopo = r["disponibile"] + delta
            c.execute("UPDATE demo_varianti SET disponibile=? WHERE store=? AND item_id=?", (dopo, store, item_id))
            c.execute("INSERT INTO demo_idempotenza(store, chiave, richiesta, risposta) VALUES(?,?,?,?)",
                      (store, chiave, richiesta, json.dumps(dopo)))
            incerte = impostazione(c, "demo_incerte", 0)
            if incerte:
                salva_impostazione(c, "demo_incerte", incerte - 1)
        if incerte:
            raise Incerta(f"{DEMO.nomi_store[store]}: nessuna risposta (esito incerto simulato dalla demo)")
        return dopo


# ---------------------------------------------------------------------------------------------- dati sintetici

PRODOTTI = [
    # nome, varianti, marca finta
    ("Candela profumata ambra", [""], "Lumen"), ("Candela profumata fico", [""], "Lumen"),
    ("Candela profumata vaniglia", [""], "Lumen"), ("Diffusore bastoncini agrumi", ["100 ml", "250 ml"], "Aria"),
    ("Olio da massaggio mandorla", ["100 ml", "250 ml"], "Corpo"), ("Olio da massaggio arnica", ["100 ml"], "Corpo"),
    ("Crema mani al karité", ["50 ml"], "Corpo"), ("Crema corpo cocco", ["200 ml"], "Corpo"),
    ("Scrub corpo sale marino", [""], "Corpo"), ("Bagnoschiuma lavanda", ["500 ml"], "Corpo"),
    ("Sali da bagno eucalipto", [""], "Corpo"), ("Burrocacao miele", [""], "Corpo"),
    ("Accappatoio in cotone", ["S", "M", "L"], "Tessile"), ("Kimono in raso", ["S/M", "L/XL"], "Tessile"),
    ("Pigiama in modal", ["S", "M", "L"], "Tessile"), ("Calze in cashmere", ["Grigio", "Blu", "Rosso"], "Tessile"),
    ("Pantofole in feltro", ["38", "40", "42"], "Tessile"), ("Mascherina per dormire in seta", ["Nero", "Rosa"], "Tessile"),
    ("Tazza in ceramica", ["Bianco", "Salvia"], "Casa"), ("Teiera in ghisa", [""], "Casa"),
    ("Infuso rilassante", ["20 bustine"], "Tisane"), ("Infuso zenzero e limone", ["20 bustine"], "Tisane"),
    ("Tisana notte", ["20 bustine"], "Tisane"), ("Miele millefiori", ["250 g"], "Dispensa"),
    ("Cioccolato fondente 70%", ["100 g"], "Dispensa"), ("Biscotti al burro", ["200 g"], "Dispensa"),
    ("Libro di ricette vegetariane", [""], "Libri"), ("Agenda 2027", ["A5"], "Carta"),
    ("Taccuino a righe", ["A5", "A6"], "Carta"), ("Penna stilografica", ["Nero"], "Carta"),
    ("Borraccia termica", ["500 ml", "750 ml"], "Viaggio"), ("Beauty case", [""], "Viaggio"),
    ("Pochette in lino", ["Naturale"], "Viaggio"), ("Specchio da borsa", [""], "Viaggio"),
    ("Pettine in legno", [""], "Cura"), ("Spazzola in setole naturali", [""], "Cura"),
    ("Lima per unghie in vetro", [""], "Cura"), ("Fascia per capelli", ["Crema", "Nero"], "Cura"),
    ("Profumo per ambienti rosa", ["100 ml"], "Aria"), ("Gel igienizzante mani", ["75 ml"], "Cura"),
    ("Set regalo relax", [""], "Regali"), ("Carta regalo", ["Rotolo"], "Regali"),
    ("Buono regalo cartaceo", [""], "Regali"),
]


def _ean(base12: str) -> str:
    s = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(base12))
    return base12 + str((10 - s % 10) % 10)


def _svg(testo: str, n: int) -> str:
    tinte = ["#E8D5C4", "#D9E4DD", "#F1E3D3", "#DCE3EC", "#EFD9DF", "#E6E1D3", "#D8E2E9", "#F0E6CC"]
    iniziali = "".join(p[0] for p in testo.split()[:2]).upper()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160" viewBox="0 0 160 160">'
            f'<rect width="160" height="160" rx="18" fill="{tinte[n % len(tinte)]}"/>'
            f'<text x="80" y="96" font-family="Georgia,serif" font-size="48" text-anchor="middle" fill="#5b4a3f">{iniziali}</text></svg>')


def immagine(n: int) -> str:
    return f"/demo/img/{n}.svg"


FOTO = Path(__file__).resolve().parent / "static" / "demo" / "prodotti"


def _slug(testo: str) -> str:
    t = unicodedata.normalize("NFKD", testo).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def nome_foto(prodotto: str, variante: str = "") -> str:
    return _slug(prodotto) + (f"-{_slug(variante)}" if variante else "")


def foto(prodotto: str, variante: str, n: int) -> str:
    """La foto del colore o del prodotto (generate con strumenti/foto_demo.py); se manca, il riquadro con le iniziali."""
    for nome in ([nome_foto(prodotto, variante)] if variante else []) + [nome_foto(prodotto)]:
        if (FOTO / f"{nome}.jpg").exists():
            return f"/static/demo/prodotti/{nome}.jpg"
    return immagine(n)


def svg_prodotto(c, n: int) -> str | None:
    r = c.execute("SELECT prodotto FROM demo_varianti WHERE immagine=? LIMIT 1", (immagine(n),)).fetchone()
    return _svg(r["prodotto"], n) if r else None


def popola(c, adesso: datetime | None = None):
    """Svuota e ricrea i negozi finti. Deterministico: stessi dati a ogni ripristino."""
    rnd = random.Random(20260925)
    adesso = adesso or datetime.now(timezone.utc)
    with transazione(c):
        for t in ("demo_varianti", "demo_venduto", "demo_idempotenza"):
            c.execute(f"DELETE FROM {t}")
        n = 0
        varianti = []
        for pi, (nome, vv, marca) in enumerate(PRODOTTI):
            for vi, var in enumerate(vv):
                n += 1
                # alcuni EAN iniziano con zeri: devono restare stringhe dall'inizio alla fine
                base = f"00{8000000000 + n * 7919:010d}" if n % 9 == 0 else f"80{rnd.randrange(10**9, 10**10):010d}"
                varianti.append({"n": n, "pid": pi + 1, "prodotto": nome, "variante": var, "sku": f"{marca[:3].upper()}-{pi + 1:03d}-{vi + 1}",
                                 "ean": _ean(base[:12]), "img": foto(nome, var, pi + 1)})
        for v in varianti:
            n = v["n"]
            presenza = {"demo-centro": n % 11 != 5, "demo-porto": n % 13 != 7}   # alcuni articoli solo in un negozio
            presenza["demo-web"] = presenza["demo-centro"]
            base_centro = rnd.choice([0, 1, 2, 3, 4, 6, 8, 12, 20])
            base_porto = rnd.choice([0, 0, 1, 1, 2, 3, 5, 9])
            for store, q in (("demo-centro", base_centro), ("demo-web", base_centro), ("demo-porto", base_porto)):
                if not presenza[store]:
                    continue
                ean = v["ean"]
                if n == 4 and store == "demo-porto":
                    ean = ""                                  # EAN mancante a Porto
                if n == 17:
                    ean = "" if store != "demo-porto" else ean   # EAN mancante al Centro (su cassa e sito)
                if n == 23 and store == "demo-porto":
                    ean = varianti[24]["ean"]                 # EAN doppio a Porto: due articoli con lo stesso codice
                if n == 30 and store == "demo-web":
                    q = max(0, q - 2)                         # sito non allineato alla cassa
                if n == 12 and store == "demo-porto":
                    q = -1                                    # giacenza negativa (vendita senza merce)
                impegnata = 1 if (store != "demo-porto" and n % 7 == 0 and q > 1) else 0
                c.execute("""INSERT INTO demo_varianti(store, variant_id, product_id, item_id, prodotto, variante, sku, ean,
                             immagine, stato, tracciata, in_sede, disponibile, impegnata) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (store, f"{store}/v{n}", f"{store}/p{v['pid']}", f"{store}/i{n}", v["prodotto"], v["variante"],
                           v["sku"], ean, v["img"], "ACTIVE", 1, 1, q - impegnata, impegnata))
        # vendite degli ultimi 45 giorni: il Porto vende più in fretta di quanto riceve
        riga = 0
        for v in varianti:
            for store, lam in (("demo-porto", 0.16), ("demo-centro", 0.10), ("demo-web", 0.05)):
                if not c.execute("SELECT 1 FROM demo_varianti WHERE store=? AND variant_id=?", (store, f"{store}/v{v['n']}")).fetchone():
                    continue
                for giorno in range(45):
                    if rnd.random() < lam:
                        riga += 1
                        quando = (adesso - timedelta(days=giorno, hours=rnd.randrange(0, 10))).isoformat(timespec="seconds")
                        c.execute("INSERT INTO demo_venduto VALUES(?,?,?,?,?,?)",
                                  (store, f"{store}/riga{riga}", f"{store}/v{v['n']}", LOC[store], quando, rnd.choice([1, 1, 1, 2])))
        _trasferimenti_shopify(c, adesso)
        salva_impostazione(c, "demo_errore_sync", True)
        salva_impostazione(c, "demo_incerte", 0)
        salva_impostazione(c, "demo_vendita", False)


def _trasferimenti_shopify(c, adesso):
    """
    Trasferimenti fatti "su Shopify", con gli effetti che avrebbe Shopify: il movimento c'è solo sul negozio Shopify
    dove è stato registrato (la cassa del Centro non avvisa il sito, il Porto non avvisa nessuno).
      #T0101 cassa del Centro, uscita senza destinazione (nota «scatola per il Porto»)
      #T0102 Porto, uscita senza destinazione
      #T0103 sito del Centro, uscita pronta ma non ancora partita
      #T0104 cassa del Centro, carico da fornitore
      #T0106 sito del Centro, lo stesso carico registrato di nuovo a mano (come succedeva davvero con i carichi registrati a mano)
      #T0105 Porto, carico da fornitore
    """
    def scegli(store, n, salta=0, minimo=4):
        # articoli con merce, presenti anche nell'altro negozio: quelli che si spostano davvero
        altro = "demo-porto" if store != "demo-porto" else "demo-centro"
        return c.execute("""SELECT * FROM demo_varianti v WHERE store=? AND disponibile >= ? AND ean != '' AND EXISTS(
                            SELECT 1 FROM demo_varianti x WHERE x.store=? AND x.ean=v.ean) ORDER BY CAST(SUBSTR(variant_id,
                            INSTR(variant_id, '/v') + 2) AS INTEGER) LIMIT ? OFFSET ?""", (store, minimo, altro, n, salta)).fetchall()

    def crea(store, numero, stato, giorni_fa, nota, varianti, quantita, verso):
        righe = []
        for v, q in zip(varianti, quantita):
            v = c.execute("SELECT * FROM demo_varianti WHERE store=? AND ean=?", (store, v["ean"])).fetchone()
            n = v["variant_id"].rsplit("/v", 1)[-1]
            fatto = q if stato in ("TRANSFERRED", "IN_PROGRESS") else 0
            righe.append({"line_id": f"{store}/tl{numero}-{n}", "titolo": v["prodotto"], "item_id": v["item_id"],
                          "variant_id": v["variant_id"], "sku": v["sku"], "ean": v["ean"], "totale": q, "spedita": fatto,
                          "ricevuta": fatto if verso == "ingresso" else 0})
            if fatto:
                c.execute("UPDATE demo_varianti SET disponibile=disponibile+? WHERE store=? AND variant_id=?",
                          (fatto if verso == "ingresso" else -fatto, store, v["variant_id"]))
        quando = (adesso - timedelta(days=giorni_fa, hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        uscita = verso == "uscita"
        c.execute("""INSERT INTO demo_trasferimenti(store, transfer_id, nome, stato, creato_il, nota, origine_location, origine_nome,
                     destinazione, destinazione_location, righe) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                  (store, f"gid://demo/InventoryTransfer/{numero}", f"#T{numero:04d}", stato, quando, nota,
                   LOC[store] if uscita else None, DEMO.canale(store).sede if uscita else None,
                   None if uscita else DEMO.canale(store).sede, None if uscita else LOC[store], json.dumps(righe)))
    c.execute("DELETE FROM demo_trasferimenti")
    crea("demo-centro", 101, "TRANSFERRED", 1, "scatola per il Porto", scegli("demo-centro", 4), [1, 2, 1, 2], "uscita")
    crea("demo-porto", 102, "TRANSFERRED", 0, "", scegli("demo-porto", 2), [1, 2], "uscita")
    crea("demo-web", 103, "READY_TO_SHIP", 0, "", scegli("demo-web", 1, salta=6), [1], "uscita")
    fornitura = scegli("demo-centro", 5, salta=8, minimo=0)
    crea("demo-centro", 104, "TRANSFERRED", 0, "ordine fornitore n. 4471", fornitura, [6, 4, 3, 5, 2], "ingresso")
    crea("demo-web", 106, "TRANSFERRED", 0, "", fornitura, [6, 4, 3, 5, 2], "ingresso")
    crea("demo-porto", 105, "TRANSFERRED", 0, "", scegli("demo-porto", 2, salta=3, minimo=0), [4, 3], "ingresso")
