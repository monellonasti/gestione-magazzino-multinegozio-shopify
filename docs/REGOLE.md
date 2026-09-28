# Regole di stock e limiti

Come ragiona il programma, in parole. Il codice delle regole è in `magazzino/regole.py` (funzioni pure, provate in `tests/test_regole.py`).

## Chi è chi

- **Negozio fisico**: dove sta la merce e da dove si preleva. Sono due, qui chiamati Negozio A e Negozio B (i nomi veri stanno in `negozi.json`, fuori da git).
- **Canale principale**: il negozio Shopify e la sede da cui si leggono le giacenze del negozio fisico.
  - Negozio A: il negozio Shopify con la cassa, sede A.
  - Negozio B: il suo negozio Shopify, sede B.
- **Canale specchio**: un altro negozio Shopify che vende la **stessa merce fisica**. Oggi è il negozio online di A, sede A.
  - Quando la merce parte o arriva, il programma aggiorna principale e specchi.
  - Due magazzini fisici diversi non ricevono **mai** automaticamente le stesse quantità: sono conti separati.
- **Disponibile**: il `available` di Shopify, cioè giacenza meno merce già impegnata in ordini.
- **Esclusi**:
  - il magazzino esterno con quantità da fornitore e le sedi vuote dentro i negozi Shopify di A;
  - le varianti non tracciate;
  - i prodotti archiviati senza merce.

## Abbinamento fra negozi

- Si abbina **per variante**, con l'EAN (barcode) trattato come **testo**.
  - Si tolgono solo gli spazi ai bordi.
  - `0012345678905` e `12345678905` sono codici diversi.
- Si abbina da solo solo un EAN che compare **una volta** in ciascun negozio Shopify.
- Se l'EAN manca o è doppio, la variante resta da sola e nasce un'anomalia.
  - Lo SKU uguale compare solo come **suggerimento**.
- Il responsabile può associare a mano una variante a un articolo, con un motivo obbligatorio. Nome, data e motivo restano nel registro.
  - L'associazione manuale vince sull'automatico.
  - Se l'articolo di destinazione ha già una variante su quel negozio Shopify, non è permessa.

## Reintegro

Si sceglie il negozio da rifornire, quello di origine, e una di **due modalità separate**. Le due modalità **non si sommano mai**: sono domande diverse e si sceglie quale fare.

- **Reintegro del venduto**: rimanda le unità consumate nel periodo e non ancora rimandate.
  - `fabbisogno = venduto non coperto − pezzi già in arrivo per altri motivi`
- **Ripristino della scorta obiettivo**: si attiva se disponibile + in arrivo è **sotto la minima**, e riporta il negozio all'**obiettivo**.
  - `fabbisogno = obiettivo − (disponibile + in arrivo)`
  - Una giacenza negativa conta come zero: è un'anomalia da contare, non fabbisogno.

**Consumo (venduto)** di una riga d'ordine:

`consumo = currentQuantity + pezzi rimborsati SENZA rimessa a magazzino (NO_RESTOCK)`

| Caso | Conta come consumo? | Perché |
|---|---|---|
| Vendita normale | sì | la merce è uscita |
| Annullo o reso con rimessa a magazzino (CANCEL, RETURN) | no | Shopify ha già ricaricato la giacenza |
| Rimborso senza reso (NO_RESTOCK) | **sì** | un rimborso non è merce rientrata |
| Unità rimosse modificando l'ordine | no | non sono in `currentQuantity` |
| Ordine di prova | no | |
| Riga senza variante (articolo personalizzato in cassa, «DVD») | no | non si può abbinare |
| Evasa da un'altra sede (per esempio il magazzino esterno) | no | non è uscita da questo negozio |

Il consumo di A è la somma delle vendite della cassa e del negozio online **evase dalla sede A**. Il periodo è a giorni interi, ora di Roma.

**Periodi sovrapposti.** Ogni trasferimento di reintegro registra quali righe d'ordine copre (tabella `coperture`). Un'altra lista con un periodo che si sovrappone scala le unità già coperte, riga per riga. In pratica:
- se un trasferimento viene **annullato**, le vendite tornano scoperte;
- se viene **approvato o spedito con meno pezzi**, restano coperte le vendite più vecchie e si liberano le più recenti.

**Quantità proposta** = la più piccola fra:
- il fabbisogno;
- la disponibilità **utilizzabile** all'origine, cioè disponibile − pezzi promessi ad altri trasferimenti non ancora spediti − scorta minima dell'origine.

Il magazziniere vede solo:
- immagine, prodotto, variante, SKU ed EAN;
- quantità da prelevare, origine e destinazione;
- ubicazione e note operative.

Vendite, prezzi e ordini non arrivano al suo browser.

## Bilanciamento

Si basa **solo sulle giacenze e sulle soglie**: non prevede la domanda. Quantità diverse nei due negozi non sono un errore.

- Scatta quando un negozio, contando ciò che gli sta già arrivando, è **sotto la sua minima**.
- La quantità è la più piccola fra:
  - quanto serve per arrivare all'obiettivo;
  - quanto l'origine può cedere restando **sopra la propria minima**;
  - **metà della differenza** fra i due negozi, così l'origine non finisce sotto la destinazione (niente ping-pong). Con un pezzo dispari vince il negozio con priorità, per ora A. È la regola che il cliente usava già nei suoi script: con minima 1 e obiettivo alto si ottengono gli stessi numeri.
- Una sola direzione per articolo e piano.
- Se c'è già un trasferimento aperto in un verso, il verso opposto non si propone.
- **Soglie**: predefinite per negozio (minima 1, obiettivo 2), con eccezioni per singolo articolo modificabili dalla scheda articolo.

## Trasferimenti: quando si muove la merce

| Stato | Su Shopify | Nel programma |
|---|---|---|
| Bozza | niente | proposta, modificabile dal responsabile |
| Approvato | niente | pezzi **prenotati**: non si propongono ad altri trasferimenti |
| In preparazione | niente | prelievo (lettore o a mano), anche parziale con motivo |
| Spedito | **scarico** all'origine della quantità spedita, su principale e specchi | i pezzi sono **in viaggio**: esistono solo qui |
| Ricevuto (anche a più riprese) | **carico** a destinazione dei pezzi arrivati buoni, su principale e specchi | danneggiati e mancanti diventano differenze |
| Chiusura differenze (responsabile) | mancanti: persi (nulla), rientrati all'origine (carico origine) o arrivati dopo (carico destinazione); danneggiati scartati: nulla | anomalia chiusa, motivo registrato |
| Annullato (solo prima della spedizione) | niente | prenotazioni e coperture liberate |

Ogni pezzo si scarica una volta e si carica una volta. I pezzi in viaggio non stanno in nessuna delle due giacenze Shopify: si vedono nella colonna «In trasferimento».

Shopify non trasferisce merce fra **negozi Shopify diversi**: i suoi trasferimenti vivono dentro un solo negozio. Per questo il programma coordina da sé scarico e carico, con rettifiche relative (`inventoryAdjustQuantities`) sui singoli negozi.

## Trasferimenti fatti su Shopify senza destinazione

Nell'admin di Shopify si può creare un trasferimento che parte da una sede **senza destinazione** (per esempio «Negozio A → nessuna destinazione»). Shopify scala la merce, ma **solo sul negozio Shopify dove è stato creato**: il negozio di arrivo non riceve niente, e nemmeno l'altro canale dello stesso negozio fisico. Prima questo lavoro lo facevano tre script lanciati a mano, in cartelle diverse.

**Rilevamento.** A ogni lettura il programma legge i trasferimenti di tutti i negozi Shopify, a partire dalla data indicata nelle Impostazioni. Tiene quelli che:
- non hanno destinazione;
- partono dalla sede di un nostro canale (il magazzino esterno no);
- sono già partiti (stato Shopify `IN_PROGRESS` o `TRANSFERRED`, con pezzi spediti).

La quantità è `shippedQuantity`, cioè quella che Shopify ha davvero scalato. Gli altri casi:
- quelli ancora in bozza o pronti per la spedizione restano «non ancora partiti» e diventano da destinare quando partono;
- quelli annullati escono dall'elenco;
- i carichi «da nessuno → Negozio A» (merce da fornitore) si trattano a parte, nella sezione seguente.

Ogni trasferimento da destinare apre un'anomalia e compare in «Da fare» e in Trasferimenti › Fatti su Shopify.

**Suggerimento.** Per ogni negozio fisico diverso da quello di origine il programma dice:
- se la nota del trasferimento lo nomina;
- quanti articoli del trasferimento esistono lì;
- quanti sono lì sotto la scorta minima.

Suggerisce il primo in quest'ordine. Con due negozi è quasi sempre l'altro, ma i motivi aiutano a scoprire un errore, per esempio articoli che nel negozio suggerito non esistono.

**Scelta** (con anteprima dei movimenti e conferma):

| Scelta | Su Shopify |
|---|---|
| Un negozio, «carica subito» | scarico sugli **altri canali** del negozio di origine (spuntabile) + carico a destinazione su tutti i canali. Nasce un trasferimento del programma già «ricevuto» |
| Un negozio, «in viaggio» | scarico sugli altri canali dell'origine. Nasce un trasferimento «spedito» che si riceve con il flusso normale: danneggiati e mancanti compresi |
| Nessuna destinazione | solo lo scarico sugli altri canali dell'origine (merce uscita e basta) |
| Già gestito fuori dal programma (responsabile) | niente, con una nota obbligatoria |

- Il negozio Shopify dove è stato creato il trasferimento **non si tocca**: Shopify l'ha già scalato.
- La spunta «scala anche l'altro canale» si toglie se lo stesso movimento è già stato fatto a mano anche lì. Se sull'altro canale c'è un trasferimento con gli stessi pezzi a pochi giorni di distanza, il programma lo segnala e toglie la spunta da solo.
- Ogni trasferimento Shopify si decide **una volta sola**: il doppio clic e due persone insieme non producono un secondo carico.
- Le righe con una variante che il programma non conosce (non tracciata o sconosciuta) restano fuori e aprono un'anomalia da sistemare a mano. Lo stesso vale per un articolo che non esiste nel negozio di arrivo.
- Sui negozi veri si considerano i trasferimenti creati **dal primo giorno di uso** (27/09/2026). Quelli di settembre erano già stati caricati con gli script: se la data si sposta indietro, vanno segnati «già gestito».

## Carichi da fornitore registrati su Shopify

Un carico da fornitore si registra su Shopify come trasferimento **senza origine** verso una sede («nessuno → Negozio A»). Shopify carica la merce **solo sul negozio Shopify dove viene registrato**. Finora la duplicazione sull'altro canale si faceva a mano, registrando lo stesso carico due volte: il 07/09/2026 lo stesso carico di 11 pezzi compariva sia sulla cassa sia sul negozio online di A.

**Rilevamento.** È lo stesso delle uscite: il programma tiene i trasferimenti senza origine verso la sede di un nostro canale, dalla data indicata nelle Impostazioni.
- La quantità è quella **accettata** nelle spedizioni (`acceptedQuantity`), cioè quella che Shopify ha davvero caricato. I pezzi rifiutati non contano.
- Un carico ricevuto in parte conta quello già accettato.
- Un carico non ancora ricevuto aspetta.
- Se sull'altro canale dello stesso negozio c'è un carico con gli stessi pezzi a pochi giorni di distanza, il programma lo segnala come **possibile doppione**.

**Scelte**, con anteprima e conferma:

| Scelta | Cosa succede su Shopify |
|---|---|
| **Tutto al negozio di arrivo** | con «duplica 1:1» spuntato: lo stesso carico sugli altri canali del negozio (il negozio online se era sulla cassa, o viceversa) |
| **Dividi con l'altro negozio** | duplicazione 1:1 come sopra, poi la quota dell'altro negozio diventa un trasferimento: **da portare** (bozza se decide il magazziniere, approvato se decide il responsabile: poi preparazione, spedizione e ricezione normali) oppure **già arrivata** (scarico subito dal negozio di arrivo e carico subito all'altro) |
| **Stesse quantità anche all'altro negozio (1:1)** | duplicazione 1:1 come sopra e lo stesso carico anche sull'altro negozio: il fornitore ha consegnato la stessa merce a tutti e due |
| **Stessa consegna già registrata sull'altro canale** | niente (è il doppione): serve una nota |
| **Già gestito fuori dal programma** (responsabile) | niente: serve una nota |

**La regola della divisione** si sceglie ogni volta:
- **quanto va all'altro negozio** di ogni riga: 1/2, 1/3, 2/3, 1/4 o 3/4;
- **dove va il pezzo dispari** quando la divisione non è esatta:
  - resta al negozio di arrivo;
  - va all'altro;
  - una volta ciascuno, riga dopo riga (si comincia dal negozio di arrivo).

Esempio con metà: righe da 5, 4 e 3 pezzi → all'altro negozio 2, 2, 1 (dispari al negozio di arrivo), oppure 3, 2, 2 (all'altro), oppure 2, 2, 2 (alternato).

Dopo aver applicato la regola, ogni riga si può **correggere a mano** prima dell'anteprima: all'altro negozio vanno da 0 ai pezzi arrivati. L'ultima regola usata viene riproposta la volta dopo.

Regole comuni:
- Il negozio Shopify dove è stato registrato il carico **non si ricarica**.
- Ogni carico si decide **una volta sola**.
- Le righe con una variante che il programma non conosce restano fuori e aprono un'anomalia.
- Sui negozi veri valgono i carichi registrati **dal primo giorno d'uso** in poi. Quelli di settembre sono già stati gestiti a mano.

## Scritture sicure

Le due scritture di un movimento (per esempio la cassa e il negozio online di A) non sono una transazione unica. Ognuna è una riga della tabella `operazioni`, con il proprio stato e la propria chiave.

1. **Prima di spedire** il programma rilegge da Shopify la giacenza dell'origine. Se nel frattempo si è venduto e i pezzi non bastano, **non spedisce** e mostra quali righe ridurre.
2. **Prima di ogni scrittura** rilegge la giacenza. Uno scarico che la porterebbe sotto zero diventa **conflitto** e non parte.
3. La scrittura usa `changeFromQuantity` e la chiave `@idempotent`, entrambe obbligatorie dall'API 2026-04. Se qualcuno ha venduto fra lettura e scrittura, Shopify rifiuta: il programma rilegge e riprova con una chiave nuova, fino a 3 volte.
4. Lo stato «in corso» si salva **prima** della chiamata. Se il programma si ferma a metà, al riavvio l'operazione è **incerta**.
5. Un esito **incerto** (nessuna risposta) si risolve ripetendo la **stessa** chiamata con la **stessa** chiave. Shopify ricorda le chiavi per 24 ore e non applica due volte. Oltre 23 ore l'operazione passa a **da verificare**: decide il responsabile guardando lo storico della giacenza su Shopify, con una nota obbligatoria.
6. Se riesce solo una delle due scritture (per esempio lo specchio non ha l'articolo), la riuscita resta. L'altra diventa un'anomalia «aggiornamento fallito», con i pulsanti *Riprova* e *Non serve più*.
7. Doppio clic, pagina ricaricata, due persone insieme:
   - ogni modulo ha un gettone usa-e-getta;
   - ogni trasferimento ha un numero di versione, che cambia anche a ogni pezzo letto o scritto a mano: una conferma preparata prima di un'altra lettura viene rifiutata, e si scrive solo quello che l'anteprima ha mostrato;
   - ogni lettura del lettore ha un suo identificativo: se la rete cade e il browser la ripete, si conta una volta sola;
   - ogni movimento ha una chiave unica.

## Anomalie e rettifiche

- **Anomalie dei dati**, ricalcolate a ogni lettura (si aprono e si chiudono da sole):
  - EAN mancante;
  - EAN doppio;
  - associazione ambigua;
  - articolo in un solo negozio (solo se ha merce);
  - disponibilità negativa;
  - canali non allineati (principale e specchio con giacenze diverse);
  - articolo mancante sul canale specchio.
- **Anomalie operative**:
  - lettura di un negozio non riuscita;
  - scrittura non riuscita;
  - differenza alla ricezione.
- **Rettifiche**:
  - servono una fonte esplicita, anteprima, motivo e conferma del responsabile, e restano nel registro con il prima e il dopo;
  - le fonti sono due: «conteggio fisico», con i pezzi contati, oppure «il canale principale è giusto», che allinea gli specchi;
  - la giacenza si rilegge alla conferma: se è cambiata dopo l'anteprima, la rettifica si annulla.
- Si possono **ignorare in blocco**, con un solo motivo, le anomalie che di solito non vanno corrette (EAN mancante, manca sul negozio gemello, solo in un negozio). Ognuna resta nel registro, e un'anomalia ignorata non si riapre alla lettura successiva.
- Il programma **non sceglie da solo** quale numero è giusto. Sui negozi veri le rettifiche restano spente finché non ci sono `SCRITTURE_SHOPIFY=1` e `RETTIFICHE=1`: fino ad allora confronta e segnala.

## Limiti noti

- **Ordini**: solo gli ultimi 60 giorni (l'app non ha `read_all_orders`). Il reintegro del venduto non va oltre.
- **Una sede per negozio Shopify.** Per un'altra sede serve un'altra riga in `magazzino/negozi.py`.
- **Riallineamento fra i canali**: il programma **segnala** le differenze fra la cassa e il negozio online di A, ma **non propaga gli ordini** da un canale all'altro. Quel lavoro resta allo script di riallineamento che il cliente usa già, con la regola già decisa: vince la più bassa se giustificata da ordini, il resto a mano. I trasferimenti fatti dal programma aggiornano entrambi i canali, quindi non creano differenze.
- **Trasferimenti Shopify con destinazione** (fra sedi dello stesso negozio Shopify) non vengono letti.
- **Ubicazioni a scaffale**: Shopify non le ha, si inseriscono dalla scheda articolo. Sui negozi veri all'inizio sono vuote.
- **Previsioni**: il bilanciamento non prevede la domanda. Il reintegro guarda solo il passato.

## Da decidere prima di accendere le scritture

Sono le scelte che cambiano i movimenti reali. Finché non sono decise, le scritture restano spente.

1. **Chi è il canale principale di A.** Oggi è il negozio Shopify con la cassa, e quello online fa da specchio. La disponibilità mostrata e il controllo prima della spedizione usano il principale.
2. **Quando scaricare l'origine.** Proposta attuale: alla spedizione, e nel frattempo la prenotazione vive solo nel programma. In alternativa si potrebbe spostare la merce in «riservato» su Shopify già alla preparazione, così online non si vende ciò che è nello scatolone.
3. **Visibilità nell'admin Shopify.** Oggi il programma fa rettifiche relative con un riferimento al documento (`logistics://magazzino/trasferimento/TR-0001`). In più si potrebbe creare il trasferimento nativo sul negozio di origine («Negozio A → nessuna destinazione»), come si faceva a mano.
4. **Soglie predefinite**: minima 1 e obiettivo 2 per entrambi i negozi, e quali articoli meritano una soglia propria.
5. **Rettifiche**: se accenderle, e se «il canale principale è giusto» è una fonte accettabile per le differenze fra la cassa e il negozio online, oppure se serve sempre un conteggio.
