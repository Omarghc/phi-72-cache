import os
import time
import json
from datetime import datetime
from typing import Dict, Optional, List, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont

# ========================
# VIDEO / REEL (moviepy)
# ========================
try:
    from moviepy.editor import ImageClip, concatenate_videoclips, AudioFileClip
except ImportError:
    ImageClip = None
    concatenate_videoclips = None
    AudioFileClip = None

# ========================
# CONFIG
# ========================
LOTERIAS_A_PUBLICAR = [
    "Quiniela Leidsa", "Lotería Nacional", "Loteka", "La Primera", "New York Tarde", "New York Noche",
    "Mega Millones", "Pega 3", "Loto - Super Loto Más", "Super Kino TV", "Loto Pool",
    "Pega 3 Más", "Super Palé", "Quiniela Loteka", "La Suerte 12:30", "La Suerte 18:00",
    "Loteria Real Tarde", "Quiniela LoteDom", "La Primera Día", "La Primera Tarde", "La Primera Noche",
    "Loteka Noche", "Mega Chances", "El Quinielón Día", "El Quinielón Noche",
]

GRAPH = "https://graph.facebook.com/v24.0"
IG_USER_ID = os.getenv("IG_USER_ID")
IG_TOKEN = os.getenv("IG_TOKEN")

# GitHub para alojar la imagen / video y obtener URL pública
GH_TOKEN = os.getenv("GH_TOKEN")  # token personal con scope repo
GH_REPO = os.getenv("GH_REPO", "omarghc/insta-assets")  # owner/repo
GH_BRANCH = os.getenv("GH_BRANCH", "main")
GH_BASE_RAW = f"https://raw.githubusercontent.com/{GH_REPO}/{GH_BRANCH}"

# Audio para el reel (debe existir en el repo / entorno donde corre el script)
AUDIO_REEL_PATH = os.getenv("AUDIO_REEL_PATH", "bancard_theme.mp3")
DURACION_IMAGEN_REEL = float(os.getenv("REEL_IMAGE_DURATION", "2.5"))  # segundos
FPS_REEL = int(os.getenv("REEL_FPS", "30"))

# ========================
# EXCEPCIÓN
# ========================
class IGError(Exception):
    pass

# ========================
# IG DEBUG ACCOUNT
# ========================
def ig_get_account_info(user_id: Optional[str] = None) -> Dict:
    uid = user_id or IG_USER_ID
    if not uid or not IG_TOKEN:
        raise IGError("Faltan IG_USER_ID o IG_TOKEN para debug de cuenta.")
    url = f"{GRAPH}/{uid}"
    params = {
        "fields": "id,username,profile_picture_url,name",
        "access_token": IG_TOKEN,
    }
    r = requests.get(url, params=params, timeout=30)
    print("IG /account_info:", r.status_code, r.text)
    if r.status_code not in (200, 201):
        raise IGError(f"Error obteniendo info de cuenta: {r.status_code} {r.text}")
    return r.json()

# ========================
# GITHUB: SUBIR ARCHIVO Y OBTENER URL PÚBLICA
# ========================
from base64 import b64encode as _  # evitar error si borras import por accidente
import base64  # import real

def github_put_file(local_path: str, dest_path: str) -> str:
    """
    Sube un archivo a GH (Contents API) y devuelve la RAW URL pública.
    Sirve tanto para PNG como para MP4.
    """
    if not GH_TOKEN:
        raise RuntimeError("Falta GH_TOKEN en variables de entorno.")

    if not os.path.exists(local_path):
        raise RuntimeError(f"No existe el archivo local a subir: {local_path}")

    with open(local_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("utf-8")

    api = f"https://api.github.com/repos/{GH_REPO}/contents/{dest_path.lstrip('/')}"
    payload = {
        "message": f"post: {os.path.basename(dest_path)}",
        "content": content_b64,
        "branch": GH_BRANCH,
    }
    headers = {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    r = requests.put(api, headers=headers, data=json.dumps(payload), timeout=60)
    print("GitHub PUT:", r.status_code, r.text)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub {r.status_code}: {r.text}")

    # RAW URL (con anti-caché)
    bust = int(time.time())
    return f"{GH_BASE_RAW}/{dest_path}?t={bust}"

# ========================
# IG: PUBLICAR POR image_url
# ========================
def ig_publish_image_url(image_url: str, caption: str, user_id: Optional[str] = None) -> str:
    uid = user_id or IG_USER_ID
    if not uid or not IG_TOKEN:
        raise IGError("Faltan IG_USER_ID o IG_TOKEN")

    # 1) crear contenedor
    url_media = f"{GRAPH}/{uid}/media"
    data = {
        "image_url": image_url,
        "caption": caption,
        "access_token": IG_TOKEN,
    }
    r = requests.post(url_media, data=data, timeout=60)
    print("IG /media (IMAGE):", r.status_code, r.text)

    if r.status_code not in (200, 201):
        raise IGError(f"Error creando media: {r.status_code} {r.text}")

    creation_id = r.json().get("id")
    if not creation_id:
        raise IGError(f"Sin creation_id: {r.text}")

    # 2) publicar el contenedor
    url_publish = f"{GRAPH}/{uid}/media_publish"
    r2 = requests.post(
        url_publish,
        data={"creation_id": creation_id, "access_token": IG_TOKEN},
        timeout=60,
    )
    print("IG /media_publish (IMAGE):", r2.status_code, r2.text)

    if r2.status_code not in (200, 201):
        raise IGError(f"Error publicando media: {r2.status_code} {r2.text}")

    media_id = r2.json().get("id")
    if not media_id:
        raise IGError("No se recibió media_id en la publicación")

    info = requests.get(
        f"{GRAPH}/{media_id}",
        params={"fields": "permalink,media_type,caption,timestamp", "access_token": IG_TOKEN},
        timeout=30,
    ).json()

    print("IG /media_info (IMAGE):", info)
    return info.get("permalink", "")

# ========================
# IG: PUBLICAR VIDEO (REEL) POR video_url
# ========================
def ig_publish_video_url(video_url: str, caption: str, user_id: Optional[str] = None) -> str:
    """
    Crea y publica un VIDEO usando video_url.
    Si el video es vertical y corto, IG lo trata como Reel.
    """
    uid = user_id or IG_USER_ID
    if not uid or not IG_TOKEN:
        raise IGError("Faltan IG_USER_ID o IG_TOKEN para publicar video.")

    url_media = f"{GRAPH}/{uid}/media"
    data = {
        "media_type": "VIDEO",
        "video_url": video_url,
        "caption": caption,
        "access_token": IG_TOKEN,
    }
    r = requests.post(url_media, data=data, timeout=300)
    print("IG /media (VIDEO):", r.status_code, r.text)

    if r.status_code not in (200, 201):
        raise IGError(f"Error creando media VIDEO: {r.status_code} {r.text}")

    creation_id = r.json().get("id")
    if not creation_id:
        raise IGError(f"Sin creation_id para VIDEO: {r.text}")

    # Darle un tiempito a IG para procesar el video
    print("⌛ Esperando 20s para procesar el video en IG…")
    time.sleep(20)

    url_publish = f"{GRAPH}/{uid}/media_publish"
    r2 = requests.post(
        url_publish,
        data={"creation_id": creation_id, "access_token": IG_TOKEN},
        timeout=120,
    )
    print("IG /media_publish (VIDEO):", r2.status_code, r2.text)

    if r2.status_code not in (200, 201):
        raise IGError(f"Error publicando VIDEO: {r2.status_code} {r2.text}")

    media_id = r2.json().get("id")
    if not media_id:
        raise IGError("No se recibió media_id en la publicación de VIDEO")

    info = requests.get(
        f"{GRAPH}/{media_id}",
        params={"fields": "permalink,media_type,caption,timestamp", "access_token": IG_TOKEN},
        timeout=60,
    ).json()

    print("IG /media_info (VIDEO):", info)
    return info.get("permalink", "")

# ========================
# IMAGEN
# ========================
def ajustar_fuente_responsive(texto, font_path, max_width, max_font_size):
    font_size = max_font_size
    while font_size > 10:
        try:
            font = ImageFont.truetype(font_path, font_size)
        except:
            return ImageFont.load_default()

        bbox = font.getbbox(texto)
        if (bbox[2] - bbox[0]) <= max_width:
            return font
        font_size -= 1

    return ImageFont.load_default()

def formatear_hora(hora_str):
    formatos = ["%I:%M %p", "%I:%M%p", "%H:%M"]
    for fmt in formatos:
        try:
            dt = datetime.strptime(hora_str, fmt)
            return dt.strftime("%I:%M").lstrip("0")
        except:
            continue
    return None

def obtener_hora_legible(resultado):
    if resultado.get("hora"):
        h = formatear_hora(resultado["hora"])
        if h:
            return h

    hora_scrapeo = resultado.get("hora_scrapeo")
    if hora_scrapeo:
        try:
            dt = datetime.strptime(hora_scrapeo, "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%I:%M").lstrip("0")
        except:
            return None
    return None

def generar_publicacion(nombre_loteria, numeros, hora, plantilla_path, salida_path):
    img = Image.open(plantilla_path).convert("RGBA")
    draw = ImageDraw.Draw(img)

    posibles_fuentes = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/ARIALBD.TTF",
    ]

    font_path = None
    for f in posibles_fuentes:
        if os.path.exists(f):
            font_path = f
            break

    if not font_path:
        print("⚠️ No se encontró fuente del sistema, usando default.")
        font_numeros = ImageFont.load_default()
        font_hora = ImageFont.load_default()
        font_loteria = ImageFont.load_default()
    else:
        font_numeros = ImageFont.truetype(font_path, 160)
        font_hora = ImageFont.truetype(font_path, 70)
        font_loteria = ajustar_fuente_responsive(nombre_loteria.upper(), font_path, 900, 90)

    bbox_loteria = draw.textbbox((0, 0), nombre_loteria.upper(), font=font_loteria)
    x_loteria = (1080 - (bbox_loteria[2] - bbox_loteria[0])) // 2
    draw.text((x_loteria, 670), nombre_loteria.upper(), font=font_loteria, fill=(255, 204, 0))

    visibles = numeros[:2]
    texto_numeros = "–".join(visibles + ["?"])
    bbox_numeros = draw.textbbox((0, 0), texto_numeros, font=font_numeros)
    x_numeros = (1080 - (bbox_numeros[2] - bbox_numeros[0])) // 2
    draw.text((x_numeros, 770), texto_numeros, font=font_numeros, fill=(255, 255, 255))

    if hora:
        bbox_hora = draw.textbbox((0, 0), hora, font=font_hora)
        x_hora = 1080 - (bbox_hora[2] - bbox_hora[0]) - 80
        draw.text((x_hora, 1030), hora, font=font_hora, fill=(255, 204, 0))

    img.save(salida_path)
    print(f"✅ Imagen guardada: {salida_path}")

# ========================
# VIDEO: CREAR REEL DESDE IMÁGENES
# ========================
def crear_reel_desde_imagenes(imagenes: List[str], salida_video: str, audio_path: Optional[str] = None) -> Optional[str]:
    """
    Recibe una lista de rutas de imágenes (PNG), crea un video 1080x1920 (reel)
    mostrándolas secuencialmente y opcionalmente le agrega audio.
    """
    if not imagenes:
        print("🎬 No hay imágenes para crear el reel.")
        return None

    if ImageClip is None or concatenate_videoclips is None:
        print("❌ moviepy no está instalado. No se generará el reel.")
        return None

    clips = []
    for img_path in imagenes:
        if not os.path.exists(img_path):
            print(f"⚠️ Imagen no encontrada para reel: {img_path}")
            continue

        print(f"🎬 Añadiendo al reel: {img_path}")
        clip = ImageClip(img_path)
        w, h = clip.size
        scale = min(1080 / w, 1920 / h)
        clip = clip.resize(scale)

        clip = clip.on_color(
            size=(1080, 1920),
            color=(0, 0, 0),
            col_opacity=1
        ).set_duration(DURACION_IMAGEN_REEL)

        clips.append(clip)

    if not clips:
        print("❌ Ninguna imagen válida para el reel.")
        return None

    video = concatenate_videoclips(clips, method="compose")

    # Audio
    if audio_path and os.path.exists(audio_path) and AudioFileClip is not None:
        print(f"🎵 Agregando audio al reel: {audio_path}")
        audio = AudioFileClip(audio_path)
        audio = audio.audio_loop(duration=video.duration)
        video = video.set_audio(audio)
        audio_enabled = True
    else:
        if audio_path:
            print(f"⚠️ Audio no encontrado o moviepy sin AudioFileClip: {audio_path}")
        audio_enabled = False

    print(f"💾 Renderizando reel en {salida_video} (audio={'sí' if audio_enabled else 'no'})...")
    video.write_videofile(
        salida_video,
        fps=FPS_REEL,
        codec="libx264",
        audio_codec="aac" if audio_enabled else None,
        bitrate="4000k",
        threads=4,
    )
    print(f"✅ Reel generado: {salida_video}")
    return salida_video

# ========================
# DATA: ÚLTIMO RESULTADO POR LOTERÍA
# ========================
def obtener_resultados_para_publicar(api_url) -> List[Tuple[str, list, Optional[str], Optional[str], Optional[str]]]:
    """
    Devuelve lista de tuplas:
    (nombre_loteria, numeros, hora_legible, hora_scrapeo, fecha_str)
    usando SIEMPRE el resultado más reciente por lotería,
    SIN filtrar por fecha (para asegurarnos que publique algo).
    """
    data = requests.get(api_url, timeout=30).json()

    latest: Dict[str, Tuple[list, Optional[str], Optional[str], Optional[str], datetime]] = {}
    total = 0
    total_match_nombre = 0

    for resultado in data.get("resultados", []):
        total += 1
        nombre = resultado.get("loteria", "")
        if nombre not in LOTERIAS_A_PUBLICAR:
            continue

        total_match_nombre += 1
        numeros = resultado.get("numeros", [])
        hora_legible = obtener_hora_legible(resultado)
        hora_scrapeo = resultado.get("hora_scrapeo")
        fecha = resultado.get("fecha")

        try:
            ts = datetime.strptime(hora_scrapeo, "%Y-%m-%d %H:%M:%S") if hora_scrapeo else datetime.min
        except:
            ts = datetime.min

        if nombre not in latest:
            latest[nombre] = (numeros, hora_legible, hora_scrapeo, fecha, ts)
        else:
            _, _, _, _, ts_prev = latest[nombre]
            if ts > ts_prev:
                latest[nombre] = (numeros, hora_legible, hora_scrapeo, fecha, ts)

    print(f"🔎 Total resultados en JSON: {total}")
    print(f"✅ Coinciden por nombre (LOTERIAS_A_PUBLICAR): {total_match_nombre}")
    print(f"🎯 Loterías con resultado seleccionado: {len(latest)}")

    items: List[Tuple[str, list, Optional[str], Optional[str], Optional[str]]] = []
    for nombre, (numeros, hora_legible, hora_scrapeo, fecha, ts) in latest.items():
        items.append((nombre, numeros, hora_legible, hora_scrapeo, fecha))

    def ordenar(e):
        _, _, _, h, _ = e
        if not h:
            return datetime.min
        try:
            return datetime.strptime(h, "%Y-%m-%d %H:%M:%S")
        except:
            return datetime.min

    return sorted(items, key=ordenar)

# ========================
# MAIN
# ========================
if __name__ == "__main__":
    api_url = "https://omarghc.github.io/sync-phi72/resultados_combinados.json"
    plantilla = "plantilla_bancard.png"

    # Debug: ver a qué cuenta estamos posteando
    try:
        info = ig_get_account_info()
        username = info.get("username", "?")
        name = info.get("name", "")
        print(f"📛 Publicando en la cuenta IG: @{username} ({name})")
    except Exception as e:
        print("⚠️ No se pudo obtener info de la cuenta IG:", e)

    resultados = obtener_resultados_para_publicar(api_url)

    if not resultados:
        print("⚠️ No hay resultados para publicar (ninguna lotería de la lista).")
        exit()

    # Guardaremos aquí las imágenes NUEVAS generadas en esta ejecución
    imagenes_para_reel: List[str] = []

    for nombre, numeros, hora, hora_scrapeo, fecha_str in resultados:
        fecha_slug = fecha_str if fecha_str else datetime.now().strftime("%Y-%m-%d")
        try:
            fecha_humana = datetime.strptime(fecha_slug, "%Y-%m-%d").strftime("%d/%m/%Y")
        except:
            fecha_humana = datetime.now().strftime("%d/%m/%Y")

        slug_nombre = (
            nombre.replace(" ", "_")
                  .replace(":", "")
                  .replace("/", "_")
                  .replace("__", "_")
        )

        nombre_archivo = f"post_{fecha_slug}_{slug_nombre}.png"
        archivo_publicado = nombre_archivo + ".published"

        print(f"🧩 Procesando: {nombre} | archivo: {nombre_archivo}")

        if os.path.exists(archivo_publicado):
            print(f"⏭ Ya publicado previamente: {nombre_archivo}")
            continue

        # 1) Generar imagen local
        generar_publicacion(
            nombre_loteria=nombre,
            numeros=numeros,
            hora=hora,
            plantilla_path=plantilla,
            salida_path=nombre_archivo
        )

        # La añadimos a la lista para el reel
        imagenes_para_reel.append(nombre_archivo)

        # 2) Subir a GitHub para obtener image_url
        gh_dest = f"posts/{fecha_slug}/{nombre_archivo}"
        try:
            public_url = github_put_file(nombre_archivo, gh_dest)
            print("🔗 URL pública:", public_url)
        except Exception as e:
            print("❌ Error subiendo a GitHub (imagen):", e)
            continue

        # 3) Caption
        if numeros:
            preview = " - ".join(numeros[:3]) if len(numeros) >= 3 else " - ".join(numeros)
        else:
            preview = "Pendiente"

        hora_texto = hora or "hora no disponible"

        caption = (
            f"🎯 {nombre} — resultado de la fecha {fecha_humana}\n"
            f"🧮 Números: {preview} … (completo en la app)\n"
            f"⏰ Sorteo de las {hora_texto}\n\n"
            "📲 Descarga la app BancaRD y:\n"
            "• Recibe alertas en tiempo real\n"
            "• Ve TODOS los resultados del día\n"
            "• Convierte tus sueños en números de la suerte\n\n"
            "#BancaRD #ResultadosRD #LoteriasRD #Quinielas #Leidsa #Loteka #LoteriaNacional"
        )

        # 4) Publicar en IG con image_url
        try:
            permalink = ig_publish_image_url(public_url, caption)
            print("📣 Publicado en IG:", permalink)

            with open(archivo_publicado, "w") as f:
                f.write("published")

            print(f"📝 Marcado como publicado: {archivo_publicado}")

        except Exception as e:
            print("❌ Error publicando en IG (imagen):", e)

    # ========================
    # REEL AL FINAL DEL RUN
    # ========================
    if imagenes_para_reel:
        print(f"🎬 Generando reel con {len(imagenes_para_reel)} imágenes nuevas…")
        hoy_slug = datetime.now().strftime("%Y-%m-%d")
        nombre_reel_local = f"reel_{hoy_slug}.mp4"

        try:
            reel_path = crear_reel_desde_imagenes(
                imagenes_para_reel,
                salida_video=nombre_reel_local,
                audio_path=AUDIO_REEL_PATH,
            )
        except Exception as e:
            print("❌ Error generando el reel:", e)
            reel_path = None

        if reel_path:
            gh_dest_reel = f"reels/{hoy_slug}/{nombre_reel_local}"
            try:
                public_video_url = github_put_file(reel_path, gh_dest_reel)
                print("🔗 URL pública del reel:", public_video_url)

                reel_caption = (
                    "📊 Resumen visual de los resultados del día con BancaRD 🎰📲\n\n"
                    "Descarga la app y no te pierdas ningún número:\n"
                    "• Resultados actualizados\n"
                    "• Todas las loterías en un solo lugar\n"
                    "• Herramientas para tus jugadas\n\n"
                    "#BancaRD #ResultadosRD #ReelLoterias #LoteriasRD"
                )

                try:
                    permalink_reel = ig_publish_video_url(public_video_url, reel_caption)
                    print("📣 Reel publicado en IG:", permalink_reel)
                except Exception as e:
                    print("❌ Error publicando el reel en IG:", e)

            except Exception as e:
                print("❌ Error subiendo el reel a GitHub:", e)
    else:
        print("ℹ️ No se generaron imágenes nuevas hoy, no se creará reel.")
