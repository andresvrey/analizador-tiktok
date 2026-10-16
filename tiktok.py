# -*- coding: utf-8 -*-
"""Radar de TikTok: descubre videos, mide su rendimiento y baja el metraje.

Sin clave de API, sin cuenta, sin MCP de terceros. Se apoya en tres superficies
públicas de TikTok que **no piden firma ni sesión** (verificado el 24-sep-2026):

  * `tiktok.com/embed/tag/<hashtag>`  -> 8-10 videos tope del hashtag
  * `tiktok.com/embed/@<usuario>`     -> ~13 videos recientes de la cuenta
  * `tiktok.com/embed/v2/<id>`        -> métricas finas de un video

Las tres devuelven el estado SSR en `<script id="__FRONTITY_CONNECT_STATE__">`.
No hay `SIGI_STATE` en el embed: buscarlo es perder la tarde.

**yt-dlp quedó de respaldo, no de motor.** Sus números coinciden exactamente con
los del embed (comprobado video a video), así que solo se lo llama para tapar
huecos: la duración cuando el embed la devuelve en cero, y el MP4 cuando el
embed no expone `playAddr`.

**Lo que cada superficie sirve.** El hashtag devuelve el tope histórico del
tag, no lo de esta semana: es el catálogo de lo que ya funcionó. El perfil sí
devuelve lo reciente, incluso publicaciones de horas: es la señal fresca. Para
lo que está subiendo *ahora mismo* hace falta el Creative Center de TikTok, que
sin login solo muestra tres hashtags por país y va por navegador.

Las dos métricas que más dicen no son las vistas ni los likes:

  * **compartidos sobre vistas** — un video que la gente reenvía es un video
    que el algoritmo empuja.
  * **vistas sobre seguidores** (`indice_alcance`) — cuánto se salió el video
    de la audiencia propia de la cuenta. Un 50x es un video que se rompió;
    un 2x es una cuenta grande publicando lo de siempre.

Uso:
    python tiktok.py tag marketingdigital emprendimiento --enriquecer
    python tiktok.py perfil @cuenta1 @cuenta2 --enriquecer
    python tiktok.py ficha https://www.tiktok.com/@cuenta/video/<id>
    python tiktok.py bajar <url> --salida metraje
    python tiktok.py analizar <url-o-mp4> --gancho

    # guardar el barrido como JSON + tabla markdown
    python tiktok.py tag marketingdigital --enriquecer --salida radar

Autor: Andrés Rey (https://andresvrey.com). Licencia MIT.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

UA =("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# TikTok limita por IP sin avisar. Medio segundo entre peticiones alcanza.
PAUSA = 0.5


def _salida_utf8():
    """Windows imprime en cp1252 y los emoji de los captions lo revientan."""
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def ytdlp():
    """Devuelve cómo invocar yt-dlp, o None si no está instalado.

    En Windows pip suele dejar el .exe fuera del PATH; por eso el segundo
    intento es el módulo, que funciona si se instaló con el mismo Python.
    """
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    cmd = [sys.executable, "-m", "yt_dlp"]
    try:
        subprocess.run(cmd + ["--version"], capture_output=True,
                       timeout=25, check=True)
        return cmd
    except Exception:
        return None


# --- lectura de las superficies públicas -------------------------------------

# TikTok responde 503 «overload-protect» a ratos, sobre todo en los hashtags
# grandes. No es un bloqueo: a los pocos segundos la misma URL contesta.
ESPERAS_REINTENTO = (3, 8, 20)


def _pedir(url):
    pet = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
    })
    for espera in ESPERAS_REINTENTO + (None,):
        try:
            with urllib.request.urlopen(pet, timeout=35) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or espera is None:
                raise
            print(f"  TikTok respondió {e.code}; reintento en {espera}s",
                  file=sys.stderr)
            time.sleep(espera)


def _estado_ssr(url):
    """El JSON que TikTok deja en la página de embed. None si no está."""
    m = re.search(r'<script id="__FRONTITY_CONNECT_STATE__"[^>]*>(.*?)</script>',
                  _pedir(url), re.S)
    return json.loads(m.group(1)) if m else None


def _items_embed(url):
    """Saca los videos del estado SSR de un embed de tag o de perfil.

    Los videos son los diccionarios que traen a la vez `desc` y `playCount`.
    """
    datos = _estado_ssr(url)
    if datos is None:
        return []
    hallados = []

    def recorrer(o):
        if isinstance(o, dict):
            if "desc" in o and "playCount" in o:
                hallados.append(o)
            for v in o.values():
                recorrer(v)
        elif isinstance(o, list):
            for v in o:
                recorrer(v)

    recorrer(datos)
    vistos, unicos = set(), []
    for it in hallados:
        if it.get("id") and it["id"] not in vistos:
            vistos.add(it["id"])
            unicos.append(it)
    return unicos


def _normalizar(it, origen):
    autor = it.get("authorUniqueId") or ""
    return {
        "id": it.get("id"),
        "autor": autor,
        "url": f"https://www.tiktok.com/@{autor}/video/{it.get('id')}",
        "caption": (it.get("desc") or "").strip(),
        "vistas": _ent(it.get("playCount")),
        "vertical": (_ent(it.get("height")) or 0) > (_ent(it.get("width")) or 0),
        "portada": it.get("coverUrl"),
        "origen": origen,
    }


def descubrir_tag(tag):
    tag = tag.lstrip("#")
    return [_normalizar(it, f"#{tag}")
            for it in _items_embed(f"https://www.tiktok.com/embed/tag/{tag}")]


def descubrir_perfil(usuario):
    usuario = usuario.lstrip("@")
    return [_normalizar(it, f"@{usuario}")
            for it in _items_embed(f"https://www.tiktok.com/embed/@{usuario}")]


# --- ficha de un video -------------------------------------------------------

def _id_de(url):
    m = re.search(r"/video/(\d+)", url) or re.fullmatch(r"\s*(\d{10,})\s*", url)
    return m.group(1) if m else None


def _ent(v):
    """TikTok mezcla enteros y cadenas en el mismo campo según el video."""
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _tasas(d):
    """Añade las tres tasas sobre vistas y el índice de alcance."""
    vistas = d.get("vistas") or 0
    tasa = lambda n: round(100.0 * (n or 0) / vistas, 3) if vistas else None
    # Compartidos sobre vistas es el mejor proxy de empuje algorítmico.
    d["tasa_compartido_pct"] = tasa(d.get("compartidos"))
    d["tasa_like_pct"] = tasa(d.get("likes"))
    d["tasa_comentario_pct"] = tasa(d.get("comentarios"))
    seg = d.get("seguidores") or 0
    d["indice_alcance"] = round(vistas / seg, 1) if seg and vistas else None
    return d


def datos_embed(url):
    """El nodo `videoData` del embed/v2. None si el video no está o cambió."""
    vid = _id_de(url)
    if not vid:
        return None
    try:
        datos = _estado_ssr(f"https://www.tiktok.com/embed/v2/{vid}")
    except Exception:
        return None
    if datos is None:
        return None

    def buscar(o):
        if isinstance(o, dict):
            if "videoData" in o:
                return o["videoData"]
            for v in o.values():
                g = buscar(v)
                if g:
                    return g
        elif isinstance(o, list):
            for v in o:
                g = buscar(v)
                if g:
                    return g

    return buscar(datos)


def _ficha_embed(url):
    vd = datos_embed(url)
    if not vd or not vd.get("itemInfos"):
        return None
    it = vd["itemInfos"]
    aut = vd.get("authorInfos") or {}
    mus = vd.get("musicInfos") or {}
    est = vd.get("authorStats") or {}
    meta = (it.get("video") or {}).get("videoMeta") or {}
    ct = _ent(it.get("createTime"))
    autor = aut.get("uniqueId") or ""
    d = {
        "id": str(it.get("id") or ""),
        "autor": autor,
        "url": f"https://www.tiktok.com/@{autor}/video/{it.get('id')}",
        "caption": (it.get("text") or "").strip(),
        "vistas": _ent(it.get("playCount")) or 0,
        "likes": _ent(it.get("diggCount")) or 0,
        "comentarios": _ent(it.get("commentCount")) or 0,
        "compartidos": _ent(it.get("shareCount")) or 0,
        "duracion_s": _ent(meta.get("duration")) or None,
        # La fecha se lee en UTC a propósito: así coincide con `upload_date`
        # de yt-dlp y dos corridas del mismo día no se contradicen.
        "publicado": time.strftime("%Y-%m-%d", time.gmtime(ct)) if ct else None,
        "audio": mus.get("musicName"),
        "artista": mus.get("authorName"),
        "audio_original": bool(mus.get("original")),
        "seguidores": _ent(est.get("followerCount")) or None,
        "vertical": (_ent(meta.get("height")) or 0) > (_ent(meta.get("width")) or 0),
        "fuente": "embed",
    }
    return _tasas(d)


def _ficha_ytdlp(url, exe=None):
    exe = exe or ytdlp()
    if not exe:
        return None
    try:
        p = subprocess.run(exe + ["--dump-json", "--no-warnings", url],
                           capture_output=True, timeout=120)
        if p.returncode != 0:
            return None
        d = json.loads(p.stdout.decode("utf-8", "replace"))
    except Exception:
        return None
    fecha = d.get("upload_date") or ""
    return _tasas({
        "id": str(d.get("id") or ""),
        "autor": d.get("uploader") or "",
        "url": d.get("webpage_url") or url,
        "caption": (d.get("title") or "").strip(),
        "vistas": d.get("view_count") or 0,
        "likes": d.get("like_count") or 0,
        "comentarios": d.get("comment_count") or 0,
        "compartidos": d.get("repost_count") or 0,
        "duracion_s": d.get("duration"),
        "publicado": f"{fecha[:4]}-{fecha[4:6]}-{fecha[6:]}" if len(fecha) == 8 else None,
        "audio": d.get("track"),
        "artista": d.get("artist"),
        "vertical": (d.get("height") or 0) > (d.get("width") or 0),
        "fuente": "yt-dlp",
    })


def ficha(url, exe=None, sin_ytdlp=False):
    """Métricas de un video. Embed primero; yt-dlp solo tapa huecos."""
    f = _ficha_embed(url)
    if f and f.get("vistas") and f.get("duracion_s"):
        return f
    if sin_ytdlp:
        return f
    g = _ficha_ytdlp(url, exe)
    if not f:
        return g
    if g:
        tapados = [k for k, v in g.items()
                   if v not in (None, "", 0) and f.get(k) in (None, "", 0)]
        f.update({k: g[k] for k in tapados})
        if tapados:
            f["fuente"] = "embed+yt-dlp"
            _tasas(f)
    return f


# --- metraje -----------------------------------------------------------------

def _playaddr(vd):
    urls = ((vd.get("itemInfos") or {}).get("video") or {}).get("urls") or []
    return urls[0] if urls else None


def _bajar_cdn(playaddr, destino):
    """El CDN de TikTok responde 403 sin `Range`, y 206 con él."""
    pet = urllib.request.Request(playaddr, headers={
        "User-Agent": UA,
        "Referer": "https://www.tiktok.com/",
        "Range": "bytes=0-",
    })
    with urllib.request.urlopen(pet, timeout=180) as r, open(destino, "wb") as f:
        shutil.copyfileobj(r, f)
    with open(destino, "rb") as f:
        cabeza = f.read(16)
    if b"ftyp" not in cabeza:
        os.remove(destino)
        raise ValueError("lo que bajó no es un MP4")
    return os.path.getsize(destino)


def _tiene_video(ruta):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=codec_name",
                        "-of", "csv=p=0", ruta], capture_output=True, timeout=60)
    return bool(p.stdout.strip())


def bajar(url, carpeta, exe=None):
    """Baja el MP4 y devuelve su ruta, o None. El CDN primero; yt-dlp de respaldo.

    Importa el orden: yt-dlp a secas devuelve solo el `.m4a` de muchos de
    estos videos, y un audio no sirve para estudiar una edición. Si lo único
    que baja es audio, se borra y se avisa: mejor sin archivo que con un
    archivo que finge ser un video.
    """
    os.makedirs(carpeta, exist_ok=True)
    # Una sola petición al embed: pedirlo dos veces dobla el riesgo de 503.
    vd = datos_embed(url)
    autor = ((vd or {}).get("authorInfos") or {}).get("uniqueId")
    base = f"{autor or 'tiktok'}-{_id_de(url) or 'video'}"
    play = _playaddr(vd) if vd else None
    if not vd:
        print("TikTok no devolvió el embed (o está limitando esta IP). "
              "Va por yt-dlp.", file=sys.stderr)
    elif play:
        destino = os.path.join(carpeta, base + ".mp4")
        try:
            tam = _bajar_cdn(play, destino)
            print(f"{destino}  ({tam / 1e6:.1f} MB)")
            return destino
        except Exception as e:
            print(f"El CDN falló ({type(e).__name__}: {e}). Va por yt-dlp.",
                  file=sys.stderr)
    else:
        print("El embed no expone playAddr para este video. Va por yt-dlp.",
              file=sys.stderr)

    exe = exe or ytdlp()
    if not exe:
        print("yt-dlp no está instalado (pip install yt-dlp).", file=sys.stderr)
        return None
    antes = set(os.listdir(carpeta))
    plantilla = os.path.join(carpeta, base + ".%(ext)s")
    subprocess.run(exe + ["-o", plantilla, "--no-warnings",
                          "-f", "bv*+ba/b[vcodec!=none]/b", url])
    nuevos = [os.path.join(carpeta, n) for n in os.listdir(carpeta)
              if n not in antes and n.startswith(base)]
    if not nuevos:
        print(f"No bajó nada de {url}.", file=sys.stderr)
        return None
    ruta = max(nuevos, key=os.path.getsize)
    if not _tiene_video(ruta):
        for n in nuevos:
            os.remove(n)
        print(f"Solo hay audio para {url}: TikTok no sirve el video por esta "
              f"vía. No se puede estudiar la edición de este.", file=sys.stderr)
        return None
    print(f"{ruta}  ({os.path.getsize(ruta) / 1e6:.1f} MB)")
    return ruta


# --- análisis de edición ------------------------------------------------------
# Todo local con ffmpeg y faster-whisper. Ninguna API.

def _ffprobe(ruta):
    p = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                        "-show_format", "-show_streams", ruta],
                       capture_output=True, timeout=120)
    return json.loads(p.stdout.decode("utf-8", "replace"))


def _cortes(ruta, umbral=0.3):
    """Segundos en los que cambia el plano.

    Detección por escena: cuenta el corte cuando cambia la imagen. Un jump cut
    sobre el mismo encuadre —el recurso más usado en TikTok— no lo ve, así que
    el número es un piso, no el total.
    """
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-i", ruta,
         "-vf", f"select='gt(scene,{umbral})',metadata=print:file=-",
         "-f", "null", "-"],
        capture_output=True, timeout=600)
    salida = (p.stdout + p.stderr).decode("utf-8", "replace")
    return [round(float(m), 2)
            for m in re.findall(r"pts_time:([\d.]+)", salida)]


def _audio(ruta):
    """Nivel medio, pico y cuánto del video tiene alguien hablando."""
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "info", "-i", ruta,
         "-af", "volumedetect,silencedetect=noise=-30dB:d=0.35",
         "-f", "null", "-"],
        capture_output=True, timeout=600)
    txt = (p.stdout + p.stderr).decode("utf-8", "replace")

    def uno(patron):
        m = re.search(patron, txt)
        return float(m.group(1)) if m else None

    silencio = sum(float(x) for x in re.findall(r"silence_duration: ([\d.]+)", txt))
    return {
        "volumen_medio_db": uno(r"mean_volume: (-?[\d.]+) dB"),
        "volumen_pico_db": uno(r"max_volume: (-?[\d.]+) dB"),
        "silencio_s": round(silencio, 1),
    }


def _gancho(ruta, segundos=15, idioma="es", modelo_whisper="large-v3"):
    """Transcribe los primeros segundos: el gancho hablado.

    Va por CPU para que corra en cualquier portátil, y el VAD va apagado
    porque se come el habla baja. `large-v3` por defecto porque `medium`
    pierde palabras; `small` sirve para una pasada rápida.
    """
    try:
        os.environ.setdefault("CT2_USE_MKL", "0")
        from faster_whisper import WhisperModel
    except Exception as e:
        return f"(no se pudo cargar faster-whisper: {e})"
    recorte = os.path.join(os.path.dirname(os.path.abspath(ruta)),
                           "_gancho_temp.wav")
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                    "-i", ruta, "-t", str(segundos), "-vn",
                    "-ac", "1", "-ar", "16000", recorte],
                   capture_output=True, timeout=300)
    try:
        # Se le pasa el audio ya decodificado: ffmpeg dejó un WAV mono a
        # 16 kHz, y así faster-whisper no pasa por PyAV, cuyas versiones
        # nuevas rompen su decodificador.
        import wave
        import numpy as np
        with wave.open(recorte, "rb") as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        muestras = pcm.astype(np.float32) / 32768.0
        modelo = WhisperModel(modelo_whisper, device="cpu", compute_type="int8")
        trozos, _ = modelo.transcribe(muestras, language=idioma, vad_filter=False)
        return " ".join(t.text.strip() for t in trozos).strip()
    except Exception as e:
        return f"(la transcripción falló: {e})"
    finally:
        if os.path.isfile(recorte):
            os.remove(recorte)


def analizar(origen, carpeta=None, umbral=0.3, transcribir=False,
             idioma="es", modelo_whisper="large-v3"):
    """Mide cómo está editado un video. Acepta URL de TikTok o un MP4 local."""
    if origen.startswith("http"):
        ruta = bajar(origen, carpeta or "metraje")
        if not ruta:
            return None
    else:
        ruta = origen
    if not os.path.isfile(ruta):
        print(f"No existe: {ruta}", file=sys.stderr)
        return None
    if not _tiene_video(ruta):
        print(f"{os.path.basename(ruta)} no tiene pista de video: no hay "
              f"edición que medir.", file=sys.stderr)
        return None

    pr = _ffprobe(ruta)
    vid = next((s for s in pr.get("streams", [])
                if s.get("codec_type") == "video"), {})
    dur = float(pr.get("format", {}).get("duration") or 0)
    try:
        num, den = vid.get("r_frame_rate", "0/1").split("/")
        fps = round(float(num) / float(den), 2) if float(den) else None
    except Exception:
        fps = None

    cortes = _cortes(ruta, umbral)
    d = {
        "archivo": os.path.basename(ruta),
        "duracion_s": round(dur, 1),
        "resolucion": f"{vid.get('width')}x{vid.get('height')}",
        "vertical": (vid.get("height") or 0) > (vid.get("width") or 0),
        "fps": fps,
        "cortes": len(cortes),
        "cortes_por_min": round(len(cortes) / (dur / 60), 1) if dur else None,
        "plano_medio_s": round(dur / (len(cortes) + 1), 1) if dur else None,
        "primer_corte_s": cortes[0] if cortes else None,
        "cortes_en_3s": sum(1 for c in cortes if c <= 3),
        "tiempos_de_corte": cortes,
    }
    d.update(_audio(ruta))
    if d["duracion_s"]:
        # Ojo con el nombre: mide audio por encima de -30 dB, no voz. Una
        # pista de música nunca baja de ahí y marca 100 % sin que hable nadie.
        sonando = max(0.0, d["duracion_s"] - (d.get("silencio_s") or 0))
        d["audio_sin_pausas_pct"] = round(100.0 * sonando / d["duracion_s"], 1)
    if transcribir:
        d["gancho_hablado"] = _gancho(ruta, idioma=idioma,
                                      modelo_whisper=modelo_whisper)
    return d


def imprimir_analisis(d):
    print(f"\n{d['archivo']}")
    print(f"  {d['duracion_s']}s · {d['resolucion']} · {d['fps']} fps"
          f"{' · vertical' if d['vertical'] else ' · HORIZONTAL'}")
    print(f"  {d['cortes']} cortes · {d['cortes_por_min']}/min · "
          f"plano medio {d['plano_medio_s']}s")
    if d["primer_corte_s"] is None:
        print("  sin un solo corte: plano único de principio a fin")
    else:
        print(f"  primer corte a los {d['primer_corte_s']}s · "
              f"{d['cortes_en_3s']} cortes en los primeros 3s")
    print(f"  audio sin pausas {d.get('audio_sin_pausas_pct')}% · "
          f"nivel medio {d.get('volumen_medio_db')} dB · "
          f"pico {d.get('volumen_pico_db')} dB")
    if d.get("gancho_hablado"):
        print(f"  gancho: {d['gancho_hablado'][:300]}")


# --- salida -----------------------------------------------------------------

def _mil(n):
    return f"{n:,}".replace(",", ".") if isinstance(n, int) else "—"


def imprimir(videos):
    for v in videos:
        cab = f"@{v['autor']} · {_mil(v.get('vistas'))} vistas"
        if v.get("tasa_compartido_pct") is not None:
            cab += (f" · {_mil(v.get('compartidos'))} comp "
                    f"({v['tasa_compartido_pct']}%)")
        if v.get("indice_alcance"):
            cab += f" · {v['indice_alcance']}x seguidores"
        if v.get("duracion_s"):
            cab += f" · {v['duracion_s']}s"
        print(f"\n{cab}")
        if v.get("publicado"):
            print(f"  publicado {v['publicado']}  audio: {v.get('audio') or '—'}"
                  f"{' (original)' if v.get('audio_original') else ''}")
        print(f"  {v['caption'][:150]}")
        print(f"  {v['url']}")


def tabla_markdown(videos, titulo):
    enriquecido = any(v.get("compartidos") is not None for v in videos)
    lin = [f"# {titulo}", "",
           f"Barrido del {time.strftime('%d-%m-%Y')} con `tiktok.py`, "
           "leído de los embeds públicos de TikTok: sin clave, sin sesión.", ""]
    if enriquecido:
        lin += ["| Autor | Vistas | Comp. | % comp. | Índice | Dur. | Audio | Gancho (caption) | URL |",
                "| :-- | --: | --: | --: | --: | --: | :-- | :-- | :-- |"]
        for v in videos:
            cap = v["caption"].replace("|", "/").replace("\n", " ")[:90]
            ind = f"{v['indice_alcance']}x" if v.get("indice_alcance") else "—"
            audio = (v.get("audio") or "—")[:24]
            if v.get("audio_original"):
                audio += " (orig.)"
            lin.append(
                f"| @{v['autor']} | {_mil(v.get('vistas'))} | {_mil(v.get('compartidos'))} "
                f"| {v.get('tasa_compartido_pct') or '—'} | {ind} "
                f"| {v.get('duracion_s') or '—'}s | {audio} "
                f"| {cap} | {v['url']} |")
    else:
        lin += ["| Autor | Vistas | Gancho (caption) | URL |", "| :-- | --: | :-- | :-- |"]
        for v in videos:
            cap = v["caption"].replace("|", "/").replace("\n", " ")[:110]
            lin.append(f"| @{v['autor']} | {_mil(v.get('vistas'))} | {cap} | {v['url']} |")
    lin += ["", "**Cómo se lee.** Ordenado por compartidos sobre vistas, no por vistas. "
            "Un video con 300.000 vistas y 2 % de compartidos enseña más que uno de "
            "30 millones con 0,1 %. El **índice** es vistas sobre seguidores de la "
            "cuenta: mide cuánto se salió el video de su propia audiencia. De aquí se "
            "saca la **estructura** del gancho, nunca el texto: copiar el texto es lo "
            "que hace que un feed se sienta clonado.", ""]
    return "\n".join(lin)


def guardar(videos, carpeta, nombre, titulo):
    os.makedirs(carpeta, exist_ok=True)
    js = os.path.join(carpeta, f"{nombre}.json")
    md = os.path.join(carpeta, f"{nombre}.md")
    with open(js, "w", encoding="utf-8") as f:
        json.dump(videos, f, ensure_ascii=False, indent=2)
    with open(md, "w", encoding="utf-8") as f:
        f.write(tabla_markdown(videos, titulo))
    print(f"\nGuardado:\n  {js}\n  {md}")


def _enriquecer(videos, exe, sin_ytdlp=False):
    total = len(videos)
    for i, v in enumerate(videos, 1):
        print(f"  [{i}/{total}] {v['url']}", file=sys.stderr)
        f = ficha(v["url"], exe, sin_ytdlp=sin_ytdlp)
        if f:
            v.update({k: val for k, val in f.items() if val is not None})
        time.sleep(PAUSA)
    return videos


def _ordenar(videos):
    return sorted(videos, key=lambda v: (v.get("tasa_compartido_pct") or -1,
                                         v.get("vistas") or 0), reverse=True)


def main():
    _salida_utf8()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    for nombre, ayuda in [("tag", "hashtags a barrer, sin #"),
                          ("perfil", "cuentas a barrer, con o sin @")]:
        p = sub.add_parser(nombre)
        p.add_argument("objetivos", nargs="+", help=ayuda)
        p.add_argument("--enriquecer", action="store_true",
                       help="pide likes, comentarios, compartidos, audio y fecha video a video")
        p.add_argument("--sin-ytdlp", action="store_true",
                       help="solo embed, sin el respaldo de yt-dlp (más rápido)")
        p.add_argument("--salida", help="carpeta donde dejar el JSON y la tabla")
        p.add_argument("--nombre", help="nombre base de los archivos de salida")

    p = sub.add_parser("ficha")
    p.add_argument("url")
    p.add_argument("--sin-ytdlp", action="store_true")

    p = sub.add_parser("bajar")
    p.add_argument("url")
    p.add_argument("--salida", required=True)

    p = sub.add_parser("analizar")
    p.add_argument("origen", nargs="+", help="URL de TikTok o ruta a un MP4")
    p.add_argument("--salida", help="carpeta donde bajar el metraje y dejar el JSON")
    p.add_argument("--umbral", type=float, default=0.3,
                   help="sensibilidad del detector de cortes (0.2 más, 0.4 menos)")
    p.add_argument("--gancho", action="store_true",
                   help="transcribe los primeros 15 s con faster-whisper (lento, CPU)")
    p.add_argument("--idioma", default="es",
                   help="idioma del gancho hablado (es, en, pt...)")
    p.add_argument("--modelo", default="large-v3",
                   help="modelo de faster-whisper: large-v3 (fiel) o small (rápido)")

    a = ap.parse_args()

    if a.cmd == "analizar":
        fichas = []
        for origen in a.origen:
            d = analizar(origen, a.salida, a.umbral, a.gancho,
                         a.idioma, a.modelo)
            if d:
                imprimir_analisis(d)
                fichas.append(d)
        if not fichas:
            return 1
        if a.salida:
            ruta = os.path.join(a.salida, "edicion.json")
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump(fichas, f, ensure_ascii=False, indent=2)
            print(f"\n{ruta}")
        return 0

    if a.cmd == "ficha":
        f = ficha(a.url, sin_ytdlp=a.sin_ytdlp)
        if not f:
            print("No se pudo leer el video.", file=sys.stderr)
            return 1
        print(json.dumps(f, ensure_ascii=False, indent=2))
        return 0

    if a.cmd == "bajar":
        return 0 if bajar(a.url, a.salida) else 1

    descubrir = descubrir_tag if a.cmd == "tag" else descubrir_perfil
    videos = []
    for obj in a.objetivos:
        try:
            hallados = descubrir(obj)
        except urllib.error.HTTPError as e:
            print(f"{obj}: HTTP {e.code} — TikTok cortó la petición.", file=sys.stderr)
            continue
        except Exception as e:
            print(f"{obj}: {type(e).__name__} {e}", file=sys.stderr)
            continue
        print(f"{obj}: {len(hallados)} videos", file=sys.stderr)
        videos += hallados
        time.sleep(PAUSA)

    # Un mismo video aparece en varios hashtags. Se queda una sola vez, con
    # todos los orígenes anotados, o la tabla lo cuenta dos veces.
    unicos = {}
    for v in videos:
        ya = unicos.get(v["id"])
        if ya:
            if v["origen"] not in ya["origen"]:
                ya["origen"] += f", {v['origen']}"
        else:
            unicos[v["id"]] = v
    if len(unicos) < len(videos):
        print(f"({len(videos) - len(unicos)} repetidos descartados)", file=sys.stderr)
    videos = list(unicos.values())

    if not videos:
        print("Nada encontrado.", file=sys.stderr)
        return 1

    if a.enriquecer:
        _enriquecer(videos, None, sin_ytdlp=a.sin_ytdlp)

    videos = _ordenar(videos)
    imprimir(videos)

    if a.salida:
        etiqueta = "hashtags" if a.cmd == "tag" else "cuentas"
        nombre = a.nombre or f"{time.strftime('%Y-%m-%d')}-tiktok-{etiqueta}"
        titulo = f"Radar TikTok — {', '.join(a.objetivos)}"
        guardar(videos, a.salida, nombre, titulo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
