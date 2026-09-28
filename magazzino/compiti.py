"""
Cosa c'è da fare, per ruolo. La stessa logica dà le sezioni della pagina «Da fare» e il numero sulla voce di menu,
così i due numeri coincidono sempre.

  magazziniere  da preparare · da ricevere · movimenti fatti su Shopify da sistemare
  responsabile  da approvare · aggiornamenti Shopify da controllare · differenze da chiudere · movimenti Shopify
"""
from . import calcoli

TIPI_SHOPIFY = ("trasferimento_senza_destinazione", "carico_fornitore")
OP_PROBLEMI = ("conflitto", "errore", "incerta", "da_verificare")

_SQL_TRASF = """SELECT t.*, COUNT(r.id) AS n_righe,
      COALESCE(SUM(CASE WHEN t.stato='bozza' THEN r.proposta ELSE r.approvata END), 0) AS pezzi,
      COALESCE(SUM(r.prelevata), 0) AS prelevati, COALESCE(SUM(r.spedita), 0) AS spediti,
      COALESCE(SUM(r.ricevuta + r.danneggiata), 0) AS arrivati
    FROM trasferimenti t LEFT JOIN righe r ON r.trasferimento_id=t.id
    WHERE t.stato IN ({}) GROUP BY t.id ORDER BY t.id"""

_SQL_DIFFERENZE = """SELECT t.id, t.codice, t.origine, t.destinazione, COUNT(r.id) AS n_righe,
      SUM(COALESCE(r.spedita,0) - r.ricevuta - r.danneggiata) AS mancanti, SUM(r.danneggiata) AS danneggiati
    FROM trasferimenti t JOIN righe r ON r.trasferimento_id=t.id
    WHERE t.stato='ricevuto' AND r.esito_differenza IS NULL
      AND (COALESCE(r.spedita,0) - r.ricevuta - r.danneggiata > 0 OR r.danneggiata > 0)
    GROUP BY t.id ORDER BY t.id"""


def _trasferimenti(c, *stati):
    return [dict(r) for r in c.execute(_SQL_TRASF.format(",".join("?" * len(stati))), stati)]


def _shopify(c):
    n = {r["verso"]: r["n"] for r in c.execute(
        "SELECT verso, COUNT(*) n FROM trasferimenti_shopify WHERE esito='da_destinare' GROUP BY verso")}
    return {"uscite": n.get("uscita", 0), "carichi": n.get("ingresso", 0), "totale": sum(n.values())}


def anomalie(c) -> dict:
    """Anomalie aperte senza i movimenti Shopify (che sono compiti, non problemi di dati)."""
    segna = ",".join("?" * len(TIPI_SHOPIFY))
    per_tipo = [(r["tipo"], r["n"]) for r in c.execute(
        f"""SELECT tipo, COUNT(*) n FROM anomalie WHERE stato='aperta' AND tipo NOT IN ({segna})
            GROUP BY tipo ORDER BY MAX(gravita) DESC, n DESC""", TIPI_SHOPIFY)]
    gravi = c.execute(f"SELECT COUNT(*) FROM anomalie WHERE stato='aperta' AND gravita>=3 AND tipo NOT IN ({segna})",
                      TIPI_SHOPIFY).fetchone()[0]
    return {"per_tipo": per_tipo, "gravi": gravi, "totale": sum(n for _, n in per_tipo)}


def da_fare(c, topo, ruolo: str) -> dict:
    out = {"shopify": _shopify(c), "sotto_scorta": calcoli.sotto_scorta(c, topo)}
    if ruolo == "magazziniere":
        out["da_preparare"] = _trasferimenti(c, "approvato", "in_preparazione")
        out["da_ricevere"] = _trasferimenti(c, "spedito")
        out["in_attesa"] = _trasferimenti(c, "bozza")
        out["totale"] = len(out["da_preparare"]) + len(out["da_ricevere"]) + out["shopify"]["totale"]
        return out
    out["da_approvare"] = _trasferimenti(c, "bozza")
    out["aggiornamenti"] = [dict(r) for r in c.execute(
        f"""SELECT o.*, t.codice FROM operazioni o LEFT JOIN trasferimenti t ON t.id=o.trasferimento_id
            WHERE o.stato IN ({','.join('?' * len(OP_PROBLEMI))}) ORDER BY o.id""", OP_PROBLEMI)]
    out["differenze"] = [dict(r) for r in c.execute(_SQL_DIFFERENZE)]
    out["anomalie"] = anomalie(c)
    out["in_corso"] = _trasferimenti(c, "approvato", "in_preparazione", "spedito")
    out["totale"] = len(out["da_approvare"]) + len(out["aggiornamenti"]) + len(out["differenze"]) + out["shopify"]["totale"]
    return out


def conteggi_menu(c, ruolo: str) -> dict:
    """Solo conteggi (una manciata di COUNT): gira a ogni pagina."""
    uno = lambda sql, *p: c.execute(sql, p).fetchone()[0]
    shopify = uno("SELECT COUNT(*) FROM trasferimenti_shopify WHERE esito='da_destinare'")
    if ruolo == "magazziniere":
        totale = uno("SELECT COUNT(*) FROM trasferimenti WHERE stato IN ('approvato','in_preparazione','spedito')") + shopify
        return {"da_fare": totale, "anomalie_gravi": 0, "shopify": shopify}
    totale = (uno("SELECT COUNT(*) FROM trasferimenti WHERE stato='bozza'")
              + uno(f"SELECT COUNT(*) FROM operazioni WHERE stato IN ({','.join('?' * len(OP_PROBLEMI))})", *OP_PROBLEMI)
              + uno(f"SELECT COUNT(*) FROM ({_SQL_DIFFERENZE})") + shopify)
    segna = ",".join("?" * len(TIPI_SHOPIFY))
    gravi = uno(f"SELECT COUNT(*) FROM anomalie WHERE stato='aperta' AND gravita>=3 AND tipo NOT IN ({segna})", *TIPI_SHOPIFY)
    return {"da_fare": totale, "anomalie_gravi": gravi, "shopify": shopify}
