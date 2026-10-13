"""Fuentes de video: tipos de cámara, plantillas por marca, descubrimiento en la red y prueba de conexión."""
import re, socket, time, uuid

# Rutas RTSP habituales por marca. Son plantillas: algunos modelos usan otras rutas.
MARCAS = {
    "generica": ["/stream1", "/stream", "/live", "/h264", "/1"],
    "hikvision": ["/Streaming/Channels/101", "/Streaming/Channels/102"],
    "dahua": ["/cam/realmonitor?channel=1&subtype=0", "/cam/realmonitor?channel=1&subtype=1"],
    "amcrest": ["/cam/realmonitor?channel=1&subtype=0"],
    "tapo": ["/stream1", "/stream2"],
    "reolink": ["/h264Preview_01_main", "/h264Preview_01_sub"],
    "axis": ["/axis-media/media.amp"],
    "foscam": ["/videoMain", "/videoSub"],
}

def tipo_fuente(s):
    """rtsp, mjpeg (transmisión web), instantanea (foto fija por web), usb (número), archivo, o None."""
    s = str(s or "").strip()
    if re.fullmatch(r"\d{1,2}", s): return "usb"
    if s.startswith(("rtsp://", "rtsps://")): return "rtsp"
    if s.startswith(("http://", "https://")):
        return "instantanea" if re.search(r"(snapshot|\.jpe?g($|\?)|/image($|\?))", s, re.I) else "mjpeg"
    if s and "://" not in s and re.search(r"\.(mp4|avi|mkv|mov)$", s, re.I): return "archivo"
    return None

def candidatos(ip, marca="generica", usuario="", clave="", puerto=554):
    from urllib.parse import quote
    auth = f"{quote(usuario, safe='')}:{quote(clave, safe='')}@" if usuario else ""
    out = []
    for m in ([marca] if marca in MARCAS else list(MARCAS)):
        for ruta in MARCAS[m]:
            u = f"rtsp://{auth}{ip}:{puerto}{ruta}"
            if u not in out: out.append(u)
    return out

def ip_local():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try: s.connect(("8.8.8.8", 80)); return s.getsockname()[0]  # no envía datos
        except OSError: return "127.0.0.1"

def escanear(prefijo=None, puerto=554):
    """Equipos de la red local con el puerto RTSP abierto."""
    from concurrent.futures import ThreadPoolExecutor
    prefijo = prefijo or ip_local().rsplit(".", 1)[0]
    def uno(i):
        ip = f"{prefijo}.{i}"
        with socket.socket() as s:
            s.settimeout(0.4)
            return ip if s.connect_ex((ip, puerto)) == 0 else None
    with ThreadPoolExecutor(64) as ex: return [x for x in ex.map(uno, range(1, 255)) if x]

_PROBE = """<?xml version="1.0" encoding="UTF-8"?><e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" xmlns:dn="http://www.onvif.org/ver10/network/wsdl"><e:Header><w:MessageID>uuid:{id}</w:MessageID><w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To><w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action></e:Header><e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body></e:Envelope>"""

def descubrir_onvif(espera=3):
    """WS-Discovery (ONVIF) por multicast; devuelve las IP que responden."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(0.5)
    s.sendto(_PROBE.format(id=uuid.uuid4()).encode(), ("239.255.255.250", 3702))
    ips, fin = set(), time.time() + espera
    while time.time() < fin:
        try: datos, _ = s.recvfrom(65535)
        except OSError: continue
        ips.update(re.findall(r"https?://(\d+\.\d+\.\d+\.\d+)", datos.decode(errors="ignore")))
    s.close()
    return sorted(ips)

def probar(url, tipo=None, seg=8):
    """Intenta leer un cuadro. Devuelve True/False."""
    import cv2
    cap = cv2.VideoCapture(int(url) if tipo == "usb" else url)
    try:
        t = time.time()
        while time.time() - t < seg:
            if cap.read()[0]: return True
        return False
    finally: cap.release()
