"""
Collegamento ai negozi Shopify veri tramite le app custom (grant client_credentials, come negozi_lib.py).

Le letture si ripetono da sole su 429, 5xx, rete e THROTTLED. Le SCRITTURE no: una scrittura senza risposta
diventa Incerta e la decide operazioni.py, ripetendola con la stessa chiave @idempotent (Shopify la ricorda per
24 ore e non la applica due volte) invece di rifarla alla cieca.

Query validate sullo schema Admin API 2026-07.
"""
import json, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

from .config import leggi_env
from .negozi import Conflitto, ErroreSorgente, Incerta, Rifiutata, Topologia

Q_SEDI = "{ shop { name myshopifyDomain } locations(first: 50) { nodes { id name } } }"

Q_CATALOGO = """query Catalogo($after: String, $loc: ID!) {
  productVariants(first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id title sku barcode
      media(first: 1) { nodes { preview { image { url(transform: {maxWidth: 160, maxHeight: 160}) } } } }
      product { id title status featuredMedia { preview { image { url(transform: {maxWidth: 160, maxHeight: 160}) } } } }
      inventoryItem { id tracked inventoryLevel(locationId: $loc) { quantities(names: ["available", "on_hand", "committed"]) { name quantity } } }
    }
  }
}"""

Q_LIVELLI = """query Livelli($ids: [ID!]!, $loc: ID!) {
  nodes(ids: $ids) { ... on InventoryItem { id inventoryLevel(locationId: $loc) { quantities(names: ["available", "on_hand"]) { name quantity } } } }
}"""

Q_VENDUTO = """query Venduto($after: String, $q: String!) {
  orders(first: 10, after: $after, query: $q, sortKey: UPDATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id createdAt updatedAt cancelledAt test
      retailLocation { id }
      lineItems(first: 20) { nodes { id quantity currentQuantity variant { id } } }
      fulfillmentOrders(first: 3) { nodes { assignedLocation { location { id } } lineItems(first: 20) { nodes { totalQuantity lineItem { id } } } } }
      refunds(first: 5) { refundLineItems(first: 20) { nodes { quantity restockType lineItem { id } } } }
    }
  }
}"""

Q_TRASFERIMENTI = """query Trasferimenti($after: String) {
  inventoryTransfers(first: 20, after: $after, sortKey: CREATED_AT, reverse: true) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id name status dateCreated note totalQuantity
      origin { name location { id } }
      destination { name location { id } }
      lineItems(first: 100) {
        pageInfo { hasNextPage endCursor }
        nodes { id title totalQuantity shippedQuantity inventoryItem { id sku variants(first: 1) { nodes { id barcode } } } }
      }
    }
  }
}"""

Q_RIGHE_TRASFERIMENTO = """query RigheTrasferimento($id: ID!, $after: String) {
  inventoryTransfer(id: $id) {
    lineItems(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { id title totalQuantity shippedQuantity inventoryItem { id sku variants(first: 1) { nodes { id barcode } } } }
    }
  }
}"""

Q_RICEVUTI = """query Ricevuti($id: ID!) {
  inventoryTransfer(id: $id) {
    shipments(first: 10) {
      nodes { id status lineItems(first: 100) { nodes { inventoryItem { id } acceptedQuantity rejectedQuantity } } }
    }
  }
}"""

M_MOVIMENTO = """mutation Movimento($input: InventoryAdjustQuantitiesInput!, $chiave: String!) {
  inventoryAdjustQuantities(input: $input) @idempotent(key: $chiave) {
    inventoryAdjustmentGroup { id changes { name delta quantityAfterChange } }
    userErrors { field message code }
  }
}"""


class Client:
    def __init__(self, nome: str, env_path: Path, domini_accettati: tuple):
        e = leggi_env(Path(env_path))
        if not e:
            raise ErroreSorgente(f"{nome}: file credenziali non trovato ({env_path})")
        dominio = (e.get("SHOPIFY_SHOP") or e.get("SHOPIFY_STORE") or "").replace("https://", "").rstrip("/")
        if dominio and not dominio.endswith(".myshopify.com"):
            dominio += ".myshopify.com"
        if dominio not in domini_accettati:
            raise ErroreSorgente(f"{nome}: il file credenziali punta a {dominio or '?'} invece di {domini_accettati[0]}")
        self.nome, self.dominio = nome, dominio
        self.api = e.get("SHOPIFY_API_VERSION") or "2026-07"
        self._id, self._segreto = e.get("SHOPIFY_CLIENT_ID"), e.get("SHOPIFY_CLIENT_SECRET")
        if not (self._id and self._segreto):
            raise ErroreSorgente(f"{nome}: mancano SHOPIFY_CLIENT_ID o SHOPIFY_CLIENT_SECRET nel file credenziali")
        self._tok, self._scade = None, 0.0

    def token(self) -> str:
        if self._tok and time.time() < self._scade:
            return self._tok
        data = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": self._id,
                                       "client_secret": self._segreto}).encode()
        req = urllib.request.Request(f"https://{self.dominio}/admin/oauth/access_token", data=data,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        for t in range(4):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    out = json.load(r)
                break
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
                if t == 3 or getattr(e, "code", 500) not in (429, 500, 502, 503, 504):
                    raise ErroreSorgente(f"{self.nome}: token non ottenuto ({getattr(e, 'code', type(e).__name__)})")
                time.sleep(1.5 * 2 ** t)
        if not out.get("access_token"):
            raise ErroreSorgente(f"{self.nome}: Shopify non ha restituito il token")
        # il token dura 24 ore (expires_in 86399): si rinnova 10 minuti prima
        self._tok, self._scade = out["access_token"], time.time() + int(out.get("expires_in") or 1200) - 600
        return self._tok

    def _richiesta(self, query, variables, timeout):
        return urllib.request.Request(
            f"https://{self.dominio}/admin/api/{self.api}/graphql.json",
            data=json.dumps({"query": query, "variables": variables or {}}).encode(),
            headers={"Content-Type": "application/json", "X-Shopify-Access-Token": self.token()})

    def query(self, query: str, variables=None, tentativi=6) -> dict:
        for t in range(tentativi):
            try:
                with urllib.request.urlopen(self._richiesta(query, variables, 120), timeout=120) as r:
                    out = json.load(r)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and t < tentativi - 1:
                    time.sleep(float(e.headers.get("Retry-After") or 1.5 * 2 ** t)); continue
                raise ErroreSorgente(f"{self.nome}: HTTP {e.code}")
            except (urllib.error.URLError, TimeoutError) as e:
                if t < tentativi - 1:
                    time.sleep(1.5 * 2 ** t); continue
                raise ErroreSorgente(f"{self.nome}: rete non raggiungibile ({e})")
            if out.get("errors"):
                if "THROTTLED" in json.dumps(out["errors"]) and t < tentativi - 1:
                    time.sleep(1.0 + t); continue
                raise ErroreSorgente(f"{self.nome}: " + "; ".join(x.get("message", "?") for x in out["errors"])[:300])
            stato = out.get("extensions", {}).get("cost", {}).get("throttleStatus", {})
            if stato and stato.get("currentlyAvailable", 1000) < 200:
                time.sleep(1.0)
            return out["data"]
        raise ErroreSorgente(f"{self.nome}: troppi tentativi")

    def mutazione(self, query: str, variables: dict) -> dict:
        """Una sola chiamata. Si ripete solo se Shopify dice chiaramente che non l'ha eseguita (429, THROTTLED)."""
        for t in range(5):
            try:
                with urllib.request.urlopen(self._richiesta(query, variables, 60), timeout=60) as r:
                    out = json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(float(e.headers.get("Retry-After") or 2)); continue
                if e.code >= 500:
                    raise Incerta(f"{self.nome}: HTTP {e.code} senza esito")
                raise Rifiutata(f"{self.nome}: HTTP {e.code}")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                raise Incerta(f"{self.nome}: nessuna risposta ({type(e).__name__})")
            if out.get("errors"):
                codici = {(x.get("extensions") or {}).get("code") for x in out["errors"]}
                if "THROTTLED" in codici:
                    time.sleep(1.0 + t); continue
                if "IDEMPOTENCY_CONCURRENT_REQUEST" in codici:
                    raise Incerta(f"{self.nome}: la stessa operazione è ancora in corso su Shopify")
                if "IDEMPOTENCY_PREVIOUS_ATTEMPT_FAILED" in codici:
                    raise Conflitto(f"{self.nome}: il tentativo precedente era fallito")
                raise Rifiutata(f"{self.nome}: " + "; ".join(x.get("message", "?") for x in out["errors"])[:300])
            return out["data"]
        raise Incerta(f"{self.nome}: Shopify ha rallentato la richiesta troppe volte")


def _quantita(livello) -> dict | None:
    return {q["name"]: q["quantity"] for q in livello["quantities"]} if livello else None


def _immagine(v) -> str:
    for nodo in (v.get("media") or {}).get("nodes") or []:
        url = (((nodo or {}).get("preview") or {}).get("image") or {}).get("url")
        if url:
            return url
    fm = (v["product"].get("featuredMedia") or {}).get("preview") or {}
    return (fm.get("image") or {}).get("url") or ""


def _riga_trasferimento(r: dict) -> dict:
    ii = r.get("inventoryItem") or {}
    v = ((ii.get("variants") or {}).get("nodes") or [{}])[0] or {}
    return {"line_id": r["id"], "titolo": r.get("title") or "", "item_id": ii.get("id"), "variant_id": v.get("id"),
            "sku": (ii.get("sku") or "").strip(), "ean": (v.get("barcode") or "").strip(),
            "totale": r["totalQuantity"], "spedita": r["shippedQuantity"]}


def consumo_ordine(o: dict) -> list:
    """
    Pezzi usciti dal magazzino per ogni riga d'ordine, e da quale sede.
      consumo = currentQuantity + pezzi rimborsati SENZA rimessa a magazzino (NO_RESTOCK)
    currentQuantity esclude già rimborsati e rimossi. Un reso o un annullo rimesso a magazzino (RETURN, CANCEL,
    LEGACY_RESTOCK) non è consumo: Shopify ha già ricaricato la giacenza. Un rimborso senza rimessa lo è: la
    merce è uscita e non è tornata. Ordini di prova: zero. Righe senza variante (articoli personalizzati in cassa): escluse.
    """
    senza_rientro = {}
    for rf in o.get("refunds") or []:
        for r in rf["refundLineItems"]["nodes"]:
            if r["restockType"] == "NO_RESTOCK":
                senza_rientro[r["lineItem"]["id"]] = senza_rientro.get(r["lineItem"]["id"], 0) + r["quantity"]
    sede_riga = {}
    for fo in (o.get("fulfillmentOrders") or {}).get("nodes") or []:
        loc = ((fo.get("assignedLocation") or {}).get("location") or {}).get("id")
        for li in fo["lineItems"]["nodes"]:
            k = li["lineItem"]["id"]
            if loc and li["totalQuantity"] >= sede_riga.get(k, (None, -1))[1]:
                sede_riga[k] = (loc, li["totalQuantity"])
    riserva = (o.get("retailLocation") or {}).get("id")
    out = []
    for li in o["lineItems"]["nodes"]:
        if not li.get("variant"):
            continue
        pezzi = 0 if o.get("test") else li["currentQuantity"] + senza_rientro.get(li["id"], 0)
        out.append({"riga_ordine": li["id"], "variant_id": li["variant"]["id"],
                    "location_id": sede_riga.get(li["id"], (riserva, 0))[0], "data": o["createdAt"], "pezzi": pezzi})
    return out


class SorgenteShopify:
    """Stessa interfaccia di demo.SorgenteDemo: il resto del programma non sa quale delle due sta usando."""
    reale = True

    def __init__(self, topo: Topologia, env_negozi: dict):
        self.topo, self.client = topo, {}
        from .config import chiave_env
        for store in topo.stores:
            percorso = env_negozi.get(chiave_env(store))
            if not percorso:
                raise ErroreSorgente(f"Manca ENV_{chiave_env(store)} nel .env: indica il file credenziali di {topo.nomi_store[store]}")
            self.client[store] = Client(topo.nomi_store[store], Path(percorso), topo.domini[store])

    def sedi(self, store: str) -> dict:
        d = self.client[store].query(Q_SEDI)
        if d["shop"]["myshopifyDomain"] not in self.topo.domini[store]:
            raise ErroreSorgente(f"{store}: risponde {d['shop']['myshopifyDomain']}")
        return {l["name"]: l["id"] for l in d["locations"]["nodes"]}

    PERMESSI = ("read_products", "read_inventory", "write_inventory", "read_locations", "read_orders",
                "read_merchant_managed_fulfillment_orders")

    def verifica_permessi(self) -> dict:
        """Per ogni negozio: permessi che il programma usa e che l'app NON ha (lista vuota = tutto a posto)."""
        out = {}
        for store, cl in self.client.items():
            try:
                d = cl.query("{ currentAppInstallation { accessScopes { handle } } }")
                ha = {x["handle"] for x in d["currentAppInstallation"]["accessScopes"]}
                # write_* implica il read_* corrispondente
                ha |= {h.replace("write_", "read_") for h in ha if h.startswith("write_")}
                out[store] = {"mancano": [p for p in self.PERMESSI if p not in ha], "errore": None}
            except ErroreSorgente as e:
                out[store] = {"mancano": [], "errore": str(e)}
        return out

    def leggi_varianti(self, store: str, location_id: str, avanzamento=None) -> list:
        out, after = [], None
        while True:
            d = self.client[store].query(Q_CATALOGO, {"after": after, "loc": location_id})["productVariants"]
            for v in d["nodes"]:
                ii = v["inventoryItem"]
                q = _quantita(ii.get("inventoryLevel"))
                out.append({"variant_id": v["id"], "product_id": v["product"]["id"], "item_id": ii["id"],
                            "prodotto": v["product"]["title"], "variante": "" if v["title"] == "Default Title" else v["title"],
                            "sku": (v["sku"] or "").strip(), "ean": (v["barcode"] or "").strip(), "immagine": _immagine(v),
                            "stato": v["product"]["status"], "tracciata": 1 if ii["tracked"] else 0,
                            "in_sede": 1 if q is not None else 0, "disponibile": (q or {}).get("available"),
                            "giacenza": (q or {}).get("on_hand"), "impegnata": (q or {}).get("committed")})
            if avanzamento:
                avanzamento(len(out))
            if not d["pageInfo"]["hasNextPage"]:
                return out
            after = d["pageInfo"]["endCursor"]

    def leggi_venduto(self, store: str, dal_iso: str) -> list:
        """Righe d'ordine degli ordini aggiornati da dal_iso (Shopify ne restituisce al massimo 60 giorni)."""
        out, after = [], None
        q = f"updated_at:>='{dal_iso}'"
        while True:
            d = self.client[store].query(Q_VENDUTO, {"after": after, "q": q})["orders"]
            for o in d["nodes"]:
                out += consumo_ordine(o)
            if not d["pageInfo"]["hasNextPage"]:
                return out
            after = d["pageInfo"]["endCursor"]

    def leggi_trasferimenti(self, store: str, dal_iso: str) -> list:
        """Trasferimenti creati da dal_iso in poi (dal piu' recente), con le righe."""
        out, after = [], None
        while True:
            d = self.client[store].query(Q_TRASFERIMENTI, {"after": after})["inventoryTransfers"]
            for t in d["nodes"]:
                if t["dateCreated"] < dal_iso:
                    return out
                righe, pagina = t["lineItems"]["nodes"], t["lineItems"]["pageInfo"]
                while pagina["hasNextPage"]:
                    x = self.client[store].query(Q_RIGHE_TRASFERIMENTO, {"id": t["id"], "after": pagina["endCursor"]})
                    righe += x["inventoryTransfer"]["lineItems"]["nodes"]
                    pagina = x["inventoryTransfer"]["lineItems"]["pageInfo"]
                righe = [_riga_trasferimento(r) for r in righe]
                if t["origin"] is None and t["destination"]:
                    # carico senza origine (da fornitore): conta quello che Shopify ha accettato, spedizione per spedizione
                    accettati = {}
                    for sp in self.client[store].query(Q_RICEVUTI, {"id": t["id"]})["inventoryTransfer"]["shipments"]["nodes"]:
                        for x in sp["lineItems"]["nodes"]:
                            iid = (x.get("inventoryItem") or {}).get("id")
                            accettati[iid] = accettati.get(iid, 0) + x["acceptedQuantity"]
                    for r in righe:
                        r["ricevuta"] = accettati.pop(r["item_id"], 0)
                out.append({"transfer_id": t["id"], "nome": t["name"], "stato": t["status"], "creato_il": t["dateCreated"],
                            "nota": t.get("note") or "", "origine_location": ((t["origin"] or {}).get("location") or {}).get("id"),
                            "origine_nome": (t["origin"] or {}).get("name"),
                            "destinazione": (t["destination"] or {}).get("name") if t["destination"] else None,
                            "destinazione_location": ((t["destination"] or {}).get("location") or {}).get("id"),
                            "righe": righe})
            if not d["pageInfo"]["hasNextPage"]:
                return out
            after = d["pageInfo"]["endCursor"]

    def leggi_livelli(self, store: str, location_id: str, item_ids: list) -> dict:
        out = {}
        for i in range(0, len(item_ids), 50):
            d = self.client[store].query(Q_LIVELLI, {"ids": item_ids[i:i + 50], "loc": location_id})
            for n in d["nodes"]:
                if n:
                    q = _quantita(n.get("inventoryLevel"))
                    out[n["id"]] = None if q is None else {"disponibile": q.get("available"), "giacenza": q.get("on_hand")}
        return out

    def aggiusta(self, store, location_id, item_id, delta, change_from, motivo, documento, chiave) -> int:
        """Movimento relativo sul 'disponibile' (sposta anche l'on hand). Ritorna il disponibile dopo il movimento."""
        d = self.client[store].mutazione(M_MOVIMENTO, {"chiave": chiave, "input": {
            "name": "available", "reason": motivo, "referenceDocumentUri": documento,
            "changes": [{"inventoryItemId": item_id, "locationId": location_id, "delta": delta, "changeFromQuantity": change_from}]}})
        r = d["inventoryAdjustQuantities"]
        errori = r.get("userErrors") or []
        if any(e.get("code") == "CHANGE_FROM_QUANTITY_STALE" for e in errori):
            raise Conflitto("la giacenza è cambiata nel frattempo")
        if errori:
            raise Rifiutata("; ".join(f"{e.get('code') or ''} {e.get('message')}".strip() for e in errori)[:300])
        cambi = (r.get("inventoryAdjustmentGroup") or {}).get("changes") or []
        dopo = next((x["quantityAfterChange"] for x in cambi if x["name"] == "available"), None)
        return dopo if dopo is not None else change_from + delta
