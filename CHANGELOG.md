# Cambios

## Corrección de enlaces con vista previa

- Los enlaces recibidos con vista previa de Telegram ingresan correctamente a la cola. Se guarda solo la información necesaria para responder y recuperar el trabajo, evitando los valores internos del SDK que impedían serializar el mensaje.
- Un error al guardar un trabajo informa al usuario que no se pudo iniciar, en vez de dejar el estado «Preparando descarga».
- La comprobación funcional de multimedia recorre el manejador de mensajes con una vista previa, además de descargar, convertir y enviar el video.
- Tres regresiones adicionales comprueban recepción de enlaces, recuperación del contexto de respuesta y aviso ante fallas de persistencia.

## Versión inicial

- Autorización obligatoria por usuario y chat, con inicio cerrado ante configuración incompleta.
- Adjuntos con texto y documentos sin nombre recibidos correctamente.
- Nombres únicos, publicación atómica de copias y contención de rutas.
- Publicación atómica de directorios extraídos entre discos, con recuperación de copias temporales interrumpidas.
- Resultados de yt-dlp asociados a cada trabajo y envío de todas sus salidas.
- Uso de rutas locales en la API de Telegram para enviar archivos.
- Selección de `7zz` o `7z`, validación de archivos comprimidos y soporte completo de tar comprimido.
- Cola SQLite persistente, límite de cola y supervisión de workers.
- Notificaciones protegidas y llamadas de qBittorrent fuera del bucle de eventos.
- Credenciales de qBittorrent por entorno; eliminación de contraseñas de ejemplo en el código.
- Rutas temporales de Telegram alineadas con los montajes y porcentaje de disco corregido.
- Código y caché de Telegram en montajes de solo lectura; acceso limitado al almacenamiento necesario.
- Chequeo de salud, límites de tareas, pruebas de regresión y comprobaciones funcionales optativas.

- Conversión de videos completos a MP4 H.264 Baseline/AAC, con duración verificada, orientación conservada y reproducción progresiva.
- Rutas internas genéricas y ejemplos sin configuración privada.
