# Manuale utente — Analisi ETF e Chronos

## 1. Che cosa fa il programma

### 1.1 Analisi storica

L’app confronta un singolo ETF con il portafoglio salvato oppure lo studia come portafoglio al 100%. Mostra andamento cumulato, rendimento, volatilità, drawdown, correlazione e contributi dei componenti. Usa soltanto osservazioni comuni realmente disponibili e non ricostruisce artificialmente la storia precedente al lancio di un ETF.

### 1.2 Previsioni Chronos

La sezione **Previsioni Chronos** contiene il modulo giornaliero di rischio. Premendo **Genera report completo** si ottengono distribuzioni di rendimento e volatilità realizzata futura a 5, 10 e 20 sedute, con Q10, Q25, Q50, Q75 e Q90. La previsione usa i prezzi e le serie economiche già salvati e non avvia un aggiornamento dati.

Il precedente motore Chronos mensile per WORLD, MOMENTUM, QUALITY e TREND rimane separato e compatibile. I suoi strumenti avanzati di scenario BCE, confronto fra ETF, riconciliazione previsto/reale e valutazione delle variabili non modificano il portafoglio.

### 1.3 Limiti e uso esclusivamente di ricerca

Chronos opera in modalità **SHADOW**: produce informazioni e test, ma non modifica pesi, non esegue ordini e non mostra raccomandazioni BUY/SELL. I quantili sono quantili previsivi, non intervalli di confidenza classici. Il programma non ricava probabilità di drawdown da Q10/Q50/Q90, perché questi valori non costituiscono percorsi Monte Carlo coerenti.

## 2. Avvio e chiusura

### 2.1 Avvio dal desktop

Fare doppio clic su **Analisi ETF** oppure su `Avvia Analisi ETF.cmd`. Si apre una pagina locale. Attendere che compaiano il titolo **Analisi ETF**, lo stato dei dati e il menu laterale.

### 2.2 Chiusura della pagina e del programma

Chiudere la scheda del browser non cancella dati o configurazioni. Per arrestare anche il servizio locale, chiudere la finestra del lanciatore. Un eventuale calcolo avanzato avviato come job in background conserva il proprio stato; il pulsante giornaliero di rischio, invece, termina il calcolo prima di mostrare il risultato.

## 3. Primo utilizzo guidato

### 3.1 Controllare lo stato dei dati

Nella parte superiore verificare data di acquisizione e intervallo storico. Se lo stato indica dati mancanti, le funzioni che richiedono prezzi vengono disabilitate. Un dato salvato non viene aggiornato automaticamente.

### 3.2 Aggiornare prezzi e dati economici

Premere **Aggiorna dati** soltanto quando si desiderano nuove quotazioni. Premere **Aggiorna dati economici** per acquisire una nuova vintage di ERP Damodaran, Treasury nominale e reale, breakeven, VIX, EUR/USD e delle altre serie configurate. I due aggiornamenti sono separati; generare un report non usa Internet. Se un’operazione fallisce, il programma conserva l’ultima versione valida. Non chiudere il lanciatore durante l’aggiornamento.

### 3.3 Generare la prima previsione

Aprire **Previsioni Chronos** e premere **Genera report completo**. Il modello locale può impiegare alcuni secondi su CPU. Leggere prima Q50 e Q90 della volatilità a 20 giorni, poi confrontarle con sigma20, sigma60, EWMA e sigmaForecast. Il riquadro beta mostra separatamente il calcolo di produzione e quello Chronos sperimentale.

## 4. Portafoglio base e portafoglio da studiare

### 4.1 Composizione base non modificabile

La configurazione predefinita ripristinabile è 60% SWDA, 15% IWMO, 15% IWQU e 10% DBMFE. Il ripristino non cancella gli ETF aggiunti al catalogo. Lo snapshot patrimoniale del 17 agosto 2026 è separato dai dati usati per addestrare o valutare Chronos.

### 4.2 Selezionare quattro ETF

Nella sezione **Portafoglio** è possibile sostituire i componenti con ETF già presenti nel catalogo e impostare nuovi pesi. La somma deve essere 100%. Il portafoglio base resta disponibile come configurazione di ripristino.

### 4.3 Inserire simbolo o ISIN

Nella sezione **ETF** inserire un simbolo di borsa oppure un ISIN valido. Il programma accetta soltanto strumenti identificati come ETF, con quotazione in EUR e identità coerente. Quotazioni diverse dello stesso fondo possono essere distinte dal mercato di negoziazione.

### 4.4 Impostare e salvare i pesi

Modificare la colonna **Peso %**, controllare che non vi siano valori nulli o negativi e salvare. Una configurazione non valida non sostituisce quella precedente.

### 4.5 Ripristinare la composizione predefinita

Usare **Ripristina portafoglio predefinito** e confermare. La composizione torna a 60/15/15/10; il catalogo degli ETF aggiunti resta disponibile.

## 5. Aggiornamento dei dati Chronos

### 5.1 Dati dei prezzi

Il modulo giornaliero usa adjusted close: rendimenti, split e distribuzioni dipendono quindi dalla qualità della serie fornita. Controlla ordine, duplicati, valori mancanti, non finiti e prezzi non positivi. Gli orizzonti sono espressi in sedute osservate, non in giorni di calendario.

### 5.2 Tasso BCE, Treasury USA, inflazione USA, petrolio e liquidità

Il motore mensile dispone di tasso sui depositi BCE, Treasury USA decennale nominale, Brent, liquidità BIS e inflazione USA. I valori vengono resi disponibili solo dopo la data di pubblicazione configurata e poi mantenuti costanti fino alla nuova osservazione; non vengono interpolati nel futuro.

Il report giornaliero carica, quando disponibili alla data di cutoff, VIX, tassi nominali e reali, breakeven, EUR/USD, ERP, CPI, Brent, liquidità BIS e serie relative di Momentum, Quality e Trend. La tabella **Serie economiche disponibili** indica per ciascuna serie categoria, frequenza, prima e ultima data, numero di osservazioni, ritardo di pubblicazione e idoneità point-in-time. Un’avvertenza nel report segnala serie mancanti o non utilizzabili.

Le vintage ricostruite rispettano le date di pubblicazione configurate e il carry-forward dell’ultima informazione disponibile, senza interpolazione. Per fonti che non offrono vere vintage storiche, questa è una ricostruzione prudente ma non elimina il rischio delle revisioni retroattive; la limitazione rimane visibile nei metadati.

### 5.3 Cosa accade in caso di errore

Un download, un hash o uno schema non valido interrompe la pubblicazione. L’archivio precedente rimane leggibile. Aprire **Dettagli tecnici** solo se serve comunicare l’errore; non correggere manualmente i file dentro una vintage.

## 6. Generazione e lettura delle previsioni

### 6.1 Scenari BCE invariato, discesa e rialzo

Il flusso mensile avanzato produce ECB_FLAT, ECB_DOWN_100BP ed ECB_UP_100BP. Sono scenari ipotetici, non valori futuri conosciuti. Il modulo giornaliero di rischio corrente non introduce tassi futuri come se fossero osservazioni certe.

### 6.2 Previsioni dei singoli ETF

Il flusso diretto può costruire una previsione separata per ciascun ETF. **Genera previsione** è parte del workflow avanzato conservato nel motore; la pagina corrente espone anzitutto la previsione di rischio SWDA/MSCI World, perché è il bersaglio principale del controllo di portafoglio.

### 6.3 Percorsi del portafoglio da studiare e del portafoglio base

I percorsi centrali q50 dei singoli ETF possono essere aggregati con i pesi del portafoglio candidato e confrontati con la traccia del portafoglio base. Non vengono create bande Q10/Q90 di portafoglio sommando quantili marginali, perché tale operazione non descriverebbe correttamente la dipendenza fra strumenti.

### 6.4 Significato di q10, q50 e q90

Q10 è una soglia di coda bassa: idealmente il risultato reale è inferiore a Q10 circa il 10% delle volte. Q50 è la mediana. Q90 è una soglia conservativa di volatilità, non una certezza né il “peggior caso”. Q25 e Q75 aiutano a leggere la dispersione centrale.

### 6.5 Storico breve e assenza della banda di portafoglio

`STORICO_BREVE` o `INSUFFICIENT_HISTORY` indicano che non esistono abbastanza osservazioni per un confronto affidabile. DBMFE, per esempio, ha una storia molto più corta di SWDA. L’assenza della banda probabilistica aggregata è intenzionale e protegge da una falsa precisione.

## 7. Valutazione delle variabili

### 7.1 Avvio del calcolo in background

Premendo **Avvia valutazione ablation** l’app crea un job Windows separato e registra richiesta, modello, dati e risultato. Il browser può essere chiuso mentre il calcolo continua. Il job confronta gruppi di covariate nel walk-forward e riporta anche i p-value mese per mese; un p-value non sostituisce la dimensione dell’effetto né la verifica fuori campione.

### 7.2 Chiusura e riapertura della pagina

Un job avanzato già avviato conserva lo stato anche se la pagina viene chiusa. Alla riapertura non avviare un duplicato: controllare prima STARTING, RUNNING, SUCCEEDED, FAILED oppure INTERRUPTED.

### 7.3 Utile, dannosa, inconcludente e storico insufficiente

`USEFUL` significa che la variabile riduce la perdita fuori campione nel confronto definito; `HARMFUL` la aumenta; `INCONCLUSIVE` indica che l’incertezza include zero; `INSUFFICIENT_HISTORY` impedisce una classificazione. Un risultato utile su pochi punti non autorizza l’uso in produzione.

### 7.4 Riavvio di un calcolo interrotto

Se compare INTERRUPTED, usare **Riprova valutazione** nel workflow avanzato. Un archivio SUCCEEDED non viene sovrascritto; una richiesta identica può riutilizzare il risultato verificato.

## 8. Volatilità e qualità delle previsioni

### 8.1 Volatilità storica

sigma20 e sigma60 sono deviazioni standard campionarie degli ultimi 20 e 60 rendimenti giornalieri, annualizzate con radice di 252. sigmaForecast è 35% sigma20 + 65% sigma60. EWMA usa lambda 0,94. sigmaRisk sperimentale usa 25% sigma20 + 35% sigma60 + 40% Chronos Q50 oppure Q90.

### 8.2 Ampiezza degli intervalli

La differenza Q90−Q10 misura la dispersione della previsione. La calibrazione confronta la copertura empirica con il livello nominale: per esempio Q90 dovrebbe contenere circa il 90% dei risultati. Una copertura scadente vieta l’uso della previsione come controllo operativo.

### 8.3 Differenza tra previsto e reale

Per ogni origine walk-forward vengono salvati cutoff, data finale, valori previsti e valori reali. L’errore con segno è previsto meno reale nelle metriche giornaliere; MAE e RMSE ne misurano l’ampiezza. Nel monitor mensile già esistente la riconciliazione conserva esplicitamente la convenzione usata nel file.

## 9. Esportazione dei risultati

### 9.1 File CSV disponibili

Il walk-forward giornaliero crea `predictions.csv`, con quantili, risultati reali e baseline. `metrics.json` contiene MAE, RMSE, bias, correlazioni, sign accuracy, pinball, coperture e QLIKE. Il report operativo aggiunge confronto beta shadow, metriche storiche di utilità del portafoglio, catalogo economico e stato delle covariate. L’ablation in background pubblica i contributi delle serie e i p-value mensili. I confronti storici dell’app offrono i propri CSV scaricabili.

### 9.2 Manifesti e provenienza dei dati

Ogni valutazione contiene `manifest.json` con versione del dataset, hash della configurazione, orizzonti, quantili e hash dei file generati. Non modificare un archivio pubblicato: una modifica provoca un errore di collisione o di hash.

## 10. Risoluzione dei problemi

### 10.1 Modello non disponibile

Chronos-2 deve essere presente nella cache locale. Se manca, il calcolo non parte. Il programma non scarica automaticamente il modello durante una previsione offline.

### 10.2 Dati mancanti o non aggiornati

Premere **Aggiorna dati** e attendere la nuova data di acquisizione. Se l’aggiornamento fallisce, continuare a usare l’ultima vintage valida oppure consultare i dettagli tecnici.

### 10.3 Simbolo o ISIN non riconosciuto

Controllare mercato, valuta EUR e forma dell’ISIN. Provare il simbolo completo di suffisso, per esempio `.MI` o `.PA`. Non forzare manualmente un’identità non verificata.

### 10.4 Pesi non validi

Verificare che ogni peso sia positivo e che il totale sia 100%. Ripristinare il portafoglio predefinito se la configurazione salvata è corrotta.

### 10.5 Storico insufficiente

Ridurre le aspettative, non i controlli: un ETF recente non può dimostrare comportamento nel 2008 o nel 2020. Attendere nuove osservazioni oppure usare un proxy separato e chiaramente etichettato.

### 10.6 Valutazione interrotta

Controllare lo stato del job e il file di log. Riavviare soltanto tramite **Riprova valutazione**. Se il problema riguarda memoria o tempo CPU, ridurre il numero di origini, non la correttezza temporale.

## 11. Glossario

- **Adjusted close:** prezzo rettificato usato per calcolare rendimenti compatibili con eventi societari.
- **Ablation:** confronto che aggiunge o rimuove gruppi di variabili per misurarne l’apporto fuori campione.
- **Cutoff:** ultima informazione disponibile al momento della previsione.
- **Drawdown:** perdita percentuale rispetto al precedente massimo del capitale.
- **ERP:** premio per il rischio azionario implicito.
- **EWMA:** volatilità che assegna più peso alle osservazioni recenti.
- **Pinball loss:** perdita usata per valutare un quantile probabilistico.
- **Point-in-time:** dato reso disponibile solo dalla sua reale data di pubblicazione.
- **Q10/Q25/Q50/Q75/Q90:** quantili della distribuzione prevista.
- **QLIKE:** metrica di errore adatta al confronto fra previsioni di volatilità.
- **Shadow mode:** modello osservato e valutato senza effetto automatico sulle decisioni.
- **sigmaRisk:** combinazione sperimentale di volatilità recente, strutturale e Chronos.
- **Walk-forward:** valutazione temporale che addestra/contestualizza fino a T, prevede il futuro e poi avanza.
