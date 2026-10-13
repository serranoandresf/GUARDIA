"""Autenticación sin dependencias externas: contraseñas (scrypt), 2FA TOTP (RFC 6238) y sesiones firmadas."""
import base64, hashlib, hmac, json, os, secrets, struct, time

def hash_clave(clave):
    sal = os.urandom(16)
    return f"scrypt$14$8$1${sal.hex()}${hashlib.scrypt(clave.encode(), salt=sal, n=2 ** 14, r=8, p=1, dklen=32).hex()}"

def verificar_clave(clave, guardado):
    try:
        _, n, r, p, sal, h = guardado.split("$")
        calc = hashlib.scrypt(clave.encode(), salt=bytes.fromhex(sal), n=2 ** int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc.hex(), h)
    except (ValueError, TypeError): return False

def nuevo_secreto(): return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")

def totp(secreto, t=None, paso=30, digitos=6):
    clave = base64.b32decode(secreto + "=" * (-len(secreto) % 8), casefold=True)
    h = hmac.new(clave, struct.pack(">Q", int((time.time() if t is None else t) // paso)), hashlib.sha1).digest()
    o = h[-1] & 15
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** digitos).zfill(digitos)

def verificar_totp(secreto, codigo, ventana=1, t=None):
    ahora = time.time() if t is None else t
    return any(hmac.compare_digest(totp(secreto, ahora + d * 30), str(codigo).strip()) for d in range(-ventana, ventana + 1))

def uri_totp(secreto, cuenta, emisor="Guardia"):
    from urllib.parse import quote
    return f"otpauth://totp/{quote(emisor)}:{quote(cuenta)}?secret={secreto}&issuer={quote(emisor)}"

def _b64(b): return base64.urlsafe_b64encode(b).decode().rstrip("=")
def _unb64(s): return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

def crear_sesion(clave, usuario, ttl=43200, ahora=None):
    cuerpo = _b64(json.dumps({"u": usuario, "exp": int((time.time() if ahora is None else ahora) + ttl)}).encode())
    return f"s1.{cuerpo}.{_b64(hmac.new(clave, cuerpo.encode(), hashlib.sha256).digest())}"

def verificar_sesion(clave, token, ahora=None):
    try:
        _, cuerpo, firma = token.split(".")
        if not hmac.compare_digest(_b64(hmac.new(clave, cuerpo.encode(), hashlib.sha256).digest()), firma): return None
        d = json.loads(_unb64(cuerpo))
        return d if d["exp"] > (time.time() if ahora is None else ahora) else None
    except Exception: return None
