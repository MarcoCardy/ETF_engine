# Dashboard locale per analisi ETF v1.0

Data approvazione: 2026-09-04. Software di ricerca, non autorità di investimento o negoziazione.

## Obiettivo

Fornire un'applicazione personale avviabile dal desktop che apra nel browser una pagina locale e permetta, senza comandi tecnici, di:

1. consultare e modificare il portafoglio principale;
2. ripristinare il portafoglio predefinito `60% SWDA / 15% IWMO / 15% IWQU / 10% DBMFE`;
3. aggiungere un ETF tramite simbolo di borsa oppure ISIN, confermandone l'identità;
4. aggiornare i dati soltanto su richiesta;
5. confrontare storicamente un singolo ETF con il portafoglio corrente;
6. studiare un ETF come portafoglio autonomo al 100%;
7. visualizzare e scaricare rendimenti, volatilità, perdite e contributi.

La prima versione riguarda esclusivamente le analisi storiche. La previsione Chronos resta visibile come sviluppo successivo, ma non è attiva.

## Scelta tecnica

L'interfaccia usa Streamlit ed è servita esclusivamente su `127.0.0.1`. Streamlit è l'unica nuova dipendenza applicativa: fornisce moduli, tabelle, grafici e download senza introdurre un server web o componenti grafici costruiti su misura.

Un lanciatore Windows conservato nel progetto usa l'interprete dell'ambiente virtuale esistente, avvia l'app e apre il browser predefinito. L'installazione crea un collegamento sul desktop. Un errore di avvio deve lasciare visibile un messaggio comprensibile e non modificare dati o configurazioni.

L'interfaccia è un adattatore sottile sopra `perpetual_engine.portfolio_monitor`: aggiornamenti, validazioni, calcoli e pubblicazione transazionale dei risultati continuano a risiedere nel motore esistente. Non viene duplicata la logica finanziaria nell'app.

## Avvio e comportamento offline

All'avvio l'app legge soltanto i dati e i risultati già salvati. Non effettua richieste di rete e mostra chiaramente:

- data e ora dell'ultimo aggiornamento riuscito;
- periodo storico comune disponibile;
- eventuale assenza o insufficienza dei dati.

La rete è usata soltanto da azioni esplicite dell'utente: `Aggiorna dati` e ricerca di un nuovo ETF. Un aggiornamento fallito non sostituisce mai l'ultimo insieme di dati valido.

## Navigazione

La barra laterale contiene quattro sezioni.

### Portafoglio

Mostra strumenti, simboli, ISIN, mercati, valute e pesi del portafoglio corrente. L'utente può aggiungere o rimuovere righe e modificare i pesi. Il salvataggio è consentito soltanto quando:

- ogni peso è numerico, finito e maggiore di zero;
- ogni strumento ha un'identità già confermata;
- non esistono simboli o ISIN duplicati;
- la somma dei pesi è 100%, entro una tolleranza di arrotondamento di `0,01` punti percentuali.

Il comando `Ripristina portafoglio predefinito` richiede conferma e ricrea esattamente:

- `SWDA.MI`, ISIN `IE00B4L5Y983`, peso 60%;
- `IWMO.MI`, ISIN `IE00BP3QZ825`, peso 15%;
- `IWQU.MI`, ISIN `IE00BP3QZ601`, peso 15%;
- `DBMFE.PA`, ISIN `LU2951555403`, peso 10%.

Il portafoglio predefinito rimane immutabile; il ripristino ne copia i valori nello stato corrente.

### ETF

Mostra il catalogo degli ETF già confermati. Una casella accetta un simbolo, per esempio `GRID.MI`, oppure un ISIN. La ricerca restituisce, quando disponibili, nome, simbolo, ISIN, borsa e valuta.

L'app non salva automaticamente il primo risultato. Se esiste una sola corrispondenza valida, ne mostra comunque il riepilogo per conferma; se le corrispondenze sono più di una, l'utente sceglie la quotazione esatta. Una risposta priva di simbolo, mercato o valuta, una mancata corrispondenza o un contrasto con un ISIN ufficiale già configurato bloccano il salvataggio con una spiegazione.

Aggiungere un ETF al catalogo non cambia il portafoglio. La rimozione dal catalogo è bloccata mentre l'ETF è usato dal portafoglio corrente.

### Confronti

Permette di scegliere un ETF confermato e confrontarlo con il portafoglio corrente. Il confronto usa esclusivamente mesi completi condivisi da tutte le serie necessarie e mostra sempre le date iniziale e finale.

La schermata comprende:

- crescita normalizzata di 100 euro;
- rendimento cumulato;
- rendimento annualizzato, accompagnato dalla durata del campione;
- volatilità annualizzata;
- volatilità mobile a 12 mesi, assente finché non esistono 12 rendimenti mensili;
- drawdown nel tempo e drawdown massimo;
- correlazione mensile con il portafoglio;
- tabella dei rendimenti mensili e conteggio dei mesi vinti da ciascun lato;
- contributo mensile e cumulato di ogni componente al rendimento del portafoglio.

Per studiare un ETF autonomamente, l'utente può selezionare la modalità `Portafoglio singolo (100%)`. Il benchmark di confronto resta il portafoglio principale e non viene alterato.

I grafici mostrano esplicitamente quando il campione è breve. Non vengono presentati rendimento annualizzato, correlazione o decorrelazione come risultati stabili senza un periodo adeguato. I dati sottostanti e la tabella riepilogativa sono scaricabili in CSV.

### Previsioni Chronos

La prima versione mostra lo stato `Non ancora attivo` e spiega che la previsione sarà aggiunta dopo la verifica delle analisi storiche. Non avvia modelli e non presenta risultati simulati.

L'evoluzione prevista riutilizzerà gli stessi strumenti e dati verificati, aggiungendo i percorsi del tasso sui depositi BCE e le covariate già definite dal progetto: Treasury USA decennale espresso in percentuale, inflazione USA, petrolio e proxy della liquidità mondiale. Ogni nuova serie sarà sottoposta a valutazione del contributo predittivo e il sistema conserverà la differenza tra previsione emessa e valore realizzato.

## Stato e file persistenti

`config/portfolio_p_v1.json` resta la sorgente versionata del portafoglio predefinito e delle identità ufficiali. Al primo avvio l'app ne crea una copia operativa sotto `data/dashboard_v1/`; le modifiche dell'utente riguardano soltanto questa copia.

La copia operativa contiene portafoglio corrente e catalogo ETF. Ogni salvataggio viene prima validato, scritto in un file temporaneo e poi sostituito atomicamente. Un file corrotto non viene accettato: l'app segnala il problema e offre il ripristino dal predefinito senza cancellare automaticamente il file problematico.

I prezzi, i manifesti e i report continuano a usare le directory e i formati già prodotti dal monitor di portafoglio. L'app non introduce un database e non converte gli archivi esistenti.

## Aggiornamento dati

Il pulsante globale `Aggiorna dati`:

1. usa la configurazione operativa validata;
2. aggiorna tutti gli strumenti del portafoglio e del catalogo;
3. mostra avanzamento e strumento corrente;
4. pubblica il nuovo insieme solo dopo il completamento delle verifiche esistenti;
5. aggiorna il timestamp mostrato soltanto dopo una pubblicazione riuscita;
6. ricarica analisi e grafici dai nuovi dati.

Durante l'operazione i comandi che cambiano portafoglio o catalogo sono disabilitati. In caso di errore, l'app riporta lo strumento e la causa disponibile, mantiene i dati precedenti e consente di riprovare.

## Flusso dei calcoli

```text
Portafoglio corrente + catalogo confermato
                  |
                  v
       Aggiornamento esplicito dati
                  |
                  v
       Vintage validato e immutabile
                  |
                  v
   ETF scelto + portafoglio corrente
                  |
                  v
 Report storico comune -> grafici + CSV
```

I risultati di un confronto sono identificati dagli hash della configurazione, del vintage e dell'ETF selezionato. Un cambiamento dei pesi non modifica un report precedente: produce un nuovo risultato oppure riusa quello già esistente con identici input.

## Errori e messaggi

I messaggi destinati all'utente usano linguaggio operativo: cosa non è disponibile, quale dato è coinvolto e quale azione è possibile. I dettagli tecnici restano in un pannello espandibile utile alla diagnosi.

L'app deve gestire senza perdere stato almeno:

- ambiente virtuale o dipendenze mancanti;
- porta locale già occupata;
- assenza di rete;
- simbolo o ISIN sconosciuto o ambiguo;
- valuta o identità incoerente;
- pesi non validi;
- storico insufficiente o senza periodo comune;
- download incompleto, manifesti non validi e report non leggibili.

## Verifica

Lo sviluppo segue test-first. I controlli minimi coprono:

- creazione, validazione, salvataggio atomico e ripristino dello stato operativo;
- vincoli sui pesi e protezione del portafoglio predefinito;
- ricerca e conferma tramite simbolo e ISIN, incluse ambiguità e incoerenze;
- assenza di rete all'avvio;
- conservazione dei dati precedenti dopo un aggiornamento fallito;
- intervallo mensile comune e metriche mostrate nei confronti;
- modalità ETF singolo al 100% senza modifica del portafoglio principale;
- rendering delle quattro sezioni e download CSV mediante il supporto di test di Streamlit;
- avvio locale vincolato a `127.0.0.1`;
- regressione dell'intera suite esistente.

La verifica manuale finale parte da un avvio pulito tramite collegamento desktop, usa i dati salvati, modifica e ripristina il portafoglio, aggiunge un ETF di prova, aggiorna i dati e genera un confronto completo.

## Criteri di accettazione

La prima versione è completa quando l'utente può, senza terminale:

1. aprire l'app dall'icona sul desktop;
2. vedere immediatamente dati salvati, copertura e ultimo aggiornamento;
3. modificare, salvare e ripristinare il portafoglio;
4. aggiungere un ETF tramite simbolo o ISIN dopo averne confermato l'identità;
5. aggiornare i dati soltanto premendo il comando dedicato;
6. confrontare un ETF o un portafoglio monostrumento con il portafoglio principale;
7. leggere grafici e metriche di rendimento, volatilità, drawdown, correlazione e contributo;
8. scaricare i risultati in CSV;
9. ritrovare intatti portafoglio e dati dopo la chiusura e la riapertura.

## Esclusioni della v1

- Nessuna previsione Chronos attiva.
- Nessun trading, collegamento a broker o modifica automatica dei pesi.
- Nessun aggiornamento automatico all'avvio o pianificato.
- Nessuna esposizione dell'app alla rete locale o a Internet: è raggiungibile soltanto dal computer su cui viene avviata.
- Nessun account utente, database o sincronizzazione cloud aggiuntiva.
- Nessuna ottimizzazione automatica del portafoglio.
- Nessun nuovo formato di report oltre ai CSV e agli artefatti già esistenti.
