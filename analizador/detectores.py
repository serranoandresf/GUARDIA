"""Motores de detección intercambiables. Cada uno expone detectar() -> [(clase, confianza, (x1, y1, x2, y2))] e indices()."""

class DetectorUltralytics:
    """Requiere `ultralytics`, con licencia AGPL-3.0 (los pesos yolov8*.pt también). Vender un producto cerrado con él
    exige una licencia comercial de Ultralytics. Para evitarlo, agrega otro motor con un modelo de licencia permisiva."""
    def __init__(self, ruta="yolov8n.pt"):
        from ultralytics import YOLO
        self.m = YOLO(ruta); self.nombres = self.m.names

    def indices(self, nombres): return [i for i, n in self.nombres.items() if n in nombres]

    def detectar(self, frame, clases, conf):
        r = self.m.predict(frame, classes=clases, conf=conf, verbose=False)[0]
        return [(self.nombres[int(b.cls)], float(b.conf), tuple(b.xyxy[0].tolist())) for b in r.boxes]

class DetectorMovimiento:
    """Sin IA y sin licencias restrictivas (solo numpy): agrupa las zonas que cambian entre cuadros y las reporta como
    "movimiento". No distingue personas de sombras o árboles: úsalo con zonas y la confirmación temporal."""
    def __init__(self, umbral=25, celda=8, min_celdas=3):
        self.umbral, self.celda, self.min_celdas, self.prev = umbral, celda, min_celdas, None

    def indices(self, nombres): return [n for n in nombres if n == "movimiento"]

    def detectar(self, frame, clases, conf):  # `conf` no se usa: aquí manda el umbral de cambio
        import numpy as np
        g = frame.astype(np.float32).mean(axis=2) if frame.ndim == 3 else frame.astype(np.float32)
        c, (h, w) = self.celda, g.shape[:2]
        H, W = h // c, w // c
        if H == 0 or W == 0: return []
        pq = g[:H * c, :W * c].reshape(H, c, W, c).mean(axis=(1, 3))
        prev, self.prev = self.prev, pq
        if prev is None or prev.shape != pq.shape: return []
        activo = np.abs(pq - prev) > self.umbral
        vistos, cajas = np.zeros_like(activo), []
        for y0, x0 in zip(*np.nonzero(activo)):
            if vistos[y0, x0]: continue
            pila, celdas = [(y0, x0)], []; vistos[y0, x0] = True
            while pila:
                y, x = pila.pop(); celdas.append((y, x))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < H and 0 <= nx < W and activo[ny, nx] and not vistos[ny, nx]:
                        vistos[ny, nx] = True; pila.append((ny, nx))
            if len(celdas) >= self.min_celdas:
                ys, xs = zip(*celdas)
                cajas.append(("movimiento", min(1.0, len(celdas) / 20), (int(min(xs)) * c, int(min(ys)) * c, (int(max(xs)) + 1) * c, (int(max(ys)) + 1) * c)))
        return cajas

def crear(cfg):
    motor = cfg.get("motor", "ultralytics")
    if motor == "ultralytics": return DetectorUltralytics(cfg.get("modelo", "yolov8n.pt"))
    if motor == "movimiento": return DetectorMovimiento()
    raise SystemExit(f"Motor de detección desconocido: {motor}. Agrega una clase con indices() y detectar() en detectores.py")
