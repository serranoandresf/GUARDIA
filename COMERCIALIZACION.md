# Guardia: estado frente a una venta comercial

**Estado honesto:** prototipo avanzado, todavía no un producto vendible. El servidor y el analizador nunca se han probado con cámaras reales ni se han ejecutado fuera de pruebas unitarias. Un producto de seguridad que falla causa daño real y responsabilidad para quien lo vende.

## Bloqueantes antes de vender
1. **Licencias.** Ultralytics YOLO es AGPL-3.0 (ver `AVISOS_TERCEROS.md`). Además, este repositorio tiene licencia **MIT**: si lo subes público, cualquiera puede usarlo y revenderlo. Si vas a venderlo, usa un repositorio privado y una licencia propietaria; decídelo con un abogado antes de publicar.
2. **Pruebas reales.** Matriz de cámaras (marcas, firmwares, resoluciones), 72 horas o más de operación continua, cortes de red y de energía, y medición de falsos positivos y omisiones con videos propios etiquetados.
3. **Seguridad.** Prueba de penetración independiente, contraseñas con doble verificación o passkeys, revisión de dependencias y política de actualizaciones.
4. **Legal (Colombia, confirmar con abogado):** términos de servicio, política de tratamiento de datos (Ley 1581 de 2012), avisos de videovigilancia, registro de bases de datos si aplica y limitación de responsabilidad. Revisa también que el nombre "Guardia" esté disponible como marca.
5. **Soporte.** Instalación guiada, documentación para no técnicos, canal de soporte y plan de actualizaciones.

## Brechas técnicas (esfuerzo relativo)
| Brecha | Esfuerzo |
|---|---|
| Cuentas de usuario con 2FA y roles por persona | M |
| Notificaciones push propias (Web Push/FCM) con imagen y acciones | M |
| Clips de video y línea de tiempo | M |
| Video en vivo dentro del panel (WebRTC) | M |
| Modelos de humo, fuego y placas, y motor de detección de licencia permisiva | L |
| Aplicaciones móviles nativas o empaquetadas | M |
| Multiusuario en la nube (varios clientes) con aislamiento de datos | L |
| Instalador de un comando y actualizaciones firmadas | M |
| Pruebas end-to-end e integración continua completa | M |

## Modelos de negocio posibles (sin precios)
- **Licencia instalada (on-premise):** el cliente tiene sus datos; menos costo de nube; más soporte de instalación.
- **Suscripción en la nube (SaaS):** ingresos recurrentes; exige multiusuario, cumplimiento de datos y operación 24/7.
- **Equipo + servicio:** mini PC preconfigurado más instalación; más margen por unidad, más logística.
