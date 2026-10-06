# Validación

Validación actualizada el 6 de octubre de 2026, en un entorno Docker con Python 3.11, Telegram Bot API local, qBittorrent y FFmpeg.

- 29 pruebas de regresión aprobadas en la imagen reconstruida, sin pruebas omitidas.
- Reproducido y corregido el fallo que dejaba los enlaces con vista previa en «Preparando descarga»: el mensaje completo contenía valores internos del SDK que no se podían guardar en JSON.
- Dos enlaces públicos, uno de X y otro de Instagram, descargados, convertidos y enviados mediante el manejador de mensajes con vista previa: 19.193.059 bytes en total, con formato y duración completa verificados.
- Los mismos enlaces recibidos desde un cliente externo de Telegram completaron también sus trabajos en el servicio desplegado. Los resultados conservaron los aproximadamente 63 y 6 segundos originales, con H.264/AAC. Cola pendiente vacía al finalizar y servicio saludable, sin reinicios.
- Regresiones específicas para mensajes con vista previa, recuperación del usuario/chat y contexto de respuesta, y aviso al usuario ante un error al guardar el trabajo.
- Adjuntos con texto, archivo sin nombre y ZIP comprobados nuevamente después del despliegue.
- Reproducción con imagen y sonido de uno de los videos convertidos confirmada en el cliente WhatsApp.
- Conversión real de WebM VP9/Opus a MP4 H.264 Baseline/AAC.
- Duración completa verificada; videos verticales, sin audio y con píxeles no cuadrados comprobados.
- Salidas de 30 fps, YUV 4:2:0, dimensiones pares y cabecera MP4 al inicio.
- Metadatos descriptivos de los archivos de prueba excluidos del resultado.
- Copias o conversiones fallidas no publican archivos incompletos. La recuperación reutiliza un video convertido válido.
- Audio sin video conservado como archivo; salida que supera el límite de envío rechazada.
- Autorización por usuario/chat, adjuntos con texto, documentos sin nombre, extracción ZIP/tar.gz, cola persistente, aislamiento de trabajos y salud de workers comprobados.

El script de pruebas funcionales usa transferencias y respuestas reales, con entradas simuladas en el dispatcher, incluyendo los objetos de vista previa del SDK. Además se verificaron los trabajos de los dos enlaces recibidos desde un cliente externo de Telegram. La reproducción en WhatsApp fue confirmada por el usuario en la validación anterior. Las comprobaciones funcionales no se ejecutan en GitHub Actions porque requieren credenciales y producen mensajes.

La prueba de torrent usa una semilla HTTP dentro de Docker, sin probar peers o trackers públicos. El soporte de cada red social depende de yt-dlp y de los permisos del contenido. El formato convertido está preparado para compartir por WhatsApp, pero cada cliente puede imponer límites de tamaño adicionales; no se recortan ni dividen los videos para cumplir esos límites.

Este repositorio contiene ejemplos genéricos. No se incluyen credenciales, IDs de usuarios, hostname privado, rutas del servidor ni registros de mensajes.
