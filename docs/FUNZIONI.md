# Le funzioni di Magazzino, e perché ci sono

55 funzioni in 10 aree. Accanto a ognuna, il problema di tutti i giorni da cui nasce. Il racconto del progetto è nel [README](../README.md).

## Accesso e ruoli (5)

| Funzione | Il problema da cui nasce |
|---|---|
| App che si apre dal browser, anche dal tablet | Chi lavora fra gli scaffali non sta al computer e non deve imparare l'admin di Shopify. Serve qualcosa che si apre come un sito, con il tablet in mano. |
| Due ruoli, magazziniere e responsabile | Chi prepara i pacchi non ha bisogno di vendite, prezzi e ordini, e quei dati non devono girare. E le decisioni che contano hanno bisogno di qualcuno che le approvi. |
| Password personale, blocco dopo 5 tentativi | Il tablet resta in negozio, alla portata di tutti. Ogni movimento deve avere un nome, e nessuno deve entrare provando password a caso. |
| Demo con negozi finti | Per formare le persone e provare le regole serve un posto dove sbagliare non costa niente. Nella demo si provano anche i guasti: la rete che cade, una vendita durante l'aggiornamento. |
| Aggiornamenti delle giacenze spenti all'inizio | Prima di toccare le giacenze vere vanno decise le regole. Fino ad allora l'app legge, calcola e prepara, ma non sposta niente. |

## Lettura dei negozi (3)

| Funzione | Il problema da cui nasce |
|---|---|
| Rilettura dei tre siti ogni quarto d'ora, con l'etichetta di stato | Decidere su numeri vecchi è peggio che non decidere. Chi guarda deve capire subito se i dati sono aggiornati, vecchi o in errore, e con «Rileggi i negozi» li aggiorna quando serve. |
| Chi è chi: cassa, shop online, secondo negozio | Tre siti ma due negozi fisici: cassa e shop online del negozio A vendono gli stessi pezzi, e il magazzino esterno del fornitore non è merce nostra. Se l'app li confonde, conta due volte la stessa merce o sposta merce che non c'è. |
| Venduto senza dati dei clienti, resi e rimborsi contati bene | Per rifornire serve sapere cosa è uscito, non chi l'ha comprato. Un reso rientrato a scaffale non va rimandato, un rimborso senza reso sì: altrimenti il reintegro sbaglia. |

## Inventario (8)

| Funzione | Il problema da cui nasce |
|---|---|
| I due negozi affiancati, abbinati col codice a barre | Per sapere quanti pezzi ci sono in tutto bisognava aprire due siti e confrontare. E il codice a barre va preso com'è: 0012345 e 12345 sono prodotti diversi, mentre un foglio di calcolo li confonde. |
| Ricerca per nome, codice articolo o codice a barre, con i filtri | In negozio si ha il pezzo in mano, non il nome esatto: si spara il codice e si trova. I filtri rispondono alle domande di ogni giorno: cosa è sotto scorta, cosa è finito, cosa è in viaggio. |
| Scheda articolo | Per una domanda semplice («quanti ne abbiamo? ne stanno arrivando?») si giravano più schermate. Qui c'è tutto in una pagina: giacenze, trasferimenti aperti, anomalie. |
| Scorta minima e obiettivo, con eccezioni per articolo | Senza soglie il riordino si fa a occhio. Ogni negozio ha il suo giro, e alcuni articoli meritano una scorta diversa. |
| Scaffale di ogni articolo | Shopify non sa dove sta un pezzo. Senza lo scaffale si cerca a memoria, e chi è nuovo non trova niente. |
| Foto dell'articolo in ogni lista | Un prodotto si riconosce dalla foto prima che dal nome: meno pezzi sbagliati nello scatolone. Sui negozi veri le foto arrivano da Shopify. |
| Collegamento a mano, con un motivo | Alcuni articoli non si abbinano da soli, per un codice mancante o doppio. Il responsabile li collega, e il motivo resta scritto per chi viene dopo. |
| «Collega al suggerito» | Quando l'app ha già un candidato, per esempio lo stesso codice articolo, collegare deve essere un clic e non una ricerca. |

## Reintegro e bilanciamento (5)

| Funzione | Il problema da cui nasce |
|---|---|
| Reintegro del venduto, senza doppioni | Il negozio che vende deve riavere quello che ha venduto. Se due liste coprono gli stessi giorni, la stessa vendita non va rimandata due volte. |
| Reintegro fino alla scorta obiettivo | Oltre al venduto, un negozio sceso sotto il minimo va riportato in scorta, per non restare senza gli articoli che girano. |
| Bilanciamento fra i negozi | Se un negozio ha tanto e l'altro è sotto scorta conviene spostare, ma senza svuotare chi cede e senza che la merce torni indietro la settimana dopo. Il pezzo dispari va sempre allo stesso negozio, così non si discute. |
| Dalla lista alla bozza di trasferimento | Dalla lista al trasferimento non si ricopia niente: si spuntano le righe e nasce la bozza. |
| Direzione ricordata e periodi pronti (7, 14, 30 giorni) | Il reintegro si fa quasi sempre nello stesso verso e sugli stessi periodi. L'app se lo ricorda, e non si riparte da zero ogni volta. |

## Trasferimenti fra negozi (14)

| Funzione | Il problema da cui nasce |
|---|---|
| Percorso tracciato, dalla bozza alla ricezione | Prima uno spostamento era una serie di passaggi slegati e nessuno sapeva a che punto fosse. Ora ha uno stato, come una spedizione tracciata. |
| Approvazione con correzione delle quantità | Il responsabile deve vedere e poter correggere prima che la merce si muova, non scoprirlo dopo. |
| Approvazione con i numeri davanti | Per approvare bene serve sapere quanto è libero all'origine e quanto ha già la destinazione, senza aprire altre pagine. |
| Pezzi prenotati dall'approvazione | Due liste non devono promettere gli stessi pezzi: una volta approvati, sono di quel trasferimento. |
| Preparazione col lettore | Contare a mano è dove nascono gli errori. Col lettore ogni codice è un pezzo e la riga diventa verde quando è completa; chi non ha il lettore scrive i numeri. |
| Lista in ordine di scaffale | In ordine alfabetico si gira il negozio avanti e indietro. In ordine di scaffale si fa un giro solo. |
| Spedizione anche incompleta, con il motivo | Capita di non trovare un pezzo: si spedisce lo stesso e il motivo resta. E se intanto quel pezzo è stato venduto online, l'app si ferma prima di spedire quello che non c'è. |
| Scarico e carico su cassa e shop online insieme | Era il lavoro dei quattro passaggi a mano: togliere all'origine, aggiungere all'arrivo, ripetere sul secondo sito. Se se ne salta uno, la giacenza online è sbagliata. |
| Anteprima prima di aggiornare Shopify | Prima di toccare le giacenze che vede il cliente, chi conferma deve vedere cosa cambia su ogni sito. |
| L'anteprima vale per quello che mostra | Il controllo deve corrispondere a quello che succede davvero. Se un collega legge un pezzo in più dopo l'anteprima, la conferma si ferma e si ricontrolla. Il buco l'ha trovato il product manager durante il restyling. |
| Ricezione in più colli, con pezzi buoni e rotti | La merce non arriva sempre tutta insieme né sempre intera, e i pezzi rotti non devono finire in vendita. |
| Conteggio all'arrivo col lettore | Anche all'arrivo si conta meglio sparando i codici che a occhio. |
| Chiusura delle differenze | Un pezzo che manca all'arrivo va spiegato: perso, tornato indietro o arrivato dopo. Senza chiusura le differenze restano appese e la giacenza non torna. |
| Elenco che parte dagli aperti, con il «Prossimo passo» | Nell'elenco interessa cosa è ancora aperto e chi deve muoversi. La colonna dice il prossimo passo: approvare, preparare, ricevere. |

## Movimenti fatti direttamente su Shopify (5)

| Funzione | Il problema da cui nasce |
|---|---|
| Uscite senza destinazione | C'è chi fa ancora il trasferimento dall'admin di Shopify senza dire dove va la merce: Shopify la toglie da un solo sito e il negozio di arrivo non riceve niente. L'app se ne accorge, suggerisce dove è andata spiegando perché e sistema gli altri siti. |
| Carico da fornitore copiato uguale sull'altro sito | Un arrivo registrato sulla cassa non compare sullo shop online, e viceversa. Prima si registrava due volte a mano. |
| Divisione del carico con l'altro negozio | Spesso un arrivo va diviso fra i due negozi. La quota cambia da consegna a consegna (metà, un terzo…) e il pezzo dispari deve andare dove si decide, non a caso. Se divide il magazziniere, la quota passa dal responsabile. |
| Doppioni riconosciuti | Finora il carico si registrava due volte a mano, una per sito: il 7 settembre lo stesso carico da 11 pezzi compare su tutti e due. Se succede ancora, l'app lo riconosce e non lo raddoppia. |
| Ogni movimento si decide una volta sola | Due persone sulla stessa segnalazione, o un doppio clic, non devono caricare la merce due volte. |

## Aggiornamenti sicuri su Shopify (4)

| Funzione | Il problema da cui nasce |
|---|---|
| Nessun doppio movimento se Shopify non risponde | Se la rete cade a metà non si sa se il movimento è passato. Ripetere alla cieca raddoppia la merce, non ripetere la perde. L'app ripete senza rischio di doppioni; se il dubbio dura quasi un giorno, decide il responsabile. |
| Giacenza ricontrollata al momento dell'aggiornamento | Mentre il magazziniere prepara, i siti continuano a vendere. Se il numero è cambiato, l'app non scrive una giacenza vecchia. |
| Doppio clic, pagina ricaricata, due persone insieme | Sul tablet il doppio tocco capita spesso, e due colleghi possono lavorare sullo stesso trasferimento. Nessuna delle due cose deve raddoppiare un movimento. |
| Aggiornamenti riusciti a metà, sempre in vista | Se la cassa si aggiorna e lo shop online no, il problema non deve sparire: resta in vista con «Riprova» o «Non serve più». |

## Pulizia e controllo dei dati (4)

| Funzione | Il problema da cui nasce |
|---|---|
| Anomalie trovate a ogni lettura | I dati di Shopify si sporcano con il tempo: codici mancanti o doppi, articoli su un sito e non sull'altro, giacenze negative. Senza un elenco unico si scoprono lavorando, quando qualcosa va storto. Alla prima lettura erano 263. |
| Anomalie per urgenza, con il «Cosa fare» e la chiusura in blocco | Un elenco di 263 righe non lo smaltisce nessuno. Raggruppate per urgenza, con l'azione scritta accanto, sì. Quelle che non contano si chiudono tutte insieme con un motivo. |
| Correzioni di giacenza con fonte e conferma | Correggere un numero a sensazione crea nuovi errori. Ogni correzione dice da dove viene il numero giusto, un conteggio fisico o la cassa, e la conferma il responsabile. |
| Storico di ogni movimento | Quando un numero non torna, la prima domanda è: chi l'ha toccato e quando? Qui c'è la risposta, con il prima e il dopo. |

## Documenti (2)

| Funzione | Il problema da cui nasce |
|---|---|
| Lista di prelievo in PDF da stampare | Non tutti vogliono il tablet in mano, e a volte il tablet è scarico: la lista su carta, con le caselle da spuntare, funziona sempre. |
| Export in Excel e CSV | Riepiloghi e conti si fanno spesso in Excel. Un foglio aperto male trasforma i codici a barre in numeri e perde gli zeri iniziali: negli export restano come testo. |

## Facile per chi non è digitale (5)

| Funzione | Il problema da cui nasce |
|---|---|
| «Da fare» per ruolo | La prima domanda di chi arriva al lavoro è «cosa devo fare oggi?». Una pagina piena di numeri non risponde; una lista di cose da fare, con il pulsante accanto, sì. |
| Menu per ruolo | Meno voci, meno errori: il magazziniere vede solo le sue sezioni. |
| «Cerca o spara un codice» da ogni pagina | Il gesto naturale in magazzino è sparare il codice. Deve funzionare ovunque, senza cercare prima la pagina giusta. |
| Lettore con suono, vibrazione e «Annulla l'ultima» | Quando si sparano codici di fila non si guarda lo schermo: il suono dice se è andata, la vibrazione avverte dell'errore, una lettura sbagliata si annulla. I numeri scritti a mano si salvano da soli. |
| Schermate per tablet e telefono | In magazzino si lavora in piedi, spesso con una mano sola: pulsanti grandi, azioni sempre in vista, menu a icone. |
