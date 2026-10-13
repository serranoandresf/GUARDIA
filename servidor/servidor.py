"""API de Guardia. Variables: GUARDIA_TOKEN (obligatoria, 16+ caracteres), GUARDIA_ORIGENES, GUARDIA_DATOS, NTFY_TOPIC, NTFY_SERVIDOR."""
import base64, hashlib, json, os, secrets, sqlite3, threading, time, urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Literal, Optional
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Path as P, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import seguridad as G

VERSION = "0.6.0"
TOKEN = os.environ.get("GUARDIA_TOKEN", "")
if len(TOKEN) < 16: raise SystemExit("GUARDIA_TOKEN es obligatoria y debe tener al menos 16 caracteres.")
CLAVE_S = hashlib.sha256(b"guardia-sesion:" + TOKEN.encode()).digest()  # cambiar GUARDIA_TOKEN cierra todas las sesiones
NTFY, NTFY_SRV = os.environ.get("NTFY_TOPIC"), os.environ.get("NTFY_SERVIDOR", "https://ntfy.sh")
DATOS = Path(os.environ.get("GUARDIA_DATOS", "datos")); (DATOS / "fotos").mkdir(parents=True, exist_ok=True)
UUID = r"^[0-9a-f-]{36}$"
NOMBRE = r"^[a-z0-9_]{1,40}$"
ESC_MIN = int(os.environ.get("ESCALAR_MIN", "5"))
RETENCION = int(os.environ.get("RETENCION_DIAS", "30"))
TODOS, ESCR = ("propietario", "familiar", "ver", "analizador"), ("propietario", "familiar")

def db():
    c = sqlite3.connect(DATOS / "guardia.db", timeout=10); c.row_factory = sqlite3.Row; return c
with db() as c:
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""CREATE TABLE IF NOT EXISTS eventos(id TEXT PRIMARY KEY, hora REAL, recibido REAL, camara TEXT, zona TEXT,
        tipo TEXT, confianza REAL, estado TEXT DEFAULT 'nuevo', foto TEXT, escalado INTEGER DEFAULT 0);
        CREATE INDEX IF NOT EXISTS ix_hora ON eventos(hora);
        CREATE TABLE IF NOT EXISTS cfg(k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS latidos(fuente TEXT PRIMARY KEY, hora REAL, camaras TEXT);
        CREATE TABLE IF NOT EXISTS dispositivos(id TEXT PRIMARY KEY, nombre TEXT, rol TEXT, hash TEXT UNIQUE, creado REAL, activo INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS auditoria(hora REAL, quien TEXT, accion TEXT);
        CREATE TABLE IF NOT EXISTS fichas(slug TEXT PRIMARY KEY, hora REAL, ficha TEXT);
        CREATE TABLE IF NOT EXISTS usuarios(nombre TEXT PRIMARY KEY, hash TEXT, rol TEXT, totp TEXT, totp_activo INTEGER DEFAULT 0, fallos INTEGER DEFAULT 0, bloqueado REAL DEFAULT 0, creado REAL);""")

app = FastAPI(title="Guardia", version=VERSION)
ORIG = [o for o in os.environ.get("GUARDIA_ORIGENES", "").split(",") if o]
app.add_middleware(CORSMiddleware, allow_origins=ORIG, allow_methods=["GET", "POST", "PUT", "PATCH"], allow_headers=["x-token", "content-type"])

@app.middleware("http")
async def cabeceras(req, call_next):
    r = await call_next(req)
    r.headers["X-Content-Type-Options"] = "nosniff"; r.headers["Referrer-Policy"] = "no-referrer"; r.headers["X-Frame-Options"] = "DENY"
    if not req.url.path.startswith("/panel"): r.headers["Cache-Control"] = "no-store"  # las respuestas de la API no se guardan en caché
    return r

@app.get("/metricas")
def metricas(q=Depends(rol("propietario"))):
    """Formato de texto de Prometheus."""
    with db() as c:
        ev = c.execute("SELECT estado, COUNT(*) n FROM eventos GROUP BY estado").fetchall(); hb = c.execute("SELECT MAX(hora) h FROM latidos").fetchone()["h"]
    return PlainTextResponse("\n".join([f'guardia_eventos{{estado="{r["estado"]}"}} {r["n"]}' for r in ev] + [f"guardia_latido_edad_segundos {int(time.time() - hb) if hb else -1}"]) + "\n")

FALLOS = defaultdict(list)
def quien(req, tok):
    """Devuelve (rol, nombre). El token de GUARDIA_TOKEN es el propietario; los demás son dispositivos revocables."""
    ip, ahora = (req.client.host if req.client else "?"), time.time()
    FALLOS[ip] = [t for t in FALLOS[ip] if ahora - t < 300]
    if len(FALLOS[ip]) >= 10: raise HTTPException(429, "Demasiados intentos. Espera 5 minutos.")
    if secrets.compare_digest(tok.encode(), TOKEN.encode()): return ("propietario", "principal")
    if tok.startswith("s1."):  # sesión de un usuario con clave (y 2FA si lo activó)
        s = G.verificar_sesion(CLAVE_S, tok)
        if s:
            with db() as c: u = c.execute("SELECT nombre, rol FROM usuarios WHERE nombre=?", (s["u"],)).fetchone()
            if u: return (u["rol"], u["nombre"])
        FALLOS[ip].append(ahora); raise HTTPException(401, "Sesión inválida o vencida")
    with db() as c:
        d = c.execute("SELECT nombre, rol FROM dispositivos WHERE hash=? AND activo=1", (hashlib.sha256(tok.encode()).hexdigest(),)).fetchone()
    if d: return (d["rol"], d["nombre"])
    FALLOS[ip].append(ahora); raise HTTPException(401, "Token inválido")

def rol(*ok):
    def dep(req: Request, x_token: str = Header("")):
        q = quien(req, x_token)
        if q[0] not in ok: raise HTTPException(403, "Sin permiso para esta acción")
        return q
    return dep

def auditar(quien_, accion):
    with db() as c: c.execute("INSERT INTO auditoria VALUES(?,?,?)", (time.time(), quien_, accion[:200]))

def push_texto(t):
    if not NTFY: return
    try: urllib.request.urlopen(urllib.request.Request(f"{NTFY_SRV}/{NTFY}", data=t.encode(), headers={"Title": "Guardia", "Priority": "high"}), timeout=5)
    except Exception: pass

class Alerta(BaseModel):
    id: str = Field(pattern=UUID)
    hora: float = Field(gt=1.6e9, lt=4e9)
    camara: str = Field(max_length=60)
    zona: str = Field("", max_length=60)
    tipo: str = Field(max_length=30, pattern=r"^[a-z_]+$")
    confianza: float = Field(0, ge=0, le=1)
    foto: Optional[str] = Field(None, max_length=600_000)

class Cambio(BaseModel): estado: Literal["nuevo", "visto", "atendido", "falsa_alarma"]
class Armado(BaseModel): armado: Literal["off", "per", "tot"]
class Latido(BaseModel):
    fuente: str = Field(max_length=40)
    camaras: dict[str, str] = Field(default_factory=dict, max_length=50)

@app.get("/salud")
def salud(): return {"ok": True, "version": VERSION}

@app.post("/alertas")
def nueva(a: Alerta, bg: BackgroundTasks, q=Depends(rol("propietario", "familiar", "analizador"))):
    foto = None
    if a.foto:
        try: datos = base64.b64decode(a.foto, validate=True)
        except Exception: raise HTTPException(422, "La foto no es base64 válido")
        if not datos.startswith(b"\xff\xd8"): raise HTTPException(422, "La foto debe ser JPEG")
        foto = f"{a.id}.jpg"; (DATOS / "fotos" / foto).write_bytes(datos)
    with db() as c:
        n = c.execute("INSERT OR IGNORE INTO eventos(id,hora,recibido,camara,zona,tipo,confianza,foto) VALUES(?,?,?,?,?,?,?,?)",
                      (a.id, a.hora, time.time(), a.camara, a.zona, a.tipo, a.confianza, foto)).rowcount
    if n: bg.add_task(push_texto, f"{a.tipo} en {a.camara} ({a.zona})")
    return {"ok": True, "nuevo": bool(n)}

@app.get("/alertas")
def alertas(limite: int = 100, q=Depends(rol(*TODOS))):
    with db() as c:
        f = c.execute("SELECT * FROM eventos ORDER BY hora DESC LIMIT ?", (max(1, min(limite, 500)),)).fetchall()
    return [{**{k: r[k] for k in ("id", "hora", "camara", "zona", "tipo", "confianza", "estado")}, "foto": bool(r["foto"])} for r in f]

@app.patch("/alertas/{id}")
def cambiar_alerta(cambio: Cambio, id: str = P(pattern=UUID), q=Depends(rol(*ESCR))):
    with db() as c:
        if cambio.estado == "visto": c.execute("UPDATE eventos SET estado='visto' WHERE id=? AND estado='nuevo'", (id,))
        elif not c.execute("UPDATE eventos SET estado=? WHERE id=?", (cambio.estado, id)).rowcount: raise HTTPException(404, "No existe")
    return {"ok": True}

@app.get("/alertas/{id}/foto")
def foto(id: str = P(pattern=UUID), q=Depends(rol("propietario", "familiar", "ver"))):
    ruta = DATOS / "fotos" / f"{id}.jpg"
    if not ruta.exists(): raise HTTPException(404, "Sin foto")
    return FileResponse(ruta, media_type="image/jpeg")

def armado_actual():
    with db() as c:
        r = c.execute("SELECT v FROM cfg WHERE k='armado'").fetchone()
    return r["v"] if r else "off"

@app.get("/estado")
def estado(q=Depends(rol(*TODOS))): return {"armado": armado_actual()}

@app.put("/estado")
def poner_estado(e: Armado, q=Depends(rol(*ESCR))):
    with db() as c: c.execute("INSERT OR REPLACE INTO cfg VALUES('armado',?)", (e.armado,))
    auditar(q[1], f"armado: {e.armado}")
    return {"armado": e.armado}

@app.post("/latido")
def latido(l: Latido, q=Depends(rol("propietario", "analizador"))):
    with db() as c: c.execute("INSERT OR REPLACE INTO latidos VALUES(?,?,?)", (l.fuente, time.time(), json.dumps(l.camaras)[:2000]))
    return {"ok": True}

@app.get("/sistema")
def sistema(q=Depends(rol("propietario", "familiar", "ver"))):
    with db() as c: f = c.execute("SELECT * FROM latidos").fetchall()
    ahora = time.time()
    return {"armado": armado_actual(), "version": VERSION, "fuentes": [
        {"fuente": r["fuente"], "hace_s": int(ahora - r["hora"]), "camaras": json.loads(r["camaras"]), "caido": ahora - r["hora"] > 120} for r in f]}

class Ficha(BaseModel):
    nombre: str = Field(max_length=60)
    tipo: str = Field(max_length=20)
    ancho: int = Field(ge=0, le=20000)
    alto: int = Field(ge=0, le=20000)
    fps: float = Field(ge=0, le=1000)
    brillo: float
    contraste: float
    nitidez: float
    ruido: float = 0
    ir: bool
    consejos: list[str] = Field(max_length=10)
    params: dict[str, float] = Field(max_length=10)

@app.post("/camaras/{slug}/ficha")
def guardar_ficha(f: Ficha, slug: str = P(pattern=NOMBRE), q=Depends(rol("propietario", "analizador"))):
    if any(len(x) > 200 for x in f.consejos): raise HTTPException(422, "Consejo demasiado largo")
    with db() as c: c.execute("INSERT OR REPLACE INTO fichas VALUES(?,?,?)", (slug, time.time(), json.dumps(f.model_dump())))
    return {"ok": True}

@app.get("/camaras")
def camaras(q=Depends(rol("propietario", "familiar", "ver"))):
    with db() as c: f = c.execute("SELECT * FROM fichas").fetchall()
    return [{"slug": r["slug"], "hora": r["hora"], "ficha": json.loads(r["ficha"])} for r in f]

class AjustesIn(BaseModel):
    datos: dict = Field(max_length=20)

@app.get("/ajustes")
def leer_ajustes(q=Depends(rol(*TODOS))):
    """Cámaras, contactos y medidas compartidos entre todos tus dispositivos."""
    with db() as c:
        r = c.execute("SELECT v FROM cfg WHERE k='ajustes'").fetchone(); n = c.execute("SELECT v FROM cfg WHERE k='ajustes_v'").fetchone()
    return {"v": int(n["v"]) if n else 0, "datos": json.loads(r["v"]) if r else {}}

@app.put("/ajustes")
def guardar_ajustes(a: AjustesIn, q=Depends(rol(*ESCR))):
    t = json.dumps(a.datos)
    if len(t) > 100_000: raise HTTPException(413, "Demasiados datos")
    with db() as c:
        n = c.execute("SELECT v FROM cfg WHERE k='ajustes_v'").fetchone(); v = (int(n["v"]) if n else 0) + 1
        c.execute("INSERT OR REPLACE INTO cfg VALUES('ajustes',?)", (t,)); c.execute("INSERT OR REPLACE INTO cfg VALUES('ajustes_v',?)", (str(v),))
    return {"v": v}

class NuevoUsuario(BaseModel):
    nombre: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    clave: str = Field(min_length=10, max_length=128)
    rol: Literal["propietario", "familiar", "ver"] = "familiar"

class Entrada(BaseModel):
    nombre: str = Field(max_length=40)
    clave: str = Field(max_length=128)
    codigo: Optional[str] = Field(None, max_length=8)

class Codigo(BaseModel): codigo: str = Field(max_length=8)

@app.post("/usuarios")
def crear_usuario(u: NuevoUsuario, q=Depends(rol("propietario"))):
    """El primer propietario se crea con el token principal; después cada persona entra con usuario, clave y 2FA."""
    with db() as c:
        try: c.execute("INSERT INTO usuarios(nombre,hash,rol,creado) VALUES(?,?,?,?)", (u.nombre, G.hash_clave(u.clave), u.rol, time.time()))
        except sqlite3.IntegrityError: raise HTTPException(409, "Ese usuario ya existe")
    auditar(q[1], f"creó usuario {u.nombre} ({u.rol})")
    return {"ok": True}

@app.get("/usuarios")
def lista_usuarios(q=Depends(rol("propietario"))):
    with db() as c: f = c.execute("SELECT nombre, rol, totp_activo FROM usuarios ORDER BY nombre").fetchall()
    return [dict(r) for r in f]

@app.post("/auth/entrar")
def entrar(e: Entrada, req: Request):
    ip, ahora = (req.client.host if req.client else "?"), time.time()
    with db() as c:
        u = c.execute("SELECT * FROM usuarios WHERE nombre=?", (e.nombre,)).fetchone()
        if u and u["bloqueado"] > ahora: raise HTTPException(429, "Cuenta bloqueada unos minutos por intentos fallidos")
        ok = bool(u) and G.verificar_clave(e.clave, u["hash"]) and (not u["totp_activo"] or G.verificar_totp(u["totp"], e.codigo or ""))
        if not u: G.verificar_clave(e.clave, G.hash_clave("x"))  # mismo tiempo de respuesta si el usuario no existe
        if ok: c.execute("UPDATE usuarios SET fallos=0 WHERE nombre=?", (e.nombre,))
        elif u:
            f = u["fallos"] + 1
            c.execute("UPDATE usuarios SET fallos=?, bloqueado=? WHERE nombre=?", (0 if f >= 5 else f, ahora + 300 if f >= 5 else 0, e.nombre))
    if not ok:
        auditar(e.nombre[:40], f"entrada fallida desde {ip}")
        raise HTTPException(401, "Usuario, clave o código incorrectos")
    auditar(e.nombre, f"entró desde {ip}")
    return {"token": G.crear_sesion(CLAVE_S, u["nombre"]), "rol": u["rol"], "expira_s": 43200}

@app.post("/auth/2fa")
def preparar_2fa(req: Request, x_token: str = Header("")):
    if not x_token.startswith("s1."): raise HTTPException(403, "Inicia sesión con tu usuario")
    nombre, sec = quien(req, x_token)[1], G.nuevo_secreto()
    with db() as c: c.execute("UPDATE usuarios SET totp=?, totp_activo=0 WHERE nombre=?", (sec, nombre))
    return {"secreto": sec, "uri": G.uri_totp(sec, nombre)}  # escanea la uri como código QR en tu app de autenticación

@app.post("/auth/2fa/confirmar")
def confirmar_2fa(k: Codigo, req: Request, x_token: str = Header("")):
    if not x_token.startswith("s1."): raise HTTPException(403, "Inicia sesión con tu usuario")
    nombre = quien(req, x_token)[1]
    with db() as c:
        u = c.execute("SELECT totp FROM usuarios WHERE nombre=?", (nombre,)).fetchone()
        if not u or not u["totp"] or not G.verificar_totp(u["totp"], k.codigo): raise HTTPException(422, "Código incorrecto")
        c.execute("UPDATE usuarios SET totp_activo=1 WHERE nombre=?", (nombre,))
    auditar(nombre, "activó 2FA")
    return {"ok": True}

class Disp(BaseModel):
    nombre: str = Field(max_length=40)
    rol: Literal["familiar", "ver", "analizador"]

@app.post("/dispositivos")
def crear_dispositivo(d: Disp, q=Depends(rol("propietario"))):
    tok, did = secrets.token_urlsafe(24), secrets.token_hex(8)
    with db() as c: c.execute("INSERT INTO dispositivos(id,nombre,rol,hash,creado) VALUES(?,?,?,?,?)", (did, d.nombre, d.rol, hashlib.sha256(tok.encode()).hexdigest(), time.time()))
    auditar(q[1], f"creó dispositivo {d.nombre} ({d.rol})")
    return {"id": did, "token": tok}  # el token solo se muestra ahora; se guarda su huella

@app.get("/dispositivos")
def lista_dispositivos(q=Depends(rol("propietario"))):
    with db() as c: f = c.execute("SELECT id, nombre, rol, creado, activo FROM dispositivos ORDER BY creado DESC").fetchall()
    return [dict(r) for r in f]

@app.delete("/dispositivos/{id}")
def revocar(id: str = P(pattern=r"^[0-9a-f]{16}$"), q=Depends(rol("propietario"))):
    with db() as c:
        if not c.execute("UPDATE dispositivos SET activo=0 WHERE id=?", (id,)).rowcount: raise HTTPException(404, "No existe")
    auditar(q[1], f"revocó dispositivo {id}")
    return {"ok": True}

@app.get("/auditoria")
def auditoria(q=Depends(rol("propietario"))):
    with db() as c: f = c.execute("SELECT hora, quien, accion FROM auditoria ORDER BY hora DESC LIMIT 100").fetchall()
    return [dict(r) for r in f]

@app.post("/captura/{nombre}")
async def subir_captura(req: Request, nombre: str = P(pattern=NOMBRE), q=Depends(rol("propietario", "analizador"))):
    d = await req.body()
    if len(d) > 400_000 or not d.startswith(b"\xff\xd8"): raise HTTPException(422, "Captura JPEG de hasta 400 KB")
    (DATOS / "fotos" / f"captura_{nombre}.jpg").write_bytes(d)
    return {"ok": True}

@app.get("/captura/{nombre}")
def ver_captura(nombre: str = P(pattern=NOMBRE), q=Depends(rol("propietario", "familiar", "ver"))):
    ruta = DATOS / "fotos" / f"captura_{nombre}.jpg"
    if not ruta.exists(): raise HTTPException(404, "Aún sin captura")
    return FileResponse(ruta, media_type="image/jpeg")

def vigilante():
    avisado, limpio = False, 0
    while True:
        time.sleep(30)
        try:
            with db() as c: ult = c.execute("SELECT MAX(hora) h FROM latidos").fetchone()["h"]
            caido = armado_actual() != "off" and (ult is None or time.time() - ult > 120)
            if caido and not avisado: push_texto("El analizador no responde: nadie está vigilando."); avisado = True
            elif not caido: avisado = False
            with db() as c:
                viejas = c.execute("SELECT id, tipo, camara FROM eventos WHERE estado='nuevo' AND escalado=0 AND recibido<?", (time.time() - 60 * ESC_MIN,)).fetchall()
                for e in viejas:
                    push_texto(f"Sin atender: {e['tipo']} en {e['camara']}")
                    c.execute("UPDATE eventos SET escalado=1 WHERE id=?", (e["id"],))
            if time.time() - limpio > 86400:  # retención: borra eventos y fotos viejos
                limpio, lim = time.time(), time.time() - 86400 * RETENCION
                with db() as c:
                    for r in c.execute("SELECT foto FROM eventos WHERE hora<? AND foto IS NOT NULL", (lim,)).fetchall():
                        (DATOS / "fotos" / r["foto"]).unlink(missing_ok=True)
                    c.execute("DELETE FROM eventos WHERE hora<?", (lim,))
        except Exception: pass

@app.on_event("startup")
def iniciar(): threading.Thread(target=vigilante, daemon=True).start()

# El servidor entrega el panel en /panel/: así todos tus dispositivos usan una sola dirección (sin CORS ni Pages).
WEB = Path(os.environ.get("GUARDIA_WEB") or Path(__file__).resolve().parent.parent / "web")
if WEB.exists(): app.mount("/panel", StaticFiles(directory=WEB, html=True), name="panel")
