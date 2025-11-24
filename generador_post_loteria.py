import os
import time
import json
from datetime import datetime
from typing import Dict, Optional, List, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont

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

# ========================
# EXCEPCIÓN
# ========================
class IGError(Exception):
    pass

# ========================
# IG DIRECT UPLOAD
# ========================
def ig_publish_image_file(local_path: str, caption: str, user_id: Optional[str] = None) -> str:
    uid = user_id or IG_USER_ID
    if not uid or not IG_TOKEN:
        raise IGError("Faltan IG_USER_ID o IG_TOKEN")

    url_media = f"{GRAPH}/{uid}/media"

    with open(local_path, "rb") as f:
        files = {"image_file": f}
        data = {
            "caption": caption,
            "access_token": IG_TOKEN,
        }
        r = requests.post(url_media, files=files, data=data, timeout=60)

    print("IG /media:", r.status_code, r.text)

    if r.status_code not in (200, 201):
        raise IGError(f"Error creando media: {r.status_code} {r.text}")

    creation_id = r.json().get("id")
    if not creation_id:
        raise IGError(f"Sin creation_id: {r.text}")

    url_publish = f"{GRAPH}/{uid}/media_publish"
    r2 = requests.post(url_publish, data={"creation_id": creation_id, "access_token": IG_TOKEN}, timeout=60)

    print("IG /media_publish:", r2.status_code, r2.text)

    if r2.status_code not in (200, 201):
        raise IGError(f"Error publicando media: {r2.status_code} {r2.text}")

    media_id = r2.json().get("id")
    if not media_id:
        raise IGError("No se recibió media_id en la publicación")

    info = requests.get(
        f"{GRAPH}/{media_id}",
        params={"fields": "permalink", "access_token": IG_TOKEN},
        timeout=30,
    ).json()

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

    latest: Dict[str, Tuple[list, Optional[str], Optional[str], Optional[str]]] = {}
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

        # Parse hora_scrapeo para determinar el más reciente
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

    # Convertir dict a lista ordenada por hora_scrapeo
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

    resultados = obtener_resultados_para_publicar(api_url)

    if not resultados:
        print("⚠️ No hay resultados para publicar (ninguna lotería de la lista).")
        exit()

    for nombre, numeros, hora, hora_scrapeo, fecha_str in resultados:
        # Si la fecha del JSON viene bien, la usamos. Si no, hoy.
        fecha_slug = fecha_str if fecha_str else datetime.now().strftime("%Y-%m-%d")
        fecha_humana = None
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

        generar_publicacion(
            nombre_loteria=nombre,
            numeros=numeros,
            hora=hora,
            plantilla_path=plantilla,
            salida_path=nombre_archivo
        )

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

        try:
            permalink = ig_publish_image_file(nombre_archivo, caption)
            print("📣 Publicado en IG:", permalink)

            with open(archivo_publicado, "w") as f:
                f.write("published")

            print(f"📝 Marcado como publicado: {archivo_publicado}")

        except Exception as e:
            print("❌ Error publicando en IG:", e)
