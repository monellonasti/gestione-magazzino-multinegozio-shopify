"""
Regole di stock: reintegro, bilanciamento e coperture del venduto. Funzioni pure (niente database, niente Shopify),
usate identiche in modalità reale e in demo. Le regole spiegate a parole sono in docs/REGOLE.md.

Nomi usati:
  disp        disponibile secondo Shopify (on hand meno impegnato) nella sede principale del negozio
  arrivo      pezzi già in viaggio o pianificati verso il negozio (bozze, approvati, spediti non ancora ricevuti)
  uscita      pezzi già promessi dal negozio ad altri trasferimenti non ancora spediti (bozze e approvati)
  minima      scorta minima: sotto questa soglia si interviene, e l'origine non ci scende mai
  obiettivo   scorta obiettivo: quando si interviene si riporta il negozio fin qui
"""
from dataclasses import dataclass, field

STATI_APERTI = ("bozza", "approvato", "in_preparazione", "spedito")


@dataclass
class Soglia:
    minima: int
    obiettivo: int


def soglia(soglie: dict, negozio: str, chiave: str) -> Soglia:
    """soglie: {(negozio, chiave o '*'): Soglia}. La soglia dell'articolo vince su quella del negozio."""
    return soglie.get((negozio, chiave)) or soglie.get((negozio, "*")) or Soglia(1, 2)


@dataclass
class Proposta:
    chiave: str
    quantita: int
    fabbisogno: int
    utilizzabile: int
    vincoli: list = field(default_factory=list)      # perché la quantità è quella (testo per il responsabile)
    note: list = field(default_factory=list)         # note operative (per tutti: niente vendite né prezzi)


def utilizzabile_origine(disp, uscita: int, minima: int) -> int:
    """Pezzi che l'origine può cedere senza scendere sotto la sua scorta minima."""
    if disp is None:
        return 0
    return max(0, disp - uscita - minima)


def reintegro(modo: str, chiave: str, *, disp_dest, disp_orig, arrivo_dest: int, uscita_orig: int,
              soglia_dest: Soglia, soglia_orig: Soglia, venduto_scoperto: int = 0, nome_origine: str = "") -> Proposta:
    """
    modo "venduto": rimandare a destinazione le unità consumate nel periodo e non ancora coperte da altri reintegri.
                    I pezzi già in arrivo per altri motivi (arrivo_dest) si scalano.
    modo "scorta":  se destinazione + in arrivo è sotto la minima, riportarla all'obiettivo.
    Le due cose NON si sommano: sono due domande diverse e si sceglie quale fare.
    """
    vincoli, note = [], []
    if modo == "venduto":
        fabbisogno = max(0, venduto_scoperto - arrivo_dest)
        if arrivo_dest and venduto_scoperto:
            vincoli.append(f"{min(arrivo_dest, venduto_scoperto)} già in arrivo da altri trasferimenti")
    elif modo == "scorta":
        proiezione = max(0, disp_dest or 0) + arrivo_dest     # una giacenza negativa è un'anomalia, non fabbisogno
        fabbisogno = max(0, soglia_dest.obiettivo - proiezione) if proiezione < soglia_dest.minima else 0
        if arrivo_dest:
            vincoli.append(f"{arrivo_dest} già in arrivo")
    else:
        raise ValueError(modo)

    utile = utilizzabile_origine(disp_orig, uscita_orig, soglia_orig.minima)
    q = min(fabbisogno, utile)
    if fabbisogno and disp_orig is None:
        vincoli.append("articolo non presente all'origine"); note.append("Non presente nel negozio di origine")
    elif fabbisogno and q < fabbisogno:
        motivo = f"all'origine restano {utile} pezzi utilizzabili (scorta minima {soglia_orig.minima}"
        motivo += f", {uscita_orig} già promessi ad altri trasferimenti)" if uscita_orig else ")"
        vincoli.append(motivo)
        dove = f"a {nome_origine}" if nome_origine else "all'origine"
        note.append(f"Meno del necessario: {dove} non ce n'è abbastanza" if q else f"{dove[0].upper() + dove[1:]} non ce n'è abbastanza")
    return Proposta(chiave, q, fabbisogno, utile, vincoli, note)


def bilanciamento(chiave: str, a: str, b: str, *, disp: dict, arrivo: dict, uscita: dict, soglie: dict,
                  priorita: str | None = None, aperti_verso: set = frozenset(), nomi: dict | None = None) -> tuple | None:
    """
    Una sola proposta per articolo, in una sola direzione (niente movimenti opposti nello stesso piano).
    Scatta quando la destinazione (con quello che le sta già arrivando) è sotto la sua minima. La quantità è la
    più piccola fra:
      - quanto serve per arrivare all'obiettivo della destinazione;
      - quanto l'origine può cedere restando sopra la sua minima;
      - metà della differenza fra i due negozi, così l'origine non finisce sotto la destinazione (se la differenza
        è dispari il pezzo in più va al negozio con priorità: la regola che il cliente usava già: "la parte maggiore al negozio principale").
    aperti_verso: direzioni (origine, destinazione) con un trasferimento aperto per questo articolo: la direzione
    opposta non si propone.
    Solo giacenze: non prevede la domanda. Ritorna (origine, destinazione, Proposta) oppure None.
    """
    for o, d in ((a, b), (b, a)):
        if disp.get(o) is None or disp.get(d) is None:
            continue
        if (d, o) in aperti_verso:
            continue
        sd, so = soglie[d], soglie[o]
        proiezione_d = max(0, disp[d]) + arrivo.get(d, 0)
        if proiezione_d >= sd.minima:
            continue
        origine_netta = disp[o] - uscita.get(o, 0)
        fabbisogno = sd.obiettivo - proiezione_d
        cedibile = max(0, origine_netta - so.minima)
        diff = origine_netta - proiezione_d
        equo = max(0, diff // 2 + (1 if diff % 2 and priorita == d else 0))
        q = min(fabbisogno, cedibile, equo)
        if q <= 0:
            continue
        vincoli = []
        if q < fabbisogno:
            if q == cedibile:
                vincoli.append(f"l'origine non scende sotto la sua minima ({so.minima})")
            if q == equo:
                vincoli.append("l'origine non scende sotto la destinazione")
        motivo = (f"{(nomi or {}).get(d, d)} ha {disp[d]}" + (f" (+{arrivo[d]} in arrivo)" if arrivo.get(d) else "") +
                  f" (minima {sd.minima}): si porta verso {sd.obiettivo}")
        return o, d, Proposta(chiave, q, fabbisogno, cedibile, [motivo] + vincoli, [])
    return None


def effettiva(stato: str, riga: dict) -> int:
    """Quanti pezzi di una riga di trasferimento contano davvero, secondo lo stato del trasferimento."""
    if stato == "annullato":
        return 0
    if stato == "bozza":
        return riga["proposta"]
    if stato in ("approvato", "in_preparazione"):
        return riga["approvata"] or 0
    if stato == "spedito":
        return riga["spedita"] or 0
    # ricevuto: conta la merce arrivata buona, più quella dichiarata arrivata dopo
    extra = (riga["spedita"] or 0) - riga["ricevuta"] - riga["danneggiata"] if riga.get("esito_differenza") == "arrivata_dopo" else 0
    return riga["ricevuta"] + extra


def coperture_effettive(coperture: list, efficace: int) -> dict:
    """
    coperture: righe (riga_ordine, pezzi, data) assegnate a una riga di trasferimento, in ordine qualsiasi.
    Se la riga è stata ridotta (approvata o spedita meno del proposto) le unità coperte si tolgono partendo dalle
    vendite più recenti: le più vecchie restano coperte. Ritorna {riga_ordine: pezzi coperti}.
    """
    out, resto = {}, max(0, efficace)
    for c in sorted(coperture, key=lambda x: (x["data"], x["riga_ordine"])):
        q = min(c["pezzi"], resto)
        out[c["riga_ordine"]] = out.get(c["riga_ordine"], 0) + q
        resto -= q
    return out


def assegna_copertura(vendite_scoperte: list, quantita: int) -> list:
    """Assegna `quantita` alle vendite non ancora coperte, dalla più vecchia. vendite: (riga_ordine, store, data, scoperti)."""
    out, resto = [], quantita
    for v in sorted(vendite_scoperte, key=lambda x: (x["data"], x["riga_ordine"])):
        if resto <= 0:
            break
        q = min(v["scoperti"], resto)
        if q > 0:
            out.append({**v, "pezzi": q})
            resto -= q
    return out


FRAZIONI = {"1/2": (1, 2), "1/3": (1, 3), "2/3": (2, 3), "1/4": (1, 4), "3/4": (3, 4)}
REGOLE_DISPARI = {"a": "il pezzo in più resta al negozio di arrivo", "b": "il pezzo in più va all'altro negozio",
                  "alterna": "una volta ciascuno, riga dopo riga (si comincia dal negozio di arrivo)"}


def ripartisci(quantita: list, num: int, den: int, dispari: str) -> list:
    """
    Carico da fornitore diviso fra due negozi: quota dell'ALTRO negozio per ogni riga (num/den della riga).
    Quando la divisione non è esatta decide `dispari` (vedi REGOLE_DISPARI). Il negozio di arrivo tiene il resto.
      ripartisci([5, 4, 3], 1, 2, "a")       -> [2, 2, 1]
      ripartisci([5, 4, 3], 1, 2, "b")       -> [3, 2, 2]
      ripartisci([5, 4, 3], 1, 2, "alterna") -> [2, 2, 2]
    """
    if dispari not in REGOLE_DISPARI or not 0 < num <= den:
        raise ValueError("regola di divisione non valida")
    out, tocca_all_altro = [], False
    for q in quantita:
        quota, resto = divmod(q * num, den)
        if resto:
            if dispari == "b" or (dispari == "alterna" and tocca_all_altro):
                quota += 1
            if dispari == "alterna":
                tocca_all_altro = not tocca_all_altro
        out.append(quota)
    return out
