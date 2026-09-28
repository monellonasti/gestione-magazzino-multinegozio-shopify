"""Abbinamento delle varianti per EAN: stringhe, zeri iniziali, doppioni, mancanti, associazioni manuali."""
from magazzino.abbinamento import abbina
from magazzino.negozi import DEMO


def v(store, vid, ean, disp=1, sku="", **kw):
    return {"store": store, "variant_id": vid, "ean": ean, "sku": sku, "prodotto": f"P{vid}", "variante": "",
            "tracciata": 1, "stato": "ACTIVE", "disponibile": disp, "giacenza": disp, "in_sede": 1, **kw}


def tipi(anomalie):
    return sorted(a["tipo"] for a in anomalie)


def test_zeri_iniziali_restano_e_non_si_confondono():
    varianti = [v("demo-centro", "a", "0012345678905"), v("demo-web", "a", "0012345678905"),
                v("demo-porto", "b", "12345678905")]          # stesso numero senza zeri: NON e' lo stesso codice
    articoli, _ = abbina(varianti, {}, DEMO)
    assert "E:0012345678905" in articoli
    assert articoli["E:0012345678905"]["ean"] == "0012345678905"
    assert set(articoli["E:0012345678905"]["canali"]) == {"demo-centro", "demo-web"}
    assert "E:12345678905" in articoli and set(articoli["E:12345678905"]["canali"]) == {"demo-porto"}


def test_abbinamento_univoco_fra_negozi():
    varianti = [v("demo-centro", "a", "8001"), v("demo-web", "a", "8001"), v("demo-porto", "b", " 8001 ")]
    articoli, anomalie = abbina(varianti, {}, DEMO)
    assert articoli["E:8001"]["stato"] == "abbinato"
    assert set(articoli["E:8001"]["canali"]) == {"demo-centro", "demo-web", "demo-porto"}
    assert anomalie == []


def test_ean_doppio_non_si_abbina_e_crea_anomalia():
    varianti = [v("demo-centro", "a", "8001"), v("demo-web", "a", "8001"),
                v("demo-porto", "b", "8001"), v("demo-porto", "c", "8001")]
    articoli, anomalie = abbina(varianti, {}, DEMO)
    assert set(articoli["E:8001"]["canali"]) == {"demo-centro", "demo-web"}     # il Porto non entra
    assert articoli["V:demo-porto:b"]["stato"] == "EAN doppio"
    assert articoli["V:demo-porto:c"]["stato"] == "EAN doppio"
    assert "ean_doppio" in tipi(anomalie)


def test_ean_mancante_anomalia_con_suggerimento_sku():
    varianti = [v("demo-centro", "a", "", sku="ABC-1"), v("demo-porto", "b", "8002", sku="abc-1")]
    articoli, anomalie = abbina(varianti, {}, DEMO)
    assert articoli["V:demo-centro:a"]["stato"] == "EAN mancante"
    mancante = next(a for a in anomalie if a["tipo"] == "ean_mancante")
    assert "Suggerimento" in mancante["dettaglio"]                     # lo SKU suggerisce, non abbina
    assert "E:8002" in articoli and "demo-centro" not in articoli["E:8002"]["canali"]


def test_associazione_manuale_vince():
    varianti = [v("demo-centro", "a", "", sku="X"), v("demo-web", "a", "", sku="X"), v("demo-porto", "b", "8003")]
    manuali = {("demo-centro", "a"): "E:8003", ("demo-web", "a"): "E:8003"}
    articoli, anomalie = abbina(varianti, manuali, DEMO)
    assert articoli["E:8003"]["stato"] == "manuale"
    assert set(articoli["E:8003"]["canali"]) == {"demo-centro", "demo-web", "demo-porto"}
    assert "ean_mancante" not in tipi(anomalie)


def test_manuale_in_conflitto_con_automatico_diventa_ambigua():
    varianti = [v("demo-porto", "b", "8004"), v("demo-porto", "c", "", sku="Y"), v("demo-centro", "a", "8004")]
    articoli, anomalie = abbina(varianti, {("demo-porto", "c"): "E:8004"}, DEMO)
    assert articoli["E:8004"]["canali"]["demo-porto"]["variant_id"] == "c"
    assert "V:demo-porto:b" in articoli
    assert "associazione_ambigua" in tipi(anomalie)


def test_canali_non_allineati_e_negativi():
    varianti = [v("demo-centro", "a", "8005", disp=5), v("demo-web", "a", "8005", disp=3), v("demo-porto", "b", "8005", disp=-1)]
    _, anomalie = abbina(varianti, {}, DEMO)
    assert "canali_non_allineati" in tipi(anomalie)
    assert "quantita_incoerente" in tipi(anomalie)


def test_solo_un_negozio_e_specchio_mancante():
    varianti = [v("demo-centro", "a", "8006", disp=4)]
    articoli, anomalie = abbina(varianti, {}, DEMO)
    assert articoli["E:8006"]["stato"] == "solo Negozio Centro"
    assert {"solo_un_negozio", "specchio_mancante"} <= set(tipi(anomalie))


def test_varianti_non_tracciate_ignorate():
    articoli, _ = abbina([v("demo-centro", "a", "8007", tracciata=0)], {}, DEMO)
    assert articoli == {}
