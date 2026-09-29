"""Il file .env: i numeri sbagliati fermano l'avvio con un messaggio chiaro."""
import pytest

from magazzino import config


@pytest.mark.parametrize("riga, errore", [("SYNC_MINUTI=0", "SYNC_MINUTI"), ("SYNC_MINUTI=-5", "SYNC_MINUTI"),
                                          ("SYNC_MINUTI=abc", "SYNC_MINUTI"), ("PORTA=70000", "PORTA"), ("PORTA=otto", "PORTA")])
def test_numeri_non_validi_nel_env(tmp_path, riga, errore):
    # con SYNC_MINUTI=0 la lettura dei negozi ripartirebbe subito, di continuo
    env = tmp_path / ".env"
    env.write_text(f"MODALITA=demo\n{riga}\n", encoding="utf-8")
    with pytest.raises(SystemExit, match=errore):
        config.carica(env_file=env)


def test_numeri_validi_nel_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text("MODALITA=demo\nSYNC_MINUTI=5\nPORTA=8080\n", encoding="utf-8")
    c = config.carica(env_file=env)
    assert (c.sync_minuti, c.porta) == (5, 8080)


def _negozi(tmp_path, modifica):
    import json
    from pathlib import Path
    d = json.loads((Path(__file__).resolve().parent.parent / "negozi.example.json").read_text(encoding="utf-8"))
    modifica(d)
    f = tmp_path / "negozi.json"
    f.write_text(json.dumps(d) if isinstance(d, dict) else d, encoding="utf-8")
    return f


def test_negozi_json_scritto_male_da_un_messaggio_chiaro(tmp_path):
    from magazzino.negozi import da_file
    def senza_domini(d):
        del d["negozi"][0]["canali"][0]["domini"]

    def store_doppio(d):
        d["negozi"][1]["canali"][0]["store"] = d["negozi"][0]["canali"][0]["store"]

    def stesso_id(d):
        d["negozi"][1]["id"] = d["negozi"][0]["id"]

    for modifica, attesa in ((senza_domini, "manca il campo 'domini'"), (store_doppio, "compare due volte"),
                             (stesso_id, "stesso id")):
        with pytest.raises(ValueError, match=attesa):
            da_file(_negozi(tmp_path, modifica))
    rotto = tmp_path / "rotto.json"
    rotto.write_text('{"negozi": [ {"id": "a",, } ]}', encoding="utf-8")
    with pytest.raises(ValueError, match=r"rotto.json non è scritto bene \(riga 1"):
        da_file(rotto)


def test_negozi_json_d_esempio_valido(tmp_path):
    from magazzino.negozi import da_file
    topo = da_file(_negozi(tmp_path, lambda d: None))
    assert len(topo.negozi) == 2 and all(isinstance(v, tuple) for v in topo.domini.values())
