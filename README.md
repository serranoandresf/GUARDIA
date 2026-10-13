# Guardia

Panel, analizador de cámaras y servidor de avisos para hogar, negocio y finca. **Es un prototipo: ayuda a detectar y avisar, no garantiza protección.** Pruébalo con cámaras y personas reales antes de depender de él.

```
web/        Panel PWA (JS puro). Muestra la última captura que sube el analizador (una por minuto), no video en vivo.
analizador/ Lee RTSP, detecta con YOLO + tracking y envía alertas con foto.
servidor/   API FastAPI + SQLite: alertas, estado de armado, latido, fotos.
pruebas/    Pruebas de la lógica (python3 pruebas/test_nucleo.py).
```

## Inicio rápido: subir a GitHub y abrir la app desde el enlace
1. Descomprime `guardia-completo.zip`. Entra a la carpeta resultante: **lo que hay dentro** (no la carpeta misma) es el repositorio.
2. En GitHub crea el repositorio (vacío, sin README) y sube todo. Lo más fiable en Windows es **GitHub Desktop**: *File → Add local repository* → elige la carpeta → *Publish repository*.
   Alternativa con la web: *Add file → Upload files* y arrastra todo el contenido (límite de 100 archivos por subida; este proyecto tiene menos). Al terminar, comprueba que en la raíz aparezcan las carpetas `app`, `analizador`, `servidor`, `web`, `tests` y `docs`.
3. *Settings → Pages → Build and deployment → Deploy from a branch → `main` / `(root)` → Save*.
4. En uno o dos minutos la app abre en `https://TU-USUARIO.github.io/NOMBRE-DEL-REPO/`. El `index.html` de la raíz es la app (idéntico a `web/index.html`; `python scripts/sincronizar_raiz.py` los mantiene iguales y las pruebas lo verifican).

## Tenerlo en todos tus dispositivos
El **código** vive en GitHub (clónalo en cualquier equipo). La **app** la entrega tu servidor en `https://TU-SERVIDOR/panel/`: abres esa dirección en el celular, la tablet o el computador y la instalas como app. Las cámaras, contactos y medidas se **sincronizan entre dispositivos** (se guardan en el servidor). Lo que no se comparte, a propósito: el token de cada dispositivo y el aspecto (tema, color).

1. **Sube el repositorio a GitHub** (privado, si quieres):
   ```bash
   unzip guardia.zip && cd guardia
   git init && git add . && git commit -m "Guardia"
   git branch -M main
   git remote add origin https://github.com/TU-USUARIO/guardia.git
   git push -u origin main
   ```
   `.gitignore` deja fuera `.env`, `config.json`, `go2rtc.yaml`, la base de datos y las fotos. Revisa con `git status` que no subas claves. En la pestaña *Actions* verás las pruebas.
2. **En el equipo del servidor:** `git clone` del repositorio, `cp .env.example .env` y `cp go2rtc.example.yaml go2rtc.yaml`, edita ambos y ejecuta `docker compose --env-file .env up -d --build`.
3. **Dale HTTPS** (necesario para instalar la app y para que el token no viaje en claro). Opciones sin abrir puertos del router, no probadas aquí: Tailscale (`tailscale serve`) o un túnel de Cloudflare (`cloudflared tunnel`). Consulta su documentación.
4. **En cada dispositivo:** abre `https://TU-SERVIDOR/panel/` → Ajustes → pega el token (o crea un token propio para ese dispositivo en *Dispositivos*) → *Guardar y probar* → menú del navegador → *Agregar a la pantalla de inicio*.
5. **En el equipo del analizador (junto a las cámaras):** `cd analizador && pip install -r requirements.txt && python conectar.py descubrir`, luego `agregar`, y `python camara.py`.

Abrir la app desde el enlace de GitHub Pages: *Settings → Pages → Deploy from a branch → `main` / `(root)`*. El `index.html` de la raíz es la propia app (copia de `web/`; se mantiene con `python scripts/sincronizar_raiz.py`), así que `https://TU-USUARIO.github.io/NOMBRE-DEL-REPO/` abre la app directamente (no hace falta ningún flujo de GitHub Actions). El servidor y el analizador siguen corriendo en tus equipos. Si el panel se sirve desde Pages, el servidor debe tener HTTPS y `GUARDIA_ORIGENES=https://TU-USUARIO.github.io` (solo el origen, sin ruta). La sincronización guarda lo último que cambió: si dos dispositivos editan a la vez, gana el último. Los contactos (con teléfono) quedan guardados en tu servidor.

## Qué hace hoy
- **Detección:** personas, vehículos y animales (clases COCO), con filtro de movimiento, confirmación en 3 inferencias, zonas poligonales, área mínima y agrupación de avisos. **Merodeo** = persona en la zona más de `merodeo_seg` segundos.
- **Horarios:** *Siempre* vigila con el sistema en perímetro o armado total; *Noche* hace lo mismo solo en el horario nocturno (editable, por defecto 18:00–06:00); *Armado* vigila solo en armado total.
- **Fiabilidad:** el analizador marca cámara caída, tapada o congelada, envía un latido cada 30 s y reintenta alertas con espera creciente (la cola vive en memoria: se pierde si el proceso se apaga). El servidor avisa por ntfy si el analizador deja de responder con el sistema armado.
- **Servidor:** SQLite, id único por evento (reintentos sin duplicados), estados de evento, foto JPEG por alerta, token comparado en tiempo constante, límite de intentos y validación de campos.
- **Panel:** sincroniza armado y alertas cada 15 s, muestra "Sin vigilancia" si el analizador no da señal y permite ver la foto y marcar falsa alarma.

## Novedades de la versión 0.3
- **Dispositivos con permisos:** el token principal es el propietario; desde Ajustes creas tokens para familiares (ver y armar), solo lectura o el analizador, y los revocas. El servidor guarda solo su huella y registra quién arma o desarma (`GET /auditoria`).
- **Claves fuera del panel:** el panel no acepta URLs RTSP con usuario y clave (las quita de datos anteriores). Van en `config.json` del analizador o en la variable `GUARDIA_CLAVE_<NOMBRE>`. El token del servidor ya no se guarda en el navegador salvo que actives *Recordar*. En `go2rtc.yaml` reemplaza `USUARIO:CLAVE`.
- **Editor de zonas:** el analizador sube una captura por minuto; en cada cámara, *Zona* te deja marcar el área sobre la imagen real y exporta el polígono.
- **Acuse y escalado:** abrir Alertas marca los avisos como vistos. Si uno sigue sin verse tras `ESCALAR_MIN` minutos (5 por defecto), el servidor lo reenvía por ntfy.
- **Capas de protección** (disuadir, detectar, demorar, responder) en *Mejorar*; el porcentaje se llama ahora "medidas completadas" y aclara que no mide riesgo.
- **CI:** `.github/workflows/ci.yml` ejecuta las pruebas, incluidas las del servidor.

## Conectar cualquier cámara
El analizador acepta: cámaras de red **RTSP** (ONVIF), **transmisiones web MJPEG**, **fotos fijas por web** (snapshot), **webcams USB** y **archivos de video**. Funciona con cualquier marca que ofrezca RTSP, y trae plantillas para Hikvision, Dahua/Amcrest, Tapo, Reolink, Axis y Foscam (pueden variar por modelo).

```bash
cd analizador
python conectar.py descubrir                 # busca ONVIF y equipos con el puerto 554 abierto
python conectar.py agregar 192.168.1.20 --nombre Entrada --marca hikvision --usuario admin --clave CLAVE
```
`agregar` prueba las rutas de la marca, mide la imagen (resolución, fps, brillo, nitidez, infrarrojo, ruido) y la guarda en `config.json` (permisos solo para ti). Al iniciar, `camara.py` repite la medición cada 6 horas y **ajusta sola** la sensibilidad al movimiento, la confianza y el tamaño mínimo (lo que fijes tú en la configuración manda). El panel muestra la ficha y los consejos de cada cámara.

No funcionan las cámaras que solo transmiten a una nube propietaria sin RTSP ni ONVIF (por ejemplo muchos modelos de Ring, Nest o Wyze sin firmware alternativo).

## Revisión y salud del sistema
`AUDITORIA.md` lista los fallos encontrados, los corregidos y los pendientes. Dentro de la app, *Salud del sistema* (Inicio) revisa en vivo: servidor y token, analizador, estado de cada cámara, HTTP sin cifrar, zonas sin definir, consejos de imagen, contactos, notificaciones y medidas. Las alertas no entregadas se guardan en `analizador/pendientes/` y el servidor borra eventos y fotos tras `RETENCION_DIAS` (30 por defecto). Si abres `web/index.html` como archivo local, define `GUARDIA_ORIGENES=null`.

## Plan de protección, informe y contraste
- **Evalúa tu lugar** (Mejorar): 6 preguntas según el modo (tiempo solo, valor a proteger, vecinos, incidentes, tiempo de ayuda y una propia de hogar, negocio o finca). La app ordena las medidas pendientes por su peso y por las capas que tu situación más necesita, explica el motivo de cada una y te dice cuánto del plan pendiente reduce completarla (*¿Y si?*). Es una guía de prioridades, **no mide el riesgo real** ni incluye precios.
- **Informe de 7 días** (Alertas): total, falsas alarmas, cámaras más activas, hora pico y sugerencias (por ejemplo, marcar una zona si más del 40% de una cámara son falsas alarmas). Se calcula con los datos del servidor.
- **Contraste:** el panel avisa si el color elegido se ve poco sobre el fondo y elige el color del texto con mejor contraste.
- Las pruebas de esta lógica están en `pruebas/test_panel.js` (`node pruebas/test_panel.js`) y corren en la integración continua.

## Cuentas con usuario, clave y 2FA
Con el token principal (propietario) crea tu usuario: `POST /usuarios` (clave de 10+ caracteres). Luego, en el panel (Ajustes → *Iniciar sesión con usuario y clave*), entras y obtienes una sesión de 12 horas; `POST /auth/2fa` te da un secreto para tu app de autenticación y `POST /auth/2fa/confirmar` lo activa. Las contraseñas se guardan con scrypt, hay bloqueo de 5 minutos tras 5 fallos y cada entrada queda en `/auditoria`. Todo se maneja desde Ajustes → *Usuarios y seguridad* (crear usuarios, activar el 2FA con el secreto en tu app de autenticación y ver el registro de actividad). Arquitectura objetivo: `ARQUITECTURA.md`.

## Sin IA: detección por movimiento
Con `"motor": "movimiento"` en `config.json` y el módulo *Movimiento (sin IA)* en la cámara, el analizador no necesita YOLO (solo numpy): agrupa las zonas que cambian y avisa. Sirve para equipos modestos y evita la dependencia AGPL, pero no distingue personas de sombras o árboles; úsalo con zonas marcadas.

## Motor de video industrial (Eje 1)
El paquete `app/video/` contiene el nuevo núcleo: dominio estricto, buffer circular con backpressure, ingesta por cámara con circuit breaker y reconexión
con jitter, y un Write-Ahead Log que no pierde eventos confirmados ante cortes de energía. Documentación y contrato de durabilidad en
`docs/EJE1_MOTOR_DE_VIDEO.md`. **Todavía no está conectado** al analizador y al servidor existentes (`analizador/` y `servidor/`): la integración
es el siguiente paso. Ejes pendientes: 2 (sincronización offline), 3 (endurecimiento y sesiones) y la parte restante del 4.

## Para comercializarlo
Lee `COMERCIALIZACION.md` y `AVISOS_TERCEROS.md` antes de vender: hay un bloqueante de licencia (YOLO es AGPL-3.0) y el repositorio está bajo MIT. El servidor expone cabeceras de seguridad y `/metricas` (formato Prometheus, solo propietario). El seguimiento de objetos es propio (`seguimiento.py`) y el motor de detección es intercambiable (`detectores.py`); el comportamiento del seguimiento propio frente al de ByteTrack no se ha comparado con video real.

## Qué NO hace
Video real dentro del panel (go2rtc se abre aparte), humo/fuego y placas, cuentas con contraseña o passkeys, push con imagen y botones, clips de video, sensores, ocaso/amanecer automático, motor de riesgo con costos, ni pruebas end-to-end. El token propietario y el de cada dispositivo viajan por HTTP si no pones HTTPS delante.

## Verificación
Probado: la lógica del analizador (zonas, horarios, confirmación, validación) con `pruebas/test_nucleo.py`. **No probado:** servidor en ejecución (las pruebas del servidor existen pero no se pudieron correr en el entorno de desarrollo), analizador con cámaras reales y YOLO, ntfy, go2rtc, Docker y el panel en un celular. Las versiones fijadas en `servidor/requirements.txt` no se verificaron instalándolas.

## Seguridad
Cambia las claves de fábrica de cámaras y router, no expongas RTSP a internet, publica el servidor solo con HTTPS (Caddy o un túnel como Tailscale o Cloudflare Tunnel) y trata el tema de ntfy como una contraseña. Si grabas personas, consulta la normativa local de datos personales y videovigilancia.

MIT.
