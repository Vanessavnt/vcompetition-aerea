from bson import ObjectId
from fastapi import FastAPI, APIRouter, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import random
import secrets
import string
import hashlib
from pathlib import Path
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Literal, Any
import uuid
from datetime import datetime, timedelta, timezone, date
import bcrypt
import jwt
import httpx
import html as _html
from collections import defaultdict
import time as _time


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Mongo
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

JWT_SECRET = os.environ['JWT_SECRET']
JWT_EXPIRE_MINUTES = int(os.environ.get('JWT_EXPIRE_MINUTES', '1440'))

# Email (Emergent-managed Resend proxy)
EMAIL_BASE_URL = "https://integrations.emergentagent.com"
EMAIL_KEY = os.environ.get("EMERGENT_EMAIL_KEY", "")
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "VFIT")

# ----- Rate limiting in-memory -----
_login_attempts: dict = defaultdict(list)
_public_attempts: dict = defaultdict(list)

def _rate_check(bucket: dict, key: str, limit: int, window: int):
    now = _time.time()
    bucket[key] = [t for t in bucket[key] if now - t < window]
    if len(bucket[key]) >= limit:
        raise HTTPException(status_code=429, detail="Troppe richieste, riprova più tardi")
    bucket[key].append(now)

def _esc(s: Any) -> str:
    return _html.escape(str(s or ""), quote=True)


def _gen_referral_code() -> str:
    return "VFIT" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def _gen_buono_code() -> str:
    return "-".join("".join(random.choices(string.ascii_uppercase + string.digits, k=4)) for _ in range(2))

app = FastAPI(title="VFIT API")
api = APIRouter(prefix="/api")
security = HTTPBearer(auto_error=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------- Helpers ----------------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


def make_token(user: dict, minutes: Optional[int] = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["id"],
        "email": user["email"],
        "ruolo": user["ruolo"],
        "iat": now,
        "exp": now + timedelta(minutes=minutes if minutes is not None else JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


async def current_user(creds: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=["HS256"])
    except Exception:
        raise HTTPException(status_code=401, detail="Token non valido")
    user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0, "password_hash": 0})
    if not user:
        raise HTTPException(status_code=401, detail="Utente non trovato")
    return user


def require_teacher(user: dict = Depends(current_user)) -> dict:
    if user["ruolo"] not in ("teacher", "admin"):
        raise HTTPException(status_code=403, detail="Solo insegnanti")
    return user


async def _wallet_accredita(allievo_id: str, importo: float, causale: str, ref_id: Optional[str] = None) -> str:
    if importo <= 0:
        return ""
    mid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.wallet_movimenti.insert_one({
        "id": mid,
        "allievo_id": allievo_id,
        "importo": round(importo, 2),
        "tipo": "accredito",
        "causale": causale,
        "riferimento_id": ref_id,
        "created_at": now,
    })
    await db.allievi.update_one({"id": allievo_id}, {"$inc": {"wallet_saldo": round(importo, 2)}})
    return mid


async def _wallet_utilizza(allievo_id: str, importo: float, causale: str, ref_id: Optional[str] = None) -> str:
    if importo <= 0:
        return ""
    a = await db.allievi.find_one({"id": allievo_id}, {"_id": 0})
    saldo = float(a.get("wallet_saldo", 0.0) if a else 0.0)
    if saldo + 1e-6 < importo:
        raise HTTPException(status_code=400, detail=f"Saldo wallet insufficiente (€ {saldo:.2f})")
    mid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.wallet_movimenti.insert_one({
        "id": mid,
        "allievo_id": allievo_id,
        "importo": -round(importo, 2),
        "tipo": "utilizzo",
        "causale": causale,
        "riferimento_id": ref_id,
        "created_at": now,
    })
    await db.allievi.update_one({"id": allievo_id}, {"$inc": {"wallet_saldo": -round(importo, 2)}})
    return mid


async def _accredita_crediti_vfit(quantita: int, motivo: str, ref_id: Optional[str] = None, nome: Optional[str] = None) -> None:
    """Accredita crediti VFIT (interno insegnante) e traccia storico."""
    if quantita <= 0:
        return
    now = datetime.now(timezone.utc).isoformat()
    await db.crediti_storico.insert_one({
        "id": str(uuid.uuid4()),
        "delta": quantita,
        "tipo": "accredito",
        "motivo": motivo,
        "riferimento_id": ref_id,
        "nome": nome,
        "created_at": now,
    })
    await db.crediti_vfit.update_one(
        {"_id": "main"},
        {"$inc": {"saldo": quantita, "totale_guadagnati": quantita}},
        upsert=True,
    )


async def _utilizza_crediti_vfit(quantita: int, motivo: str) -> int:
    if quantita <= 0:
        raise HTTPException(status_code=400, detail="Quantità non valida")
    doc = await db.crediti_vfit.find_one({"_id": "main"})
    saldo = int((doc or {}).get("saldo", 0))
    if saldo < quantita:
        raise HTTPException(status_code=400, detail=f"Crediti insufficienti (hai {saldo})")
    now = datetime.now(timezone.utc).isoformat()
    await db.crediti_storico.insert_one({
        "id": str(uuid.uuid4()),
        "delta": -quantita,
        "tipo": "utilizzo",
        "motivo": motivo,
        "created_at": now,
    })
    await db.crediti_vfit.update_one(
        {"_id": "main"},
        {"$inc": {"saldo": -quantita, "spesi": quantita}},
    )
    return saldo - quantita


async def _staff_corsi_ids(user: dict) -> List[str]:
    """Lista degli id corsi assegnati a uno staff."""
    u = await db.users.find_one({"id": user["id"]}, {"_id": 0, "corsi_ids": 1})
    return list((u or {}).get("corsi_ids", []) or [])


async def require_staff_or_teacher(user: dict = Depends(current_user)) -> dict:
    if user["ruolo"] not in ("teacher", "admin", "staff"):
        raise HTTPException(status_code=403, detail="Non autorizzato")
    return user


# ---------------------- Models ----------------------
class UserPublic(BaseModel):
    id: str
    email: EmailStr
    nome: str
    ruolo: Literal["admin", "teacher", "staff", "student"]
    allievo_id: Optional[str] = None
    corsi_ids: Optional[List[str]] = None


class LoginBody(BaseModel):
    email: EmailStr
    password: str
    ricordami: bool = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic


class PasswordResetRequestBody(BaseModel):
    email: EmailStr


class PasswordResetConfirmBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    nuova_password: str = Field(min_length=6, max_length=100)


class AllievoCreate(BaseModel):
    nome: str
    cognome: str
    email: Optional[EmailStr] = None
    telefono: Optional[str] = None
    data_nascita: Optional[str] = None  # ISO date
    note: Optional[str] = None
    foto: Optional[str] = None  # base64
    certificato_scadenza: Optional[str] = None  # ISO date
    create_login: bool = False
    password: Optional[str] = None
    codice_referral_usato: Optional[str] = None  # codice dell'invitante
    stato: Optional[str] = "attivo"


class Allievo(BaseModel):
    id: str
    nome: str
    cognome: str
    email: Optional[str] = None
    telefono: Optional[str] = None
    data_nascita: Optional[str] = None
    note: Optional[str] = None
    foto: Optional[str] = None
    certificato_scadenza: Optional[str] = None
    user_id: Optional[str] = None
    wallet_saldo: float = 0.0
    codice_referral: Optional[str] = None
    created_at: str
    stato: Optional[str] = "attivo"


class RichiestaIscrizioneCreate(BaseModel):
    corso_id: str
    nome: str
    cognome: str
    telefono: str
    email: Optional[EmailStr] = None


class RichiestaIscrizione(BaseModel):
    id: str
    corso_id: str
    corso_nome: str
    nome: str
    cognome: str
    telefono: str
    email: Optional[EmailStr] = None
    stato: str = "in_attesa"
    allievo_id: Optional[str] = None
    created_at: str


class CorsoCreate(BaseModel):
    nome: str
    descrizione: Optional[str] = None
    insegnante: str
    sala: Literal["Sala 1", "Sala 2"]
    giorno_settimana: int = Field(ge=0, le=6)  # 0=Mon
    ora_inizio: str  # "HH:MM"
    ora_fine: str
    capacita: int = Field(ge=1, default=15)
    livello: Optional[str] = None
    colore: Optional[str] = None


class Corso(CorsoCreate):
    id: str
    created_at: str


class LezioneCreate(BaseModel):
    corso_id: str
    data: str  # ISO date YYYY-MM-DD
    ora_inizio: Optional[str] = None
    ora_fine: Optional[str] = None
    note: Optional[str] = None


class Lezione(BaseModel):
    id: str
    corso_id: str
    corso_nome: str
    sala: str
    insegnante: str
    data: Optional[str] = None
    ora_inizio: str
    ora_fine: str
    capacita: int
    note: Optional[str] = None
    prenotati: List[str] = []  # allievo_ids
    presenze: List[str] = []
    lista_attesa: List[str] = []  # allievo_ids in ordine di arrivo
    created_at: str


class PrenotazioneBody(BaseModel):
    allievo_id: str


class CorsoIscrizioneBody(BaseModel):
    allievo_id: str
    giorni_frequenza: List[int] = []


class PresenzeBody(BaseModel):
    presenti: List[str]


class PagamentoCreate(BaseModel):
    allievo_id: str
    tipo: Literal["abbonamento", "lezione_singola"]
    importo: float
    data_pagamento: str  # ISO
    scadenza: Optional[str] = None  # ISO date, obligatory for abbonamento
    metodo: Optional[str] = "contanti"
    note: Optional[str] = None


class Pagamento(PagamentoCreate):
    id: str
    created_at: str


class Impostazioni(BaseModel):
    nome_scuola: str = "VFIT DANZA & FITNESS DI VANESSA VENTO"
    intestatario: Optional[str] = None
    iban: Optional[str] = None
    sumup_link: Optional[str] = None
    paypal_link: Optional[str] = None
    contatto_email: Optional[str] = None
    contatto_telefono: Optional[str] = None
    indirizzo: Optional[str] = None
    sito_web: Optional[str] = None
    accetta_contanti: bool = True
    causale_default: Optional[str] = None  # es. "Abbonamento VFIT - {nome_allievo}"
    # Wallet & referral
    bonus_benvenuto_importo: float = 2.0
    referral_attivo: bool = True
    referral_importo_invitante: float = 5.0
    referral_importo_invitato: float = 5.0
    # Crediti VFIT (per Vanessa)
    crediti_per_iscrizione: int = 5


# ---------------------- Wallet / Buoni ----------------------
class WalletAccreditaBody(BaseModel):
    importo: float = Field(gt=0)
    causale: str = "Accredito manuale"


class BuonoCreate(BaseModel):
    importo: float = Field(gt=0)
    tipo: Literal["manuale", "benvenuto", "referral", "promozione"] = "manuale"
    scadenza: Optional[str] = None  # ISO date
    allievo_id: Optional[str] = None  # None => generico (chiunque lo può riscattare)
    note: Optional[str] = None
    codice: Optional[str] = None  # None => auto


class RiscattaBody(BaseModel):
    codice: str = Field(min_length=3, max_length=40)
    allievo_id: Optional[str] = None  # se lo riscatta l'insegnante per conto di qualcuno


# ---------------------- Startup ----------------------
@app.on_event("startup")
async def startup():
    await db.users.create_index("email", unique=True)
    await db.users.create_index("id", unique=True)
    await db.allievi.create_index("id", unique=True)
    await db.corsi.create_index("id", unique=True)
    await db.lezioni.create_index("id", unique=True)
    await db.pagamenti.create_index("id", unique=True)
    await db.buoni.create_index("id", unique=True)
    await db.buoni.create_index("codice", unique=True)
    await db.wallet_movimenti.create_index("allievo_id")
    await db.allievi.create_index("codice_referral", unique=True, sparse=True)
    await db.eventi.create_index("data")
    await db.password_resets.create_index("token_hash", unique=True)
    await db.password_resets.create_index("user_id")

    seeds = [
        (os.environ["SEED_TEACHER_EMAIL"], os.environ["SEED_TEACHER_PASSWORD"], "Insegnante", "teacher"),
        (os.environ["SEED_ADMIN_EMAIL"], os.environ["SEED_ADMIN_PASSWORD"], "Admin", "admin"),
    ]
    for email, password, nome, ruolo in seeds:
        email_lc = email.lower()
        existing = await db.users.find_one({"email": email_lc})
        if not existing:
            await db.users.insert_one({
                "id": str(uuid.uuid4()),
                "email": email_lc,
                "nome": nome,
                "ruolo": ruolo,
                "password_hash": hash_password(password),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            logger.info(f"Seeded user: {email_lc}")

    # Backfill: assegna codice referral + wallet_saldo agli allievi esistenti
    async for a in db.allievi.find({"$or": [{"codice_referral": {"$exists": False}}, {"codice_referral": None}]}, {"_id": 0, "id": 1}):
        # Genera codice unico
        for _ in range(6):
            code = _gen_referral_code()
            if not await db.allievi.find_one({"codice_referral": code}):
                await db.allievi.update_one({"id": a["id"]}, {"$set": {"codice_referral": code}})
                break
    await db.allievi.update_many({"wallet_saldo": {"$exists": False}}, {"$set": {"wallet_saldo": 0.0}})


@app.on_event("shutdown")
async def shutdown():
    client.close()


# ---------------------- Auth ----------------------
@api.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginBody, request: Any = None):
    from fastapi import Request as _Req
    email = body.email.lower()
    # Rate limit: max 5 tentativi per 5 minuti per email
    _rate_check(_login_attempts, email, limit=5, window=300)
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Email o password non valide")
    # Reset attempts on success
    _login_attempts.pop(email, None)
    public = {
        "id": user["id"],
        "email": user["email"],
        "nome": user["nome"],
        "ruolo": user["ruolo"],
        "allievo_id": user.get("allievo_id"),
        "corsi_ids": user.get("corsi_ids") or [],
    }
    # Ricordami: token lungo (30 giorni) se checkato, altrimenti 4 ore
    minutes = 60 * 24 * 30 if body.ricordami else 60 * 4
    token = make_token(public, minutes=minutes)
    return {"access_token": token, "user": public}


# ---- Password reset ----
def _hash_reset_token(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def _reset_email_html(nome: str, link: str, imp: dict) -> str:
    nome_scuola = _esc(imp.get("nome_scuola", "VFIT"))
    link_esc = _esc(link)
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#FCFCFB;padding:24px 0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
      <tr><td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="background:#FFFFFF;border:1px solid #E7E5E4;border-radius:12px;padding:32px;">
          <tr><td align="center" style="padding-bottom:16px;">
            <div style="color:#E6008E;font-size:28px;font-weight:800;letter-spacing:4px;">VFIT</div>
            <div style="color:#78716C;font-size:12px;margin-top:4px;">Danza & Fitness</div>
          </td></tr>
          <tr><td style="padding:12px 0;border-top:1px solid #F5F5F4;"></td></tr>
          <tr><td style="color:#1C1917;font-size:20px;font-weight:600;padding-bottom:12px;">Ciao {_esc(nome)},</td></tr>
          <tr><td style="color:#44403C;font-size:15px;line-height:1.6;padding-bottom:16px;">
            Hai richiesto di reimpostare la tua password. Clicca sul pulsante qui sotto per crearne una nuova:
          </td></tr>
          <tr><td align="center" style="padding:16px 0;">
            <a href="{link_esc}" style="display:inline-block;background:#E6008E;color:#FFFFFF;padding:14px 28px;border-radius:999px;font-weight:700;text-decoration:none;font-size:14px;">Reimposta password</a>
          </td></tr>
          <tr><td style="color:#78716C;font-size:12px;line-height:1.5;padding-top:8px;">
            Il link scade tra 1 ora. Se non hai richiesto tu questa email, ignorala pure — la tua password resterà invariata.
          </td></tr>
          <tr><td style="color:#78716C;font-size:11px;line-height:1.5;padding-top:16px;border-top:1px solid #F5F5F4;text-align:center;margin-top:20px;">
            {nome_scuola}<br/>
            <a href="{link_esc}" style="color:#78716C;font-size:11px;">{link_esc}</a>
          </td></tr>
        </table>
      </td></tr>
    </table>
    """


@api.post("/auth/password-reset/request")
async def password_reset_request(body: PasswordResetRequestBody):
    email = body.email.lower()
    # Rate limit per email/IP: max 3 richieste per 30 minuti
    _rate_check(_public_attempts, f"reset:{email}", limit=3, window=1800)
    # SEMPRE risposta OK per non rivelare se l'email esiste
    user = await db.users.find_one({"email": email})
    if user:
        token = secrets.token_urlsafe(32)
        token_hash = _hash_reset_token(token)
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        # Invalida token precedenti dell'utente
        await db.password_resets.delete_many({"user_id": user["id"]})
        await db.password_resets.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "token_hash": token_hash,
            "expires_at": expires.isoformat(),
            "used": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        # Costruisci link (frontend gestirà /password-reset?token=...)
        base = os.environ.get("FRONTEND_URL") or "https://studio-scheduler-54.preview.emergentagent.com"
        link = f"{base}/password-reset?token={token}"
        imp = await _get_impostazioni()
        html = _reset_email_html(user.get("nome", ""), link, imp)
        try:
            await _send_email(email, "Reimposta la tua password - VFIT", html)
        except HTTPException:
            # Fallisci silenziosamente per non rivelare info; log lato server
            logger.error(f"Password reset email failed for {email}")
    return {"ok": True, "message": "Se l'email è registrata, riceverai un link per reimpostare la password."}


@api.post("/auth/password-reset/confirm")
async def password_reset_confirm(body: PasswordResetConfirmBody):
    token_hash = _hash_reset_token(body.token.strip())
    doc = await db.password_resets.find_one({"token_hash": token_hash, "used": False})
    if not doc:
        raise HTTPException(status_code=400, detail="Token non valido o già usato")
    try:
        exp = datetime.fromisoformat(doc["expires_at"])
    except Exception:
        raise HTTPException(status_code=400, detail="Token corrotto")
    if exp < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Token scaduto, richiedi un nuovo reset")
    # Aggiorna password
    new_hash = hash_password(body.nuova_password)
    r = await db.users.update_one({"id": doc["user_id"]}, {"$set": {"password_hash": new_hash}})
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    await db.password_resets.update_one({"id": doc["id"]}, {"$set": {"used": True, "used_at": datetime.now(timezone.utc).isoformat()}})
    return {"ok": True, "message": "Password reimpostata con successo"}


@api.get("/auth/me", response_model=UserPublic)
async def me(user: dict = Depends(current_user)):
    return user


# ---------------------- Allievi ----------------------
@api.get("/allievi", response_model=List[Allievo])
async def list_allievi(user: dict = Depends(require_teacher)):
    docs = await db.allievi.find({}, {"_id": 0}).sort("cognome", 1).to_list(1000)
    return docs


@api.post("/allievi", response_model=Allievo)
async def create_allievo(body: AllievoCreate, user: dict = Depends(require_teacher)):
    aid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": aid,
        "nome": body.nome.strip(),
        "cognome": body.cognome.strip(),
        "email": body.email.lower() if body.email else None,
        "telefono": body.telefono,
        "data_nascita": body.data_nascita,
        "note": body.note,
        "foto": body.foto,
        "certificato_scadenza": body.certificato_scadenza,
        "stato": body.stato if body.stato in ("attivo", "ritirata") else "attivo",
        "user_id": None,
        "wallet_saldo": 0.0,
        "codice_referral": _gen_referral_code(),
        "created_at": now,
    }
    # Optionally create student login
    if body.create_login and body.email and body.password:
        existing = await db.users.find_one({"email": body.email.lower()})
        if existing:
            raise HTTPException(status_code=409, detail="Email già registrata")
        uid = str(uuid.uuid4())
        await db.users.insert_one({
            "id": uid,
            "email": body.email.lower(),
            "nome": f"{body.nome} {body.cognome}",
            "ruolo": "student",
            "password_hash": hash_password(body.password),
            "allievo_id": aid,
            "created_at": now,
        })
        doc["user_id"] = uid
    await db.allievi.insert_one(doc)
    # ---- Bonus benvenuto ----
    imp = await _get_impostazioni()
    bonus = float(imp.get("bonus_benvenuto_importo", 0) or 0)
    if bonus > 0:
        await _wallet_accredita(aid, bonus, "🎁 Bonus di benvenuto")
    # ---- Referral ----
    if body.codice_referral_usato and imp.get("referral_attivo", True):
        code = body.codice_referral_usato.strip().upper()
        invitante = await db.allievi.find_one({"codice_referral": code}, {"_id": 0})
        if invitante and invitante["id"] != aid:
            b_inv = float(imp.get("referral_importo_invitante", 0) or 0)
            b_ito = float(imp.get("referral_importo_invitato", 0) or 0)
            if b_inv > 0:
                await _wallet_accredita(invitante["id"], b_inv, f"🤝 Referral: hai invitato {body.nome} {body.cognome}", aid)
            if b_ito > 0:
                await _wallet_accredita(aid, b_ito, f"🤝 Referral: invitato da {invitante['nome']} {invitante['cognome']}", invitante["id"])
    # Ritorna il documento aggiornato con saldo/wallet effettivi
    fresh = await db.allievi.find_one({"id": aid}, {"_id": 0})
    # Accredita crediti VFIT all'insegnante
    crediti = int(imp.get("crediti_per_iscrizione", 5) or 0)
    if crediti > 0:
        await _accredita_crediti_vfit(crediti, f"Nuovo allievo: {body.nome} {body.cognome}", aid, f"{body.nome} {body.cognome}")
    return fresh


@api.put("/allievi/{aid}", response_model=Allievo)
async def update_allievo(aid: str, body: AllievoCreate, user: dict = Depends(require_teacher)):
    update = {
        "nome": body.nome.strip(),
        "cognome": body.cognome.strip(),
        "email": body.email.lower() if body.email else None,
        "telefono": body.telefono,
        "data_nascita": body.data_nascita,
        "note": body.note,
        "foto": body.foto,
        "certificato_scadenza": body.certificato_scadenza,
        "stato": body.stato if body.stato in ("attivo", "ritirata") else "attivo",
    }
    result = await db.allievi.find_one_and_update(
        {"id": aid}, {"$set": update}, projection={"_id": 0}, return_document=True
    )
    if not result:
        raise HTTPException(status_code=404, detail="Allievo non trovato")
    return result


@api.delete("/allievi/{aid}")
async def delete_allievo(aid: str, user: dict = Depends(require_teacher)):
    a = await db.allievi.find_one({"id": aid})
    if not a:
        raise HTTPException(status_code=404, detail="Allievo non trovato")
    await db.allievi.delete_one({"id": aid})
    if a.get("user_id"):
        await db.users.delete_one({"id": a["user_id"]})
    await db.lezioni.update_many({}, {"$pull": {"prenotati": aid, "presenze": aid}})
    await db.pagamenti.delete_many({"allievo_id": aid})
    return {"ok": True}


# ---------------------- Corsi ----------------------
@api.get("/corsi", response_model=List[Corso])
async def list_corsi(user: dict = Depends(current_user)):
    q: dict = {}
    if user["ruolo"] == "staff":
        ids = await _staff_corsi_ids(user)
        q["id"] = {"$in": ids or ["__none__"]}
    docs = await db.corsi.find(q, {"_id": 0}).sort("giorno_settimana", 1).to_list(1000)
    return docs


@api.post("/corsi", response_model=Corso)
async def create_corso(body: CorsoCreate, user: dict = Depends(require_teacher)):
    doc = body.model_dump()
    doc["id"] = str(uuid.uuid4())
    doc["created_at"] = datetime.now(timezone.utc).isoformat()
    await db.corsi.insert_one(doc.copy())
    doc.pop("_id", None)
    return doc


@api.put("/corsi/{cid}", response_model=Corso)
async def update_corso(cid: str, body: CorsoCreate, user: dict = Depends(require_teacher)):
    update = body.model_dump()
    result = await db.corsi.find_one_and_update(
        {"id": cid}, {"$set": update}, projection={"_id": 0}, return_document=True
    )
    if not result:
        raise HTTPException(status_code=404, detail="Corso non trovato")
    return result


@api.delete("/corsi/{cid}")
async def delete_corso(cid: str, user: dict = Depends(require_teacher)):
    result = await db.corsi.delete_one({"id": cid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Corso non trovato")
    await db.lezioni.delete_many({"corso_id": cid})
    return {"ok": True}




async def _corso_iscritti_ids(corso_id: str):
    """Restituisce gli ID degli allievi iscritti al corso, senza duplicati.

    La fonte principale è iscrizioni_corsi (stato approvata); includiamo anche
    gli eventuali prenotati storici delle lezioni per mantenere compatibilità
    con i dati già presenti nel database.
    """
    richieste = await db.iscrizioni_corsi.find(
        {"corso_id": corso_id, "stato": "approvata", "allievo_id": {"$ne": None}},
        {"_id": 0, "allievo_id": 1}
    ).to_list(5000)

    lezioni = await db.lezioni.find(
        {"corso_id": corso_id},
        {"_id": 0, "prenotati": 1}
    ).to_list(5000)

    ids = []
    seen = set()

    for richiesta in richieste:
        aid = richiesta.get("allievo_id")
        if aid and aid not in seen:
            seen.add(aid)
            ids.append(aid)

    for lezione in lezioni:
        for aid in (lezione.get("prenotati") or []):
            if aid not in seen:
                seen.add(aid)
                ids.append(aid)

    return ids


@api.get("/corsi/{cid}/iscritti")
async def corso_iscritti(cid: str, user: dict = Depends(require_teacher)):
    """Elenco degli allievi iscritti a tutte le lezioni di un corso."""
    corso = await db.corsi.find_one({"id": cid}, {"_id": 0})

    if not corso:
        raise HTTPException(status_code=404, detail="Corso non trovato")

    ids = await _corso_iscritti_ids(cid)

    allievi = await db.allievi.find(
        {"id": {"$in": ids}},
        {"_id": 0, "id": 1, "nome": 1, "cognome": 1}
    ).to_list(1000)

    by_id = {a["id"]: a for a in allievi}

    elenco = [
        by_id[aid]
        for aid in ids
        if aid in by_id
    ]

    capacita = int(corso.get("capacita") or 0)

    return {
        "corso_id": cid,
        "capacita": capacita,
        "iscritti": len(elenco),
        "posti_disponibili": max(capacita - len(elenco), 0),
        "allievi": elenco,
    }


@api.post("/corsi/{cid}/iscritti")
async def aggiungi_allievo_al_corso(
    cid: str,
    body: CorsoIscrizioneBody,
    user: dict = Depends(require_teacher),
):
    """Iscrive manualmente un allievo a un corso dal pannello insegnante."""
    corso = await db.corsi.find_one({"id": cid}, {"_id": 0})
    if not corso:
        raise HTTPException(status_code=404, detail="Corso non trovato")

    allievo = await db.allievi.find_one({"id": body.allievo_id}, {"_id": 0})
    if not allievo:
        raise HTTPException(status_code=404, detail="Allievo non trovato")

    gia_iscritto = await db.iscrizioni_corsi.find_one({
        "corso_id": cid,
        "allievo_id": body.allievo_id,
        "stato": "approvata",
    }, {"_id": 0})
    if gia_iscritto:
        raise HTTPException(status_code=409, detail="Allievo già iscritto al corso")

    # Considera anche gli iscritti storici alle lezioni.
    iscritti_ids = await _corso_iscritti_ids(cid)
    capacita = int(corso.get("capacita") or 0)
    if capacita > 0 and len(iscritti_ids) >= capacita:
        raise HTTPException(status_code=409, detail="Il corso è al completo")

    now = datetime.now(timezone.utc).isoformat()
    await db.iscrizioni_corsi.insert_one({
        "id": str(uuid.uuid4()),
        "corso_id": cid,
        "corso_nome": corso.get("nome", ""),
        "nome": allievo.get("nome", ""),
        "cognome": allievo.get("cognome", ""),
        "telefono": allievo.get("telefono", ""),
        "email": allievo.get("email"),
        "stato": "approvata",
        "allievo_id": body.allievo_id,
        "created_at": now,
        "approved_at": now,
        "approved_by": user.get("id"),
        "manuale": True,
        "giorni_frequenza": sorted(set(int(x) for x in (body.giorni_frequenza or []))),
    })

    # Aggiunge l'allievo solo alle lezioni dei giorni scelti.
    giorni_frequenza = sorted(set(int(x) for x in (body.giorni_frequenza or [])))

    if any(g < 0 or g > 6 for g in giorni_frequenza):
        raise HTTPException(status_code=400, detail="Giorno di frequenza non valido")

    if not giorni_frequenza:
        raise HTTPException(status_code=400, detail="Seleziona almeno un giorno di frequenza")

    lezioni = await db.lezioni.find(
        {"corso_id": cid},
        {"_id": 0, "id": 1, "data": 1, "capacita": 1, "prenotati": 1}
    ).to_list(2000)

    for lezione in lezioni:
        try:
            giorno_lezione = date.fromisoformat(str(lezione.get("data") or "")[:10]).weekday()
        except Exception:
            continue

        if giorno_lezione not in giorni_frequenza:
            continue

        prenotati = lezione.get("prenotati") or []

        if body.allievo_id in prenotati:
            continue

        cap_lezione = int(lezione.get("capacita") or 0)

        if cap_lezione <= 0 or len(prenotati) < cap_lezione:
            await db.lezioni.update_one(
                {"id": lezione["id"], "prenotati": {"$ne": body.allievo_id}},
                {"$push": {"prenotati": body.allievo_id},
                 "$pull": {"lista_attesa": body.allievo_id}},
            )

    return {
        "ok": True,
        "corso_id": cid,
        "allievo_id": body.allievo_id,
        "message": "Allievo aggiunto al corso",
    }


# ---------------------- Corsi pubblici ----------------------

@api.get("/pubblico/corsi-disponibili")
async def pubblico_corsi_disponibili():
    """Elenco pubblico dei corsi con posti disponibili."""
    corsi = await db.corsi.find(
        {},
        {"_id": 0}
    ).sort("giorno_settimana", 1).to_list(1000)

    result = []

    for corso in corsi:
        corso_id = corso.get("id")

        richieste = await db.iscrizioni_corsi.find(
            {"corso_id": corso_id, "stato": "approvata"},
            {"_id": 0, "allievo_id": 1}
        ).to_list(1000)

        iscritti = len({
            x.get("allievo_id")
            for x in richieste
            if x.get("allievo_id")
        })

        capacita = int(corso.get("capacita") or 0)
        posti_disponibili = max(capacita - iscritti, 0)

        result.append({
            "id": corso_id,
            "nome": corso.get("nome", ""),
            "giorno_settimana": corso.get("giorno_settimana"),
            "ora_inizio": corso.get("ora_inizio"),
            "ora_fine": corso.get("ora_fine"),
            "sala": corso.get("sala"),
            "insegnante": corso.get("insegnante"),
            "capacita": capacita,
            "iscritti": iscritti,
            "posti_disponibili": posti_disponibili,
            "completo": capacita > 0 and iscritti >= capacita,
        })

    return result


@api.post("/pubblico/richieste-iscrizione")
async def pubblico_richiesta_iscrizione(body: RichiestaIscrizioneCreate):
    """Riceve una richiesta pubblica di iscrizione. Non crea ancora l'Allievo."""
    corso = await db.corsi.find_one({"id": body.corso_id}, {"_id": 0})

    if not corso:
        raise HTTPException(status_code=404, detail="Corso non trovato")

    approvate = await db.iscrizioni_corsi.count_documents({
        "corso_id": body.corso_id,
        "stato": "approvata",
    })

    capacita = int(corso.get("capacita") or 0)

    if capacita > 0 and approvate >= capacita:
        raise HTTPException(
            status_code=409,
            detail="Il corso è al completo"
        )

    duplicata = await db.iscrizioni_corsi.find_one({
        "corso_id": body.corso_id,
        "stato": "in_attesa",
        "$or": [
            {"email": body.email} if body.email else {"telefono": body.telefono},
            {"telefono": body.telefono},
        ],
    }, {"_id": 0})

    if duplicata:
        raise HTTPException(
            status_code=409,
            detail="Hai già inviato una richiesta per questo corso"
        )

    rid = str(uuid.uuid4())

    doc = {
        "id": rid,
        "corso_id": body.corso_id,
        "corso_nome": corso.get("nome", ""),
        "nome": body.nome.strip(),
        "cognome": body.cognome.strip(),
        "telefono": body.telefono.strip(),
        "email": str(body.email).strip() if body.email else None,
        "stato": "in_attesa",
        "allievo_id": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    await db.iscrizioni_corsi.insert_one(doc)

    return {
        "ok": True,
        "message": "Richiesta ricevuta. La scuola ti contatterà per la conferma.",
    }


@api.get("/richieste-iscrizione")
async def lista_richieste_iscrizione(
    user: dict = Depends(require_teacher)
):
    docs = await db.iscrizioni_corsi.find(
        {},
        {"_id": 0}
    ).sort("created_at", -1).to_list(1000)

    return docs


@api.post("/richieste-iscrizione/{rid}/rifiuta")
async def rifiuta_richiesta_iscrizione(
    rid: str,
    user: dict = Depends(require_teacher)
):
    result = await db.iscrizioni_corsi.update_one(
        {"id": rid, "stato": "in_attesa"},
        {"$set": {
            "stato": "rifiutata",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }}
    )

    if result.modified_count == 0:
        raise HTTPException(
            status_code=404,
            detail="Richiesta non trovata o già gestita"
        )

    return {"ok": True}


@api.post("/richieste-iscrizione/{rid}/approva")
async def approva_richiesta_iscrizione(
    rid: str,
    user: dict = Depends(require_teacher)
):
    richiesta = await db.iscrizioni_corsi.find_one(
        {"id": rid, "stato": "in_attesa"},
        {"_id": 0}
    )

    if not richiesta:
        raise HTTPException(
            status_code=404,
            detail="Richiesta non trovata o già gestita"
        )

    corso = await db.corsi.find_one(
        {"id": richiesta["corso_id"]},
        {"_id": 0}
    )

    if not corso:
        raise HTTPException(
            status_code=404,
            detail="Corso non trovato"
        )

    # Ricontrollo capienza al momento dell'approvazione.
    approvate = await db.iscrizioni_corsi.count_documents({
        "corso_id": richiesta["corso_id"],
        "stato": "approvata",
    })

    capacita = int(corso.get("capacita") or 0)

    if capacita > 0 and approvate >= capacita:
        raise HTTPException(
            status_code=409,
            detail="Il corso è diventato completo"
        )

    # Cerca prima un Allievo esistente tramite email.
    allievo = None

    if richiesta.get("email"):
        allievo = await db.allievi.find_one(
            {"email": str(richiesta["email"]).lower()},
            {"_id": 0}
        )

    # Se non trovato, prova nome + cognome + telefono.
    if not allievo:
        allievo = await db.allievi.find_one({
            "nome": richiesta["nome"],
            "cognome": richiesta["cognome"],
            "telefono": richiesta["telefono"],
        }, {"_id": 0})

    if allievo:
        allievo_id = allievo["id"]

        aggiornamento = {
            "nome": richiesta["nome"],
            "cognome": richiesta["cognome"],
            "telefono": richiesta["telefono"],
        }

        if richiesta.get("email"):
            aggiornamento["email"] = str(richiesta["email"]).lower()

        await db.allievi.update_one(
            {"id": allievo_id},
            {"$set": aggiornamento}
        )

    else:
        allievo_id = str(uuid.uuid4())

        nuovo_allievo = {
            "id": allievo_id,
            "nome": richiesta["nome"],
            "cognome": richiesta["cognome"],
            "email": str(richiesta["email"]).lower()
                if richiesta.get("email") else None,
            "telefono": richiesta["telefono"],
            "data_nascita": None,
            "note": None,
            "foto": None,
            "certificato_scadenza": None,
            "user_id": None,
            "wallet_saldo": 0.0,
            "codice_referral": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        await db.allievi.insert_one(nuovo_allievo)

    # Salva l'iscrizione al corso separatamente dall'Allievo.
    await db.iscrizioni_corsi.update_one(
        {"id": rid},
        {"$set": {
            "stato": "approvata",
            "allievo_id": allievo_id,
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "approved_by": user.get("id"),
        }}
    )

    # Aggiunge l'Allievo alle lezioni già presenti del corso,
    # senza duplicarlo e rispettando la capienza di ogni lezione.
    lezioni = await db.lezioni.find(
        {"corso_id": richiesta["corso_id"]},
        {"_id": 0, "id": 1}
    ).to_list(2000)

    for lezione in lezioni:
        await db.lezioni.update_one(
            {
                "id": lezione["id"],
                "prenotati": {"$ne": allievo_id},
                "$expr": {
                    "$lt": [
                        {"$size": {"$ifNull": ["$prenotati", []]}},
                        "$capacita"
                    ]
                },
            },
            {
                "$push": {"prenotati": allievo_id},
                "$pull": {"lista_attesa": allievo_id},
            }
        )

    return {
        "ok": True,
        "allievo_id": allievo_id,
        "message": "Richiesta approvata e iscrizione registrata",
    }


# ---------------------- Lezioni ----------------------
@api.get("/lezioni")
async def list_lezioni(
    da: Optional[str] = None, a: Optional[str] = None, sala: Optional[str] = None,
    user: dict = Depends(current_user)
):
    q: dict = {}
    if da and a:
        q["data"] = {"$gte": da, "$lte": a}
    elif da:
        q["data"] = {"$gte": da}
    if sala:
        q["sala"] = sala
    # Staff: filtra solo alle lezioni dei propri corsi
    if user["ruolo"] == "staff":
        ids = await _staff_corsi_ids(user)
        q["corso_id"] = {"$in": ids or ["__none__"]}
    docs = await db.lezioni.find(q, {"_id": 0}).sort([("data", 1), ("ora_inizio", 1)]).to_list(2000)
    # Privacy: agli studenti non esporre gli id degli altri prenotati/presenze
    if user["ruolo"] == "student":
        aid = user.get("allievo_id")
        out = []
        for d in docs:
            prenotati = d.get("prenotati", [])
            lista_attesa = d.get("lista_attesa", [])
            presenze = d.get("presenze", [])
            safe = {k: v for k, v in d.items() if k not in ("prenotati", "presenze", "lista_attesa")}
            safe["prenotati_count"] = len(prenotati)
            safe["lista_attesa_count"] = len(lista_attesa)
            safe["sono_prenotato"] = bool(aid and aid in prenotati)
            safe["sono_in_attesa"] = bool(aid and aid in lista_attesa)
            safe["sono_presente"] = bool(aid and aid in presenze)
            safe["prenotati"] = [aid] if safe["sono_prenotato"] else []
            out.append(safe)
        return out
    return docs


@api.post("/lezioni", response_model=Lezione)
async def create_lezione(body: LezioneCreate, user: dict = Depends(require_teacher)):
    corso = await db.corsi.find_one({"id": body.corso_id}, {"_id": 0})
    if not corso:
        raise HTTPException(status_code=404, detail="Corso non trovato")
    lid = str(uuid.uuid4())
    doc = {
        "id": lid,
        "corso_id": corso["id"],
        "corso_nome": corso["nome"],
        "sala": corso["sala"],
        "insegnante": corso["insegnante"],
        "data": body.data,
        "ora_inizio": body.ora_inizio or corso["ora_inizio"],
        "ora_fine": body.ora_fine or corso["ora_fine"],
        "capacita": corso["capacita"],
        "note": body.note,
        "prenotati": [],
        "presenze": [],
        "lista_attesa": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.lezioni.insert_one(doc.copy())
    doc.pop("_id", None)
    return doc


@api.delete("/lezioni/{lid}")
async def delete_lezione(lid: str, user: dict = Depends(require_teacher)):
    result = await db.lezioni.delete_one({"id": lid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Lezione non trovata")
    return {"ok": True}


@api.post("/lezioni/{lid}/prenota", response_model=Lezione)
async def prenota(lid: str, body: PrenotazioneBody, user: dict = Depends(current_user)):
    lez = await db.lezioni.find_one({"id": lid}, {"_id": 0})
    if not lez:
        raise HTTPException(status_code=404, detail="Lezione non trovata")
    if user["ruolo"] == "student":
        if user.get("allievo_id") != body.allievo_id:
            raise HTTPException(status_code=403, detail="Puoi prenotare solo per te stesso")
    if body.allievo_id in lez.get("prenotati", []):
        return lez
    # SEC-005: atomic update to prevent overbooking race condition
    result = await db.lezioni.update_one(
        {
            "id": lid,
            "prenotati": {"$ne": body.allievo_id},
            "$expr": {"$lt": [{"$size": {"$ifNull": ["$prenotati", []]}}, "$capacita"]},
        },
        {"$push": {"prenotati": body.allievo_id}, "$pull": {"lista_attesa": body.allievo_id}},
    )
    if result.modified_count == 0:
        # Ricontrolla se già prenotato o al completo
        lez2 = await db.lezioni.find_one({"id": lid}, {"_id": 0})
        if body.allievo_id in lez2.get("prenotati", []):
            return lez2
        raise HTTPException(status_code=409, detail="Lezione al completo")
    return await db.lezioni.find_one({"id": lid}, {"_id": 0})


@api.post("/lezioni/{lid}/lista-attesa", response_model=Lezione)
async def lista_attesa(lid: str, body: PrenotazioneBody, user: dict = Depends(current_user)):
    lez = await db.lezioni.find_one({"id": lid}, {"_id": 0})
    if not lez:
        raise HTTPException(status_code=404, detail="Lezione non trovata")
    if user["ruolo"] == "student":
        if user.get("allievo_id") != body.allievo_id:
            raise HTTPException(status_code=403, detail="Puoi iscriverti solo per te stesso")
    lez.setdefault("lista_attesa", [])
    if body.allievo_id in lez["prenotati"]:
        raise HTTPException(status_code=400, detail="Sei già prenotato per questa lezione")
    if body.allievo_id in lez["lista_attesa"]:
        return lez
    lez["lista_attesa"].append(body.allievo_id)
    await db.lezioni.update_one({"id": lid}, {"$set": {"lista_attesa": lez["lista_attesa"]}})
    return lez


@api.post("/lezioni/{lid}/annulla")
async def annulla(lid: str, body: PrenotazioneBody, user: dict = Depends(current_user)):
    lez = await db.lezioni.find_one({"id": lid}, {"_id": 0})
    if not lez:
        raise HTTPException(status_code=404, detail="Lezione non trovata")
    if user["ruolo"] == "student" and user.get("allievo_id") != body.allievo_id:
        raise HTTPException(status_code=403, detail="Non autorizzato")
    lez.setdefault("lista_attesa", [])
    was_prenotato = body.allievo_id in lez["prenotati"]
    promoted_allievo_id = None
    if was_prenotato:
        lez["prenotati"].remove(body.allievo_id)
    if body.allievo_id in lez["presenze"]:
        lez["presenze"].remove(body.allievo_id)
    if body.allievo_id in lez["lista_attesa"]:
        lez["lista_attesa"].remove(body.allievo_id)
    # Auto-promozione: se posto liberato e c'è qualcuno in attesa
    if was_prenotato and lez["lista_attesa"] and len(lez["prenotati"]) < lez["capacita"]:
        promoted_allievo_id = lez["lista_attesa"].pop(0)
        lez["prenotati"].append(promoted_allievo_id)
    await db.lezioni.update_one(
        {"id": lid},
        {"$set": {"prenotati": lez["prenotati"], "presenze": lez["presenze"], "lista_attesa": lez["lista_attesa"]}}
    )
    promosso = None
    if promoted_allievo_id:
        p = await db.allievi.find_one({"id": promoted_allievo_id}, {"_id": 0})
        if p:
            # SEC-002: non esporre PII di altri utenti agli studenti
            if user["ruolo"] == "student":
                promosso = {"id": p["id"], "nome": p["nome"]}
            else:
                promosso = {"id": p["id"], "nome": p["nome"], "cognome": p["cognome"], "email": p.get("email"), "telefono": p.get("telefono")}
    return {"lezione": lez, "promosso": promosso}


@api.post("/lezioni/{lid}/presenze", response_model=Lezione)
async def set_presenze(lid: str, body: PresenzeBody, user: dict = Depends(require_staff_or_teacher)):
    lez = await db.lezioni.find_one({"id": lid}, {"_id": 0})
    if not lez:
        raise HTTPException(status_code=404, detail="Lezione non trovata")
    if user["ruolo"] == "staff":
        ids = await _staff_corsi_ids(user)
        if lez.get("corso_id") not in (ids or []):
            raise HTTPException(status_code=403, detail="Non puoi gestire questa lezione")
    validi = [a for a in body.presenti if a in lez["prenotati"]]
    await db.lezioni.update_one({"id": lid}, {"$set": {"presenze": validi}})
    lez["presenze"] = validi
    return lez


# ---------------------- Pagamenti ----------------------
def _stato_pagamento(scadenza: Optional[str]) -> str:
    if not scadenza:
        return "saldato"
    try:
        d = date.fromisoformat(scadenza)
    except Exception:
        return "saldato"
    today = date.today()
    if d < today:
        return "scaduto"
    if (d - today).days <= 7:
        return "in_scadenza"
    return "attivo"


@api.get("/pagamenti")
async def list_pagamenti(allievo_id: Optional[str] = None, user: dict = Depends(current_user)):
    q = {}
    if user["ruolo"] == "student":
        q["allievo_id"] = user.get("allievo_id")
    elif allievo_id:
        q["allievo_id"] = allievo_id
    docs = await db.pagamenti.find(q, {"_id": 0}).sort("data_pagamento", -1).to_list(2000)
    for d in docs:
        d["stato"] = _stato_pagamento(d.get("scadenza"))
    return docs


@api.post("/pagamenti", response_model=Pagamento)
async def create_pagamento(body: PagamentoCreate, user: dict = Depends(require_teacher)):
    doc = body.model_dump()
    doc["id"] = str(uuid.uuid4())
    doc["created_at"] = datetime.now(timezone.utc).isoformat()
    await db.pagamenti.insert_one(doc.copy())
    doc.pop("_id", None)
    return doc


@api.delete("/pagamenti/{pid}")
async def delete_pagamento(pid: str, user: dict = Depends(require_teacher)):
    result = await db.pagamenti.delete_one({"id": pid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Pagamento non trovato")
    return {"ok": True}


@api.get("/pagamenti/riepilogo")
async def riepilogo_pagamenti(user: dict = Depends(require_teacher)):
    """Ritorna riepilogo per ogni allievo: ultimo pagamento e stato."""
    allievi = await db.allievi.find({}, {"_id": 0}).to_list(1000)
    result = []
    total_incassato = 0.0
    scaduti_count = 0
    in_scadenza_count = 0
    for a in allievi:
        pagamenti = await db.pagamenti.find({"allievo_id": a["id"]}, {"_id": 0}).sort("data_pagamento", -1).to_list(100)
        for p in pagamenti:
            total_incassato += float(p.get("importo", 0))
        ultimo = pagamenti[0] if pagamenti else None
        abbonamenti = [p for p in pagamenti if p["tipo"] == "abbonamento"]
        ultimo_abb = abbonamenti[0] if abbonamenti else None
        stato = _stato_pagamento(ultimo_abb["scadenza"]) if ultimo_abb else "nessuno"
        if stato == "scaduto":
            scaduti_count += 1
        elif stato == "in_scadenza":
            in_scadenza_count += 1
        result.append({
            "allievo_id": a["id"],
            "nome": a["nome"],
            "cognome": a["cognome"],
            "email": a.get("email"),
            "telefono": a.get("telefono"),
            "ultimo_pagamento": ultimo,
            "ultimo_abbonamento": ultimo_abb,
            "stato": stato,
            "stato_allievo": a.get("stato", "attivo"),
            "totale_pagato": sum(float(p.get("importo", 0)) for p in pagamenti),
        })
    return {
        "totale_incassato": total_incassato,
        "scaduti": scaduti_count,
        "in_scadenza": in_scadenza_count,
        "allievi": result,
    }


# ---------------------- Impostazioni ----------------------
async def _get_impostazioni() -> dict:
    doc = await db.impostazioni.find_one({"_id": "main"}, {"_id": 0})
    defaults = Impostazioni().model_dump()
    if not doc:
        return defaults
    # Merge defaults con doc salvato: campi nuovi ereditano il default
    for k, v in defaults.items():
        if k not in doc or doc[k] is None:
            doc[k] = v
    return doc


@api.get("/impostazioni", response_model=Impostazioni)
async def get_impostazioni(user: dict = Depends(require_teacher)):
    return await _get_impostazioni()


@api.put("/impostazioni", response_model=Impostazioni)
async def update_impostazioni(body: Impostazioni, user: dict = Depends(require_teacher)):
    data = body.model_dump()
    await db.impostazioni.update_one({"_id": "main"}, {"$set": data}, upsert=True)
    return data


# ---------------------- Email promemoria ----------------------
def _metodi_pagamento_html(imp: dict) -> str:
    rows = []
    if imp.get("iban"):
        intest = imp.get("intestatario") or imp.get("nome_scuola", "")
        rows.append(
            f'<tr><td style="padding:6px 0;color:#78716C;font-size:12px;">BONIFICO</td>'
            f'<td style="padding:6px 0;color:#1C1917;font-size:13px;">'
            f'<b>IBAN:</b> {imp["iban"]}<br/><b>Intestatario:</b> {intest}</td></tr>'
        )
    if imp.get("sumup_link"):
        rows.append(
            f'<tr><td style="padding:6px 0;color:#78716C;font-size:12px;">SUMUP</td>'
            f'<td style="padding:6px 0;"><a href="{imp["sumup_link"]}" style="color:#E6008E;text-decoration:none;font-weight:600;font-size:13px;">Paga online con SumUp →</a></td></tr>'
        )
    if imp.get("paypal_link"):
        rows.append(
            f'<tr><td style="padding:6px 0;color:#78716C;font-size:12px;">PAYPAL</td>'
            f'<td style="padding:6px 0;"><a href="{imp["paypal_link"]}" style="color:#E6008E;text-decoration:none;font-weight:600;font-size:13px;">Paga con PayPal →</a></td></tr>'
        )
    if imp.get("accetta_contanti"):
        indirizzo = _esc(imp.get("indirizzo") or "presso la segreteria della scuola")
        rows.append(
            f'<tr><td style="padding:6px 0;color:#78716C;font-size:12px;">CONTANTI</td>'
            f'<td style="padding:6px 0;color:#1C1917;font-size:13px;">Puoi pagare direttamente presso {indirizzo}</td></tr>'
        )
    if not rows:
        return ""
    return (
        '<table cellpadding="0" cellspacing="0" width="100%" style="margin-top:16px;border-top:1px solid #F5F5F4;padding-top:12px;">'
        '<tr><td colspan="2" style="color:#E6008E;font-size:11px;font-weight:800;letter-spacing:1px;padding-bottom:6px;">MODALITÀ DI PAGAMENTO</td></tr>'
        + "".join(rows) + "</table>"
    )


def _email_promemoria_html(nome: str, scadenza: str, stato: str, imp: dict) -> str:
    is_scaduto = stato == "scaduto"
    titolo = "Il tuo abbonamento è scaduto" if is_scaduto else "Il tuo abbonamento è in scadenza"
    color = "#964545" if is_scaduto else "#C78D4A"
    nome_scuola_esc = _esc(imp.get('nome_scuola', 'VFIT'))
    scadenza_esc = _esc(scadenza)
    nome_esc = _esc(nome)
    messaggio = (
        f"Ti ricordiamo che il tuo abbonamento presso <b>{nome_scuola_esc}</b> è scaduto il <b>{scadenza_esc}</b>."
        if is_scaduto else
        f"Ti ricordiamo che il tuo abbonamento presso <b>{nome_scuola_esc}</b> scade il <b>{scadenza_esc}</b>."
    )
    metodi = _metodi_pagamento_html(imp)
    sito = _esc(imp.get('sito_web', '')) if imp.get('sito_web') else ''
    sito_row = f'<br/><a href="{sito}" style="color:#E6008E;text-decoration:none;">{sito}</a>' if sito else ''
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#FCFCFB;padding:24px 0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
      <tr><td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="background:#FFFFFF;border:1px solid #E7E5E4;border-radius:12px;padding:32px;">
          <tr><td align="center" style="padding-bottom:16px;">
            <div style="color:#E6008E;font-size:28px;font-weight:800;letter-spacing:4px;">VFIT</div>
            <div style="color:#78716C;font-size:12px;margin-top:4px;">Danza & Fitness</div>
          </td></tr>
          <tr><td style="padding:12px 0;border-top:1px solid #F5F5F4;"></td></tr>
          <tr><td style="color:#1C1917;font-size:20px;font-weight:600;padding-bottom:12px;">Ciao {nome_esc},</td></tr>
          <tr><td style="color:#44403C;font-size:15px;line-height:1.6;padding-bottom:16px;">{titolo}.</td></tr>
          <tr><td style="color:#44403C;font-size:14px;line-height:1.6;padding-bottom:16px;">{messaggio}</td></tr>
          <tr><td style="color:#44403C;font-size:14px;line-height:1.6;padding-bottom:24px;">
            Ti invitiamo a rinnovare al più presto per continuare a partecipare alle lezioni.
          </td></tr>
          <tr><td align="center" style="padding:12px 0;">
            <span style="display:inline-block;background:{color};color:#FFFFFF;padding:8px 16px;border-radius:999px;font-size:12px;font-weight:700;letter-spacing:1px;text-transform:uppercase;">
              {"Scaduto" if is_scaduto else "In scadenza"}
            </span>
          </td></tr>
          <tr><td>{metodi}</td></tr>
          <tr><td style="color:#78716C;font-size:12px;line-height:1.5;padding-top:24px;border-top:1px solid #F5F5F4;text-align:center;">
            A presto in sala!<br/><b>{nome_scuola_esc}</b>{sito_row}
          </td></tr>
        </table>
      </td></tr>
    </table>
    """


def _email_ricevuta_html(nome: str, pagamento: dict, imp: dict) -> str:
    tipo_label = "Abbonamento" if pagamento["tipo"] == "abbonamento" else "Lezione singola"
    nome_esc = _esc(nome)
    nome_scuola_esc = _esc(imp.get('nome_scuola', 'VFIT'))
    metodo_esc = _esc(pagamento.get('metodo') or 'contanti')
    data_esc = _esc(pagamento['data_pagamento'])
    scad_row = ""
    if pagamento.get("scadenza"):
        scad_row = f'<tr><td style="padding:6px 0;color:#78716C;font-size:12px;">SCADENZA</td><td style="padding:6px 0;color:#1C1917;font-size:14px;">{_esc(pagamento["scadenza"])}</td></tr>'
    sito = _esc(imp.get('sito_web', '')) if imp.get('sito_web') else ''
    sito_row = f'<br/><a href="{sito}" style="color:#E6008E;text-decoration:none;">{sito}</a>' if sito else ''
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#FCFCFB;padding:24px 0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
      <tr><td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="background:#FFFFFF;border:1px solid #E7E5E4;border-radius:12px;padding:32px;">
          <tr><td align="center" style="padding-bottom:16px;">
            <div style="color:#E6008E;font-size:28px;font-weight:800;letter-spacing:4px;">VFIT</div>
            <div style="color:#78716C;font-size:12px;margin-top:4px;">Danza & Fitness</div>
          </td></tr>
          <tr><td style="padding:12px 0;border-top:1px solid #F5F5F4;"></td></tr>
          <tr><td style="color:#1C1917;font-size:20px;font-weight:600;padding-bottom:12px;">Ciao {nome_esc},</td></tr>
          <tr><td style="color:#44403C;font-size:15px;line-height:1.6;padding-bottom:20px;">
            Confermiamo di aver ricevuto il tuo pagamento. Grazie!
          </td></tr>
          <tr><td align="center" style="padding:12px 0;">
            <span style="display:inline-block;background:#5C7662;color:#FFFFFF;padding:8px 20px;border-radius:999px;font-size:12px;font-weight:700;letter-spacing:1px;text-transform:uppercase;">Pagamento ricevuto</span>
          </td></tr>
          <tr><td>
            <table cellpadding="0" cellspacing="0" width="100%" style="margin-top:16px;border-top:1px solid #F5F5F4;padding-top:12px;">
              <tr><td style="padding:6px 0;color:#78716C;font-size:12px;">TIPO</td><td style="padding:6px 0;color:#1C1917;font-size:14px;font-weight:600;">{tipo_label}</td></tr>
              <tr><td style="padding:6px 0;color:#78716C;font-size:12px;">IMPORTO</td><td style="padding:6px 0;color:#1C1917;font-size:18px;font-weight:700;">€ {pagamento['importo']:.2f}</td></tr>
              <tr><td style="padding:6px 0;color:#78716C;font-size:12px;">DATA</td><td style="padding:6px 0;color:#1C1917;font-size:14px;">{data_esc}</td></tr>
              {scad_row}
              <tr><td style="padding:6px 0;color:#78716C;font-size:12px;">METODO</td><td style="padding:6px 0;color:#1C1917;font-size:14px;text-transform:capitalize;">{metodo_esc}</td></tr>
            </table>
          </td></tr>
          <tr><td style="color:#78716C;font-size:12px;line-height:1.5;padding-top:24px;border-top:1px solid #F5F5F4;text-align:center;margin-top:16px;">
            Conserva questa email come promemoria.<br/><b>{nome_scuola_esc}</b>{sito_row}
          </td></tr>
        </table>
      </td></tr>
    </table>
    """


async def _send_email(to: str, subject: str, html: str) -> dict:
    if not EMAIL_KEY:
        raise HTTPException(status_code=500, detail="Servizio email non configurato")
    payload = {"to": [to], "subject": subject, "html": html, "from_name": EMAIL_FROM_NAME}
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{EMAIL_BASE_URL}/api/v1/email/send",
                             headers={"X-Email-Key": EMAIL_KEY}, json=payload)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"Email fail: {e.response.status_code} {e.response.text}")
        raise HTTPException(status_code=502, detail="Errore invio email")
    except Exception as e:
        logger.error(f"Email error: {e}")
        raise HTTPException(status_code=500, detail="Errore invio email")


class InviaPromemoriaBody(BaseModel):
    allievo_id: str


@api.post("/pagamenti/invia-promemoria")
async def invia_promemoria(body: InviaPromemoriaBody, user: dict = Depends(require_teacher)):
    allievo = await db.allievi.find_one({"id": body.allievo_id}, {"_id": 0})
    if not allievo:
        raise HTTPException(status_code=404, detail="Allievo non trovato")
    if not allievo.get("email"):
        raise HTTPException(status_code=400, detail="L'allievo non ha un'email registrata")
    abb = await db.pagamenti.find(
        {"allievo_id": body.allievo_id, "tipo": "abbonamento"}, {"_id": 0}
    ).sort("data_pagamento", -1).to_list(1)
    if not abb:
        raise HTTPException(status_code=400, detail="Nessun abbonamento trovato")
    stato = _stato_pagamento(abb[0].get("scadenza"))
    if stato not in ("in_scadenza", "scaduto"):
        raise HTTPException(status_code=400, detail="Nessun promemoria da inviare (abbonamento attivo)")
    imp = await _get_impostazioni()
    html = _email_promemoria_html(f"{allievo['nome']} {allievo['cognome']}", abb[0]["scadenza"], stato, imp)
    subject = "Il tuo abbonamento è scaduto" if stato == "scaduto" else "Il tuo abbonamento sta per scadere"
    result = await _send_email(allievo["email"], subject, html)
    return {"ok": True, "email": allievo["email"], "stato": stato, "email_id": result.get("id")}


@api.post("/pagamenti/invia-promemoria-tutti")
async def invia_promemoria_tutti(user: dict = Depends(require_teacher)):
    allievi = await db.allievi.find({}, {"_id": 0}).to_list(1000)
    imp = await _get_impostazioni()
    inviati: List[dict] = []
    saltati: List[dict] = []
    for a in allievi:
        if not a.get("email"):
            saltati.append({"allievo": f"{a['nome']} {a['cognome']}", "motivo": "email mancante"})
            continue
        abb_list = await db.pagamenti.find(
            {"allievo_id": a["id"], "tipo": "abbonamento"}, {"_id": 0}
        ).sort("data_pagamento", -1).to_list(1)
        if not abb_list:
            saltati.append({"allievo": f"{a['nome']} {a['cognome']}", "motivo": "nessun abbonamento"})
            continue
        stato = _stato_pagamento(abb_list[0].get("scadenza"))
        if stato not in ("in_scadenza", "scaduto"):
            continue
        html = _email_promemoria_html(f"{a['nome']} {a['cognome']}", abb_list[0]["scadenza"], stato, imp)
        subject = "Il tuo abbonamento è scaduto" if stato == "scaduto" else "Il tuo abbonamento sta per scadere"
        try:
            await _send_email(a["email"], subject, html)
            inviati.append({"allievo": f"{a['nome']} {a['cognome']}", "email": a["email"], "stato": stato})
        except HTTPException as e:
            saltati.append({"allievo": f"{a['nome']} {a['cognome']}", "motivo": e.detail})
    return {"inviati": inviati, "saltati": saltati, "totale_inviati": len(inviati)}


class InviaRicevutaBody(BaseModel):
    pagamento_id: str


@api.post("/pagamenti/invia-ricevuta")
async def invia_ricevuta(body: InviaRicevutaBody, user: dict = Depends(require_teacher)):
    pag = await db.pagamenti.find_one({"id": body.pagamento_id}, {"_id": 0})
    if not pag:
        raise HTTPException(status_code=404, detail="Pagamento non trovato")
    allievo = await db.allievi.find_one({"id": pag["allievo_id"]}, {"_id": 0})
    if not allievo:
        raise HTTPException(status_code=404, detail="Allievo non trovato")
    if not allievo.get("email"):
        raise HTTPException(status_code=400, detail="L'allievo non ha un'email registrata")
    imp = await _get_impostazioni()
    html = _email_ricevuta_html(f"{allievo['nome']} {allievo['cognome']}", pag, imp)
    result = await _send_email(allievo["email"], "Ricevuta di pagamento - VFIT", html)
    return {"ok": True, "email": allievo["email"], "email_id": result.get("id")}


# ---------------------- Compleanni / Annunci / Foto ----------------------
@api.get("/allievi/compleanni")
async def compleanni(user: dict = Depends(require_teacher)):
    """Ritorna gli allievi che compiono gli anni oggi o questo mese."""
    today = date.today()
    docs = await db.allievi.find({"data_nascita": {"$ne": None}}, {"_id": 0}).to_list(1000)
    oggi_list = []
    mese_list = []
    for a in docs:
        try:
            dn = date.fromisoformat(a["data_nascita"])
        except Exception:
            continue
        if dn.month == today.month:
            eta = today.year - dn.year - (1 if (today.month, today.day) < (dn.month, dn.day) else 0)
            item = {**a, "eta_prossima": today.year - dn.year, "giorno": dn.day, "mese": dn.month}
            if dn.day == today.day:
                item["eta_oggi"] = eta if (today.month, today.day) >= (dn.month, dn.day) else eta + 1
                oggi_list.append(item)
            mese_list.append(item)
    mese_list.sort(key=lambda x: x["giorno"])
    return {"oggi": oggi_list, "mese": mese_list}


class FotoBody(BaseModel):
    foto: str = Field(max_length=2_500_000)  # SEC: limite ~2.5MB base64


@api.patch("/allievi/{aid}/foto")
async def update_foto(aid: str, body: FotoBody, user: dict = Depends(current_user)):
    if user["ruolo"] == "student" and user.get("allievo_id") != aid:
        raise HTTPException(status_code=403, detail="Non autorizzato")
    r = await db.allievi.find_one_and_update(
        {"id": aid}, {"$set": {"foto": body.foto}}, projection={"_id": 0}, return_document=True
    )
    if not r:
        raise HTTPException(status_code=404, detail="Allievo non trovato")
    return r


class AnnuncioCreate(BaseModel):
    titolo: str
    testo: str
    tipo: Literal["info", "avviso", "urgente"] = "info"
    attivo: bool = True


class Annuncio(AnnuncioCreate):
    id: str
    created_at: str


@api.get("/annunci", response_model=List[Annuncio])
async def list_annunci(solo_attivi: bool = False, user: dict = Depends(current_user)):
    q: dict = {}
    if solo_attivi or user["ruolo"] == "student":
        q["attivo"] = True
    docs = await db.annunci.find(q, {"_id": 0}).sort("created_at", -1).to_list(200)
    return docs


@api.post("/annunci", response_model=Annuncio)
async def create_annuncio(body: AnnuncioCreate, user: dict = Depends(require_staff_or_teacher)):
    doc = body.model_dump()
    doc["id"] = str(uuid.uuid4())
    doc["created_at"] = datetime.now(timezone.utc).isoformat()
    await db.annunci.insert_one(doc.copy())
    return doc


@api.put("/annunci/{aid}", response_model=Annuncio)
async def update_annuncio(aid: str, body: AnnuncioCreate, user: dict = Depends(require_staff_or_teacher)):
    r = await db.annunci.find_one_and_update(
        {"id": aid}, {"$set": body.model_dump()}, projection={"_id": 0}, return_document=True
    )
    if not r:
        raise HTTPException(status_code=404, detail="Annuncio non trovato")
    return r


@api.delete("/annunci/{aid}")
async def delete_annuncio(aid: str, user: dict = Depends(require_staff_or_teacher)):
    r = await db.annunci.delete_one({"id": aid})
    if r.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Annuncio non trovato")
    return {"ok": True}


# ---------------------- Pubblico (ospiti) ----------------------
@api.get("/pubblico/impostazioni")
async def impostazioni_pubbliche():
    imp = await _get_impostazioni()
    return {
        "nome_scuola": imp.get("nome_scuola"),
        "sito_web": imp.get("sito_web"),
        "contatto_email": imp.get("contatto_email"),
        "contatto_telefono": imp.get("contatto_telefono"),
        "indirizzo": imp.get("indirizzo"),
    }


@api.get("/pubblico/lezioni")
async def lezioni_pubbliche():
    today = date.today().isoformat()
    docs = await db.lezioni.find(
        {"data": {"$gte": today}}, {"_id": 0}
    ).sort([("data", 1), ("ora_inizio", 1)]).to_list(200)
    result = []
    for l in docs:
        result.append({
            "id": l["id"],
            "corso_nome": l["corso_nome"],
            "sala": l["sala"],
            "insegnante": l["insegnante"],
            "data": l["data"],
            "ora_inizio": l["ora_inizio"],
            "ora_fine": l["ora_fine"],
            "capacita": l["capacita"],
            "posti_disponibili": max(0, l["capacita"] - len(l.get("prenotati", []))),
        })
    return result


class PrenotazioneOspiteBody(BaseModel):
    nome: str = Field(min_length=1, max_length=80)
    cognome: str = Field(min_length=1, max_length=80)
    email: EmailStr
    telefono: str = Field(min_length=5, max_length=30)
    lezione_id: str = Field(max_length=64)
    note: Optional[str] = Field(default=None, max_length=500)


@api.post("/pubblico/prenota-ospite")
async def prenota_ospite(body: PrenotazioneOspiteBody):
    # SEC-003: rate limiting per email + telefono (max 3 richieste/ora)
    _rate_check(_public_attempts, f"e:{body.email.lower()}", limit=3, window=3600)
    _rate_check(_public_attempts, f"t:{body.telefono.strip()}", limit=3, window=3600)
    lez = await db.lezioni.find_one({"id": body.lezione_id}, {"_id": 0})
    if not lez:
        raise HTTPException(status_code=404, detail="Lezione non trovata")
    doc = {
        "id": str(uuid.uuid4()),
        "nome": body.nome.strip(),
        "cognome": body.cognome.strip(),
        "email": body.email.lower(),
        "telefono": body.telefono.strip(),
        "lezione_id": body.lezione_id,
        "lezione_info": f"{lez['corso_nome']} · {lez['data']} · {lez['ora_inizio']} · {lez['sala']}",
        "stato": "in_attesa",
        "note": body.note,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.prenotazioni_ospiti.insert_one(doc.copy())
    return {"ok": True, "id": doc["id"], "messaggio": "Prenotazione ricevuta! Ti contatteremo per la conferma."}


@api.get("/prenotazioni-ospiti")
async def list_prenotazioni_ospiti(stato: Optional[str] = None, user: dict = Depends(require_teacher)):
    q: dict = {}
    if stato: q["stato"] = stato
    docs = await db.prenotazioni_ospiti.find(q, {"_id": 0}).sort("created_at", -1).to_list(1000)
    return docs


@api.post("/prenotazioni-ospiti/{pid}/conferma")
async def conferma_ospite(pid: str, user: dict = Depends(require_teacher)):
    po = await db.prenotazioni_ospiti.find_one({"id": pid}, {"_id": 0})
    if not po:
        raise HTTPException(status_code=404, detail="Prenotazione non trovata")
    esistente = None
    if po.get("email"):
        esistente = await db.allievi.find_one({"email": po["email"].lower()}, {"_id": 0})
    creato_ora = False
    if esistente:
        allievo_id = esistente["id"]
    else:
        allievo_id = str(uuid.uuid4())
        imp = await _get_impostazioni()
        await db.allievi.insert_one({
            "id": allievo_id,
            "nome": po["nome"],
            "cognome": po["cognome"],
            "email": po.get("email"),
            "telefono": po.get("telefono"),
            "data_nascita": None,
            "note": "Creato da prenotazione ospite",
            "user_id": None,
            "wallet_saldo": 0.0,
            "codice_referral": _gen_referral_code(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        creato_ora = True
        # Bonus benvenuto
        bonus = float(imp.get("bonus_benvenuto_importo", 0) or 0)
        if bonus > 0:
            await _wallet_accredita(allievo_id, bonus, "🎁 Bonus di benvenuto")
        # Crediti VFIT all'insegnante
        crediti = int(imp.get("crediti_per_iscrizione", 5) or 0)
        if crediti > 0:
            await _accredita_crediti_vfit(crediti, f"Nuovo allievo (ospite): {po['nome']} {po['cognome']}", allievo_id, f"{po['nome']} {po['cognome']}")
    lez = await db.lezioni.find_one({"id": po["lezione_id"]}, {"_id": 0})
    added = False
    if lez and allievo_id not in lez.get("prenotati", []) and len(lez.get("prenotati", [])) < lez["capacita"]:
        prenot = lez.get("prenotati", []) + [allievo_id]
        await db.lezioni.update_one({"id": po["lezione_id"]}, {"$set": {"prenotati": prenot}})
        added = True
    await db.prenotazioni_ospiti.update_one({"id": pid}, {"$set": {"stato": "confermata", "allievo_id": allievo_id}})
    return {"ok": True, "allievo_id": allievo_id, "aggiunto_a_lezione": added, "nuovo_allievo_creato": creato_ora}


@api.post("/prenotazioni-ospiti/{pid}/rifiuta")
async def rifiuta_ospite(pid: str, user: dict = Depends(require_teacher)):
    r = await db.prenotazioni_ospiti.update_one({"id": pid}, {"$set": {"stato": "rifiutata"}})
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Prenotazione non trovata")
    return {"ok": True}


@api.delete("/prenotazioni-ospiti/{pid}")
async def delete_ospite(pid: str, user: dict = Depends(require_teacher)):
    r = await db.prenotazioni_ospiti.delete_one({"id": pid})
    if r.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Prenotazione non trovata")
    return {"ok": True}


# ---------------------- Shop (eShop) ----------------------
SHOP_CATEGORIES = ["Divisa", "Borraccia", "Zaino", "Borsone", "T-shirt", "Top"]


class ProdottoCreate(BaseModel):
    categoria: str
    nome: str
    descrizione: Optional[str] = None
    prezzo: float
    foto: Optional[str] = None
    disponibile: bool = True
    taglie: Optional[List[str]] = None


class Prodotto(ProdottoCreate):
    id: str
    created_at: str


@api.get("/shop/categorie")
async def shop_categorie(user: dict = Depends(current_user)):
    return SHOP_CATEGORIES


@api.get("/shop/prodotti", response_model=List[Prodotto])
async def list_prodotti(categoria: Optional[str] = None, user: dict = Depends(current_user)):
    q: dict = {}
    if categoria: q["categoria"] = categoria
    if user["ruolo"] == "student": q["disponibile"] = True
    docs = await db.prodotti.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    return docs


@api.post("/shop/prodotti", response_model=Prodotto)
async def create_prodotto(body: ProdottoCreate, user: dict = Depends(require_staff_or_teacher)):
    if body.categoria not in SHOP_CATEGORIES:
        raise HTTPException(status_code=400, detail="Categoria non valida")
    doc = body.model_dump()
    doc["id"] = str(uuid.uuid4())
    doc["created_at"] = datetime.now(timezone.utc).isoformat()
    await db.prodotti.insert_one(doc.copy())
    return doc


@api.put("/shop/prodotti/{pid}", response_model=Prodotto)
async def update_prodotto(pid: str, body: ProdottoCreate, user: dict = Depends(require_staff_or_teacher)):
    r = await db.prodotti.find_one_and_update(
        {"id": pid}, {"$set": body.model_dump()}, projection={"_id": 0}, return_document=True
    )
    if not r: raise HTTPException(status_code=404, detail="Prodotto non trovato")
    return r


@api.delete("/shop/prodotti/{pid}")
async def delete_prodotto(pid: str, user: dict = Depends(require_staff_or_teacher)):
    r = await db.prodotti.delete_one({"id": pid})
    if r.deleted_count == 0: raise HTTPException(status_code=404, detail="Prodotto non trovato")
    return {"ok": True}


# ---------------------- Wallet & Buoni ----------------------
@api.get("/wallet/mio")
async def wallet_mio(user: dict = Depends(current_user)):
    if user["ruolo"] != "student":
        raise HTTPException(status_code=403, detail="Solo allievi")
    aid = user.get("allievo_id")
    if not aid:
        raise HTTPException(status_code=400, detail="Allievo non collegato")
    a = await db.allievi.find_one({"id": aid}, {"_id": 0})
    if not a:
        raise HTTPException(status_code=404, detail="Allievo non trovato")
    mov = await db.wallet_movimenti.find({"allievo_id": aid}, {"_id": 0}).sort("created_at", -1).to_list(200)
    return {
        "saldo": round(float(a.get("wallet_saldo", 0.0)), 2),
        "codice_referral": a.get("codice_referral"),
        "movimenti": mov,
    }


@api.get("/allievi/{aid}/wallet")
async def get_wallet_allievo(aid: str, user: dict = Depends(require_teacher)):
    a = await db.allievi.find_one({"id": aid}, {"_id": 0})
    if not a:
        raise HTTPException(status_code=404, detail="Allievo non trovato")
    mov = await db.wallet_movimenti.find({"allievo_id": aid}, {"_id": 0}).sort("created_at", -1).to_list(200)
    return {
        "saldo": round(float(a.get("wallet_saldo", 0.0)), 2),
        "codice_referral": a.get("codice_referral"),
        "nome": a.get("nome"),
        "cognome": a.get("cognome"),
        "movimenti": mov,
    }


@api.post("/allievi/{aid}/wallet/accredita")
async def wallet_accredita_manuale(aid: str, body: WalletAccreditaBody, user: dict = Depends(require_teacher)):
    a = await db.allievi.find_one({"id": aid}, {"_id": 0})
    if not a:
        raise HTTPException(status_code=404, detail="Allievo non trovato")
    await _wallet_accredita(aid, body.importo, body.causale or "Accredito manuale")
    fresh = await db.allievi.find_one({"id": aid}, {"_id": 0})
    return {"ok": True, "saldo": round(float(fresh.get("wallet_saldo", 0.0)), 2)}


@api.get("/buoni")
async def list_buoni(stato: Optional[str] = None, user: dict = Depends(require_teacher)):
    q: dict = {}
    if stato == "attivi":
        q = {"riscattato_da": None, "attivo": True}
    elif stato == "riscattati":
        q = {"riscattato_da": {"$ne": None}}
    docs = await db.buoni.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    return docs


@api.post("/buoni")
async def create_buono(body: BuonoCreate, user: dict = Depends(require_teacher)):
    codice = (body.codice or _gen_buono_code()).strip().upper()
    exists = await db.buoni.find_one({"codice": codice})
    if exists:
        raise HTTPException(status_code=409, detail="Codice già esistente")
    doc = body.model_dump()
    doc["codice"] = codice
    doc["id"] = str(uuid.uuid4())
    doc["attivo"] = True
    doc["riscattato_da"] = None
    doc["riscattato_il"] = None
    doc["created_at"] = datetime.now(timezone.utc).isoformat()
    await db.buoni.insert_one(doc.copy())
    doc.pop("_id", None)
    return doc


@api.post("/buoni/riscatta")
async def riscatta_buono(body: RiscattaBody, user: dict = Depends(current_user)):
    codice = body.codice.strip().upper()
    b = await db.buoni.find_one({"codice": codice}, {"_id": 0})
    if not b or not b.get("attivo", True):
        raise HTTPException(status_code=404, detail="Codice non valido")
    if b.get("riscattato_da"):
        raise HTTPException(status_code=400, detail="Buono già utilizzato")
    if b.get("scadenza"):
        try:
            if date.fromisoformat(b["scadenza"]) < date.today():
                raise HTTPException(status_code=400, detail="Buono scaduto")
        except HTTPException:
            raise
        except Exception:
            pass
    # Determina destinatario
    if user["ruolo"] == "student":
        aid = user.get("allievo_id")
    else:
        aid = body.allievo_id
    if not aid:
        raise HTTPException(status_code=400, detail="Allievo mancante")
    # Se il buono è destinato a un allievo specifico, deve corrispondere
    if b.get("allievo_id") and b["allievo_id"] != aid:
        raise HTTPException(status_code=403, detail="Questo buono non è per te")
    await db.buoni.update_one(
        {"id": b["id"]},
        {"$set": {"riscattato_da": aid, "riscattato_il": datetime.now(timezone.utc).isoformat()}},
    )
    await _wallet_accredita(aid, float(b["importo"]), f"Buono {b.get('tipo','manuale')} · {codice}", b["id"])
    fresh = await db.allievi.find_one({"id": aid}, {"_id": 0})
    return {
        "ok": True,
        "importo": float(b["importo"]),
        "nuovo_saldo": round(float(fresh.get("wallet_saldo", 0.0)), 2),
    }


@api.delete("/buoni/{bid}")
async def delete_buono(bid: str, user: dict = Depends(require_teacher)):
    b = await db.buoni.find_one({"id": bid}, {"_id": 0})
    if not b:
        raise HTTPException(status_code=404, detail="Buono non trovato")
    if b.get("riscattato_da"):
        raise HTTPException(status_code=400, detail="Non puoi eliminare un buono già riscattato")
    await db.buoni.delete_one({"id": bid})
    return {"ok": True}


# ---------------------- Report finanziario ----------------------
@api.get("/report/statistiche")
async def report_statistiche(user: dict = Depends(require_teacher)):
    today = date.today()
    # incassi ultimi 12 mesi
    mesi_labels = ["Gen","Feb","Mar","Apr","Mag","Giu","Lug","Ago","Set","Ott","Nov","Dic"]
    incassi_mensili: List[dict] = []
    # Costruisci lista di (anno, mese) per gli ultimi 12 mesi
    y, m = today.year, today.month
    months_range: List[tuple] = []
    for _ in range(12):
        months_range.append((y, m))
        m -= 1
        if m == 0: m = 12; y -= 1
    months_range.reverse()
    all_pay = await db.pagamenti.find({}, {"_id": 0}).to_list(5000)
    by_ym: dict = defaultdict(lambda: {"totale": 0.0, "abbonamenti": 0.0, "singole": 0.0, "count": 0})
    for p in all_pay:
        try:
            d = date.fromisoformat(p["data_pagamento"])
        except Exception:
            continue
        key = (d.year, d.month)
        imp = float(p.get("importo", 0) or 0)
        by_ym[key]["totale"] += imp
        by_ym[key]["count"] += 1
        if p.get("tipo") == "abbonamento":
            by_ym[key]["abbonamenti"] += imp
        else:
            by_ym[key]["singole"] += imp
    for (yy, mm) in months_range:
        v = by_ym.get((yy, mm), {"totale": 0.0, "abbonamenti": 0.0, "singole": 0.0, "count": 0})
        incassi_mensili.append({
            "anno": yy,
            "mese": mm,
            "label": f"{mesi_labels[mm-1]} {str(yy)[-2:]}",
            "totale": round(v["totale"], 2),
            "abbonamenti": round(v["abbonamenti"], 2),
            "singole": round(v["singole"], 2),
            "count": v["count"],
        })
    incasso_mese = incassi_mensili[-1]["totale"] if incassi_mensili else 0
    incasso_prec = incassi_mensili[-2]["totale"] if len(incassi_mensili) > 1 else 0
    variazione = 0.0
    if incasso_prec > 0:
        variazione = round(((incasso_mese - incasso_prec) / incasso_prec) * 100, 1)
    elif incasso_mese > 0:
        variazione = 100.0
    incasso_anno = round(sum(x["totale"] for x in incassi_mensili if x["anno"] == today.year), 2)
    # Distribuzione metodi
    metodi: dict = defaultdict(float)
    for p in all_pay:
        m_ = (p.get("metodo") or "contanti").lower()
        metodi[m_] += float(p.get("importo", 0) or 0)
    # Top allievi
    top_by_aid: dict = defaultdict(float)
    count_by_aid: dict = defaultdict(int)
    for p in all_pay:
        aid = p.get("allievo_id")
        if aid:
            top_by_aid[aid] += float(p.get("importo", 0) or 0)
            count_by_aid[aid] += 1
    allievi_list = await db.allievi.find({}, {"_id": 0}).to_list(2000)
    all_map = {a["id"]: a for a in allievi_list}
    top_allievi = []
    for aid, tot in sorted(top_by_aid.items(), key=lambda x: -x[1])[:10]:
        a = all_map.get(aid)
        if not a: continue
        top_allievi.append({
            "allievo_id": aid,
            "nome": a.get("nome"),
            "cognome": a.get("cognome"),
            "totale": round(tot, 2),
            "numero_pagamenti": count_by_aid[aid],
        })
    # Prossime scadenze (abbonamenti in scadenza nei prossimi 14 giorni + scaduti recenti)
    scadenze = []
    attivi = 0
    scaduti_c = 0
    in_scadenza_c = 0
    for a in allievi_list:
        abb = await db.pagamenti.find({"allievo_id": a["id"], "tipo": "abbonamento"}, {"_id": 0}).sort("data_pagamento", -1).to_list(1)
        if not abb:
            continue
        scad = abb[0].get("scadenza")
        if not scad:
            continue
        try:
            d = date.fromisoformat(scad)
        except Exception:
            continue
        giorni = (d - today).days
        stato = _stato_pagamento(scad)
        if stato == "attivo": attivi += 1
        elif stato == "in_scadenza": in_scadenza_c += 1; attivi += 1
        elif stato == "scaduto": scaduti_c += 1
        if -30 <= giorni <= 14:
            scadenze.append({
                "allievo_id": a["id"],
                "nome": a.get("nome"),
                "cognome": a.get("cognome"),
                "scadenza": scad,
                "giorni": giorni,
                "stato": stato,
            })
    scadenze.sort(key=lambda x: x["giorni"])
    # Totale accreditato su wallet
    tot_wallet = 0.0
    for a in allievi_list:
        tot_wallet += float(a.get("wallet_saldo", 0.0) or 0.0)
    return {
        "kpi": {
            "incasso_mese_corrente": round(incasso_mese, 2),
            "incasso_mese_precedente": round(incasso_prec, 2),
            "variazione_pct": variazione,
            "incasso_anno": incasso_anno,
            "allievi_totali": len(allievi_list),
            "allievi_attivi": attivi,
            "abbonamenti_scaduti": scaduti_c,
            "abbonamenti_in_scadenza": in_scadenza_c,
            "saldo_wallet_totale": round(tot_wallet, 2),
        },
        "incassi_mensili": incassi_mensili,
        "distribuzione_metodi": {k: round(v, 2) for k, v in metodi.items()},
        "top_allievi": top_allievi,
        "prossime_scadenze": scadenze[:20],
    }


# ---------------------- Crediti VFIT (insegnante loyalty) ----------------------
@api.get("/crediti-vfit")
async def get_crediti_vfit(user: dict = Depends(require_teacher)):
    doc = await db.crediti_vfit.find_one({"_id": "main"}) or {}
    storico = await db.crediti_storico.find({}, {"_id": 0}).sort("created_at", -1).to_list(200)
    return {
        "saldo": int(doc.get("saldo", 0)),
        "totale_guadagnati": int(doc.get("totale_guadagnati", 0)),
        "spesi": int(doc.get("spesi", 0)),
        "storico": storico,
    }


class UtilizzaCreditiBody(BaseModel):
    quantita: int = Field(ge=1)
    motivo: str = Field(min_length=1, max_length=200)


@api.post("/crediti-vfit/utilizza")
async def utilizza_crediti_vfit(body: UtilizzaCreditiBody, user: dict = Depends(require_teacher)):
    nuovo_saldo = await _utilizza_crediti_vfit(body.quantita, body.motivo)
    return {"ok": True, "nuovo_saldo": nuovo_saldo}


# ---------------------- Eventi ----------------------
class EventoCreate(BaseModel):
    titolo: str = Field(min_length=1, max_length=120)
    descrizione: Optional[str] = Field(default=None, max_length=2000)
    data: str  # ISO date YYYY-MM-DD
    ora: Optional[str] = None  # HH:MM
    luogo: Optional[str] = None
    foto: Optional[str] = None  # base64
    link_iscrizione: Optional[str] = None
    categoria: Literal["concorso", "saggio", "workshop", "stage", "spettacolo", "altro"] = "altro"
    attivo: bool = True


class Evento(EventoCreate):
    id: str
    created_at: str


@api.get("/eventi", response_model=List[Evento])
async def list_eventi(solo_attivi: bool = False, user: dict = Depends(current_user)):
    q: dict = {}
    if solo_attivi or user["ruolo"] in ("student", "staff"):
        q["attivo"] = True
    docs = await db.eventi.find(q, {"_id": 0}).sort("data", 1).to_list(500)
    return docs


@api.get("/pubblico/eventi")
async def list_eventi_pubblici():
    today = date.today().isoformat()
    docs = await db.eventi.find({"attivo": True, "data": {"$gte": today}}, {"_id": 0}).sort("data", 1).to_list(200)
    return docs


@api.post("/eventi", response_model=Evento)
async def create_evento(body: EventoCreate, user: dict = Depends(require_teacher)):
    doc = body.model_dump()
    doc["id"] = str(uuid.uuid4())
    doc["created_at"] = datetime.now(timezone.utc).isoformat()
    await db.eventi.insert_one(doc.copy())
    doc.pop("_id", None)
    return doc


@api.put("/eventi/{eid}", response_model=Evento)
async def update_evento(eid: str, body: EventoCreate, user: dict = Depends(require_teacher)):
    r = await db.eventi.find_one_and_update(
        {"id": eid}, {"$set": body.model_dump()}, projection={"_id": 0}, return_document=True
    )
    if not r:
        raise HTTPException(status_code=404, detail="Evento non trovato")
    return r


@api.delete("/eventi/{eid}")
async def delete_evento(eid: str, user: dict = Depends(require_teacher)):
    r = await db.eventi.delete_one({"id": eid})
    if r.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Evento non trovato")
    return {"ok": True}



# ---------------------- Open Day ----------------------
class OpenDaySlot(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    disciplina: str
    data: Optional[str] = None
    ora_inizio: str
    ora_fine: str
    insegnante: Optional[str] = None
    sala: Optional[str] = None
    posti: Optional[int] = None


class OpenDayAdesione(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nome: str
    cognome: str
    telefono: Optional[str] = None
    email: Optional[str] = None
    disciplina: Optional[str] = None
    orario: Optional[str] = None
    slot_id: Optional[str] = None
    allievo_id: Optional[str] = None
    note: Optional[str] = None
    stato: Optional[str] = "Da confermare"
    fonte: Optional[str] = None


class OpenDayCreate(BaseModel):
    titolo: str = Field(min_length=1, max_length=120)
    data: str
    note: Optional[str] = Field(default=None, max_length=2000)
    slots: List[OpenDaySlot] = []
    adesioni: List[OpenDayAdesione] = []


class OpenDay(OpenDayCreate):
    id: str
    created_at: str


def _open_day_adesione_matches_slot(adesione: dict, slot: dict) -> bool:
    """Collega un'adesione al suo slot.

    Le nuove prenotazioni usano sempre slot_id. Il confronto per
    disciplina/orario resta solo come compatibilità con le vecchie
    adesioni create prima dell'introduzione di slot_id.
    """
    slot_id = slot.get("id")
    if adesione.get("slot_id"):
        return adesione.get("slot_id") == slot_id

    disciplina_a = str(adesione.get("disciplina") or "").strip().casefold()
    disciplina_s = str(slot.get("disciplina") or "").strip().casefold()

    def normalizza_orario(value: str) -> str:
        value = value.replace("–", "-")
        parts = [part.strip() for part in value.split("-", 1)]
        return " - ".join(parts) if len(parts) == 2 else value.strip()

    orario_a = normalizza_orario(str(adesione.get("orario") or ""))
    orario_s = normalizza_orario(
        f"{slot.get('ora_inizio', '')} - {slot.get('ora_fine', '')}"
    )

    return disciplina_a == disciplina_s and orario_a == orario_s


def _open_day_count_slot(adesioni: list, slot: dict) -> int:
    return sum(
        1 for adesione in adesioni
        if _open_day_adesione_matches_slot(adesione, slot)
    )


@api.get("/open-day/disponibili")
async def list_open_day_disponibili(user: dict = Depends(current_user)):
    if user["ruolo"] != "student":
        raise HTTPException(status_code=403, detail="Solo gli allievi possono usare questa sezione")

    docs = await db.open_day.find({}, {"_id": 0}).sort("data", 1).to_list(500)
    result = []

    for doc in docs:
        safe_slots = []

        for slot in doc.get("slots", []):
            slot_id = slot.get("id")
            disciplina = slot.get("disciplina")
            orario = f"{slot.get('ora_inizio', '')}-{slot.get('ora_fine', '')}"

            adesioni = doc.get("adesioni", [])
            count = _open_day_count_slot(adesioni, slot)

            posti = slot.get("posti")
            rimasti = max(int(posti) - count, 0) if posti is not None else None

            mio_id = user.get("allievo_id")
            sono_prenotato = any(
                a.get("allievo_id") == mio_id
                and _open_day_adesione_matches_slot(a, slot)
                for a in adesioni
            )

            safe_slot = {
                "id": slot_id,
                "disciplina": disciplina,
                "data": slot.get("data") or doc.get("data"),
                "ora_inizio": slot.get("ora_inizio"),
                "ora_fine": slot.get("ora_fine"),
                "insegnante": slot.get("insegnante"),
                "sala": slot.get("sala"),
                "posti": posti,
                "partecipanti_count": count,
                "posti_rimasti": rimasti,
                "sono_prenotato": sono_prenotato,
            }
            safe_slots.append(safe_slot)

        result.append({
            "id": doc.get("id"),
            "titolo": doc.get("titolo"),
            "data": doc.get("data"),
            "slots": safe_slots,
        })

    return result


@api.post("/open-day/{oid}/prenota/{slot_id}")
async def prenota_open_day(oid: str, slot_id: str, user: dict = Depends(current_user)):
    if user["ruolo"] != "student":
        raise HTTPException(status_code=403, detail="Solo gli allievi possono prenotare un Open Day")

    allievo_id = user.get("allievo_id")
    if not allievo_id:
        raise HTTPException(status_code=400, detail="Profilo allievo non associato")

    doc = await db.open_day.find_one({"id": oid}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Open Day non trovato")

    slot = next((x for x in doc.get("slots", []) if x.get("id") == slot_id), None)
    if not slot:
        raise HTTPException(status_code=404, detail="Orario Open Day non trovato")

    adesioni = doc.get("adesioni", [])
    disciplina = slot.get("disciplina")
    orario = f"{slot.get('ora_inizio', '')}-{slot.get('ora_fine', '')}"

    if any(
        a.get("allievo_id") == allievo_id
        and _open_day_adesione_matches_slot(a, slot)
        for a in adesioni
    ):
        raise HTTPException(status_code=409, detail="Sei già prenotato a questo Open Day")

    count = _open_day_count_slot(adesioni, slot)

    if slot.get("posti") is not None and count >= int(slot["posti"]):
        raise HTTPException(status_code=409, detail="Posti esauriti")

    allievo = await db.allievi.find_one({"id": allievo_id}, {"_id": 0})
    if not allievo:
        raise HTTPException(status_code=404, detail="Allievo non trovato")

    adesione = {
        "id": str(uuid.uuid4()),
        "nome": allievo.get("nome", ""),
        "cognome": allievo.get("cognome", ""),
        "disciplina": disciplina,
        "orario": orario,
        "slot_id": slot_id,
        "allievo_id": allievo_id,
    }

    await db.open_day.update_one(
        {"id": oid},
        {"$push": {"adesioni": adesione}}
    )

    return {"ok": True, "message": "Prenotazione Open Day confermata"}


@api.get("/open-day", response_model=List[OpenDay])
async def list_open_day(user: dict = Depends(require_teacher)):
    if user["ruolo"] != "admin":
        raise HTTPException(
            status_code=403,
            detail="Solo l'amministratore può visualizzare gli Open Day"
        )

    docs = await db.open_day.find({}, {"_id": 0}).sort("data", 1).to_list(500)

    for event in docs:
        adesioni = event.get("adesioni") or []

        for slot in event.get("slots") or []:
            slot["iscritti"] = _open_day_count_slot(adesioni, slot)

    return docs


@api.post("/open-day", response_model=OpenDay)
async def create_open_day(body: OpenDayCreate, user: dict = Depends(require_teacher)):
    if user["ruolo"] != "admin":
        raise HTTPException(status_code=403, detail="Solo l'amministratore può gestire gli Open Day")

    doc = body.model_dump()
    doc["id"] = str(uuid.uuid4())
    doc["created_at"] = datetime.now(timezone.utc).isoformat()
    await db.open_day.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.put("/open-day/{oid}", response_model=OpenDay)
async def update_open_day(oid: str, body: OpenDayCreate, user: dict = Depends(require_teacher)):
    if user["ruolo"] != "admin":
        raise HTTPException(status_code=403, detail="Solo l'amministratore può gestire gli Open Day")

    doc = body.model_dump()
    result = await db.open_day.update_one({"id": oid}, {"$set": doc})

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Open Day non trovato")

    updated = await db.open_day.find_one({"id": oid}, {"_id": 0})
    return updated


@api.delete("/open-day/{oid}")
async def delete_open_day(oid: str, user: dict = Depends(require_teacher)):
    if user["ruolo"] != "admin":
        raise HTTPException(status_code=403, detail="Solo l'amministratore può gestire gli Open Day")

    result = await db.open_day.delete_one({"id": oid})

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Open Day non trovato")

    return {"ok": True}


@api.post("/open-day/{oid}/adesioni", response_model=OpenDay)
async def add_open_day_adesione(
    oid: str,
    body: OpenDayAdesione,
    user: dict = Depends(require_teacher),
):
    if user["ruolo"] != "admin":
        raise HTTPException(status_code=403, detail="Solo l'amministratore può gestire le adesioni")

    event = await db.open_day.find_one({"id": oid}, {"_id": 0})
    if not event:
        raise HTTPException(status_code=404, detail="Open Day non trovato")

    slots_event = event.get("slots") or []
    slot = None

    if body.slot_id:
        slot = next((s for s in slots_event if s.get("id") == body.slot_id), None)
        if not slot:
            raise HTTPException(status_code=404, detail="Orario Open Day non trovato")
    else:
        # Compatibilità con vecchie versioni: prova a ricavare lo slot.
        disciplina = str(body.disciplina or "").strip().casefold()
        orario = str(body.orario or "").replace("–", "-").strip()
        matches = [
            s for s in slots_event
            if str(s.get("disciplina") or "").strip().casefold() == disciplina
            and f"{s.get('ora_inizio', '')} - {s.get('ora_fine', '')}".strip() == orario
        ]
        if len(matches) == 1:
            slot = matches[0]
        elif len(matches) > 1:
            raise HTTPException(
                status_code=400,
                detail="Seleziona l'orario preciso dell'Open Day"
            )

    if slot:
        current_count = _open_day_count_slot(event.get("adesioni") or [], slot)
        posti = slot.get("posti")
        if posti is not None and current_count >= int(posti):
            raise HTTPException(status_code=409, detail="Posti esauriti")

    adesione = body.model_dump()
    if slot:
        adesione["slot_id"] = slot["id"]
        adesione["disciplina"] = slot.get("disciplina")
        adesione["orario"] = f"{slot.get('ora_inizio', '')} - {slot.get('ora_fine', '')}"
    adesione["stato"] = adesione.get("stato") or "Da confermare"
    adesione["fonte"] = adesione.get("fonte") or "admin"

    result = await db.open_day.update_one(
        {"id": oid},
        {"$push": {"adesioni": adesione}},
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Open Day non trovato")

    return await db.open_day.find_one({"id": oid}, {"_id": 0})


@api.delete("/open-day/{oid}/adesioni/{aid}")
async def delete_open_day_adesione(
    oid: str,
    aid: str,
    user: dict = Depends(require_teacher),
):
    if user["ruolo"] != "admin":
        raise HTTPException(status_code=403, detail="Solo l'amministratore può gestire le adesioni")

    result = await db.open_day.update_one(
        {"id": oid},
        {"$pull": {"adesioni": {"id": aid}}},
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Open Day non trovato")

    return {"ok": True}


# ---------------------- Staff (insegnanti collaboratori) ----------------------
class StaffCreate(BaseModel):
    nome: str
    email: EmailStr
    password: str = Field(min_length=6, max_length=100)
    telefono: Optional[str] = None
    corsi_ids: List[str] = []


class StaffUpdate(BaseModel):
    nome: Optional[str] = None
    telefono: Optional[str] = None
    corsi_ids: Optional[List[str]] = None
    password: Optional[str] = None


class StaffPublic(BaseModel):
    id: str
    email: EmailStr
    nome: str
    telefono: Optional[str] = None
    corsi_ids: List[str] = []
    created_at: str


@api.get("/staff", response_model=List[StaffPublic])
async def list_staff(user: dict = Depends(require_teacher)):
    docs = await db.users.find({"ruolo": "staff"}, {"_id": 0, "password_hash": 0}).sort("nome", 1).to_list(200)
    return docs


@api.post("/staff", response_model=StaffPublic)
async def create_staff(body: StaffCreate, user: dict = Depends(require_teacher)):
    email = body.email.lower()
    exists = await db.users.find_one({"email": email})
    if exists:
        raise HTTPException(status_code=409, detail="Email già registrata")
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        "email": email,
        "nome": body.nome.strip(),
        "telefono": body.telefono,
        "ruolo": "staff",
        "corsi_ids": list(body.corsi_ids or []),
        "password_hash": hash_password(body.password),
        "created_at": now,
    }
    await db.users.insert_one(doc.copy())
    doc.pop("_id", None)
    doc.pop("password_hash", None)
    return doc


@api.put("/staff/{sid}", response_model=StaffPublic)
async def update_staff(sid: str, body: StaffUpdate, user: dict = Depends(require_teacher)):
    update: dict = {}
    if body.nome is not None: update["nome"] = body.nome.strip()
    if body.telefono is not None: update["telefono"] = body.telefono
    if body.corsi_ids is not None: update["corsi_ids"] = list(body.corsi_ids)
    if body.password: update["password_hash"] = hash_password(body.password)
    if not update:
        raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")
    r = await db.users.find_one_and_update(
        {"id": sid, "ruolo": "staff"}, {"$set": update},
        projection={"_id": 0, "password_hash": 0}, return_document=True,
    )
    if not r:
        raise HTTPException(status_code=404, detail="Staff non trovato")
    return r


@api.delete("/staff/{sid}")
async def delete_staff(sid: str, user: dict = Depends(require_teacher)):
    r = await db.users.delete_one({"id": sid, "ruolo": "staff"})
    if r.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Staff non trovato")
    return {"ok": True}


# ---------------------- Endpoint Staff (visibilità limitata) ----------------------
@api.get("/staff/miei-corsi")
async def staff_miei_corsi(user: dict = Depends(current_user)):
    """Corsi assegnati allo staff loggato."""
    if user["ruolo"] != "staff":
        raise HTTPException(status_code=403, detail="Solo staff")
    ids = await _staff_corsi_ids(user)
    if not ids:
        return []
    docs = await db.corsi.find({"id": {"$in": ids}}, {"_id": 0}).to_list(200)
    return docs


@api.get("/staff/miei-allievi")
async def staff_miei_allievi(user: dict = Depends(current_user)):
    """Allievi iscritti alle lezioni dei corsi dello staff."""
    if user["ruolo"] != "staff":
        raise HTTPException(status_code=403, detail="Solo staff")
    ids = await _staff_corsi_ids(user)
    if not ids:
        return []
    # Trova tutte le lezioni di quei corsi e raccogli prenotati
    lezioni = await db.lezioni.find({"corso_id": {"$in": ids}}, {"_id": 0, "prenotati": 1}).to_list(5000)
    aids = set()
    for l in lezioni:
        for a in (l.get("prenotati") or []):
            aids.add(a)
    if not aids:
        return []
    docs = await db.allievi.find(
        {"id": {"$in": list(aids)}},
        {"_id": 0, "wallet_saldo": 0, "codice_referral": 0, "note": 0},
    ).sort("cognome", 1).to_list(2000)
    return docs


# ---------------------- Health ----------------------
@api.get("/")
async def root():
    return {"app": "VFIT API", "status": "ok"}



app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# OPEN DAY PUBBLICO - accesso senza login
# ============================================================

@api.get("/pubblico/open-day")
async def list_open_day_pubblico():
    docs = await db.open_day.find({}).sort("data", 1).to_list(1000)

    result = []

    for doc in docs:
        slots_result = []

        adesioni = doc.get("adesioni") or []

        for slot in doc.get("slots") or []:
            slot_id = slot.get("id")
            disciplina = slot.get("disciplina")
            ora_inizio = slot.get("ora_inizio")
            ora_fine = slot.get("ora_fine")

            count = _open_day_count_slot(adesioni, slot)

            posti = slot.get("posti")

            if posti is None:
                posti_disponibili = None
                esaurito = False
            else:
                posti_disponibili = max(int(posti) - count, 0)
                esaurito = posti_disponibili <= 0

            slots_result.append({
                "id": slot_id,
                "disciplina": disciplina,
                "data": slot.get("data") or doc.get("data"),
                "ora_inizio": ora_inizio,
                "ora_fine": ora_fine,
                "insegnante": slot.get("insegnante"),
                "sala": slot.get("sala"),
                "posti": posti,
                "partecipanti": count,
                "posti_disponibili": posti_disponibili,
                "esaurito": esaurito,
            })

        result.append({
            "id": str(doc.get("_id")),
            "titolo": doc.get("titolo"),
            "data": doc.get("data"),
            "note": doc.get("note"),
            "slots": slots_result,
        })

    return result


@api.post("/pubblico/open-day/{oid}/prenota/{slot_id}")
async def prenota_open_day_pubblico(
    oid: str,
    slot_id: str,
    body: dict
):
    nome = str(body.get("nome") or "").strip()
    cognome = str(body.get("cognome") or "").strip()
    telefono = str(body.get("telefono") or "").strip()

    if not nome or not cognome or not telefono:
        raise HTTPException(
            status_code=400,
            detail="Inserisci nome, cognome e cellulare"
        )

    if len(nome) > 80 or len(cognome) > 80 or len(telefono) > 40:
        raise HTTPException(
            status_code=400,
            detail="Dati non validi"
        )

    doc = await db.open_day.find_one({"_id": ObjectId(oid)})

    if not doc:
        raise HTTPException(
            status_code=404,
            detail="Open Day non trovato"
        )

    slot = next(
        (s for s in (doc.get("slots") or []) if s.get("id") == slot_id),
        None
    )

    if not slot:
        raise HTTPException(
            status_code=404,
            detail="Orario Open Day non trovato"
        )

    adesioni = doc.get("adesioni") or []

    # Evita la doppia prenotazione dello stesso numero
    # per lo stesso orario.
    telefono_norm = "".join(c for c in telefono if c.isdigit())

    for a in adesioni:
        telefono_esistente = str(a.get("telefono") or "")
        telefono_esistente_norm = "".join(
            c for c in telefono_esistente if c.isdigit()
        )

        if (
            telefono_norm
            and telefono_esistente_norm == telefono_norm
            and a.get("slot_id") == slot_id
        ):
            raise HTTPException(
                status_code=400,
                detail="Questo numero è già prenotato per questo orario"
            )

    count = sum(
        1 for a in adesioni
        if a.get("slot_id") == slot_id
    )

    posti = slot.get("posti")

    if posti is not None and count >= int(posti):
        raise HTTPException(
            status_code=400,
            detail="Posti esauriti"
        )

    nuova_adesione = {
        "id": str(uuid.uuid4()),
        "nome": nome,
        "cognome": cognome,
        "telefono": telefono,
        "disciplina": slot.get("disciplina"),
        "orario": f"{slot.get('ora_inizio')} - {slot.get('ora_fine')}",
        "slot_id": slot_id,
        "stato": "Da confermare",
        "fonte": "pubblico",
    }

    await db.open_day.update_one(
        {"_id": ObjectId(oid)},
        {"$push": {"adesioni": nuova_adesione}}
    )

    return {
        "ok": True,
        "message": "Prenotazione registrata"
    }


# Rimuove un allievo da un corso senza cancellarlo dall'Anagrafica
@api.delete("/corsi/{cid}/iscritti/{allievo_id}")
async def rimuovi_allievo_dal_corso(
    cid: str,
    allievo_id: str,
    user: dict = Depends(require_teacher),
):
    corso = await db.corsi.find_one({"id": cid})
    if not corso:
        raise HTTPException(status_code=404, detail="Corso non trovato")

    # Rimuove l'iscrizione al corso.
    # Usiamo delete_many per gestire anche eventuali doppie iscrizioni
    # presenti nei dati precedenti.
    result = await db.iscrizioni_corsi.delete_many({
        "corso_id": cid,
        "allievo_id": allievo_id,
    })

    # Togli l'allievo dalle lezioni già generate del corso
    await db.lezioni.update_many(
        {"corso_id": cid},
        {"$pull": {"prenotati": allievo_id}}
    )

    await db.lezioni.update_many(
        {"corso_id": cid},
        {"$pull": {"lista_attesa": allievo_id}}
    )

    return {
        "ok": True,
        "corso_id": cid,
        "allievo_id": allievo_id,
    }

app.include_router(api)
