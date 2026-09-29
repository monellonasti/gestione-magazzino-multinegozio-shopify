"""Schema del database: gli indici che tengono veloci le pagine con i volumi di un anno."""


def test_elenco_trasferimenti_usa_gli_indici(vuoto):
    # senza indice, l'elenco dei trasferimenti contava le operazioni di ciascuno scorrendo tutta la tabella:
    # 11 secondi con 800 trasferimenti e 60.000 operazioni
    c, _, _ = vuoto
    piano = " ".join(r["detail"] for r in c.execute(
        "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM operazioni o WHERE o.trasferimento_id=? AND o.stato IN ('errore','incerta')", (1,)))
    assert "operazioni_trasferimento" in piano, piano
    piano = " ".join(r["detail"] for r in c.execute("EXPLAIN QUERY PLAN SELECT * FROM registro WHERE oggetto=?", ("TR-0001",)))
    assert "registro_oggetto" in piano, piano
