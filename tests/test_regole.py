"""Regole pure: scorte minime, reintegro nelle due modalita', bilanciamento, coperture."""
from magazzino import regole
from magazzino.regole import Soglia

S = Soglia(1, 2)


def rein(modo, **kw):
    base = dict(disp_dest=0, disp_orig=10, arrivo_dest=0, uscita_orig=0, soglia_dest=S, soglia_orig=S, venduto_scoperto=0)
    base.update(kw)
    return regole.reintegro(modo, "k", **base)


def test_origine_non_scende_sotto_la_minima():
    assert regole.utilizzabile_origine(5, 0, 2) == 3
    assert regole.utilizzabile_origine(5, 3, 2) == 0
    assert regole.utilizzabile_origine(1, 0, 2) == 0
    assert regole.utilizzabile_origine(None, 0, 0) == 0
    p = rein("venduto", venduto_scoperto=10, disp_orig=4, soglia_orig=Soglia(2, 3))
    assert p.quantita == 2 and p.fabbisogno == 10
    assert any("scorta minima 2" in x for x in p.vincoli)


def test_venduto_sostituisce_le_unita_consumate():
    p = rein("venduto", venduto_scoperto=3, disp_dest=50)             # anche con tanta merce: rimanda il venduto
    assert p.quantita == 3


def test_venduto_scala_quello_che_sta_gia_arrivando():
    assert rein("venduto", venduto_scoperto=5, arrivo_dest=2).quantita == 3
    assert rein("venduto", venduto_scoperto=2, arrivo_dest=5).quantita == 0


def test_scorta_scatta_solo_sotto_la_minima_e_riporta_all_obiettivo():
    assert rein("scorta", disp_dest=1, soglia_dest=Soglia(1, 4)).quantita == 0    # alla minima: niente
    assert rein("scorta", disp_dest=0, soglia_dest=Soglia(1, 4)).quantita == 4
    assert rein("scorta", disp_dest=0, arrivo_dest=1, soglia_dest=Soglia(2, 4)).quantita == 3
    assert rein("scorta", disp_dest=-3, soglia_dest=Soglia(1, 2)).quantita == 2   # il negativo non gonfia il fabbisogno


def test_le_due_modalita_non_si_sommano():
    # 3 venduti e un deficit di 4 rispetto all'obiettivo: nessuna modalita' propone 7
    kw = dict(disp_dest=0, venduto_scoperto=3, soglia_dest=Soglia(1, 4))
    assert rein("venduto", **kw).quantita == 3
    assert rein("scorta", **kw).quantita == 4


def test_origine_assente():
    p = rein("scorta", disp_orig=None)
    assert p.quantita == 0 and "non presente" in p.vincoli[0]


def bil(disp, arrivo=None, uscita=None, soglie=None, priorita=None, aperti=frozenset()):
    return regole.bilanciamento("k", "A", "B", disp=disp, arrivo=arrivo or {}, uscita=uscita or {},
                                soglie=soglie or {"A": Soglia(1, 2), "B": Soglia(1, 2)}, priorita=priorita, aperti_verso=aperti)


def test_bilanciamento_regola_storica():
    # la regola che il cliente usava gia': "si divide a meta', se dispari la parte maggiore al negozio con priorita'" (A)
    alti = {"A": Soglia(1, 99), "B": Soglia(1, 99)}
    assert bil({"A": 3, "B": 0}, soglie=alti, priorita="A")[2].quantita == 1        # 3/0 -> 2/1
    o, d, p = bil({"A": 0, "B": 3}, soglie=alti, priorita="A")
    assert (o, d, p.quantita) == ("B", "A", 2)                                        # 0/3 -> 2/1
    assert bil({"A": 2, "B": 0}, soglie=alti, priorita="A")[2].quantita == 1        # 2/0 -> 1/1
    assert bil({"A": 1, "B": 0}, soglie=alti, priorita="A") is None                  # 1/0: niente


def test_bilanciamento_non_porta_origine_sotto_minima_ne_sotto_destinazione():
    o, d, p = bil({"A": 10, "B": 0}, soglie={"A": Soglia(8, 9), "B": Soglia(1, 5)})
    assert p.quantita == 2 and 10 - p.quantita >= 8
    o, d, p = bil({"A": 4, "B": 0}, soglie={"A": Soglia(0, 0), "B": Soglia(1, 10)})
    assert p.quantita == 2                                                             # 2/2, non 0/4


def test_bilanciamento_una_sola_direzione_e_niente_opposti():
    r = bil({"A": 0, "B": 5})
    assert r[0] == "B" and r[1] == "A"
    assert bil({"A": 0, "B": 5}, aperti={("A", "B")}) is None      # c'e' gia' un A->B aperto: niente B->A
    assert bil({"A": 3, "B": 3}) is None                            # quantita' diverse o uguali non sono errori


def test_bilanciamento_conta_arrivi_e_uscite():
    assert bil({"A": 5, "B": 0}, arrivo={"B": 2}) is None
    assert bil({"A": 2, "B": 0}, uscita={"A": 1}) is None


def test_coperture_ridotte_dalle_vendite_piu_recenti():
    cop = [{"riga_ordine": "r1", "pezzi": 2, "data": "2026-09-01T10:00:00Z"},
           {"riga_ordine": "r2", "pezzi": 3, "data": "2026-09-05T10:00:00Z"}]
    assert regole.coperture_effettive(cop, 5) == {"r1": 2, "r2": 3}
    assert regole.coperture_effettive(cop, 3) == {"r1": 2, "r2": 1}
    assert regole.coperture_effettive(cop, 0) == {"r1": 0, "r2": 0}


def test_assegna_copertura_dalla_vendita_piu_vecchia():
    vendite = [{"riga_ordine": "b", "store": "s", "data": "2026-09-05", "scoperti": 2},
               {"riga_ordine": "a", "store": "s", "data": "2026-09-01", "scoperti": 1}]
    out = regole.assegna_copertura(vendite, 2)
    assert [(x["riga_ordine"], x["pezzi"]) for x in out] == [("a", 1), ("b", 1)]


def test_quantita_effettiva_per_stato():
    riga = {"proposta": 5, "approvata": 4, "spedita": 3, "ricevuta": 2, "danneggiata": 0, "esito_differenza": None}
    assert [regole.effettiva(s, riga) for s in ("bozza", "approvato", "in_preparazione", "spedito", "ricevuto", "annullato")] == [5, 4, 4, 3, 2, 0]
    assert regole.effettiva("ricevuto", {**riga, "esito_differenza": "arrivata_dopo"}) == 3


def test_ripartisci_meta_con_le_tre_regole_del_dispari():
    assert regole.ripartisci([5, 4, 3], 1, 2, "a") == [2, 2, 1]          # il pezzo in più resta al negozio di arrivo
    assert regole.ripartisci([5, 4, 3], 1, 2, "b") == [3, 2, 2]          # il pezzo in più va all'altro
    assert regole.ripartisci([5, 4, 3, 1], 1, 2, "alterna") == [2, 2, 2, 0]   # una volta ciascuno, riga dopo riga
    assert regole.ripartisci([6, 4, 3, 5, 2], 1, 2, "alterna") == [3, 2, 1, 3, 1]


def test_ripartisci_altre_frazioni_e_limiti():
    assert regole.ripartisci([4, 3, 1], 1, 3, "a") == [1, 1, 0]
    assert regole.ripartisci([4, 3, 1], 1, 3, "b") == [2, 1, 1]
    assert regole.ripartisci([4, 8], 3, 4, "a") == [3, 6]
    assert regole.ripartisci([0, 7], 1, 2, "a") == [0, 3]
    for q, k in zip([7, 9, 1], regole.ripartisci([7, 9, 1], 1, 2, "b")):
        assert 0 <= k <= q                                                 # mai più di quello che è arrivato
    import pytest
    with pytest.raises(ValueError):
        regole.ripartisci([2], 1, 2, "a caso")
    with pytest.raises(ValueError):
        regole.ripartisci([2], 3, 2, "a")
