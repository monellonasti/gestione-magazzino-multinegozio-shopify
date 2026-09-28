# Guida per il magazziniere

Una pagina, da tenere vicino al banco.

## Entrare

Apri il browser del PC o del tablet all'indirizzo che ti ha dato il responsabile, per esempio `http://192.168.1.20:8000`. Entra con il tuo nome utente e la tua password.

Si apre **Da fare**: quello che c'è da preparare, da ricevere e da sistemare, con un pulsante per ciascuno. Il numero sulla voce «Da fare» del menu è lo stesso della pagina.

In alto vedi se i numeri dei negozi sono aggiornati. Se l'etichetta è **rossa** o **gialla**, premi **Rileggi i negozi** prima di fidarti dei numeri; se resta rossa, avvisa il responsabile.

## Cercare un articolo o un trasferimento

Il campo **Cerca o spara un codice** in alto funziona da qualunque pagina:
- spara il codice a barre con il lettore, o scrivi lo SKU: si apre la scheda dell'articolo;
- scrivi `TR-3`: si apre il trasferimento TR-0003;
- qualsiasi altra parola: cerca nell'inventario.

Nell'**Inventario** ogni articolo mostra i pezzi disponibili nei due negozi, con lo **scaffale** sotto il numero, e quanti sono in viaggio o prenotati. «—» vuol dire che l'articolo non esiste in quel negozio.

## Fare una lista di reintegro

1. **Reintegro** → scegli **Da → A**, per esempio «Negozio Centro → Negozio Porto». La volta dopo il tablet la ripropone.
2. Scegli **Ciò che è stato venduto** (con il periodo: tocca «7», «14» o «30» giorni) oppure **Fino alla scorta obiettivo**, poi **Calcola**.
3. Togli la spunta a quello che non vuoi mandare: basta toccare la riga. Il pulsante in basso conta righe e pezzi: **Crea bozza · 28 righe · 72 pezzi**.
4. Il responsabile la approva. Finché non è approvata non si prepara.

Da **Scarica** puoi stampare la proposta o scaricarla in Excel o CSV.

## Preparare

1. In **Da fare**, sotto «Da preparare», premi **Prepara** (oppure **Continua** se l'hai già iniziata). Se ti serve la lista su carta, **Scarica → Stampa lista**: le righe sono in ordine di scaffale.
2. Premi **Inizia a preparare**. Il campo del lettore resta in alto mentre scorri.
3. Spara ogni pezzo: ogni lettura vale +1, la pagina non si ricarica e puoi sparare di seguito. Accanto al campo leggi l'esito, per esempio «Pettine in legno: 3 di 5 · scaffale D2-2», e senti un suono. La riga diventa verde quando è completa.
   - Codice sbagliato? Il programma lo dice e suona diverso: «Non è in questo trasferimento: … Mettilo da parte».
   - Letto un pezzo di troppo? **Annulla l'ultima**.
4. Senza lettore usa **−** e **+**, oppure scrivi il numero: si salva da solo.

## Spedire

1. Quando hai tutto, il pulsante in basso dice **Controlla e spedisci**. Se mancano pezzi dice **Spedisci comunque: mancano 3** e ti chiede **Perché mancano pezzi?**, per esempio «non trovati a scaffale».
2. Si apre il controllo: quanti pezzi escono e da quali negozi Shopify (la cassa e il sito, che hanno la stessa merce). Sono esattamente quelli che verranno scritti.
3. Premi **Conferma e aggiorna Shopify**, una volta sola: il secondo clic non fa nulla.
   - Se dopo il controllo qualcuno ha letto un altro pezzo, la conferma viene rifiutata: ricontrolla e conferma di nuovo.
   - Se nel frattempo è stato venduto un pezzo, il programma non spedisce e ti dice quale riga ridurre.
4. In fondo alla pagina, **Shopify aggiornato: 11 di 11** vuol dire che è tutto a posto. Se qualcosa non va, lo vede il responsabile: tu puoi andare avanti.

## Ricevere

1. In **Da fare**, sotto «Da ricevere», premi **Ricevi**. I numeri sono già compilati come «arrivato tutto» e le righe sono in ordine di scaffale del tuo negozio.
2. Correggi solo quello che manca o è rotto. In alternativa premi **Conta con il lettore**: i buoni ripartono da zero e ogni pezzo sparato aggiunge 1.
3. Premi **Controlla e registra**, controlla e conferma. Si possono registrare più colli in momenti diversi.
4. Se non arriverà altro, apri **Non arriverà altro? Chiudi la ricezione** e scrivi cosa manca: le differenze le chiude il responsabile.

## Un movimento fatto su Shopify

Se qualcuno ha registrato su Shopify un'uscita senza destinazione o un arrivo dal fornitore, in **Da fare** compare «Movimenti fatti su Shopify da sistemare». Li trovi anche in **Trasferimenti → Fatti su Shopify**.

**Uscita senza destinazione.**
1. Premi **Scegli dove è andata**: il programma suggerisce il negozio e spiega perché.
2. Rispondi a **È già arrivata?**: «Sì, carica subito», oppure «No, è in viaggio» per contarla all'arrivo.
3. Premi **Controlla prima di confermare**, guarda i movimenti e conferma.

Se non sai dov'è finita la merce, non scegliere a caso: chiedi al responsabile.

**Arrivo dal fornitore.** Premi **Decidi** e rispondi a «Dove va questa merce?»:
- **Tutto a questo negozio**: è il caso normale.
- **Dividi con l'altro negozio**: scegli quanto va di là (per esempio 1/2) e dove va il pezzo dispari. La tabella si ricalcola da sola; se serve, correggi i numeri riga per riga.
  - Se la merce per l'altro negozio va ancora portata, nasce un trasferimento da preparare.
  - Se è già lì, si carica subito.
- **Stessa consegna già registrata**: lo stesso carico inserito due volte, una per canale. Il programma te lo segnala come «possibile doppione» e, dopo il primo, ti dice «Ora sistema #T0106».

## Se qualcosa non va

| Messaggio | Cosa fare |
|---|---|
| «Azione già eseguita (doppio clic?)» | niente: era già fatto, la pagina mostra lo stato attuale |
| «Modificato da qualcun altro» | ricontrolla e ripeti: nel frattempo qualcuno ha cambiato il trasferimento (anche solo leggendo un pezzo) |
| «Le giacenze dell'origine sono cambiate» | controlla lo scaffale e riduci le righe indicate |
| «Spedizione bloccata: le modifiche alle giacenze su Shopify sono disattivate» | la spedizione dal programma non è ancora accesa: avvisa il responsabile |
| «Pagina scaduta: ricaricala e ripeti» | ricarica la pagina; le letture già fatte restano |
| Etichetta rossa in alto | un negozio non risponde: premi **Rileggi i negozi**; se resta, avvisa il responsabile |
