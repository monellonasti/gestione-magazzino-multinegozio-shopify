"""
Accesso: utenti con password (scrypt), sessione in un cookie httponly, due ruoli.

  magazziniere  inventario, liste, proposte, preparazione / spedizione / ricezione, documenti
  responsabile  in più: approvazioni, soglie, associazioni, anomalie, rettifiche, registro, impostazioni

Ogni modulo della pagina porta un gettone usa-e-getta firmato con la sessione: blocca le richieste da altri siti
e il doppio clic (lo stesso gettone non esegue due volte la stessa azione).
"""
import hashlib, hmac, secrets, time
from datetime import datetime, timedelta, timezone

from .db import ora

DURATA_SESSIONE = timedelta(hours=12)
RUOLI = ("magazziniere", "responsabile")
_tentativi: dict = {}


def hash_password(pw: str) -> str:
    sale = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=sale, n=2 ** 14, r=8, p=1)
    return f"scrypt${sale.hex()}${h.hex()}"


def verifica_password(pw: str, memorizzato: str) -> bool:
    try:
        _, sale, h = memorizzato.split("$")
        prova = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(sale), n=2 ** 14, r=8, p=1)
        return hmac.compare_digest(prova.hex(), h)
    except ValueError:
        return False


def crea_utente(c, username: str, nome: str, ruolo: str, password: str):
    if ruolo not in RUOLI:
        raise ValueError(f"ruolo deve essere {' o '.join(RUOLI)}")
    if len(password) < 8:
        raise ValueError("la password deve avere almeno 8 caratteri")
    c.execute("INSERT INTO utenti(username, nome, ruolo, hash) VALUES(?,?,?,?) ON CONFLICT(username) DO UPDATE SET "
              "nome=excluded.nome, ruolo=excluded.ruolo, hash=excluded.hash, attivo=1",
              (username.strip().lower(), nome.strip(), ruolo, hash_password(password)))


def _hash_token(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def accedi(c, username: str, password: str) -> str | None:
    """Ritorna il token di sessione, oppure None. Dopo 5 errori in 10 minuti lo stesso utente aspetta."""
    username = (username or "").strip().lower()
    adesso = time.time()
    recenti = [t for t in _tentativi.get(username, []) if adesso - t < 600]
    if len(recenti) >= 5:
        return None
    u = c.execute("SELECT * FROM utenti WHERE username=? AND attivo=1", (username,)).fetchone()
    if not u or not verifica_password(password or "", u["hash"]):
        _tentativi[username] = recenti + [adesso]
        return None
    _tentativi.pop(username, None)
    token = secrets.token_urlsafe(32)
    scade = (datetime.now(timezone.utc) + DURATA_SESSIONE).isoformat(timespec="seconds")
    c.execute("DELETE FROM sessioni WHERE scade < ?", (ora(),))
    c.execute("INSERT INTO sessioni(token, utente_id, scade) VALUES(?,?,?)", (_hash_token(token), u["id"], scade))
    return token


def bloccato(username: str) -> bool:
    adesso = time.time()
    return len([t for t in _tentativi.get((username or "").strip().lower(), []) if adesso - t < 600]) >= 5


def utente_da_token(c, token: str | None):
    if not token:
        return None
    return c.execute("""SELECT u.* FROM sessioni s JOIN utenti u ON u.id=s.utente_id
                        WHERE s.token=? AND s.scade > ? AND u.attivo=1""", (_hash_token(token), ora())).fetchone()


def esci(c, token: str | None):
    if token:
        c.execute("DELETE FROM sessioni WHERE token=?", (_hash_token(token),))


def gettone(token_sessione: str) -> str:
    r = secrets.token_urlsafe(12)
    return f"{r}.{hmac.new(token_sessione.encode(), r.encode(), 'sha256').hexdigest()[:24]}"


def gettone_valido(token_sessione: str, g: str) -> bool:
    try:
        r, firma = g.split(".")
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(hmac.new(token_sessione.encode(), r.encode(), "sha256").hexdigest()[:24], firma)


def consuma_gettone(c, g: str) -> bool:
    """True la prima volta, False se il gettone è già stato usato (doppio clic, pagina ricaricata)."""
    try:
        c.execute("INSERT INTO moduli_usati(token, il) VALUES(?,?)", (g, ora()))
        return True
    except Exception:                                   # noqa: BLE001 - chiave duplicata
        return False
