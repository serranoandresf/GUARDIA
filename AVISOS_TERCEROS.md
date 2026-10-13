# Componentes de terceros y licencias (verifica cada una en su fuente antes de vender)

| Componente | Uso | Licencia (según mi conocimiento) |
|---|---|---|
| **Ultralytics YOLO** y pesos `yolov8*.pt` | Detección de personas, vehículos y animales | **AGPL-3.0**. Vender un producto cerrado exige una licencia comercial de Ultralytics, o publicar tu código bajo AGPL. Es el punto crítico. |
| OpenCV | Lectura de video e imagen | Apache-2.0 |
| NumPy | Diagnóstico de imagen | BSD-3 |
| Requests | Envío de alertas | Apache-2.0 |
| FastAPI, Pydantic | Servidor | MIT |
| Uvicorn | Servidor | BSD-3 |
| go2rtc | Video para el navegador | MIT |
| ntfy | Avisos al celular | Apache-2.0 / GPL-2.0 (doble) |

**Cómo salir de la dependencia AGPL:** el analizador ya separa el motor (`analizador/detectores.py`) y el seguimiento (`seguimiento.py`, propio). Para vender sin licencia de Ultralytics, agrega un motor con un modelo de licencia permisiva (por ejemplo uno con Apache-2.0 o MIT) y comprueba también la licencia de sus pesos y de su conjunto de entrenamiento. Ya existe un motor **sin IA** (`"motor": "movimiento"` con el módulo `mov`, solo numpy) como alternativa de licencia permisiva, con calidad menor: no distingue personas de sombras. Falta un motor de IA con un modelo permisivo; no está escrito ni probado.
