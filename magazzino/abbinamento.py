"""
Abbinamento delle varianti fra negozi, per EAN.

Regole:
  - l'EAN è una STRINGA: si tolgono solo gli spazi ai bordi, gli zeri iniziali restano ("0012" != "12");
  - si abbina automaticamente solo un EAN presente UNA volta in ciascun negozio Shopify;
  - EAN mancante o doppio: la variante resta da sola e nasce un'anomalia; lo SKU serve solo come SUGGERIMENTO;
  - le associazioni manuali del responsabile vincono sull'automatico e sono registrate.

Funzioni pure: nessun accesso al database, così si provano nei test.
"""
from collections import defaultdict

from .negozi import Topologia


def norm_ean(v) -> str:
    return str(v or "").strip()


def considera(v: dict) -> bool:
    """Varianti che contano: tracciate e non archiviate (le archiviate solo se hanno ancora merce)."""
    if not v.get("tracciata"):
        return False
    return v.get("stato") != "ARCHIVED" or (v.get("giacenza") or 0) != 0


def abbina(varianti: list, manuali: dict, topo: Topologia) -> tuple[dict, list]:
    """
    varianti: dict con store, variant_id, ean, sku, prodotto, variante, immagine, disponibile, giacenza, ...
    manuali:  {(store, variant_id): chiave}
    Ritorna (articoli, anomalie). articoli: {chiave: {"canali": {store: variante}, "stato": ..., ...}}
    """
    vs = [v for v in varianti if considera(v)]
    per_ean = defaultdict(list)
    for v in vs:
        e = norm_ean(v.get("ean"))
        if e and (v["store"], v["variant_id"]) not in manuali:
            per_ean[(v["store"], e)].append(v)

    anomalie, gruppi, segni = [], defaultdict(dict), {}
    for v in vs:
        k = (v["store"], v["variant_id"])
        e = norm_ean(v.get("ean"))
        if k in manuali:
            chiave, segno = manuali[k], "manuale"
        elif not e:
            chiave, segno = f"V:{v['store']}:{v['variant_id']}", "mancante"
        elif len(per_ean[(v["store"], e)]) > 1:
            chiave, segno = f"V:{v['store']}:{v['variant_id']}", "doppio"
        else:
            chiave, segno = f"E:{e}", "ean"
        if v["store"] in gruppi[chiave]:
            # stesso negozio due volte nello stesso articolo: l'associazione manuale vince, l'altra resta da sola
            altra = gruppi[chiave][v["store"]]
            perdente = v if segno != "manuale" else altra
            if perdente is altra:
                gruppi[chiave][v["store"]] = v
                segni[(chiave, v["store"])] = segno
            chiave_sola = f"V:{perdente['store']}:{perdente['variant_id']}"
            gruppi[chiave_sola][perdente["store"]] = perdente
            segni[(chiave_sola, perdente["store"])] = "ambiguo"
            anomalie.append(_anomalia("associazione_ambigua", 3, chiave_sola, perdente,
                                      f"Associazione ambigua: {_nome(perdente)}",
                                      f"Su {topo.nomi_store[perdente['store']]} un'altra variante è già associata a questo "
                                      f"articolo. Questa resta separata finché non la associ a mano."))
            continue
        gruppi[chiave][v["store"]] = v
        segni[(chiave, v["store"])] = segno

    # EAN doppi: un'anomalia per codice e negozio
    for (store, e), lista in per_ean.items():
        if len(lista) > 1:
            nomi = "; ".join(f"{_nome(x)} (SKU {x.get('sku') or '-'})" for x in lista)
            anomalie.append({"dedup": f"ean_doppio:{store}:{e}", "tipo": "ean_doppio", "gravita": 3, "chiave": None,
                             "titolo": f"EAN {e} usato da {len(lista)} varianti su {topo.nomi_store[store]}",
                             "dettaglio": f"{nomi}. Finché il codice non è univoco queste varianti non si abbinano "
                                          f"all'altro negozio e non si trasferiscono."})

    articoli = {}
    per_sku = _indice_sku(gruppi, topo)
    for chiave, canali in gruppi.items():
        negozi_presenti = [n for n in topo.negozi if any(c.store in canali for c in n.canali)]
        manuale = any(segni.get((chiave, s)) == "manuale" for s in canali)
        v0 = _rappresentante(canali, topo)
        if manuale:
            stato = "manuale"
        elif chiave.startswith("V:"):
            segno = segni[(chiave, next(iter(canali)))]
            stato = {"mancante": "EAN mancante", "doppio": "EAN doppio", "ambiguo": "ambiguo"}[segno]
        elif len(negozi_presenti) == len(topo.negozi):
            stato = "abbinato"
        else:
            stato = "solo " + negozi_presenti[0].nome
        articoli[chiave] = {"chiave": chiave, "canali": canali, "stato": stato, "prodotto": v0["prodotto"],
                            "variante": v0.get("variante") or "", "sku": v0.get("sku") or "",
                            "ean": norm_ean(v0.get("ean")), "immagine": v0.get("immagine") or ""}

        giacenza_totale = sum(abs(v.get("giacenza") or 0) for v in canali.values())
        if stato == "EAN mancante" and giacenza_totale:
            suggerimento = _suggerisci(v0, chiave, per_sku, topo)
            anomalie.append(_anomalia("ean_mancante", 2, chiave, v0, f"EAN mancante: {_nome(v0)}",
                                      f"Su {topo.nomi_store[v0['store']]} (SKU {v0.get('sku') or '-'}) la variante non ha "
                                      f"codice a barre: non si abbina all'altro negozio." + suggerimento))
        if stato.startswith("solo ") and any((canali[c.store].get("disponibile") or 0) > 0
                                             for n in negozi_presenti for c in n.canali if c.store in canali):
            anomalie.append(_anomalia("solo_un_negozio", 1, chiave, v0, f"Presente in un solo negozio: {_nome(v0)}",
                                      f"Ha merce ma esiste solo in {negozi_presenti[0].nome}. Se è lo stesso articolo "
                                      f"di un altro con EAN diverso, associalo a mano."))
        for n in negozi_presenti:
            pr, sp = n.principale.store, [c.store for c in n.specchi]
            if pr in canali and (canali[pr].get("disponibile") or 0) < 0:
                anomalie.append(_anomalia("quantita_incoerente", 3, chiave, canali[pr],
                                          f"Disponibilità negativa a {n.nome}: {_nome(canali[pr])}",
                                          f"{topo.nomi_store[pr]} segna {canali[pr]['disponibile']} disponibili: "
                                          f"è stato venduto più di quanto risulti. Serve un conteggio."))
            for s in sp:
                if pr in canali and s in canali and canali[pr].get("in_sede") and canali[s].get("in_sede") \
                        and (canali[pr].get("giacenza") or 0) != (canali[s].get("giacenza") or 0):
                    anomalie.append(_anomalia("canali_non_allineati", 2, chiave, canali[pr],
                                              f"Canali non allineati a {n.nome}: {_nome(canali[pr])}",
                                              f"Stessa merce fisica, giacenze diverse: {topo.nomi_store[pr]} "
                                              f"{canali[pr].get('giacenza')}, {topo.nomi_store[s]} {canali[s].get('giacenza')}."))
                elif pr in canali and s not in canali and (canali[pr].get("giacenza") or 0) > 0:
                    anomalie.append(_anomalia("specchio_mancante", 2, chiave, canali[pr],
                                              f"Manca su {topo.nomi_store[s]}: {_nome(canali[pr])}",
                                              f"L'articolo ha merce a {n.nome} ma {topo.nomi_store[s]} non ha una variante "
                                              f"con lo stesso EAN: i movimenti non potranno aggiornarlo."))
    return articoli, anomalie


def _nome(v) -> str:
    return (v.get("prodotto") or "?") + (f" – {v['variante']}" if v.get("variante") else "")


def _anomalia(tipo, gravita, chiave, v, titolo, dettaglio):
    return {"dedup": f"{tipo}:{v['store']}:{v['variant_id']}", "tipo": tipo, "gravita": gravita,
            "chiave": chiave, "titolo": titolo, "dettaglio": dettaglio}


def _rappresentante(canali, topo):
    for n in topo.negozi:
        for c in n.canali:
            if c.store in canali:
                return canali[c.store]
    return next(iter(canali.values()))


def _indice_sku(gruppi, topo):
    idx = defaultdict(set)
    for chiave, canali in gruppi.items():
        for store, v in canali.items():
            if v.get("sku"):
                idx[(topo.negozio_di(store).id, v["sku"].strip().upper())].add(chiave)
    return idx


def _suggerisci(v, chiave, per_sku, topo) -> str:
    sku = (v.get("sku") or "").strip().upper()
    if not sku:
        return ""
    mio = topo.negozio_di(v["store"]).id
    for n in topo.negozi:
        if n.id != mio:
            cand = per_sku.get((n.id, sku), set()) - {chiave}
            if len(cand) == 1:
                return f" Suggerimento: a {n.nome} c'è un articolo con lo stesso SKU ({next(iter(cand))[2:]})."
    return ""
