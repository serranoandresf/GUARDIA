import base64, datetime as dt, os, sys, tempfile, unittest, uuid
os.environ.update(GUARDIA_TOKEN="x" * 20, GUARDIA_DATOS=tempfile.mkdtemp())
R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [R + "/analizador", R + "/servidor"]
import camara as A
import fuentes as F
import seguimiento as T
import detectores as DT
import seguridad as G
try:
    import diagnostico as D
except Exception:
    D = None

class Nucleo(unittest.TestCase):
    def test_poligono(self):
        p = [[0, 0], [1, 0], [1, 1], [0, 1]]
        self.assertTrue(A.en_poligono((.5, .5), p)); self.assertFalse(A.en_poligono((1.5, .5), p)); self.assertTrue(A.en_poligono((9, 9), []))
    def test_noche(self):
        f = lambda h: dt.datetime(2026, 1, 1, h)
        self.assertTrue(A.es_noche(f(23))); self.assertTrue(A.es_noche(f(3))); self.assertFalse(A.es_noche(f(12)))
    def test_horarios(self):
        d, n = dt.datetime(2026, 1, 1, 12), dt.datetime(2026, 1, 1, 22)
        self.assertFalse(A.debe_vigilar("Siempre", "off", n)); self.assertTrue(A.debe_vigilar("Siempre", "per", d))
        self.assertFalse(A.debe_vigilar("Noche", "tot", d)); self.assertTrue(A.debe_vigilar("Noche", "per", n))
        self.assertFalse(A.debe_vigilar("Armado", "per", n)); self.assertTrue(A.debe_vigilar("Armado", "tot", n))
    def test_confirmador(self):
        c = A.Confirmador(3)
        self.assertFalse(c.ver({1: 0}, 0)[0][1]); c.ver({1: 0}, 1); r = c.ver({1: 0}, 2)[0]
        self.assertTrue(r[1]); self.assertEqual(r[2], 2); c.ver({}, 3); self.assertFalse(c.ver({1: 0}, 4)[0][1])
    def test_validar(self):
        ok = {"servidor": "http://x", "token": "t", "camaras": [{"nombre": "a", "rtsp": "rtsp://x", "modulos": ["per"]}]}
        self.assertEqual(A.validar(ok), [])
        mal = {"servidor": "", "token": "t", "camaras": [{"nombre": "a", "rtsp": "http://x", "modulos": ["Person"], "confianza": 2, "horario": "x"}]}
        self.assertGreaterEqual(len(A.validar(mal)), 4)

    def test_slug_y_clave(self):
        self.assertEqual(A.slug("Portón Principal"), "porton_principal")
        self.assertEqual(A.url_con_clave({"nombre": "a", "rtsp": "rtsp://10.0.0.5/s", "usuario": "a b", "clave": "p@ss"}), "rtsp://a%20b:p%40ss@10.0.0.5/s")

class Seguimiento(unittest.TestCase):
    def test_ids_estables_y_nuevos(self):
        s = T.Seguidor()
        a = s.actualizar([("person", .9, (0, 0, 10, 20))], 0)[0][0]
        self.assertEqual(s.actualizar([("person", .9, (1, 0, 11, 20))], 0.5)[0][0], a)
        otro = s.actualizar([("person", .9, (1, 0, 11, 20)), ("person", .8, (100, 100, 110, 120))], 1)
        self.assertEqual(len({i for i, *_ in otro}), 2); self.assertIn(a, {i for i, *_ in otro})
    def test_clase_distinta_y_caducidad(self):
        s = T.Seguidor(paciencia=2)
        a = s.actualizar([("person", .9, (0, 0, 10, 20))], 0)[0][0]
        self.assertNotEqual(s.actualizar([("dog", .9, (0, 0, 10, 20))], 0.5)[0][0], a)
        self.assertEqual(s.actualizar([], 10), []); self.assertEqual(s.pistas, {})
    def test_iou_y_motor(self):
        self.assertAlmostEqual(T.iou((0, 0, 10, 10), (0, 0, 10, 10)), 1.0); self.assertEqual(T.iou((0, 0, 1, 1), (5, 5, 6, 6)), 0.0)
        with self.assertRaises(SystemExit): DT.crear({"motor": "inexistente"})

class Seguridad(unittest.TestCase):
    def test_totp_rfc6238(self):
        s = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # vectores de prueba del RFC 6238 (SHA-1)
        for t, esperado in ((59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"), (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130")):
            self.assertEqual(G.totp(s, t, digitos=8), esperado); self.assertEqual(G.totp(s, t), esperado[-6:])
    def test_ventana_totp(self):
        s = G.nuevo_secreto()
        self.assertTrue(G.verificar_totp(s, G.totp(s, 1000), t=1030)); self.assertFalse(G.verificar_totp(s, G.totp(s, 1000), t=1100))
    def test_clave(self):
        h = G.hash_clave("clave larga 123")
        self.assertTrue(G.verificar_clave("clave larga 123", h)); self.assertFalse(G.verificar_clave("otra", h)); self.assertFalse(G.verificar_clave("x", "basura"))
        self.assertNotEqual(G.hash_clave("a"), G.hash_clave("a"))
    def test_sesion(self):
        k = b"k" * 32; t = G.crear_sesion(k, "ana", ttl=60, ahora=1000)
        self.assertEqual(G.verificar_sesion(k, t, ahora=1030)["u"], "ana"); self.assertIsNone(G.verificar_sesion(k, t, ahora=1061))
        self.assertIsNone(G.verificar_sesion(b"z" * 32, t, ahora=1030)); self.assertIsNone(G.verificar_sesion(k, t[:-2] + "xx", ahora=1030)); self.assertIsNone(G.verificar_sesion(k, "basura"))

@unittest.skipIf(D is None, "numpy no instalado")
class Movimiento(unittest.TestCase):
    def test_caja_sobre_lo_que_cambia(self):
        import numpy as np
        d = DT.DetectorMovimiento(); fondo = np.zeros((90, 160, 3), np.uint8)
        self.assertEqual(d.detectar(fondo, [], .5), []); self.assertEqual(d.detectar(fondo, [], .5), [])
        f = fondo.copy(); f[20:52, 40:72] = 255
        r = d.detectar(f, [], .5); self.assertEqual(len(r), 1); c, _, (x1, y1, x2, y2) = r[0]
        self.assertEqual(c, "movimiento"); self.assertTrue(x1 <= 40 and x2 >= 72 and y1 <= 20 and y2 >= 52)
    def test_ruido_pequeno_y_dos_objetos(self):
        import numpy as np
        d = DT.DetectorMovimiento(); fondo = np.zeros((90, 160, 3), np.uint8); d.detectar(fondo, [], .5)
        f = fondo.copy(); f[0:8, 0:8] = 255; f[40:64, 100:124] = 255; f[40:64, 20:44] = 255
        self.assertEqual(len(d.detectar(f, [], .5)), 2)
    def test_indices(self):
        self.assertEqual(DT.DetectorMovimiento().indices(["person", "movimiento"]), ["movimiento"])
        self.assertEqual(DT.crear({"motor": "movimiento"}).indices(["person"]), [])

class Fuentes(unittest.TestCase):
    def test_tipos(self):
        for s, t in (("0", "usb"), ("rtsp://x/s", "rtsp"), ("http://x/video.mjpg", "mjpeg"), ("http://x/snapshot.jpg", "instantanea"), ("clip.mp4", "archivo"), ("hola", None)):
            self.assertEqual(F.tipo_fuente(s), t, s)
    def test_candidatos(self):
        c = F.candidatos("10.0.0.5", "hikvision", "a b", "p@ss")
        self.assertEqual(c[0], "rtsp://a%20b:p%40ss@10.0.0.5:554/Streaming/Channels/101")
        self.assertGreater(len(F.candidatos("10.0.0.5", "otra")), 10)
    def test_validar_fuentes(self):
        base = {"servidor": "http://x", "token": "t", "camaras": [{"nombre": "a", "fuente": "0", "tipo": "usb", "modulos": ["per"]}]}
        self.assertEqual(A.validar(base), [])
        base["camaras"][0]["fuente"] = "algo raro"
        self.assertEqual(len(A.validar(base)), 1)

@unittest.skipIf(D is None, "numpy no instalado")
class Diagnostico(unittest.TestCase):
    def test_oscura_ir_y_lenta(self):
        import numpy as np
        r = D.evaluar([np.full((90, 160, 3), 10, np.uint8)] * 5, 2, 640, 360)
        self.assertTrue(r["ir"]); self.assertEqual(r["params"]["confianza"], 0.6)
        self.assertTrue(any("oscura" in c for c in r["consejos"])); self.assertTrue(any("cuadros" in c for c in r["consejos"]))
    def test_imagen_con_color(self):
        import numpy as np
        g = np.random.default_rng(1)
        r = D.evaluar([np.clip(g.normal(120, 40, (90, 160, 3)), 0, 255).astype(np.uint8) for _ in range(5)], 15, 1920, 1080)
        self.assertFalse(r["ir"]); self.assertAlmostEqual(r["brillo"], 120, delta=6); self.assertEqual(r["params"]["area_min"], 0.002)

try:
    from fastapi.testclient import TestClient
    import servidor as S
except Exception as e:
    TestClient = None; print("Pruebas del servidor omitidas:", e)

@unittest.skipIf(TestClient is None, "fastapi/httpx no instalados")
class Servidor(unittest.TestCase):
    def setUp(self): self.c, self.h = TestClient(S.app), {"x-token": "x" * 20}
    def ev(self, **k): return {"id": str(uuid.uuid4()), "hora": 1.8e9, "camara": "Entrada", "zona": "Z", "tipo": "person", "confianza": .8, **k}
    def test_auth(self):
        self.assertEqual(self.c.get("/estado").status_code, 401); self.assertEqual(self.c.get("/salud").status_code, 200)
    def test_alerta_idempotente_y_foto(self):
        e = self.ev(foto=base64.b64encode(b"\xff\xd8\xff\xe0abc").decode())
        self.assertTrue(self.c.post("/alertas", json=e, headers=self.h).json()["nuevo"])
        self.assertFalse(self.c.post("/alertas", json=e, headers=self.h).json()["nuevo"])
        self.assertEqual(self.c.get(f"/alertas/{e['id']}/foto", headers=self.h).status_code, 200)
        self.assertEqual(self.c.patch(f"/alertas/{e['id']}", json={"estado": "falsa_alarma"}, headers=self.h).status_code, 200)
    def test_validacion(self):
        for mal in (self.ev(tipo="Person"), self.ev(camara="a" * 99), self.ev(foto="@@"), self.ev(id="no")):
            self.assertEqual(self.c.post("/alertas", json=mal, headers=self.h).status_code, 422)
    def test_estado_y_latido(self):
        self.assertEqual(self.c.put("/estado", json={"armado": "tot"}, headers=self.h).status_code, 200)
        self.assertEqual(self.c.put("/estado", json={"armado": "x"}, headers=self.h).status_code, 422)
        self.c.post("/latido", json={"fuente": "analizador", "camaras": {"Entrada": "ok"}}, headers=self.h)
        s = self.c.get("/sistema", headers=self.h).json()
        self.assertEqual(s["armado"], "tot"); self.assertFalse(s["fuentes"][0]["caido"])

    def test_roles_y_revocacion(self):
        d = self.c.post("/dispositivos", json={"nombre": "Celular", "rol": "ver"}, headers=self.h).json()
        v = {"x-token": d["token"]}
        self.assertEqual(self.c.get("/estado", headers=v).status_code, 200)
        self.assertEqual(self.c.put("/estado", json={"armado": "off"}, headers=v).status_code, 403)
        self.assertEqual(self.c.post("/dispositivos", json={"nombre": "x", "rol": "ver"}, headers=v).status_code, 403)
        self.c.delete(f"/dispositivos/{d['id']}", headers=self.h)
        self.assertEqual(self.c.get("/estado", headers=v).status_code, 401)
    def test_captura(self):
        self.assertEqual(self.c.post("/captura/entrada", content=b"\xff\xd8\xff\xe0x", headers=self.h).status_code, 200)
        self.assertEqual(self.c.get("/captura/entrada", headers=self.h).status_code, 200)
        self.assertEqual(self.c.post("/captura/entrada", content=b"no", headers=self.h).status_code, 422)

    def test_ficha(self):
        f = {"nombre": "Entrada", "tipo": "rtsp", "ancho": 1920, "alto": 1080, "fps": 15, "brillo": 120, "contraste": 40, "nitidez": 300, "ir": False, "consejos": ["La imagen es buena para analizar."], "params": {"movimiento": 0.01}}
        self.assertEqual(self.c.post("/camaras/entrada/ficha", json=f, headers=self.h).status_code, 200)
        self.assertEqual(self.c.get("/camaras", headers=self.h).json()[0]["ficha"]["ancho"], 1920)

    def test_ajustes_compartidos(self):
        v0 = self.c.get("/ajustes", headers=self.h).json()["v"]
        self.assertEqual(self.c.put("/ajustes", json={"datos": {"cams": []}}, headers=self.h).json()["v"], v0 + 1)
        self.assertEqual(self.c.get("/ajustes", headers=self.h).json()["datos"], {"cams": []})

    def test_cabeceras_y_metricas(self):
        self.assertEqual(self.c.get("/salud").headers["x-content-type-options"], "nosniff")
        self.assertEqual(self.c.get("/metricas").status_code, 401)
        self.assertIn("guardia_latido_edad_segundos", self.c.get("/metricas", headers=self.h).text)

    def test_usuarios_y_2fa(self):
        clave = "una-clave-larga-1"
        self.assertEqual(self.c.post("/usuarios", json={"nombre": "ana", "clave": clave, "rol": "familiar"}, headers=self.h).status_code, 200)
        r = self.c.post("/auth/entrar", json={"nombre": "ana", "clave": clave}); self.assertEqual(r.status_code, 200)
        s = {"x-token": r.json()["token"]}
        self.assertEqual(self.c.get("/estado", headers=s).status_code, 200)
        self.assertEqual(self.c.post("/dispositivos", json={"nombre": "x", "rol": "ver"}, headers=s).status_code, 403)
        sec = self.c.post("/auth/2fa", headers=s).json()["secreto"]
        self.assertEqual(self.c.post("/auth/2fa/confirmar", json={"codigo": G.totp(sec)}, headers=s).status_code, 200)
        self.assertEqual(self.c.post("/auth/entrar", json={"nombre": "ana", "clave": clave}).status_code, 401)
        self.assertEqual(self.c.post("/auth/entrar", json={"nombre": "ana", "clave": clave, "codigo": G.totp(sec)}).status_code, 200)

if __name__ == "__main__": unittest.main()
