# Dettagli tecnici

Questa pagina è per chi vuole installare Magazzino o guardarne il codice. Il racconto del progetto è nel [README](../README.md).

## Chi è chi

| Negozio fisico | Negozi Shopify che lo rappresentano |
|---|---|
| Negozio A | un negozio Shopify con la cassa, sede A (principale) + un negozio Shopify online, sede A (specchio: stessa merce) |
| Negozio B | un negozio Shopify, sede B |

I negozi veri stanno in `negozi.json`, un file che resta sul PC del negozio e fuori da git (modello: `negozi.example.json`). Le altre sedi, per esempio un magazzino esterno con quantità da fornitore, non partecipano ai trasferimenti.

Il programma si avvia sul PC del negozio e si usa dal browser, anche da un tablet collegato alla stessa rete. Si collega ai negozi tramite le app custom Shopify già in uso negli script, e le credenziali restano sul PC. La **modalità demo** usa negozi inventati ed è del tutto separata dai dati veri.

## La demo

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python magazzino.py --demo
```

Apri `http://127.0.0.1:8000`. Gli accessi della demo sono sulla pagina di login: `magazziniere` o `responsabile`, password `demo1234`.

La demo ha:
- 43 prodotti con varianti, in due negozi fisici e tre negozi Shopify;
- EAN mancanti e doppi;
- articoli sotto scorta;
- trasferimenti in ogni stato, compreso uno ricevuto a metà;
- un negozio che non risponde alla lettura.

Da **Impostazioni → Demo** si possono simulare esiti incerti e vendite durante una scrittura, oppure ripristinare tutto da capo. Si ripristina anche con `python magazzino.py demo-ripristina`.

## Avvio sui negozi veri

1. Copia `negozi.example.json` in `negozi.json` e descrivi i negozi veri: nomi, sedi, domini myshopify. Copia `.env.example` in `.env` e metti `MODALITA=reale`. Le righe `ENV_<STORE>` (una per ogni negozio Shopify di `negozi.json`) puntano ai file credenziali delle app custom, spesso quelli che usano già gli script: le credenziali non vanno copiate.
2. Crea il primo responsabile, e poi gli altri utenti (li puoi aggiungere anche da Impostazioni). La password viene chiesta a video.

   ```bash
   .venv\Scripts\python magazzino.py utente giulia --ruolo responsabile --nome "Giulia"
   ```

3. Prova la lettura dei negozi. È in sola lettura e stampa un riepilogo.

   ```bash
   .venv\Scripts\python magazzino.py sincronizza
   ```

4. Avvia il programma. Il terminale mostra anche l'indirizzo per il tablet (`http://192.168.x.x:8000`).

   ```bash
   .venv\Scripts\python magazzino.py
   ```

**Le scritture su Shopify sono spente** (`SCRITTURE_SHOPIFY=0`). Così si può consultare, calcolare, creare bozze, approvare, preparare e stampare, ma spedizione, ricezione e rettifiche sono bloccate con un messaggio. Si accendono con `SCRITTURE_SHOPIFY=1` e un riavvio, dopo aver letto [REGOLE.md](REGOLE.md) e deciso le questioni aperte in fondo a quel file.

Per avviarlo all'accensione del PC: Utilità di pianificazione di Windows, attività «all'avvio» che esegue `.venv\Scripts\python.exe magazzino.py` con cartella di lavoro questa cartella.

## Comandi

| Comando | Cosa fa |
|---|---|
| `python magazzino.py` | avvia (modalità dal `.env`) |
| `python magazzino.py --demo` | avvia la demo (database `dati/demo.db`) |
| `python magazzino.py --porta 8080` | su un'altra porta |
| `python magazzino.py utente NOME --ruolo responsabile\|magazziniere` | crea o aggiorna un utente |
| `python magazzino.py sincronizza` | legge i negozi una volta (solo lettura) |
| `python magazzino.py demo-ripristina` | demo da capo |

## Come è fatto

Un solo processo Python: FastAPI con pagine generate dal server (Jinja2), SQLite e un thread che rilegge i negozi ogni `SYNC_MINUTI`. Niente framework JavaScript, niente servizi esterni oltre a Shopify.

| File | Cosa contiene |
|---|---|
| `magazzino.py` | avvio e comandi |
| `magazzino/negozi.py` | chi è chi: negozi fisici, negozi Shopify, principale e specchi |
| `magazzino/shopify.py` | collegamento alle app custom (client_credentials, retry, limiti API, query validate su API 2026-07) |
| `magazzino/demo.py` | negozi finti con la stessa interfaccia e le stesse regole di scrittura |
| `magazzino/sync.py` | lettura dei negozi, abbinamento, anomalie, lettura periodica |
| `magazzino/abbinamento.py` | abbinamento per EAN (funzioni pure) |
| `magazzino/regole.py` | reintegro, bilanciamento, coperture (funzioni pure) |
| `magazzino/calcoli.py` | dal database agli ingressi delle regole |
| `magazzino/trasferimenti.py` | stati del trasferimento e movimenti |
| `magazzino/da_shopify.py` | movimenti fatti su Shopify: uscite senza destinazione e carichi da fornitore |
| `magazzino/operazioni.py` | scritture su Shopify idempotenti e riprendibili |
| `magazzino/rettifiche.py` | rettifiche con fonte, anteprima e conferma |
| `magazzino/export.py` | CSV, Excel, PDF |
| `strumenti/foto_demo.py` | genera le foto dei prodotti inventati della demo (OpenAI, stesso stile per tutte); la chiave si legge da un `.env` esterno e non si stampa |
| `magazzino/compiti.py` | cosa c'è da fare, per ruolo: la pagina «Da fare» e i numeri del menu vengono da qui |
| `magazzino/web.py`, `templates/`, `static/` | le pagine (in `static/`: `stile.css` con il sistema visivo e `app.js` per lettore, suoni e aiuti; tutto funziona anche senza JavaScript) |
| `magazzino/db.py` | schema SQLite e migrazioni (`MIGRAZIONI`, applicate all'avvio) |

Demo e negozi veri usano lo **stesso motore**: cambia solo la sorgente dei dati (`SorgenteDemo` o `SorgenteShopify`). In demo le credenziali Shopify non vengono mai lette. I due database sono file separati (`dati/demo.db` e `dati/magazzino.db`).

Le regole di stock, spiegate a parole, sono in [REGOLE.md](REGOLE.md). Il restyling con i due agenti AI, con le schermate prima e dopo, è in [PORTFOLIO.md](PORTFOLIO.md).

## Test

```bash
.venv\Scripts\python -m pytest -q
python tests\e2e_browser.py
```

Il secondo comando fa la prova completa nel browser. Serve Playwright: `pip install playwright` e poi `playwright install chromium`.

Risultati dell'ultima esecuzione: [TEST.md](TEST.md). Schermate: [schermate/](schermate/). Export di esempio: [esempi/](esempi/).

## Sicurezza

- Password salvate con scrypt; sessione in un cookie httponly che scade dopo 12 ore; 5 tentativi sbagliati bloccano l'utente per 10 minuti.
- Ogni modulo porta un gettone firmato con la sessione e usabile una volta sola: blocca le richieste da altri siti e i doppi clic.
- Permessi controllati sul server. Il magazziniere non riceve mai vendite, prezzi o dati degli ordini, nemmeno nascosti nella pagina o negli export.
- Del venduto il programma salva solo quantità, data e sede di ogni riga d'ordine: niente clienti né importi.
- Le credenziali non compaiono in pagine, log o export.
- Il programma è fatto per la rete del negozio. Per usarlo da fuori serve un accesso protetto (VPN, o un proxy con HTTPS); non va esposto su internet così com'è.

## Se un giorno servisse in cloud

Non serve per usarlo in negozio. Per spostarlo:
- un server Python con disco persistente per `dati/`;
- le tre coppie di credenziali in variabili d'ambiente o nei file indicati da `ENV_*`;
- HTTPS davanti (per esempio Caddy);
- una sola istanza, perché SQLite e la lettura periodica girano nello stesso processo.

Oggi non è online da nessuna parte.
