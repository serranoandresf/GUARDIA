"""Conecta una cámara nueva: la busca, prueba la conexión, mide la imagen y la agrega a config.json.
  python conectar.py descubrir
  python conectar.py agregar 192.168.1.20 --nombre Entrada --marca hikvision --usuario admin --clave CLAVE
  python conectar.py agregar 0 --nombre Webcam            (webcam USB)
  python conectar.py agregar http://192.168.1.30/snapshot.jpg --nombre Patio
"""
import argparse, json, os, re, sys, time
from camara import slug, url_con_clave
from diagnostico import evaluar
from fuentes import MARCAS, candidatos, descubrir_onvif, escanear, probar, tipo_fuente

def muestrear(url, tipo, n=25):
    import cv2
    cap = cv2.VideoCapture(int(url) if tipo == "usb" else url); fr, t0, total = [], time.time(), 0
    while time.time() - t0 < 6 and len(fr) < n:
        ok, f = cap.read()
        if not ok: break
        total += 1
        if total % 3 == 0: fr.append(cv2.resize(f, (320, int(320 * f.shape[0] / f.shape[1]))))
        h, w = f.shape[:2]
    cap.release()
    return fr, total / max(1e-6, time.time() - t0), w, h

def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = a.add_subparsers(dest="cmd", required=True)
    sub.add_parser("descubrir")
    p = sub.add_parser("agregar")
    p.add_argument("origen"); p.add_argument("--nombre", required=True); p.add_argument("--zona", default="")
    p.add_argument("--marca", default="generica", choices=list(MARCAS)); p.add_argument("--usuario", default=""); p.add_argument("--clave", default="")
    p.add_argument("--modulos", default="per"); p.add_argument("--config", default="config.json")
    x = a.parse_args()
    if x.cmd == "descubrir":
        print("Buscando cámaras ONVIF..."); onvif = descubrir_onvif()
        print("ONVIF:", ", ".join(onvif) or "ninguna respondió")
        print("Buscando equipos con el puerto 554 abierto..."); print("RTSP:", ", ".join(escanear()) or "ninguno")
        return
    tipo = tipo_fuente(x.origen)
    if tipo is None and re.fullmatch(r"\d+\.\d+\.\d+\.\d+", x.origen):
        tipo, url = "rtsp", None
        for u in candidatos(x.origen, x.marca, x.usuario, x.clave):
            print("Probando", re.sub(r"//[^/@]*@", "//***@", u)); 
            if probar(u, tipo, 8): url = u; break
        if not url: sys.exit("Ninguna ruta respondió. Revisa IP, usuario, clave y que RTSP esté activado en la cámara.")
    elif tipo:
        url = url_con_clave({"nombre": x.nombre, "fuente": x.origen, "usuario": x.usuario, "clave": x.clave})
        if not probar(url, tipo, 10): sys.exit("No se pudo leer imagen de esa fuente.")
    else: sys.exit("Origen no válido: usa una IP, una URL rtsp/http, el número de una webcam o un archivo de video.")
    print("Conectada. Midiendo la imagen...")
    fr, fps, w, h = muestrear(url, tipo)
    if not fr: sys.exit("La cámara conectó, pero no entregó cuadros suficientes.")
    f = evaluar(fr, fps, w, h, tipo)
    print(f"  {w}x{h} · {f['fps']} fps · brillo {f['brillo']} · nitidez {f['nitidez']}" + (" · infrarrojo" if f["ir"] else ""))
    for c in f["consejos"]: print("  -", c)
    cfg = json.load(open(x.config)) if os.path.exists(x.config) else {"servidor": "http://127.0.0.1:8000", "token": "", "modelo": "yolov8n.pt", "noche": ["18:00", "06:00"], "camaras": []}
    cam = {"nombre": x.nombre, "zona": x.zona or x.nombre, "fuente": re.sub(r"//[^/@]*@", "//", url), "tipo": tipo, "modulos": x.modulos.split(","), "horario": "Siempre"}
    if x.usuario: cam.update(usuario=x.usuario, clave=x.clave)
    cfg["camaras"] = [c for c in cfg["camaras"] if slug(c["nombre"]) != slug(x.nombre)] + [cam]
    json.dump(cfg, open(x.config, "w"), indent=2, ensure_ascii=False); os.chmod(x.config, 0o600)
    print(f"Guardada en {x.config} (permisos solo para ti). Al iniciar camara.py se ajustará sola con esta medición.")
    if not cfg.get("token"): print("Falta el token del servidor en config.json.")

if __name__ == "__main__":
    main()
