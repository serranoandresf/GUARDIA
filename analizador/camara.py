"""Analizador Guardia: lector del último cuadro -> movimiento -> YOLO + tracking -> reglas -> alertas."""
import base64, collections, datetime, json, os, queue, re, sys, threading, time, unicodedata, uuid

from detectores import crear
from fuentes import tipo_fuente
from seguimiento import Seguidor

MODULOS = {"per": ["person"], "veh": ["car", "truck", "motorcycle", "bus"],
           "ani": ["dog", "cat", "cow", "horse", "sheep"], "mer": ["person"], "mov": ["movimiento"]}
HORARIOS = ("Siempre", "Noche", "Armado")

def src(c): return c.get("fuente") or c.get("rtsp", "")

def slug(n):
    n = unicodedata.normalize("NFD", n.lower())
    return re.sub(r"[^a-z0-9]+", "_", "".join(c for c in n if not unicodedata.combining(c))).strip("_")[:40]

def url_con_clave(c):
    """Las claves viven solo aquí: en config.json (usuario/clave) o en GUARDIA_CLAVE_<NOMBRE>."""
    from urllib.parse import quote
    u = src(c)
    if c.get("usuario") and "@" not in u and "://" in u:
        clave = os.environ.get("GUARDIA_CLAVE_" + slug(c["nombre"]).upper(), c.get("clave", ""))
        esquema, resto = u.split("://", 1)
        u = f"{esquema}://{quote(c['usuario'], safe='')}:{quote(clave, safe='')}@{resto}"
    return u

def validar(cfg):
    e = [f"Falta '{k}'" for k in ("servidor", "token", "camaras") if not cfg.get(k)]
    for c in cfg.get("camaras", []):
        n = c.get("nombre", "?")
        if tipo_fuente(src(c)) is None: e.append(f"{n}: fuente no válida (rtsp://, http://, número de webcam o archivo de video)")
        if not c.get("modulos") or any(m not in MODULOS for m in c["modulos"]): e.append(f"{n}: modulos válidos: {list(MODULOS)}")
        if not 0.1 <= c.get("confianza", 0.5) <= 0.95: e.append(f"{n}: confianza entre 0.1 y 0.95")
        if c.get("horario", "Siempre") not in HORARIOS: e.append(f"{n}: horario debe ser {HORARIOS}")
        if any(not (0 <= x <= 1 and 0 <= y <= 1) for x, y in c.get("poligono", [])): e.append(f"{n}: poligono con puntos entre 0 y 1")
    return e

def en_poligono(p, poly):
    """Punto (x, y) normalizado dentro del polígono; sin polígono = todo el cuadro."""
    if not poly: return True
    x, y = p; dentro = False; j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]; xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi: dentro = not dentro
        j = i
    return dentro

def es_noche(ahora, desde="18:00", hasta="06:00"):
    t = ahora.strftime("%H:%M")
    return (t >= desde or t < hasta) if desde > hasta else (desde <= t < hasta)

def debe_vigilar(horario, armado, ahora, noche=("18:00", "06:00")):
    """Siempre: armado o perímetro. Noche: igual, pero solo en el horario nocturno. Armado: solo armado total."""
    if armado == "off": return False
    if horario == "Armado": return armado == "tot"
    if horario == "Noche": return es_noche(ahora, *noche)
    return True

class Confirmador:
    """Una pista se confirma tras verse en `minimo` inferencias seguidas; calcula su permanencia."""
    def __init__(self, minimo=3):
        self.minimo, self.vistas, self.desde = minimo, collections.Counter(), {}
    def ver(self, ids, ahora):
        for i in list(self.vistas):
            if i not in ids: del self.vistas[i]; self.desde.pop(i, None)
        res = []
        for i in ids:
            self.vistas[i] += 1; self.desde.setdefault(i, ahora)
            res.append((i, self.vistas[i] >= self.minimo, ahora - self.desde[i]))
        return res

class Lector(threading.Thread):
    """Lee sin parar y conserva solo el último cuadro: el análisis nunca va atrasado."""
    def __init__(self, url, kind="rtsp"):
        super().__init__(daemon=True); self.url, self.kind, self.frame, self.t, self.n, self.t0 = url, kind, None, 0, 0, 0
    def run(self):
        import cv2
        if self.kind == "instantanea":  # foto fija por web: se consulta cada segundo
            import numpy as np, requests
            while True:
                try:
                    f = cv2.imdecode(np.frombuffer(requests.get(self.url, timeout=8).content, np.uint8), cv2.IMREAD_COLOR)
                    if f is not None:
                        self.frame, self.t = f, time.time(); self.n += 1
                        if self.n == 1: self.t0 = self.t
                except Exception: pass
                time.sleep(1)
        espera = 1
        while True:
            cap = cv2.VideoCapture(int(self.url) if self.kind == "usb" else self.url)
            while cap.isOpened():
                ok, f = cap.read()
                if not ok: break
                self.frame, self.t, espera = f, time.time(), 1
                self.n += 1
                if self.n == 1: self.t0 = self.t
            cap.release(); time.sleep(espera); espera = min(espera * 2, 60)

class Salida(threading.Thread):
    """Envía alertas con reintentos y espera creciente; el id único evita duplicados en el servidor."""
    def __init__(self, base, token):
        super().__init__(daemon=True); self.q, self.base, self.h = queue.Queue(500), base.rstrip("/"), {"x-token": token}
    def enviar(self, ev):
        os.makedirs("pendientes", exist_ok=True); arch = f"pendientes/{ev['id']}.json"  # sobrevive a un reinicio
        with open(arch, "w") as fh: json.dump(ev, fh)
        try: self.q.put_nowait(("/alertas", ev, arch))
        except queue.Full: print("Cola llena: se descarta una alerta")
    def ficha(self, slug_, datos):
        try: self.q.put_nowait((f"/camaras/{slug_}/ficha", datos, None))
        except queue.Full: pass
    def run(self):
        import requests
        while True:
            (ruta, ev, arch), espera = self.q.get(), 1
            while True:
                try:
                    r = requests.post(self.base + ruta, headers=self.h, json=ev, timeout=10)
                    if r.status_code < 300: break
                    if r.status_code < 500 and r.status_code != 429:
                        print("Alerta rechazada:", r.status_code, r.text[:200]); break
                except requests.RequestException: pass
                time.sleep(espera); espera = min(espera * 2, 60)
            if arch and os.path.exists(arch): os.remove(arch)

class Vigia:
    def __init__(self, c, g):
        self.c, self.g, self.estado, self.prev, self.igual, self.ultimo = c, g, "conectando", None, None, {}
        self.ultima_captura = 0
        self.tipo = tipo_fuente(src(c)); self.auto, self.muestras, self.t_m, self.t_f, self.ficha, self.activo_hasta = {}, [], None, 0, None, 0
        self.lector = Lector(url_con_clave(c), self.tipo); self.lector.start()
        self.det, self.seg = crear(g), Seguidor()  # el seguimiento es propio: cambiar de motor no cambia el comportamiento
        self.conf = Confirmador(c.get("confirmar", 3))
        usar = {x for m in c["modulos"] for x in MODULOS[m]}
        self.clases = self.det.indices(usar)
        if not self.clases: raise SystemExit(f"{c['nombre']}: el modelo no tiene las clases de {c['modulos']}; con un modelo propio ajusta MODULOS")  # sin filtro YOLO detectaría todo

    def par(self, k, d): return self.c.get(k, self.auto.get(k, d))  # lo que fijes en config.json manda; si no, lo medido

    def paso(self, armado, salida):
        import cv2
        l, ahora = self.lector, time.time()
        if l.frame is None or ahora - l.t > 10: self.estado = "caida"; return
        f = l.frame; pq = cv2.cvtColor(cv2.resize(f, (160, 90)), cv2.COLOR_BGR2GRAY)
        if pq.mean() < 8 or pq.std() < 3: self.estado = "tapada"; return
        if self.prev is not None and cv2.absdiff(pq, self.prev).max() == 0:
            self.igual = self.igual or ahora
            if ahora - self.igual > 30: self.estado = "congelada"; return
        else: self.igual = None
        mov = self.prev is None or (cv2.absdiff(pq, self.prev) > 25).mean() > self.par("movimiento", 0.005)
        self.prev, self.estado = pq, "ok"
        if self.ficha is not None and ahora - self.t_f > 21600: self.ficha, self.t_m = None, None  # re-mide cada 6 h
        if self.ficha is None:
            from diagnostico import evaluar
            self.t_m = self.t_m or ahora
            if len(self.muestras) < 25: self.muestras.append(cv2.resize(f, (320, int(320 * f.shape[0] / f.shape[1]))))
            if len(self.muestras) >= 25 or ahora - self.t_m > 10:
                self.ficha, self.t_f = evaluar(self.muestras, l.n / max(1e-6, ahora - l.t0), f.shape[1], f.shape[0], self.tipo), ahora
                self.auto, self.muestras = self.ficha["params"], []
                salida.ficha(slug(self.c["nombre"]), {**self.ficha, "nombre": self.c["nombre"], "tipo": self.tipo})
        if not (mov or ahora < self.activo_hasta) or not debe_vigilar(self.c.get("horario", "Siempre"), armado, datetime.datetime.now(), tuple(self.g.get("noche", ("18:00", "06:00")))): return
        h, w = f.shape[:2]; ids = {}
        dets = self.det.detectar(f, self.clases, self.par("confianza", 0.5))
        for i, clase, cf_, (x1, y1, x2, y2) in self.seg.actualizar(dets, ahora):
            if (x2 - x1) * (y2 - y1) / (w * h) < self.par("area_min", 0.002): continue
            if en_poligono(((x1 + x2) / 2 / w, y2 / h), self.c.get("poligono")): ids[i] = (clase, cf_, (x1, y1, x2, y2))
        if ids: self.activo_hasta = ahora + 5  # sigue analizando aunque la persona esté quieta (merodeo)
        mods = self.c["modulos"]; directo = {x for m in mods if m != "mer" for x in MODULOS[m]}
        for i, ok, dwell in self.conf.ver(ids, ahora):
            if not ok: continue
            clase, cf, caja = ids[i]; tipos = []
            if "mer" in mods and clase == "person" and dwell >= self.c.get("merodeo_seg", 20): tipos.append("merodeo")
            if clase in directo: tipos.append(clase)
            for t in tipos:
                if ahora - self.ultimo.get(t, 0) < self.c.get("espera", 30): continue  # agrupa: un aviso por tipo
                self.ultimo[t] = ahora
                g = f.copy()
                for _i, (_c, _f, bx) in ids.items(): cv2.rectangle(g, (int(bx[0]), int(bx[1])), (int(bx[2]), int(bx[3])), (0, 90, 255), max(2, w // 320))
                cv2.putText(g, f"{t} {cf:.0%}", (int(caja[0]), max(20, int(caja[1]) - 8)), cv2.FONT_HERSHEY_SIMPLEX, max(0.6, w / 1600), (0, 90, 255), 2)
                _, jpg = cv2.imencode(".jpg", cv2.resize(g, (640, int(640 * h / w))), [cv2.IMWRITE_JPEG_QUALITY, 70])
                salida.enviar({"id": str(uuid.uuid4()), "hora": ahora, "camara": self.c["nombre"], "zona": self.c.get("zona", ""),
                               "tipo": t, "confianza": round(cf, 2), "foto": base64.b64encode(jpg.tobytes()).decode()})

def subir_capturas(vigias, base, h, requests):
    """Una captura reducida por minuto y cámara: sirve para dibujar zonas sobre la imagen real."""
    import cv2
    for v in vigias:
        f = v.lector.frame
        if f is None or time.time() - v.ultima_captura < 60: continue
        v.ultima_captura = time.time()
        _, j = cv2.imencode(".jpg", cv2.resize(f, (480, int(480 * f.shape[0] / f.shape[1]))), [cv2.IMWRITE_JPEG_QUALITY, 70])
        requests.post(f"{base}/captura/{slug(v.c['nombre'])}", headers={**h, "content-type": "image/jpeg"}, data=j.tobytes(), timeout=10)

def main():
    cfg = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "config.json"))
    errores = validar(cfg)
    if errores: sys.exit("Configuración inválida:\n- " + "\n- ".join(errores))
    import requests
    base, h = cfg["servidor"].rstrip("/"), {"x-token": cfg["token"]}
    salida = Salida(cfg["servidor"], cfg["token"]); salida.start()
    for a in sorted(os.listdir("pendientes")) if os.path.isdir("pendientes") else []:  # alertas que no llegaron antes del reinicio
        try: salida.q.put_nowait(("/alertas", json.load(open("pendientes/" + a)), "pendientes/" + a))
        except Exception: pass
    vigias = [Vigia(c, cfg) for c in cfg["camaras"]]; armado, ult, ult_e = "off", 0, 0
    print("Vigilando", len(vigias), "cámaras. Ctrl+C para salir.")
    while True:
        if time.time() - ult > 30:
            ult = time.time()
            try:
                requests.post(base + "/latido", headers=h, timeout=5, json={"fuente": "analizador", "camaras": {v.c["nombre"]: v.estado for v in vigias}})
                subir_capturas(vigias, base, h, requests)
            except Exception as e: print("Sin servidor:", e)
        if time.time() - ult_e > 5:  # el armado se consulta cada 5 s, no cada 30
            ult_e = time.time()
            try: armado = requests.get(base + "/estado", headers=h, timeout=4).json()["armado"]
            except Exception: pass
        for v in vigias:
            try: v.paso(armado, salida)
            except Exception as e: v.estado = "error"; print(v.c["nombre"], "error:", e)
        time.sleep(0.2)

if __name__ == "__main__":
    main()
