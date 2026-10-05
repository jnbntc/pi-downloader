# pi-downloader

Bot de Telegram para guardar archivos en un NAS, descargar multimedia de redes sociales, agregar torrents a qBittorrent y extraer archivos comprimidos. Incluye un servidor local de Telegram Bot API y funciona con Docker Compose.

## Funciones

- Documentos, videos y audios de Telegram, también cuando tienen un texto adjunto.
- Enlaces de YouTube, Instagram, X/Twitter, TikTok, Reddit y Facebook, sujetos al soporte de yt-dlp y a los permisos de cada publicación.
- Publicaciones con varios videos; cada trabajo conserva sus propios archivos.
- Videos completos convertidos a MP4 H.264/AAC para compartir por WhatsApp.
- Magnets, URLs `.torrent` y documentos `.torrent`.
- Extracción con 7zip, con validación de rutas, enlaces, cantidad de archivos y tamaños. Los `.tar.gz` se extraen completamente.
- `/status` y `/stats`: memoria, temperatura, discos, transferencias y workers.
- `/unzip /ruta/al/archivo` y `/extract /ruta/al/archivo`: rutas dentro de media o torrents.
- Cola persistente para multimedia y extracción, con recuperación después de un reinicio.

No descarga URLs arbitrarias de archivos ni procesa fotos o notas de voz. No garantiza acceso a publicaciones privadas o que requieren iniciar sesión. No se incluyen cookies de redes sociales.

## Videos para WhatsApp

Cada video descargado de una red social se convierte a MP4 con H.264 Baseline, píxeles YUV 4:2:0 y audio AAC estéreo cuando el original tiene sonido. Se conserva su duración completa, sin dividirlo ni recortarlo. Se ajustan las dimensiones dentro de 1280 × 720, conservando la orientación y la proporción de imagen, a 30 fotogramas por segundo. La cabecera MP4 se coloca al inicio para reproducción progresiva.

El original queda en `rrss/<id-de-trabajo>/originals/`; el MP4 convertido se guarda junto a ese directorio y es el archivo que el bot envía por Telegram. No se modifica la recepción de videos adjuntos: la conversión corresponde a descargas de redes sociales. Los metadatos descriptivos del original no se copian al MP4.

Este modo prioriza conservar cada video completo. No impone un límite de tamaño de WhatsApp ni prepara clips para estados; la aplicación puede exigir compresión adicional o envío como documento para archivos grandes. La conversión tiene un timeout configurable de 3600 segundos (`VIDEO_TIMEOUT`) y verifica el formato, la duración y el límite de envío de Telegram antes de publicar el resultado. Se requiere espacio para el original y su copia convertida.

## Configuración inicial

Necesitás Docker Compose, un token de BotFather, el API ID/API hash de tu aplicación de Telegram y una contraseña de qBittorrent. El bot corre como UID/GID 1000. Los ajustes `PUID` y `PGID` corresponden al contenedor de qBittorrent.

```sh
cp .env.example .env
chmod 600 .env
```

Completá `.env` con tus credenciales y los IDs numéricos autorizados. `ALLOWED_USER_IDS` y `ALLOWED_CHAT_IDS` admiten listas separadas por comas. Para un chat privado, ambos suelen contener el ID del usuario. Para grupos, se necesitan el ID del grupo y los IDs de las personas autorizadas. Sin IDs o credenciales requeridas, el bot no inicia.

Prepará los directorios según las rutas elegidas en `.env`. Con las rutas de ejemplo:

```sh
sudo install -d -m 775 -o 1000 -g 1000 data/media data/torrents data/workspace data/app-data/qbittorrent
sudo install -d -m 755 -o 101 -g 101 data/app-data/telegram-api data/workspace/telegram-cache
docker compose up -d telegram-api qbittorrent
```

Configurá en la WebUI de qBittorrent una contraseña fuerte que coincida con `QBIT_PASSWORD`. Desactivá las excepciones de autenticación para localhost y subredes. En una instalación nueva, la imagen puede informar una contraseña temporal en sus registros; no la publiques. El puerto web se limita a `127.0.0.1` por defecto. Podés acceder mediante un túnel SSH o ajustar `QBIT_BIND_IP` para tu red.

Después:

```sh
docker compose config --quiet
docker compose up -d --build orchestrator
docker compose ps
```

No uses `docker compose config` sin `--quiet` al compartir resultados: su salida puede incluir credenciales resueltas.

## Directorios

Las rutas externas se eligen en `.env`. Dentro de los contenedores:

| Contenido | Orquestador | Otro contenedor |
|---|---|---|
| Archivos finales | `/data/media` | Telegram API: misma ruta, solo lectura |
| Torrents | `/data/torrents`, solo lectura | qBittorrent: `/downloads` |
| Trabajo temporal | `/data/workspace` | Temporal de Telegram: subdirectorio montado en `/tmp/telegram-bot-api` |
| Caché de Telegram | `/var/lib/telegram-bot-api`, solo lectura | Telegram API: misma ruta, lectura/escritura |

El bot publica copias completas con nombres únicos. Los archivos multimedia se guardan en `rrss/<id-de-trabajo>/`; las extracciones tienen destinos únicos y se publican mediante un renombrado atómico después de copiarse al disco final. Una copia interrumpida no se considera una extracción terminada. El código se monta solo para lectura y el bot no tiene acceso al socket de Docker.

## Límites y recuperación

Los valores de ejemplo permiten dos operaciones de disco/workers concurrentes, 50 trabajos en cola, hasta 10 archivos por publicación y 8.000.000.000 bytes por adjunto recibido y 2.000.000.000 bytes por archivo enviado a Telegram. Los límites de recepción y envío son independientes. La extracción permite hasta 10.000 archivos y 10.000.000.000 bytes declarados, con otra verificación al terminar. Cada subproceso tiene un timeout; estos controles no reemplazan cuotas o un aislamiento de sistema de archivos para archivos hostiles.

La base SQLite de trabajos pendientes y el marcador de salud están bajo `workspace/.pi-downloader`, con permisos privados. Incluyen datos de mensajes necesarios para recuperar tareas; no deben publicarse. Los trabajos de multimedia y extracción sobreviven a reinicios. Una interrupción después del envío pero antes de confirmar el trabajo puede producir un envío repetido al recuperarse. Las copias de adjuntos en curso no se reanudan automáticamente.

El chequeo de salud exige un marcador reciente y el número esperado de workers. Un contenedor `unhealthy` requiere investigar; Docker Compose no lo reinicia automáticamente solo por ese estado. Los workers se supervisan y las fallas de notificaciones no los terminan.

## Pruebas

Pruebas de regresión sin conexión a Telegram:

```sh
python -m pip install -r orchestrator/requirements.txt
python -m unittest discover -s tests -v
```

Las pruebas de extracción requieren `7zz` o `7z`, y las de video requieren `ffmpeg` y `ffprobe`. GitHub Actions instala 7zip y FFmpeg y ejecuta la suite y la validación de Compose. Las dependencias directas y las imágenes de Telegram/qBittorrent están fijadas a las versiones del entorno validado; el tag base de Python y las dependencias transitivas todavía pueden cambiar. Revisá las actualizaciones con las pruebas antes de desplegar.

Las comprobaciones funcionales siguientes son optativas y tienen efectos reales. La primera envía archivos pequeños y el contenido indicado al chat autorizado; simula la entrada de mensajes, pero usa la API real para transferencias, `getFile`, copias y respuestas:

```sh
docker compose exec -T orchestrator python - --chat-id TU_ID --media-url URL_DE_PRUEBA --expected-videos 2 < scripts/functional_check.py
```

La siguiente agrega un torrent privado de 64 KiB, lo descarga desde un servidor HTTP temporal dentro de Docker, verifica sus bytes, comprueba la entrada de un magnet y elimina únicamente ese torrent de prueba:

```sh
docker compose exec -T orchestrator python - < scripts/torrent_check.py
```

Para comprobar además los handlers del bot, agregá `--chat-id TU_ID`. Esa opción envía un `.torrent` pequeño al chat y simula su recepción con texto adjunto y la de un magnet, usando transferencias y llamadas de qBittorrent reales:

```sh
docker compose exec -T orchestrator python - --chat-id TU_ID < scripts/torrent_check.py
```

No comprueba peers públicos ni trackers externos. También es necesario enviar `/status` y un archivo desde un cliente Telegram real para validar la recepción externa mediante polling. Los resultados del entorno revisado se documentan en [VALIDATION.md](VALIDATION.md).

## Credenciales y publicación

`.env`, datos, bases de estado, sesiones, cookies, registros y respaldos se excluyen de Git. Nunca agregues credenciales al repositorio ni a sus issues. Los respaldos del entorno y los informes con datos del operador deben mantenerse por separado.

El proyecto aún no incluye una licencia de redistribución elegida por su autor.
