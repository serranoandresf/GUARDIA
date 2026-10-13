# Arquitectura objetivo de Guardia

## Posicionamiento honesto
No se compite con Apple o Google en ecosistema, hardware ni escala. Se compite donde ellos no quieren o no pueden: **privacidad local primero**, **cualquier cámara de cualquier marca**, **zonas rurales y sin internet estable**, y **asesoría de seguridad integral** (no solo cámaras). Esa es la propuesta; todo lo demás es ingeniería para sostenerla.

## Principios
1. **Local primero:** el análisis y el video viven en el hub del cliente; la nube es opcional.
2. **Confianza cero:** cada dispositivo y cada persona se autentican; todo se registra; los secretos no salen del hub.
3. **Cifrado extremo a extremo** para video y fotos cuando haya nube (claves por hogar, no por empresa).
4. **Orientado a eventos:** bus de eventos entre analizador, servidor y notificaciones.
5. **Observable y actualizable:** métricas, registros estructurados y actualizaciones firmadas del hub.

## Componentes
```
Cámaras (RTSP/ONVIF/MJPEG/USB) ─► HUB (mini PC / Raspberry Pi)
   ├─ Analizador: lector → movimiento → detector (motor intercambiable) → seguimiento → reglas
   ├─ Servidor local: API, usuarios+2FA, eventos, fotos/clips, métricas
   └─ Video en vivo (WebRTC) y almacenamiento cifrado
        │ mTLS (opcional)
        ▼
PLANO DE CONTROL (nube, opcional): cuentas, relevo seguro, push (APNs/FCM/Web Push), actualizaciones OTA, facturación
        ▼
APPS: web instalable (hoy), móvil nativa/empaquetada, panel de instalador
```

## Estado frente al objetivo
| Capa | Hoy | Objetivo |
|---|---|---|
| Detección | YOLO (AGPL) tras interfaz de motor | Motor de licencia permisiva + modelos propios de humo, fuego y placas |
| Seguimiento | IoU propio, probado en pruebas unitarias | Re-identificación y validación con video real |
| Autenticación | Token principal, tokens por dispositivo, **usuarios con scrypt + 2FA TOTP + sesiones firmadas** (servidor sin ejecutar aún) | Passkeys, políticas de sesión, recuperación de cuenta |
| Datos | SQLite, retención de 30 días | Cifrado en reposo; Postgres en la nube |
| Alertas | ntfy, escalado simple | Push propio con imagen y acciones; reglas por persona, zona y horario |
| Video | Captura por minuto; go2rtc aparte | WebRTC integrado, clips con pre-buffer, línea de tiempo |
| Operación | `/metricas`, auditoría | Trazas, alertas de plataforma, actualizaciones firmadas |
| Calidad | 23 pruebas Python + pruebas del panel | Pruebas end-to-end, banco de videos etiquetados, pruebas de carga |

## Modelo de amenazas (resumen)
Cámara tapada o congelada (detectada) · hub robado o apagado (latido, aviso de caída) · token robado (por dispositivo, revocable, 2FA) · intercepción de red (HTTPS obligatorio) · insider o cuenta comprometida (auditoría, roles) · fuerza bruta (límites y bloqueo).

## Hitos (criterios a validar con datos reales, no promesas)
1. **Verdad:** servidor y analizador ejecutados con 3 marcas de cámara durante 72 h; medir falsos positivos y omisiones por cámara.
2. **Seguridad:** prueba de penetración externa; passkeys; cifrado en reposo.
3. **Producto:** push propio, clips, WebRTC, app móvil.
4. **Escala:** plano de control multiusuario, OTA firmado, soporte.

## Equipo mínimo para ejecutarlo
Ingeniería de visión/ML · backend y seguridad · frontend/móvil · QA con hardware · producto y legal.
