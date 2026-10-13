"""Diagnóstico de cámara: mide la imagen real y propone parámetros y consejos. Solo usa numpy."""
import numpy as np

def _gris(f): return f[..., 0] * 0.114 + f[..., 1] * 0.587 + f[..., 2] * 0.299

def evaluar(muestras, fps, ancho, alto, tipo="rtsp"):
    g = [_gris(m.astype(np.float32)) for m in muestras]
    brillo = float(np.mean([x.mean() for x in g])); contraste = float(np.mean([x.std() for x in g]))
    nitidez = float(np.mean([(4 * x[1:-1, 1:-1] - x[:-2, 1:-1] - x[2:, 1:-1] - x[1:-1, :-2] - x[1:-1, 2:]).var() for x in g]))
    sat = float(np.mean([(m.max(axis=2).astype(np.float32) - m.min(axis=2)).mean() for m in muestras]))
    ir = sat < 12  # casi sin color: modo infrarrojo o cámara en blanco y negro
    ruido = float(np.mean([(np.abs(a - b) > 25).mean() for a, b in zip(g, g[1:])])) if len(g) > 1 else 0.0
    c = []
    if brillo < 35: c.append("Imagen muy oscura: agrega luz o cambia el ángulo. De noche habrá más falsos avisos.")
    if brillo > 200: c.append("Imagen sobreexpuesta: evita apuntar al sol o a una lámpara.")
    if nitidez < 60: c.append("Imagen poco nítida: limpia el lente y revisa el enfoque o la resolución del stream.")
    if fps < 5 and tipo != "instantanea": c.append("Pocos cuadros por segundo: el movimiento rápido puede perderse.")
    if min(ancho, alto) < 360: c.append("Resolución baja: usa el stream principal para detectar personas lejanas.")
    if ir: c.append("La cámara está en blanco y negro (infrarrojo): el rendimiento baja; conviene luz visible.")
    if ruido > 0.02: c.append("Hay movimiento constante (árboles, reflejos o ruido): marca una zona para ignorarlo.")
    if not c: c.append("La imagen es buena para analizar.")
    params = {"movimiento": round(max(0.005, min(0.05, ruido * 3 + 0.003)), 4),
              "confianza": 0.6 if (brillo < 35 or ir or nitidez < 60) else 0.5,
              "area_min": 0.004 if min(ancho, alto) < 480 else 0.002}
    return {"ancho": int(ancho), "alto": int(alto), "fps": round(fps, 1), "brillo": round(brillo), "contraste": round(contraste),
            "nitidez": round(nitidez), "ir": bool(ir), "ruido": round(ruido, 4), "consejos": c, "params": params}
