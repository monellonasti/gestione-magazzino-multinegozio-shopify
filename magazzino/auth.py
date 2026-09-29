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
FINESTRA_TENTATIVI = 600               # secondi in cui si contano gli errori di accesso
MAX_PER_DISPOSITIVO = 5                # errori per utente dallo stesso dispositivo, poi quel dispositivo aspetta
MAX_PER_UTENTE = 20                    # errori per utente da tutti i dispositivi insieme, poi aspetta ovunque
_tentativi: dict = {}                  # (utente, indirizzo) -> orari degli errori
_hash_finto: str | None = None


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


def accedi(c, username: str, password: str, indirizzo: str = "") -> str | None:
    """
    Ritorna il token di sessione, oppure None. Dopo 5 errori in 10 minuti da un dispositivo, quell'utente aspetta su
    quel dispositivo; dopo 20 da tutti i dispositivi insieme aspetta ovunque. Così chi sbaglia apposta da un tablet
    non chiude fuori il responsabile dal suo PC, e chi prova password da più macchine viene comunque fermato.
    """
    username = (username or "").strip().lower()
    adesso = time.time()
    _pulisci(adesso)
    if _bloccato(username, indirizzo, adesso):
        return None
    u = c.execute("SELECT * FROM utenti WHERE username=? AND attivo=1", (username,)).fetchone()
    # con un nome che non esiste si fa lo stesso calcolo: il tempo di risposta non dice quali utenti ci sono
    ok = verifica_password(password or "", u["hash"] if u else _confronto_finto())
    if not u or not ok:
        _tentativi.setdefault((username, indirizzo), []).append(adesso)
        return None
    _tentativi.pop((username, indirizzo), None)
    token = secrets.token_urlsafe(32)
    scade = (datetime.now(timezone.utc) + DURATA_SESSIONE).isoformat(timespec="seconds")
    c.execute("DELETE FROM sessioni WHERE scade < ?", (ora(),))
    c.execute("INSERT INTO sessioni(token, utente_id, scade) VALUES(?,?,?)", (_hash_token(token), u["id"], scade))
    return token


def bloccato(username: str, indirizzo: str = "") -> bool:
    return _bloccato((username or "").strip().lower(), indirizzo, time.time())


def _bloccato(username: str, indirizzo: str, adesso: float) -> bool:
    recenti = lambda k: sum(1 for t in _tentativi.get(k, ()) if adesso - t < FINESTRA_TENTATIVI)
    if recenti((username, indirizzo)) >= MAX_PER_DISPOSITIVO:
        return True
    return sum(recenti(k) for k in list(_tentativi) if k[0] == username) >= MAX_PER_UTENTE


def _pulisci(adesso: float):
    """Dimentica gli errori più vecchi della finestra (anche quelli con nomi inventati)."""
    for k in [k for k, v in _tentativi.items() if not v or adesso - v[-1] >= FINESTRA_TENTATIVI]:
        del _tentativi[k]


def _confronto_finto() -> str:
    global _hash_finto
    if _hash_finto is None:
        _hash_finto = hash_password(secrets.token_hex(16))
    return _hash_finto


def utente_da_token(c, token: str | None):
    if not token:
        return None
    return c.execute("""SELECT u.* FROM sessioni s JOIN utenti u ON u.id=s.utente_id
                        WHERE s.token=? AND s.scade > ? AND u.attivo=1""", (_hash_token(token), ora())).fetchone()


def chiudi_sessioni(c, username: str, tranne: str | None = None):
    """Dopo un cambio di password le sessioni già aperte di quell'utente non valgono più (tranne quella indicata)."""
    c.execute("DELETE FROM sessioni WHERE utente_id=(SELECT id FROM utenti WHERE username=?) AND token!=?",
              ((username or "").strip().lower(), _hash_token(tranne) if tranne else ""))


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
