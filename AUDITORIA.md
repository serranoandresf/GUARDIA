# Auditoría de Guardia (versión 0.5)

## Fallos encontrados y corregidos en esta revisión
1. **Merodeo no se detectaba en personas quietas:** el análisis solo corría con movimiento. Ahora sigue analizando 5 s después de la última detección.
2. **Modelo propio sin las clases esperadas:** YOLO habría detectado *todo*. Ahora el analizador se detiene con un mensaje claro.
3. **Alertas perdidas si el analizador se reiniciaba con el servidor caído:** ahora se guardan en `pendientes/` y se reenvían.
4. **Armar o desarmar tardaba hasta 30 s en llegar al analizador:** ahora 5 s.
5. **La foto de la alerta no mostraba qué se detectó:** ahora lleva recuadros y etiqueta.
6. **El panel no avisaba si el servidor rechazaba el token ni si una cámara estaba caída, tapada o congelada:** nueva tarjeta *Salud del sistema* en Inicio y estado en rojo cuando hay cámaras sin vigilar.
7. **Un respaldo dañado podía romper el panel:** ahora se filtran los datos inválidos al restaurar.
8. **Sin retención de datos:** eventos y fotos se borran a los 30 días (`RETENCION_DIAS`).
9. **go2rtc quedaba abierto sin contraseña en la red:** el `go2rtc.yaml` ahora incluye usuario y clave de API (cámbialos).

## Encontrado y NO corregido
- **El servidor nunca se ha ejecutado** en el entorno de desarrollo; sus pruebas corren solo en GitHub Actions.
- Sin contraseñas, doble verificación ni passkeys: la seguridad depende de tokens. Sin HTTPS, viajan en claro.
- El tema de ntfy funciona como contraseña de lectura y depende de un servicio externo.
- Con `GUARDIA_ORIGENES` vacío, el servidor rechaza peticiones de otros orígenes; si abres el panel como archivo local usa `GUARDIA_ORIGENES=null`.
- La cola `pendientes/` no tiene límite de espacio si el servidor está caído por mucho tiempo.
- El panel guarda cámaras y ajustes en `localStorage` sin cifrar.

## No verificable sin hardware
Conexión con cámaras reales, descubrimiento ONVIF, rendimiento de YOLO por equipo, ntfy, go2rtc y el aspecto en un celular.
