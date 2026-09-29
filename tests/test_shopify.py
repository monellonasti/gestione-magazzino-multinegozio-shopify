"""Client Shopify senza rete: come si classificano gli errori e come si leggono le pagine (nessuna chiamata vera)."""
import http.client, io, json, urllib.error

import pytest

from magazzino import shopify
from magazzino.negozi import ErroreSorgente, Incerta
from magazzino.shopify import Client, SorgenteShopify, _troncato


class _Risposta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _risposta(dati):
    return _Risposta(json.dumps(dati).encode())


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    env = tmp_path / "negozio.env"
    env.write_text("SHOPIFY_SHOP=negozio-prova\nSHOPIFY_CLIENT_ID=id\nSHOPIFY_CLIENT_SECRET=segreto\n", encoding="utf-8")
    monkeypatch.setattr(shopify.time, "sleep", lambda s: None)
    cl = Client("Negozio di prova", env, ("negozio-prova.myshopify.com",))
    cl._tok, cl._scade = "vecchio", float("inf")
    return cl


def _rete(monkeypatch, risposte):
    """urlopen finto: restituisce (o solleva) le risposte in ordine; la richiesta del token dà sempre un token nuovo."""
    chiamate = []

    def urlopen(req, timeout=None):
        if req.full_url.endswith("/oauth/access_token"):
            return _risposta({"access_token": "nuovo", "expires_in": 86399})
        chiamate.append(req.headers.get("X-shopify-access-token"))
        r = risposte.pop(0)
        if isinstance(r, Exception):
            raise r
        return _risposta(r)
    monkeypatch.setattr(shopify.urllib.request, "urlopen", urlopen)
    return chiamate


def _http(codice):
    return urllib.error.HTTPError("https://negozio-prova.myshopify.com", codice, "errore", {}, io.BytesIO(b""))


def test_scrittura_con_risposta_troncata_e_incerta(cliente, monkeypatch):
    # la richiesta è partita, la risposta si è interrotta a metà: non si sa se Shopify l'ha applicata
    _rete(monkeypatch, [http.client.IncompleteRead(b"{")])
    with pytest.raises(Incerta):
        cliente.mutazione("mutation { x }", {})


def test_scrittura_rifiutata_con_un_4xx_non_e_una_risposta_elaborata(cliente, monkeypatch):
    # un 403 dice che QUESTA richiesta non è passata, non cosa è successo a un tentativo precedente
    _rete(monkeypatch, [_http(403)])
    with pytest.raises(ErroreSorgente) as e:
        cliente.mutazione("mutation { x }", {})
    assert type(e.value) is ErroreSorgente


def test_token_scaduto_si_rinnova_una_volta(cliente, monkeypatch):
    chiamate = _rete(monkeypatch, [_http(401), {"data": {"ok": True}}])
    assert cliente.query("{ ok }") == {"ok": True}
    assert chiamate == ["vecchio", "nuovo"]


def test_lettura_con_connessione_chiusa_si_ripete(cliente, monkeypatch):
    _rete(monkeypatch, [ConnectionResetError(), http.client.RemoteDisconnected("chiusa"), {"data": {"ok": 1}}])
    assert cliente.query("{ ok }") == {"ok": 1}


def test_carico_con_piu_spedizioni_e_piu_pagine_di_righe():
    """Un carico da fornitore grande: 2 spedizioni, la prima con righe su due pagine. Nessuna riga persa."""
    riga = lambda i, q: {"inventoryItem": {"id": f"item/{i}"}, "acceptedQuantity": q, "rejectedQuantity": 0}
    pagine = {
        ("Ricevuti", None): {"inventoryTransfer": {"shipments": {"pageInfo": {"hasNextPage": True, "endCursor": "s1"}, "nodes": [
            {"id": "sp/1", "status": "RECEIVED", "lineItems": {"pageInfo": {"hasNextPage": True, "endCursor": "r1"},
                                                               "nodes": [riga(1, 2), riga(2, 3)]}}]}}},
        ("RigheSpedizione", "r1"): {"inventoryShipment": {"lineItems": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                                                        "nodes": [riga(3, 4)]}}},
        ("Ricevuti", "s1"): {"inventoryTransfer": {"shipments": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [
            {"id": "sp/2", "status": "RECEIVED", "lineItems": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                                               "nodes": [riga(1, 5)]}}]}}},
    }

    class Finto:
        nome = "Negozio di prova"

        def query(self, q, variabili=None):
            return pagine[(q.split("(")[0].split()[-1], (variabili or {}).get("after"))]

    s = SorgenteShopify.__new__(SorgenteShopify)
    s.client = {"negozio": Finto()}
    assert s._accettati("negozio", "gid://shopify/InventoryTransfer/1") == {"item/1": 7, "item/2": 3, "item/3": 4}


def test_ordine_troncato_si_riconosce():
    pieno = {"lineItems": {"pageInfo": {"hasNextPage": False}, "nodes": []},
             "fulfillmentOrders": {"pageInfo": {"hasNextPage": False}, "nodes": []}, "refunds": []}
    assert not _troncato(pieno, rimborsi=5)
    assert _troncato({**pieno, "lineItems": {"pageInfo": {"hasNextPage": True}, "nodes": []}}, rimborsi=5)
    assert _troncato({**pieno, "refunds": [{"refundLineItems": {"pageInfo": {"hasNextPage": False}, "nodes": []}}] * 5}, rimborsi=5)
