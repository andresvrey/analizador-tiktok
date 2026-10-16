# Analizador de TikTok

**Qué está funcionando en TikTok en tu nicho, y cómo está editado.** Sin clave de API, sin cuenta y sin pagar una herramienta: un solo archivo de Python que lee las páginas públicas de TikTok y mide los videos con ffmpeg en tu propio computador.

> *English summary below.*

## Para qué sirve

Antes de grabar un video para una marca, quiero saber dos cosas: qué videos del nicho de verdad se movieron, y cómo están editados por dentro. Este script responde a las dos.

| Comando | Qué hace |
| :--- | :--- |
| `tag` | Barre uno o varios hashtags y trae los videos que más han funcionado en ellos |
| `perfil` | Barre cuentas y trae lo que publicaron hace poco, incluso hace horas |
| `ficha` | Las métricas finas de un video: vistas, likes, comentarios, compartidos, audio, fecha, duración |
| `bajar` | Baja el MP4 para estudiarlo |
| `analizar` | Mide la edición: cortes, duración media de plano, cortes en los primeros 3 segundos, nivel de audio y, con `--gancho`, la transcripción de los primeros 15 segundos |

## La métrica que importa no son las vistas

Los resultados se ordenan por **compartidos sobre vistas**. Un video que la gente reenvía es un video que el algoritmo empuja; uno con muchas vistas y pocos compartidos suele ser una cuenta grande publicando lo de siempre.

La segunda métrica es el **índice de alcance**: vistas divididas entre los seguidores de la cuenta. Mide cuánto se salió el video de su propia audiencia. Un 50x es un video que se rompió; un 2x es rutina.

De aquí se saca la **estructura** del gancho, nunca el texto. Copiar el texto es lo que hace que un feed se sienta clonado.

## Instalación

Necesitas Python 3.9 o superior y [ffmpeg](https://ffmpeg.org/download.html) en el PATH (solo para `analizar`).

```bash
git clone https://github.com/CristianVrey/analizador-tiktok.git
cd analizador-tiktok
pip install -r requirements.txt
```

Las dos dependencias son opcionales: `yt-dlp` solo tapa huecos cuando TikTok no expone un dato, y `faster-whisper` solo se usa con `--gancho`. Los comandos `tag`, `perfil` y `ficha` funcionan con la biblioteca estándar de Python.

## Uso

```bash
# Lo que más ha funcionado en dos hashtags, con métricas completas
python tiktok.py tag marketingdigital emprendimiento --enriquecer

# Lo reciente de dos cuentas
python tiktok.py perfil @cuenta1 @cuenta2 --enriquecer

# Guardar el barrido como JSON y tabla markdown
python tiktok.py tag marketingdigital --enriquecer --salida radar

# Cómo está editado un video, con el gancho transcrito
python tiktok.py analizar https://www.tiktok.com/@cuenta/video/<id> --gancho
```

`--sin-ytdlp` hace el barrido más rápido usando solo las páginas de TikTok. `--modelo small` hace la transcripción más rápida a cambio de perder algunas palabras. `--idioma en` transcribe ganchos en inglés.

## Cómo funciona

TikTok expone tres páginas de *embed* que no piden sesión ni firma: la de un hashtag, la de una cuenta y la de un video. Las tres traen sus datos en el HTML. El script los lee, quita repetidos, calcula las tasas y ordena.

Para la edición, ffmpeg detecta los cambios de plano y mide el audio, y faster-whisper transcribe el arranque. Todo corre en tu máquina: ningún video ni dato sale a una API.

## Límites, dichos de frente

- **El conteo de cortes es un piso.** La detección por escena ve cuando cambia la imagen; el *jump cut* sobre el mismo encuadre, el recurso más usado en TikTok, no lo ve.
- **El hashtag devuelve el tope histórico**, no lo de esta semana. Para lo fresco, barre perfiles.
- **TikTok puede cambiar sus páginas de embed** sin avisar. Si un comando deja de traer videos, eso es lo primero que hay que revisar.
- TikTok limita por IP. El script espera medio segundo entre peticiones; no lo quites.

## Uso responsable

Lee solo datos públicos y no inicia sesión. Úsalo para estudiar qué funciona, no para republicar el trabajo de otros: el metraje que bajes es de quien lo hizo. Revisa los [términos de TikTok](https://www.tiktok.com/legal/terms-of-service) antes de usarlo a escala.

---

## English summary

A single-file Python tool to find what's working on TikTok in a niche and measure how those videos are edited. No API key, no login, no paid tool: it reads TikTok's public embed pages (hashtag, profile, video) and analyzes footage locally with ffmpeg and faster-whisper. Results are ranked by **shares per view**, not views, plus a **reach index** (views ÷ followers). The `analizar` command reports cuts, average shot length, cuts in the first 3 seconds, audio levels and, optionally, a transcript of the spoken hook. CLI and output are in Spanish.

---

Hecho por **Andrés Rey**, consultor en inteligencia artificial y marketing desde Bogotá. Lo uso con clientes para planear la grabación de sus videos antes de prender la cámara.
[andresvrey.com](https://andresvrey.com) · [LinkedIn](https://www.linkedin.com/in/andresvrey)

Licencia [MIT](LICENSE).
