# VCOMPETITION AEREA 2027 — portale autonomo

Questo progetto è separato da VFIT: non modifica né richiede il repository VFIT.

## Funzioni incluse
- Modulo pubblico per scuole e singoli atleti.
- Esibizioni Assolo e Duo; Bungee Dance e Pole Dance solo Assolo.
- Quote: Assolo 60 €, Duo 80 € totali.
- Pagamento successivo o bonifico; caricamento facoltativo della ricevuta.
- Archivio SQLite e pannello organizzazione protetto da password.
- Notifica email a `vcompetitioneventi@gmail.com` quando SMTP è configurato.

## Avvio locale
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export ADMIN_PASSWORD='scegli-una-password-lunga'
uvicorn app:app --reload
```
Aprire http://127.0.0.1:8000 . Pannello: /gestione.

## Pubblicazione su Render
Creare un NUOVO Web Service (non collegarlo al servizio VFIT) da un nuovo repository GitHub contenente questi file. Render può usare `render.yaml`. Configurare le variabili SMTP e ADMIN_PASSWORD. Per conservare database e ricevute tra i deploy, collegare un Persistent Disk montato su `/var/data` e impostare `DATABASE_PATH=/var/data/vcompetition.db`, `UPLOAD_DIR=/var/data/uploads`.

## Prima di aprire le iscrizioni
- Configurare e verificare l'invio email SMTP.
- Configurare un archivio persistente per DB e ricevute.
- Pubblicare informativa privacy completa, titolare del trattamento, tempi di conservazione e base giuridica; il checkbox presente è solo un segnaposto e non sostituisce l'informativa.
- Predisporre il flusso corretto di autorizzazioni per minori e consenso foto/video separato.
- Comunicare i dati IBAN ufficiali: al momento il sito indica che saranno forniti dall'organizzazione dopo l'iscrizione. Non sono stati inventati.
- Verificare importi e regolamento definitivo prima dell'apertura.

Nota: il modulo non include tessera OPES e non prevede iscrizione Gruppi, coerentemente con le indicazioni ricevute e la bozza regolamento disponibile.
