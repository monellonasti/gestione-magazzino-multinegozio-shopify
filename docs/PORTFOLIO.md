# Caso: un magazzino, tre negozi Shopify, due negozi fisici

## Il problema

Un rivenditore di Napoli ha **due negozi fisici**, qui chiamati A e B, e **tre negozi Shopify**. Due dei tre, la cassa e il negozio online, vendono la **stessa merce fisica** del negozio A. Il terzo è quello di B.

Spostare merce da un negozio all'altro voleva dire:
1. creare il trasferimento nell'admin del negozio di origine;
2. lanciare uno script per caricare la destinazione, che sta su un altro negozio Shopify (Shopify non trasferisce fra negozi diversi);
3. lanciare un secondo script per ripetere lo scarico sul negozio «gemello»;
4. annotare a mano il carico nel registro dello script che riallinea le vendite fra i due gemelli.

Quattro passaggi, in tre cartelle, da fare nell'ordine giusto. Un errore produceva una giacenza sbagliata, e nessuno sapeva dire quale fosse quella vera.

Le liste di reintegro si facevano a occhio o con un export del venduto. Quelle esportazioni mostravano anche vendite e ordini a chi doveva solo prelevare.

## Cosa ho analizzato prima di scrivere codice

- **La topologia reale**, letta dalle API: quali sedi hanno merce, quali sono vuote, quale è un magazzino esterno virtuale (quantità fisse da fornitore) da escludere.
- **Le regole già decise dal titolare**: per esempio «fra i due gemelli vince la quantità più bassa solo se la differenza è giustificata da un ordine; il resto si corregge a mano».
- **Gli script esistenti**: abbinamento per EAN, `changeFromQuantity`, chiavi idempotenti, registri. Le parti solide sono state riusate, non riscritte da zero.
- **I permessi delle app**: ordini solo degli ultimi 60 giorni, niente trasferimenti nativi fra negozi diversi.

## La soluzione

Un programma Python con pagine web locali, usato da PC e tablet del negozio, con due ruoli:
- il **magazziniere** vede solo quello che serve per prelevare;
- il **responsabile** approva, imposta le soglie e decide sulle anomalie.

| Funzione | Cosa risolve |
|---|---|
| **Inventario unificato** | i due negozi fisici affiancati, abbinati per EAN trattato come testo (gli zeri iniziali contano); ricerca con il lettore barcode |
| **Reintegro** | due domande distinte e mai sommate: «rimanda il venduto» e «riporta alla scorta obiettivo». Ogni vendita coperta da un trasferimento non viene rimandata una seconda volta, anche con periodi che si sovrappongono |
| **Bilanciamento** | proposte fra i negozi basate solo sulle soglie. L'origine non scende mai sotto la sua minima e non resta con meno pezzi della destinazione (niente ping-pong) |
| **Trasferimenti** | bozza → approvato → in preparazione → spedito → ricevuto. Scarico alla spedizione e carico alla ricezione, su tutti i canali Shopify del negozio. Gestisce ricezioni parziali, pezzi mancanti o rotti e la chiusura delle differenze |
| **Trasferimenti Shopify senza destinazione** | chi sposta merce dall'admin di Shopify senza indicare dove va non lascia più giacenze a metà: il programma li trova, suggerisce il negozio di arrivo con i motivi e, alla scelta, fa il carico e allinea il canale gemello |
| **Carichi da fornitore** | un carico registrato su un solo negozio Shopify si duplica 1:1 sul canale gemello o si divide con l'altro negozio con una regola esplicita (frazione e pezzo dispari scelti ogni volta, righe correggibili). I doppioni fatti a mano vengono riconosciuti |
| **Anomalie** | coda di EAN mancanti o doppi, canali gemelli non allineati, scritture non riuscite, differenze alla ricezione |
| **Documenti** | lista di prelievo in PDF A4 con caselle di spunta e totali; Excel con gli EAN come testo; CSV protetto dalla formula injection |

## I controlli che contano

Le scritture su due negozi Shopify non sono una transazione unica. Il programma le tratta così:
- **Una riga per scrittura**, con il suo stato e la sua chiave. Se ne riesce una sola, l'altra resta visibile come anomalia.
- **Esito incerto** (nessuna risposta): si ripete la **stessa** chiamata con la **stessa** chiave idempotente, e Shopify non la applica due volte. Oltre le 24 ore di memoria delle chiavi decide una persona, con lo storico davanti.
- **Vendite nel frattempo**: la giacenza si rilegge prima di spedire e prima di ogni scrittura (`changeFromQuantity`). Se non basta, il programma si ferma e dice quali righe ridurre.
- **Doppio clic, pagina ricaricata, due persone insieme**: gettone usa-e-getta per ogni modulo, numero di versione per ogni trasferimento, chiave unica per ogni movimento.
- **Nessuna fonte di verità inventata**: le rettifiche vogliono una fonte dichiarata, un'anteprima e la conferma del responsabile. Sui negozi veri sono spente finché non si decide.
- **Riservatezza**: il ruolo del magazziniere non riceve vendite, prezzi o ordini, nemmeno negli export. Del venduto il programma conserva solo quantità, data e sede.
- **Scritture reali spente** di default. La demo usa lo stesso motore con negozi finti che simulano esiti incerti, vendite concorrenti ed errori di lettura.

## Il restyling, con un product manager e un graphic designer

Finito il programma, l'ho fatto rivedere da due figure esperte (due agenti con ruoli distinti), a partire da 56 schermate della demo: due ruoli, desktop chiaro e scuro, tablet orizzontale e verticale, telefono.

- **Il product manager** ha misurato ogni pagina sul compito più frequente, preparare con il lettore, e ha scritto un backlog P0/P1/P2 con criteri di accettazione, la tabella dei testi da cambiare e un glossario per ruolo («scrittura», «specchio» ed «esito incerto» non sono parole da magazzino).
- **Il designer** ha disegnato un sistema visivo: colori caldi del marchio con un bordeaux per le azioni principali, stati riconoscibili anche dalla forma, icone SVG disegnate apposta, un binario di icone per il tablet e schede in basso per il telefono, bersagli da 44–48 px al tatto.

**Il bug trovato dal PM.** L'anteprima della spedizione non fissava le quantità, e le letture non cambiavano la versione del trasferimento. Un pezzo letto dopo l'anteprima, da un altro tablet o da un'altra scheda, sarebbe finito nella scrittura su Shopify senza essere stato mostrato. Ora:
- il modulo di conferma porta le quantità mostrate;
- ogni lettura cambia la versione, così una conferma vecchia viene rifiutata.

C'è un test apposta.

**Cosa è cambiato:**

| Prima | Dopo |
|---|---|
| Panoramica uguale per tutti, a riquadri | **Da fare** per ruolo: cosa preparare, ricevere, approvare, sistemare, con il pulsante per farlo. Il numero sulla voce di menu segue le stesse regole della pagina, scritte in un solo modulo |
| Sei voci di menu anche per il magazziniere | menu per ruolo; **cerca o spara un codice** da qualunque pagina («TR-3», un EAN, un testo) |
| Ogni lettura ricaricava la pagina: le letture veloci si perdevano e l'esito finiva in alto, lontano dal campo | lettura senza ricaricare, con una coda che non perde nulla, esito e suono accanto al campo, **Annulla l'ultima**, righe in ordine di scaffale. Ogni lettura ha un identificativo: se la rete cade e il browser la ripete, si conta una volta sola |
| «Spedisci…» sempre uguale; il motivo sempre visibile | «Controlla e spedisci» a lista completa, «Spedisci comunque: mancano 3» (secondario) se manca qualcosa; il motivo compare solo allora |
| Anteprima in mezzo alla pagina di lavoro | pagina pulita, raggruppata per articolo, con una colonna per negozio Shopify |
| Approvazione senza numeri | accanto a ogni riga, quanto è libero all'origine e quanto ha già la destinazione |
| Ricezione con le colonne della preparazione | scaffale del negozio di arrivo, «Conta con il lettore», i rotti che abbassano da soli i buoni |
| Elenco di tutti i trasferimenti | aperti per default, colonna **Prossimo passo**, schede «Fra negozi · Fatti su Shopify» |
| Movimenti Shopify: tutte le scelte insieme | una domanda alla volta, regola di divisione solo con «Dividi», ricalcolo automatico, «Ora sistema #T0106» dopo il primo doppione |
| 263 anomalie in schede tutte uguali | gruppi in ordine di urgenza, ciascuno con «Cosa fare» e il pulsante giusto; quelle che di solito non si correggono si **ignorano in blocco** con un motivo |
| Numeri, date e parole tecniche | plurali corretti, date senza errori di fuso, nomi dei negozi al posto di «specchio» e degli id |

I vincoli sono rimasti gli stessi:
- niente framework JavaScript e niente CDN;
- ogni pagina funziona anche senza JavaScript;
- il magazziniere continua a non ricevere dati commerciali, nemmeno nelle risposte JSON del lettore.

Le coppie prima/dopo sono in [restyling/](restyling/).

Le foto dei prodotti della demo sono generate con un modello di immagini (OpenAI gpt-image-2) da `strumenti/foto_demo.py`: i prodotti sono inventati, e ogni foto ha la stessa regia (un oggetto, fondo crema, luce dorata, nessun testo né marchio).

## Verifiche fatte

Il dettaglio è in [TEST.md](TEST.md).

- 83 test automatici:
  - abbinamento EAN con zeri iniziali;
  - scorte minime;
  - reintegro con periodi sovrapposti;
  - ricezioni parziali;
  - idempotenza, esiti incerti e successi parziali;
  - permessi e assenza di dati commerciali;
  - coerenza degli export;
  - anteprima della spedizione che fissa le quantità, letture ripetute contate una volta, anomalie ignorate in blocco.
- Una prova completa nel browser (59 controlli), dalla lista di reintegro alla chiusura della differenza, più tablet e telefono.
- Una lettura **reale e in sola lettura** dei tre negozi, il 25/09/2026:
  - 20.536 varianti e le righe d'ordine degli ultimi 60 giorni, in 1 minuto e 36 secondi;
  - 6.613 articoli abbinati fra i due negozi fisici.

## Cosa si può misurare

Non riporto risultati d'uso: il programma non è ancora in servizio sui negozi veri. Queste sono le misure previste e il punto di partenza.

| Misura | Punto di partenza (verificato) | Come si misura dopo |
|---|---|---|
| Passaggi manuali per trasferimento | 4 (admin + 2 script + registro), in 3 cartelle | azioni nel registro del programma per trasferimento |
| Qualità dei codici (25/09/2026) | 82 varianti con merce senza EAN, 47 EAN doppi, 62 articoli con merce assenti sul canale gemello | stessa coda anomalie, settimana per settimana |
| Allineamento dei canali gemelli | 14 varianti diverse il 25/09, 3 giorni dopo l'ultimo riallineamento a mano; 0 dopo quel riallineamento (22/09) | anomalie «canali non allineati» aperte |
| Correzioni fatte a mano | 21 varianti lasciate da correggere a mano nel riallineamento del 16/09 | rettifiche registrate |
| Differenze alla ricezione | non tracciate prima | righe con differenza / righe ricevute |
| Tempo di preparazione | non misurato prima | da «inizia preparazione» a «spedito» (orari nel registro) |

## Schermate

Tutte in [schermate/](schermate/), dalla demo:
- «Da fare»: `02`;
- inventario e lettore: `03`, `04`;
- reintegro visto dai due ruoli: `05`, `06`;
- approvazione: `07`;
- preparazione con scanner: `08`, e su tablet `26`;
- conferma della spedizione: `09`;
- aggiornamenti su Shopify: `10`;
- differenza alla ricezione: `12`;
- bilanciamento: `13`;
- anomalie per gruppi: `14`;
- vista di stampa: `18`;
- tablet: `17`, `26`; telefono: `27`, `28`.

Gli export di esempio sono in [esempi/](esempi/). Le coppie prima/dopo del restyling sono in [restyling/](restyling/).
