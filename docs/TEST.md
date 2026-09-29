# Test eseguiti

Riepilogo dei test lanciati davvero, fra il 25 e il 29 settembre 2026 (Windows 11, Python 3.14). Le sezioni 1 e 2 sono state rifatte il 29/09, dopo l'audit (sezione 4).

## 1. Test automatici: 125 superati, 0 falliti

`.venv\Scripts\python -m pytest -q` → `125 passed`

| File | Cosa verifica |
|---|---|
| `test_abbinamento.py` (10) | EAN come stringa con zeri iniziali (`0012345678905` ≠ `12345678905`); nessun abbinamento con EAN doppio; EAN mancante con suggerimento SKU ma senza abbinamento; associazione manuale che vince sull'automatico; conflitto fra manuale e automatico → «ambigua»; canali non allineati; giacenza negativa; articolo in un solo negozio; varianti non tracciate escluse; **un'anomalia ignorata si chiude se la condizione sparisce** e si riapre se torna |
| `test_regole.py` (16) | l'origine non scende sotto la minima; reintegro del venduto; arrivi già pianificati scalati; scorta che si attiva solo sotto la minima; le due modalità non si sommano; bilanciamento con la regola che il cliente usava già («metà, il pezzo dispari al negozio con priorità»); nessun movimento opposto; coperture ridotte partendo dalle vendite più recenti; **divisione dei carichi** (metà, un terzo, tre quarti) con le tre regole del pezzo dispari, mai oltre il ricevuto; «sotto la minima» conta solo quello che l'altro negozio può coprire |
| `test_flusso.py` (14) | periodi sovrapposti che non contano due volte; annullamento che libera le vendite; approvazione ridotta; trasferimenti aperti che riducono il fabbisogno; scorta minima all'origine; **flusso completo** (proposta → approvazione → prelievo parziale con motivo → spedizione su principale e specchio → due ricezioni parziali → danneggiato non caricato → differenze chiuse con rientro all'origine); approvazione oltre il libero rifiutata; vendita durante la preparazione che blocca la spedizione; versioni e doppio clic, compreso **un pezzo preso dopo l'anteprima, che rende vecchia la conferma**; scritture spente che bloccano la spedizione; **due tablet che registrano lo stesso arrivo** (niente pezzi oltre lo spedito, un solo carico); **una differenza chiusa da due pagine** (un solo carico); **una lettura lenta che non cancella una ricezione appena scritta**; **due liste create insieme** che non coprono due volte la stessa vendita |
| `test_operazioni.py` (10) | stessa chiave = una operazione e un movimento; esito incerto ripetuto con la stessa chiave senza doppio scarico; arresto a metà scrittura; incerta oltre 23 ore dal primo invio della chiave → verifica a mano con nota (un tentativo recente non allunga la finestra); **un'incerta che riceve un errore generico resta incerta** (niente «Riprova» con una chiave nuova, niente doppio scarico); respinta da Shopify → errore; vendita durante la scrittura → rilettura e secondo tentativo; scarico oltre il disponibile → conflitto senza scrittura; **successo parziale su due negozi** e ripresa; chiave riusata con dati diversi rifiutata |
| `test_web.py` (14) | serve accedere; il magazziniere non entra in Storico e Impostazioni e non approva (403); **niente vendite al magazziniere**, né nella pagina né nel CSV; doppio invio che crea un solo trasferimento; gettone falso rifiutato; export dell'inventario con tutti i risultati filtrati (64, non la sola pagina da 50); flusso completo dalle pagine; **nessun segreto nelle pagine** (modalità reale con credenziali finte); tutte le pagine rispondono con entrambi i ruoli; numeri di pagina e trasferimenti inesistenti scritti a mano (mai errore 500); moduli con numeri non validi; firme della stampa uguali al PDF |
| `test_da_shopify.py` (11) | trasferimenti Shopify senza destinazione. Si rilevano solo quelli partiti dalla nostra sede: esclusi quelli con destinazione, quelli dal magazzino esterno, gli annullati e quelli precedenti alla data d'inizio; i «non partiti» diventano da destinare quando partono. Suggerimento con i motivi (nota, articoli presenti, sotto scorta). **Carica subito**: Porto caricato, lo specchio del Centro scalato, la cassa (già scalata da Shopify) non toccata. **In viaggio**: carico alla ricezione. Senza spunta lo specchio non si tocca. «Nessuna destinazione» scala solo lo specchio. «Gestito fuori» solo per il responsabile e senza scritture. Scritture spente e origine uguale alla destinazione rifiutate. Seconda decisione rifiutata senza movimenti. Righe sconosciute o senza corrispondenza segnalate. **Decisione su righe cambiate nel frattempo** rifiutata; anomalie di seguito visibili fra le anomalie |
| `test_carichi.py` (9) | carichi da fornitore. Rilevati solo quelli senza origine verso la nostra sede, con i pezzi accettati: un carico ricevuto in parte conta i pezzi già accettati, uno non ricevuto aspetta. **Tutto al negozio di arrivo**: il sito duplicato 1:1, la cassa non ricaricata, seconda decisione rifiutata. **Doppione fatto a mano** riconosciuto e chiuso senza scritture. **Diviso a metà, da portare**: bozza se decide il magazziniere, poi flusso normale fino alle giacenze finali giuste. **Diviso già arrivato**, con il dispari all'altro negozio. Quote corrette a mano e limiti. **Stesse quantità anche all'altro negozio**. Scritture spente, «gestito fuori» solo per il responsabile. Uscite e carichi non si confondono |
| `test_ux.py` (8) | il restyling. **L'anteprima della spedizione fissa le quantità**: un pezzo letto dopo l'anteprima fa rifiutare la conferma (409, nessuna scrittura); la nuova anteprima scrive esattamente i pezzi mostrati. **Lettore senza ricaricare**: risposta JSON senza dati commerciali, la stessa lettura ripetuta dopo un errore di rete si conta una volta sola, codice estraneo e pagina scaduta rifiutati, numero scritto a mano mai oltre l'approvato. Ricerca da qualunque pagina («TR-3», codice univoco, testo). Menu per ruolo, con il numero di «Da fare» uguale a quello della pagina. Elenco dei trasferimenti aperto sugli aperti, con il prossimo passo. **Anomalie ignorate in blocco** con un motivo, registrate una per una, vietate al magazziniere. Direzione del reintegro ricordata e nessuna finestra di conferma. Date senza fuso («2026-01-01» → 01/01/2026) |
| `test_export.py` (4) | CSV con BOM, accenti e apice davanti a `= + - @`; Excel con EAN in cella di testo e formule forzate a testo; PDF A4; totali righe e pezzi coerenti con il database in CSV ed Excel |
| `test_sicurezza.py` (7) | **scritture spente: «riprendi» e «Riprova» non scrivono** (e «riprendi» è del responsabile); «stesse quantità» mai oltre l'arrivato; differenza solo sulle righe del trasferimento; password cambiata che chiude le altre sessioni; blocco degli accessi per dispositivo (5) e per utente (20); avvisi falsi in un link non mostrati, pagine con `Cache-Control: no-store`; rettifiche e soglie con negozi o conteggi non validi |
| `test_interfaccia.py` (7) | filtro delle anomalie che resta dopo «Ignora»; anteprima di ricezione ripetibile; numeri scritti che restano dopo un errore e dopo «Torna indietro»; ritorno alla pagina giusta dopo la sessione scaduta (anche da un modulo); «Collega a mano» senza JavaScript; rettifica con il conteggio vuoto; ricarica automatica solo con le scritture accese; conferma «Approvare?» solo sul pulsante Approva |
| `test_shopify.py` (6) | client Shopify senza rete: risposta troncata a una scrittura = incerta; un 4xx non è una risposta elaborata; token scaduto rinnovato una volta; connessione chiusa a metà ripetuta; **carico con più spedizioni e più pagine di righe** senza righe perse; ordine troncato riconosciuto |
| `test_config.py` (8) | `SYNC_MINUTI` e `PORTA` non validi fermano l'avvio con un messaggio; `negozi.json` scritto male (campo mancante, negozio Shopify ripetuto, stesso id, JSON rotto) con un messaggio chiaro |
| `test_db.py` (1) | l'elenco dei trasferimenti e lo storico usano gli indici |

## 2. Prova nel browser: 59 controlli superati, 0 falliti

`python tests\e2e_browser.py`: Chromium headless (Playwright 1.60) sulla demo ripristinata. Magazziniere e responsabile lavorano in due sessioni separate.

- L'accesso è obbligatorio.
- «Da fare» del magazziniere: un trasferimento da preparare, l'errore di lettura simulato nella barra, nessuna voce Anomalie, Storico o Impostazioni. Il numero sulla voce di menu è quello della pagina.
- Dalla barra in alto, «TR-3» apre il trasferimento.
- L'inventario è paginato sul server: 50 righe su 64. La ricerca con il lettore trova l'EAN `0080001425422`.
- Il reintegro del magazziniere non contiene dati di vendita, quello del responsabile sì. Il pulsante conta righe e pezzi selezionati; un **doppio clic** su «Crea bozza» crea una sola bozza.
- Il responsabile approva riducendo una riga.
- Preparazione con lo scanner: le letture si fanno una dopo l'altra **senza ricaricare la pagina**, l'esito compare accanto al campo, e a lista completa il pulsante diventa «Controlla e spedisci».
- L'**anteprima** è una pagina pulita, senza la lista di lavoro, e mostra anche «Centro online». Dopo la conferma le giacenze del Centro calano esattamente della quantità spedita.
- Ricezione parziale: il trasferimento resta «spedito»; alla chiusura della ricezione nasce la differenza, che il responsabile chiude.
- Gli export PDF, Excel e CSV vengono scaricati.
- Bilanciamento calcolato. I trasferimenti si aprono sugli aperti, con il prossimo passo; «Tutti» li mostra tutti.
- Anomalie per gruppi (lettura non riuscita, EAN mancanti e doppi). Le 11 «solo in un negozio» si selezionano e si ignorano **in blocco** con un solo motivo.
- Uscita Shopify senza destinazione #T0101:
  - compare in «Da fare» e nell'elenco, con il Porto suggerito e preselezionato perché la nota lo nomina;
  - «È già arrivata?» si vede solo se è scelto un negozio;
  - l'anteprima mostra il carico al Porto e lo scarico su «Centro online»;
  - dopo la conferma il Porto è caricato esattamente dei pezzi del trasferimento, e il trasferimento risulta «destinato».
- Arrivi dal fornitore:
  - #T0106 sul sito è riconosciuto come doppione di #T0104 e chiuso come «stessa consegna» senza scritture;
  - su #T0104 la duplicazione 1:1 non viene proposta (c'è già il doppione) e la regola di divisione compare solo scegliendo «Dividi»;
  - divisione a metà con il pezzo dispari alternato, ricalcolata da sola a ogni scelta: il responsabile la conferma e nasce il trasferimento approvato con le quote giuste.
- Tablet (1024×768): la pagina non scorre in orizzontale e la tabella dell'inventario ci sta intera. Telefono (390×844): menu in basso, nessuno scorrimento orizzontale.

Il log del server dopo la prova non contiene errori. Schermate in `docs/schermate/`, export in `docs/esempi/`.

## 3. Lettura dei negozi veri, sola lettura

I nomi dei negozi sono sostituiti da A e B: il cliente resta anonimo. A ha due negozi Shopify con la stessa merce (la cassa e il negozio online), B ne ha uno.

`python magazzino.py sincronizza` con `SCRITTURE_SHOPIFY=0`: nessuna scrittura su Shopify. Tempo: 1 minuto e 36 secondi per i tre negozi in parallelo.

| Negozio Shopify | Varianti |
|---|---|
| A, cassa (sede A) | 6.542 |
| A, online (sede A) | 7.219 |
| B (sede B) | 6.775 |

Sono state lette anche le righe d'ordine degli ultimi 60 giorni e le giacenze in pezzi di ogni sede: i loro numeri non sono riportati, perché sono dati commerciali del cliente.

**Articoli:** 7.599 in tutto.

| Stato | Articoli |
|---|---|
| abbinati per EAN fra A e B | 6.613 |
| solo A | 601 |
| solo B | 28 |
| EAN mancante | 249 |
| EAN doppio | 108 |

**Anomalie aperte:** 263.

| Tipo | Quante |
|---|---|
| EAN mancante (solo varianti con merce) | 82 |
| articolo assente sul negozio online di A | 62 |
| in un solo negozio (con merce) | 57 |
| EAN doppio | 47 |
| cassa e online di A non allineati | 14 |
| giacenza negativa (un articolo a B) | 1 |

Le giacenze coincidono con quelle lette all'inizio del lavoro con le query dirette. Un solo inciampo, poi risolto: il file credenziali di B usa un dominio alias del negozio, e il controllo di sicurezza sul dominio all'inizio lo rifiutava. Ora `negozi.json` accetta più domini per negozio.

## 4. Trasferimenti Shopify dei negozi veri, sola lettura (27/09/2026)

Lettura da settembre con `SorgenteShopify.leggi_trasferimenti`, senza salvare nulla.

| Negozio | Trovati | Senza destinazione dalla nostra sede |
|---|---|---|
| A, cassa | 2 | **1** (41 pezzi, 14 righe su 14 abbinate) |
| A, online | 2 | nessuno (sono carichi da fornitore) |
| B | 1 | **1** (20 pezzi, 14 righe su 14 abbinate) |

Tutti e due erano stati caricati con gli script il 19/09: per questo sui negozi veri la data d'inizio è il primo giorno di uso e non ricompaiono.

## 5. Carichi da fornitore dei negozi veri, sola lettura (27/09/2026)

Pezzi accettati letti dalle spedizioni di ogni carico:

| Negozio Shopify | Carico | Data | Righe | Pezzi accettati |
|---|---|---|---|---|
| A, cassa | primo | 07/09 | 10 | 11 |
| A, online | secondo | 07/09 | 10 | 11 |
| A, online | terzo | 12/09 | 15 | 28 |

Il primo e il secondo sono la stessa consegna duplicata a mano: è il caso che il programma ora riconosce da solo. Sono di settembre, prima della data d'inizio, quindi non ricompaiono.

## Non verificato

- **Scritture sui negozi veri**: volutamente mai eseguite, nemmeno per i trasferimenti Shopify senza destinazione. Il flusso delle scritture è verificato solo sui negozi finti della demo, che imitano `changeFromQuantity` e le chiavi idempotenti di Shopify.
- **Tempi e comportamento con molti utenti insieme**: provati solo due utenti.
- **Lettore barcode fisico**: provato simulando la tastiera (codice + Invio), che è come funzionano i lettori USB.

## 4. Audit del 29/09/2026

Audit completo (sicurezza, integrità dei dati e integrazione con Shopify, pagine e accessibilità, configurazione, prestazioni). Ogni difetto è stato riprodotto, corretto e coperto da un test che fallisce sul codice di prima: i 42 test nuovi o cambiati della sezione 1.

- **Prestazioni.** Con i volumi di circa un anno (800 trasferimenti, 20.000 righe, 60.000 operazioni) l'elenco dei trasferimenti impiegava 11,4 secondi: ora 0,09 (indici della migrazione 4). Le altre pagine restano sotto i 100 ms; una lettura del lettore passa da 25 a 7 ms.
- **Prova delle correzioni nel browser** (Chromium, demo in un database temporaneo): 14 controlli superati su 14. Codice sparato con il cursore in un campo quantità; due «+» veloci con la prima risposta rallentata; errore del server durante le letture; Invio nel motivo di una spedizione incompleta; lettura prima di «Conta con il lettore»; ricarica automatica che non cancella una nota scritta.
- **Giro di tutte le pagine**: 20 pagine per 2 ruoli per 3 formati (1366, 1024, 390 px). Nessun errore JavaScript, nessuna richiesta fallita inattesa, nessuna pagina che scorre di lato (la stampa sul telefono ora ci sta).
- **Modalità reale senza credenziali**: parte, ogni pagina spiega cosa manca, nessuna connessione di rete tentata. Il `negozi.json` vero resta valido con i controlli nuovi.
- **Query Shopify nuove** (carichi e ordini letti per intero, a pagine): validate sullo schema Admin API 2026-07. Non provate sui negozi veri.

