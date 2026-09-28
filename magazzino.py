"""
Magazzino: inventario, reintegri, bilanciamento e trasferimenti fra i due negozi.

  python magazzino.py                    avvia (modalità dal .env: demo o reale)
  python magazzino.py --demo             avvia la demo con dati inventati
  python magazzino.py utente NOME --ruolo responsabile|magazziniere [--nome "Nome Cognome"]
                                         crea o aggiorna un utente (la password si chiede a video)
  python magazzino.py sincronizza        legge i negozi una volta e stampa il riepilogo (solo lettura)
  python magazzino.py demo-ripristina    riporta la demo ai dati iniziali
"""
import argparse, getpass, socket, sys

sys.stdout.reconfigure(encoding="utf-8")


def ip_locale() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main():
    p = argparse.ArgumentParser(description="Magazzino fra i due negozi")
    p.add_argument("comando", nargs="?", default="avvia", choices=["avvia", "utente", "sincronizza", "demo-ripristina"])
    p.add_argument("nome_utente", nargs="?")
    p.add_argument("--demo", action="store_true", help="usa i negozi finti")
    p.add_argument("--ruolo", choices=["magazziniere", "responsabile"])
    p.add_argument("--nome", default="")
    p.add_argument("--porta", type=int)
    a = p.parse_args()

    from magazzino import config
    from magazzino.db import connetti
    cfg = config.carica("demo" if a.demo else None)
    if a.porta:
        cfg.porta = a.porta

    if a.comando == "utente":
        from magazzino import auth
        from magazzino.avvio import prepara
        if not (a.nome_utente and a.ruolo):
            sys.exit("uso: python magazzino.py utente NOME --ruolo responsabile|magazziniere")
        pw = getpass.getpass("Password (almeno 8 caratteri): ")
        if pw != getpass.getpass("Ripeti la password: "):
            sys.exit("Le password non coincidono")
        c = connetti(cfg.db_path)
        prepara(c, cfg)
        auth.crea_utente(c, a.nome_utente, a.nome or a.nome_utente, a.ruolo, pw)
        print(f"Utente {a.nome_utente} ({a.ruolo}) salvato in {cfg.db_path.name}")
        return

    if a.comando == "sincronizza":
        from magazzino.avvio import prepara, sorgente_per, topologia
        from magazzino.sync import sincronizza
        sorgente, errore = sorgente_per(cfg)
        if errore:
            sys.exit(errore)
        c = connetti(cfg.db_path)
        prepara(c, cfg)
        errori = sincronizza(c, sorgente, topologia(cfg), lambda t: print("  " + t, end="\r", flush=True))
        print()
        for r in c.execute("SELECT store, tipo, esito, righe, messaggio FROM sincronizzazioni ORDER BY id DESC LIMIT ?",
                           (2 * len(topologia(cfg).stores),)):
            print(f"  {r['store']:12} {r['tipo']:9} {r['esito']:7} {r['righe']:6}  {r['messaggio'] or ''}")
        print("articoli:", c.execute("SELECT COUNT(*) FROM articoli").fetchone()[0])
        for r in c.execute("SELECT stato, COUNT(*) n FROM articoli GROUP BY stato ORDER BY n DESC"):
            print(f"  {r['stato']:24} {r['n']}")
        print("anomalie aperte:")
        for r in c.execute("SELECT tipo, COUNT(*) n FROM anomalie WHERE stato='aperta' GROUP BY tipo ORDER BY n DESC"):
            print(f"  {r['tipo']:24} {r['n']}")
        sys.exit(1 if errori else 0)

    if a.comando == "demo-ripristina":
        from magazzino.avvio import ripristina_demo, sorgente_per
        cfg = config.carica("demo")
        ripristina_demo(cfg, sorgente_per(cfg)[0])
        print("Demo ripristinata")
        return

    import uvicorn
    from magazzino.web import crea_app
    app = crea_app(cfg)
    c = connetti(cfg.db_path)
    if not c.execute("SELECT 1 FROM utenti WHERE attivo=1 LIMIT 1").fetchone():
        print("Nessun utente: crea il primo responsabile con\n  python magazzino.py utente NOME --ruolo responsabile\n")
    c.close()
    print(f"Magazzino ({'DEMO' if cfg.demo else 'negozi veri'}; scritture Shopify {'attive' if cfg.scritture_attive else 'DISATTIVATE'})")
    print(f"  su questo PC:     http://127.0.0.1:{cfg.porta}")
    print(f"  dalla rete locale: http://{ip_locale()}:{cfg.porta}")
    uvicorn.run(app, host=cfg.host, port=cfg.porta, log_level="warning")


if __name__ == "__main__":
    main()
