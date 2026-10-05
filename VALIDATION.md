# Validación

Validación del 5 de octubre de 2026, en un entorno Docker con Python 3.11, Telegram Bot API local, qBittorrent y FFmpeg.

- 26 pruebas de regresión aprobadas en la imagen reconstruida, sin pruebas omitidas.
- Dos videos de una publicación pública descargados, convertidos y enviados mediante Telegram: 1.557.063 bytes en total, con formato y duración completa verificados.
- Adjuntos con texto, archivo sin nombre y ZIP comprobados nuevamente después del despliegue.
- Reproducción con imagen y sonido de uno de los videos convertidos confirmada en el cliente WhatsApp.
- Conversión real de WebM VP9/Opus a MP4 H.264 Baseline/AAC.
- Duración completa verificada; videos verticales, sin audio y con píxeles no cuadrados comprobados.
- Salidas de 30 fps, YUV 4:2:0, dimensiones pares y cabecera MP4 al inicio.
- Metadatos descriptivos de los archivos de prueba excluidos del resultado.
- Copias o conversiones fallidas no publican archivos incompletos. La recuperación reutiliza un video convertido válido.
- Audio sin video conservado como archivo; salida que supera el límite de envío rechazada.
- Autorización por usuario/chat, adjuntos con texto, documentos sin nombre, extracción ZIP/tar.gz, cola persistente, aislamiento de trabajos y salud de workers comprobados.

Las pruebas funcionales de Telegram usan transferencias y respuestas reales, con entradas simuladas en el dispatcher. La recepción externa de `/status` fue confirmada desde un cliente real. Las comprobaciones funcionales no se ejecutan en GitHub Actions porque requieren credenciales y producen mensajes.

La prueba de torrent usa una semilla HTTP dentro de Docker, sin probar peers o trackers públicos. El soporte de cada red social depende de yt-dlp y de los permisos del contenido. El formato convertido está preparado para compartir por WhatsApp, pero cada cliente puede imponer límites de tamaño adicionales; no se recortan ni dividen los videos para cumplir esos límites.

Este repositorio contiene ejemplos genéricos. No se incluyen credenciales, IDs de usuarios, hostname privado, rutas del servidor ni registros de mensajes.
