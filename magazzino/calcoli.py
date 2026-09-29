"""
Dal database agli ingressi delle regole: disponibilità, trasferimenti in corso, vendite non ancora coperte.
Poi i due piani: reintegro e bilanciamento.
"""
from collections import defaultdict
from datetime import date, timedelta

from . import regole
from .db import impostazione, inizio_giorno_utc
from .negozi import Topologia
from .regole import STATI_APERTI, Soglia


def soglie(c) -> dict:
    return {(r["negozio"], r["chiave"]): Soglia(r["minima"], r["obiettivo"]) for r in c.execute("SELECT * FROM soglie")}


def disponibilita(c) -> dict:
    out = defaultdict(dict)
    for r in c.execute("SELECT chiave, negozio, disponibile FROM articolo_negozi"):
        out[r["chiave"]][r["negozio"]] = r["disponibile"]
    return out


def ubicazioni(c, negozio: str) -> dict:
    return {r["chiave"]: r["ubicazione"] for r in c.execute("SELECT chiave, ubicazione FROM ubicazioni WHERE negozio=?", (negozio,))}


def in_corso(c) -> dict:
    """
    Per articolo: arrivo[negozio], uscita[negozio], arrivo_libero[negozio] (senza coperture di venduto),
    direzioni aperte {(origine, destinazione)} e pezzi in viaggio.
    """
    out = defaultdict(lambda: {"arrivo": defaultdict(int), "uscita": defaultdict(int), "arrivo_libero": defaultdict(int),
                               "direzioni": set(), "in_viaggio": 0, "prenotati": 0})
    righe = c.execute(f"""SELECT r.*, t.stato, t.origine, t.destinazione,
                          EXISTS(SELECT 1 FROM coperture k WHERE k.riga_id=r.id) AS coperta
                          FROM righe r JOIN trasferimenti t ON t.id=r.trasferimento_id
                          WHERE t.stato IN ({','.join('?' * len(STATI_APERTI))})""", STATI_APERTI)
    for r in righe:
        x = out[r["chiave"]]
        if r["stato"] == "spedito":
            viaggio = max(0, (r["spedita"] or 0) - r["ricevuta"] - r["danneggiata"])
            q_arrivo, q_uscita = viaggio, 0
            x["in_viaggio"] += viaggio
        else:
            q_arrivo = q_uscita = regole.effettiva(r["stato"], dict(r))
            if r["stato"] != "bozza":
                x["prenotati"] += q_uscita
        x["arrivo"][r["destinazione"]] += q_arrivo
        x["uscita"][r["origine"]] += q_uscita
        if not r["coperta"]:
            x["arrivo_libero"][r["destinazione"]] += q_arrivo
        if q_arrivo or q_uscita:
            x["direzioni"].add((r["origine"], r["destinazione"]))
    return out


def coperto_per_vendita(c) -> dict:
    """{(store, riga_ordine): pezzi già coperti da reintegri non annullati}."""
    per_riga = defaultdict(list)
    for r in c.execute("""SELECT k.*, r.proposta, r.approvata, r.spedita, r.ricevuta, r.danneggiata, r.esito_differenza, t.stato
                          FROM coperture k JOIN righe r ON r.id=k.riga_id JOIN trasferimenti t ON t.id=r.trasferimento_id
                          WHERE t.stato != 'annullato'"""):
        per_riga[r["riga_id"]].append(dict(r))
    out = defaultdict(int)
    for lista in per_riga.values():
        store_di = {k["riga_ordine"]: k["store"] for k in lista}
        for riga_ordine, q in regole.coperture_effettive(lista, regole.effettiva(lista[0]["stato"], lista[0])).items():
            out[(store_di[riga_ordine], riga_ordine)] += q
    return out


def vendite(c, topo: Topologia, negozio: str, dal: date, al: date) -> dict:
    """
    Vendite (consumo) del negozio fisico nel periodo [dal, al], giorni interi a Roma, su tutti i suoi canali e solo
    dalla sua sede. Ritorna {chiave: [ {store, riga_ordine, data, pezzi, scoperti} ]}.
    """
    stores = [ch.store for ch in topo.negozio(negozio).canali]
    coperte = coperto_per_vendita(c)
    out = defaultdict(list)
    righe = c.execute(f"""SELECT v.store, v.riga_ordine, v.data, v.pezzi, ac.chiave
                          FROM venduto v JOIN sedi s ON s.store=v.store AND s.location_id=v.location_id
                          JOIN articolo_canali ac ON ac.store=v.store AND ac.variant_id=v.variant_id
                          WHERE v.store IN ({','.join('?' * len(stores))}) AND v.data >= ? AND v.data < ? AND v.pezzi > 0""",
                      (*stores, inizio_giorno_utc(dal), inizio_giorno_utc(al + timedelta(days=1))))
    for r in righe:
        gia = coperte.get((r["store"], r["riga_ordine"]), 0)
        out[r["chiave"]].append({"store": r["store"], "riga_ordine": r["riga_ordine"], "data": r["data"],
                                 "pezzi": r["pezzi"], "scoperti": max(0, r["pezzi"] - gia)})
    return out


def _filtro_testo(testo: str) -> tuple[str, list]:
    testo = (testo or "").strip().lower()
    if not testo:
        return "", []
    return " AND (a.cerca LIKE ? OR a.ean = ?)", [f"%{testo}%", testo]


def articoli(c, testo: str = "", chiavi=None) -> dict:
    where, par = _filtro_testo(testo)
    if chiavi is not None:
        chiavi = list(chiavi)
        if not chiavi:
            return {}
        where += f" AND a.chiave IN ({','.join('?' * len(chiavi))})"
        par += chiavi
    return {r["chiave"]: dict(r) for r in c.execute(f"SELECT a.* FROM articoli a WHERE 1=1{where}", par)}


def piano_reintegro(c, topo: Topologia, modo: str, origine: str, destinazione: str, dal: date, al: date, testo: str = "") -> list:
    if origine == destinazione:
        raise ValueError("origine e destinazione devono essere diverse")
    s, disp, corso = soglie(c), disponibilita(c), in_corso(c)
    vend = vendite(c, topo, destinazione, dal, al) if modo == "venduto" else {}
    if modo == "venduto":
        arts = articoli(c, testo, vend.keys())
    else:
        arts = articoli(c, testo)
    ubi = ubicazioni(c, origine)
    out = []
    for chiave, a in arts.items():
        d = disp.get(chiave, {})
        if destinazione not in d and modo == "scorta":
            continue                                          # la scorta si calcola sugli articoli del negozio
        x = corso[chiave] if chiave in corso else None
        arrivo = (x["arrivo_libero"] if modo == "venduto" else x["arrivo"])[destinazione] if x else 0
        uscita = x["uscita"][origine] if x else 0
        vendite_art = vend.get(chiave, [])
        scoperti = sum(v["scoperti"] for v in vendite_art)
        p = regole.reintegro(modo, chiave, disp_dest=d.get(destinazione), disp_orig=d.get(origine), arrivo_dest=arrivo,
                             uscita_orig=uscita, soglia_dest=regole.soglia(s, destinazione, chiave),
                             soglia_orig=regole.soglia(s, origine, chiave), venduto_scoperto=scoperti,
                             nome_origine=next((n.nome for n in topo.negozi if n.id == origine), ""))
        if not p.fabbisogno:
            continue
        out.append({**a, "proposta": p, "quantita": p.quantita, "venduto": sum(v["pezzi"] for v in vendite_art),
                    "gia_coperto": sum(v["pezzi"] - v["scoperti"] for v in vendite_art), "vendite": vendite_art,
                    "disp_dest": d.get(destinazione), "disp_orig": d.get(origine), "arrivo": arrivo, "uscita": uscita,
                    "ubicazione": ubi.get(chiave, "")})
    out.sort(key=lambda r: (-r["quantita"], r["prodotto"] or "", r["variante"] or ""))
    return out


def piano_bilanciamento(c, topo: Topologia, testo: str = "") -> list:
    a_id, b_id = (n.id for n in topo.negozi)
    s, disp, corso = soglie(c), disponibilita(c), in_corso(c)
    priorita = impostazione(c, "priorita_pari")
    out = []
    for chiave, art in articoli(c, testo).items():
        d = disp.get(chiave, {})
        if a_id not in d or b_id not in d:
            continue
        x = corso.get(chiave)
        r = regole.bilanciamento(
            chiave, a_id, b_id, disp=d, arrivo=dict(x["arrivo"]) if x else {}, uscita=dict(x["uscita"]) if x else {},
            soglie={n: regole.soglia(s, n, chiave) for n in (a_id, b_id)}, priorita=priorita,
            aperti_verso=x["direzioni"] if x else set(), nomi={n.id: n.nome for n in topo.negozi})
        if r:
            o, dest, p = r
            out.append({**art, "origine": o, "destinazione": dest, "proposta": p, "quantita": p.quantita,
                        "prima_o": d[o], "prima_d": d[dest], "dopo_o": d[o] - p.quantita, "dopo_d": d[dest] + p.quantita,
                        "ubicazione": ubicazioni(c, o).get(chiave, "")})
    out.sort(key=lambda r: (r["origine"], r["prodotto"] or "", r["variante"] or ""))
    return out


def sotto_scorta(c, topo: Topologia) -> dict:
    """
    Articoli sotto la scorta minima in ciascun negozio, contando quello che sta già arrivando, che l'ALTRO negozio
    potrebbe coprire perché ne ha. Quelli senza merce altrove sono da riordinare al fornitore, non da spostare.
    """
    s, corso = soglie(c), in_corso(c)
    out = {n.id: 0 for n in topo.negozi}
    for r in c.execute("""SELECT n.chiave, n.negozio, n.disponibile FROM articolo_negozi n
                          WHERE EXISTS(SELECT 1 FROM articolo_negozi m WHERE m.chiave=n.chiave AND m.negozio != n.negozio
                                       AND m.disponibile > 0)"""):
        arrivo = corso[r["chiave"]]["arrivo"][r["negozio"]] if r["chiave"] in corso else 0
        if max(0, r["disponibile"] or 0) + arrivo < regole.soglia(s, r["negozio"], r["chiave"]).minima:
            out[r["negozio"]] += 1
    return out
