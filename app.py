import os, sqlite3, secrets, smtplib, ssl, json
from email.message import EmailMessage
from datetime import datetime, date
from pathlib import Path
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from mutagen.mp3 import MP3

BASE=Path(__file__).parent
DB=os.getenv('DATABASE_PATH',str(BASE/'vcompetition.db'))
UPLOADS=Path(os.getenv('UPLOAD_DIR',str(BASE/'uploads')))
UPLOADS.mkdir(parents=True,exist_ok=True)
ALLOWED={'.pdf','.jpg','.jpeg','.png'}
MAX_UPLOAD=8*1024*1024
MUSIC_DIR=UPLOADS/'music'
MUSIC_DIR.mkdir(parents=True,exist_ok=True)
MAX_MUSIC=20*1024*1024
MAX_MUSIC_SECONDS=160
app=FastAPI(title='VCOMPETITION AEREA 2027')
templates=Jinja2Templates(directory=str(BASE/'templates'))
app.mount('/static',StaticFiles(directory=str(BASE/'static')),name='static')
SPECIALTIES=['Tessuto Aereo','Cerchio Aereo','Amaca','Palo Aereo','Bungee Dance','Pole Dance','Arti Varie']
AGES=['Baby (4–7)','Kids (8–10)','Teen (11–13)','Juniores (14–16)','Seniores (17–25)','Over 26']
LEVELS=['Principiante','Intermedio','Avanzato','Istruttore']
AGE_RANGES={'Baby (4–7)':(4,7),'Kids (8–10)':(8,10),'Teen (11–13)':(11,13),'Juniores (14–16)':(14,16),'Seniores (17–25)':(17,25),'Over 26':(27,150)}
EVENT_DATE=date(2027,1,30)

def db():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row
 c.execute('''CREATE TABLE IF NOT EXISTS registrations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,created TEXT,kind TEXT,school TEXT,contact TEXT,
 email TEXT,phone TEXT,athlete_names TEXT,performances TEXT,amount INTEGER,
 payment_method TEXT,receipt TEXT,notes TEXT,status TEXT DEFAULT 'Da verificare')''')
 c.commit();return c

def notify(subject,body):
 host=os.getenv('SMTP_HOST');user=os.getenv('SMTP_USER');password=os.getenv('SMTP_PASSWORD');sender=os.getenv('SMTP_FROM') or user
 if not all([host,user,password,sender]):return False
 msg=EmailMessage();msg['Subject']=subject;msg['From']=sender;msg['To']='vcompetitioneventi@gmail.com';msg.set_content(body)
 try:
  with smtplib.SMTP(host,int(os.getenv('SMTP_PORT','587')),timeout=15) as s:
   s.starttls(context=ssl.create_default_context());s.login(user,password);s.send_message(msg)
  return True
 except Exception:return False

@app.get('/',response_class=HTMLResponse)
def home(request:Request):
 return templates.TemplateResponse('index.html',{'request':request,'specialties':SPECIALTIES,'ages':AGES,'levels':LEVELS})

@app.post('/iscrizione')
async def register(request:Request,kind:str=Form(...),school:str=Form(''),contact:str=Form(...),email:str=Form(...),phone:str=Form(...),athlete_names:str=Form(''),performance_data:str=Form(...),payment_method:str=Form(...),notes:str=Form(''),receipt:UploadFile|None=File(None),music_files:list[UploadFile]=File(...),privacy:str=Form(...),rules:str=Form(...)):
 if privacy!='yes' or rules!='yes':raise HTTPException(400,'Consensi obbligatori mancanti')
 if kind not in ('Scuola','Singolo atleta'):raise HTTPException(400,'Tipologia non valida')
 if payment_method not in ('successivo','bonifico'):raise HTTPException(400,'Metodo di pagamento non valido')
 try:performances=json.loads(performance_data)
 except Exception:raise HTTPException(400,'Dati esibizioni non validi')
 if not isinstance(performances,list) or not performances:raise HTTPException(400,'Inserisci almeno una esibizione')
 if len(music_files)!=len(performances):raise HTTPException(400,'Carica un file MP3 per ogni esibizione')
 total=0
 for p in performances:
  if not isinstance(p,dict):raise HTTPException(400,'Dati esibizione non validi')
  section=p.get('section');specialty=p.get('specialty')
  if section not in ('Assolo','Duo') or specialty not in SPECIALTIES:raise HTTPException(400,'Sezione o specialità non valida')
  if specialty=='Bungee Dance' and section!='Assolo':raise HTTPException(400,'Questa specialità è ammessa solo in Assolo')
  if p.get('category') not in AGES or p.get('level') not in LEVELS:raise HTTPException(400,'Categoria o livello non valido')
  athletes=p.get('athletes');expected=1 if section=='Assolo' else 2
  if not isinstance(athletes,list) or len(athletes)!=expected:raise HTTPException(400,'Inserisci i dati di ogni atleta')
  low,high=AGE_RANGES[p['category']]
  for athlete in athletes:
   if not isinstance(athlete,dict) or not str(athlete.get('name','')).strip() or not athlete.get('dob'):raise HTTPException(400,'Nome e data di nascita sono obbligatori per ogni atleta')
   try:born=date.fromisoformat(athlete['dob'])
   except (TypeError,ValueError):raise HTTPException(400,'Data di nascita non valida')
   if born>EVENT_DATE:raise HTTPException(400,'La data di nascita non può essere successiva alla gara')
   years=EVENT_DATE.year-born.year-((EVENT_DATE.month,EVENT_DATE.day)<(born.month,born.day))
   if not low<=years<=high:raise HTTPException(400,'La data di nascita non corrisponde alla categoria selezionata')
  total+=60 if section=='Assolo' else 80
 music_names=[]
 for idx,music in enumerate(music_files):
  if not music.filename or Path(music.filename).suffix.lower()!='.mp3':
   raise HTTPException(400,'Sono ammessi esclusivamente file MP3')
  audio_data=await music.read()
  if not audio_data or len(audio_data)>MAX_MUSIC:
   raise HTTPException(400,'Il file MP3 è vuoto o supera 20 MB')
  temp_path=MUSIC_DIR/f'{secrets.token_hex(16)}.mp3'
  temp_path.write_bytes(audio_data)
  try:
   duration=MP3(temp_path).info.length
  except Exception:
   temp_path.unlink(missing_ok=True)
   raise HTTPException(400,'Uno dei file caricati non è un MP3 valido')
  if duration>MAX_MUSIC_SECONDS:
   temp_path.unlink(missing_ok=True)
   raise HTTPException(400,'La musica non può superare 2 minuti e 40 secondi (2:30 + 10 secondi di tolleranza)')
  music_names.append(temp_path.name)
  performances[idx]['music_file']=temp_path.name
 receipt_name=''
 if receipt and receipt.filename:
  ext=Path(receipt.filename).suffix.lower()
  if ext not in ALLOWED:raise HTTPException(400,'Formato ricevuta non ammesso')
  data=await receipt.read()
  if len(data)>MAX_UPLOAD:raise HTTPException(400,'La ricevuta supera 8 MB')
  receipt_name=f'{secrets.token_hex(16)}{ext}';(UPLOADS/receipt_name).write_bytes(data)
 conn=db();cur=conn.execute('INSERT INTO registrations(created,kind,school,contact,email,phone,athlete_names,performances,amount,payment_method,receipt,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(datetime.now().isoformat(timespec='minutes'),kind,school,contact,email,phone,athlete_names,json.dumps(performances,ensure_ascii=False),total,payment_method,receipt_name,notes))
 rid=cur.lastrowid;conn.commit();conn.close()
 body=f'''Nuova iscrizione VCOMPETITION AEREA 2027
Numero: {rid}
Tipo: {kind}
Scuola: {school}
Referente: {contact}
Email: {email}
Telefono: {phone}
Atleti (riepilogo): {athlete_names}
Esibizioni: {json.dumps(performances,ensure_ascii=False)}
Totale quote: {total} EUR
Pagamento: {payment_method}
Musiche MP3 salvate: {len(music_names)}
Ricevuta caricata: {'Sì' if receipt_name else 'No'}
Note: {notes}'''
 sent=notify(f'Nuova iscrizione VCOMPETITION #{rid}',body)
 return templates.TemplateResponse('success.html',{'request':request,'rid':rid,'total':total,'sent':sent})

@app.get('/gestione',response_class=HTMLResponse)
def login_page(request:Request):
 return templates.TemplateResponse('login.html',{'request':request})

@app.post('/gestione',response_class=HTMLResponse)
def admin(request:Request,password:str=Form(...)):
 expected=os.getenv('ADMIN_PASSWORD')
 if not expected or not secrets.compare_digest(password,expected):raise HTTPException(401,'Password non corretta o ADMIN_PASSWORD non configurata')
 c=db();rows=c.execute('SELECT * FROM registrations ORDER BY id DESC').fetchall();c.close()
 return templates.TemplateResponse('admin.html',{'request':request,'rows':rows})
