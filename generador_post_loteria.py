import os
import base64
import json
import time
from datetime import datetime
from typing import Dict, Optional

import requests
from PIL import Image, ImageDraw, ImageFont

# ========================
# CONFIG
# ========================
# Loterías a mostrar
LOTERIAS_A_PUBLICAR = [
    "Quiniela Leidsa", "Lotería Nacional", "Loteka", "La Primera", "New York Tarde", "New York Noche",
    "Mega Millones", "Pega 3", "Loto - Super Loto Más", "Super Kino TV", "Loto Pool",
    "Pega 3 Más", "Super Palé", "Quiniela Loteka", "La Suerte 12:30", "La Suerte 18:00",
    "Loteria Real Tarde", "Quiniela LoteDom", "La Primera Día", "La Primera Tarde", "La Primera Noche",
    "Loteka Noche", "Mega Chances", "El Quinielón Día", "El Quinielón Noche",
]

# IG
GRAPH = "https://graph.facebook.com/v24.0"
IG_USER_ID = os.getenv("IG_USER_ID")           # ej. 17841478403686973
IG_TOKEN   = os.getenv("IG_TOKEN")             # long-lived y con la Página marcada

# GitHub (para alojar la imagen y obtener una URL HTTPS pública)
GH_TOKEN   = os.getenv("GH_TOKEN")             # token personal con scope repo
GH_REPO    = os.getenv("GH_REPO", "omarghc/insta-assets")   # owner/repo
GH_BRANCH  = os.getenv("GH_BRANCH", "main")                 # rama
GH_BASE_RAW = f"https://raw.githubusercontent.com/{GH_REPO}/{GH_BRANCH}"

# ========================
# UTILIDADES IG
# ========================
class IGError(Exception):
    pass

def _g(method: str, path: str, params: Dict) -> Dict:
    url = f"{GRAPH}/{path.lstrip('/')}"
    params = {**params, "access_token": IG_TOKEN}
    for attempt in range(3):
        r = requests.request(
            method,
            url,
            params=params if method == "GET" else None,
            data=params if method == "POST" else None,
            timeout=30,
        )
        if r.status_code in (200, 201):
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(1.5 * (attempt + 1))
            continue
        raise IGError(f"IG {r.status_code}: {r.text}")
    raise IGError(f"IG falló tras reintentos: {r.status_code} | {r.text}")

def ig_publish_image(image_url: str, caption: str, user_id: Optional[str] = None) -> str:
    uid = user_id or IG_USER_ID
    if not uid or not IG_TOKEN:
        raise IGError("Faltan IG_USER_ID o IG_TOKEN")

    # 1) crear contenedor
    creation = _g("POST", f"{uid}/media", {"image_url": image_url, "caption": caption})
    creation_id = creation.get("id")
    if not creation_id:
        raise IGError(f"Sin creation_id: {creation}")

    # 2) esperar (normalmente es instant para imagen)
    for _ in range(6):
        st = _g("GET", creation_id, {"fields": "status_code"})
        if st.get("status_code") in (None, "FINISHED"):
            break
        time.sleep(1)

    # 3) publicar
    pub = _g("POST", f"{uid}/media_publish", {"creation_id": creation_id})
    media_id = pub.get("id")
    if not media_id:
        raise IGError(f"Sin media_id: {pub}")

    info = _g("GET", media_id, {"fields": "permalink"})
    return info.get("permalink", "")

# ========================
# UTILIDADES GITHUB (hosting de imagen)
# ========================
def github_put_file(local_path: str, dest_path: str) -> str:
    """
    Sube un archivo a GH (Contents API) y devuelve la RAW URL pública.
    """
    if not GH_TOKEN:
        raise RuntimeError("Falta GH_TOKEN")

    with open(local_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("utf-8")

    api = f"https://api.github.com/repos/{GH_REPO}/contents/{dest_path.lstrip('/')}"
    payload = {
        "message": f"post: {os.path.basename(dest_path)}",
        "content": content_b64,
        "branch": GH_BRANCH,
    }
    headers = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
    r = requests.put(api, headers=headers, data=json.dumps(payload), timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub {r.status_code}: {r.text}")

    # RAW URL (le agregamos un query para evitar caché)
    bust = int(time.time())
    return f"{GH_BASE_RAW}/{dest_path}?t={bust}"

# ========================
# TU LÓGICA DE IMAGEN
# ========================
def ajustar_fuente_responsive(texto, font_path, max_width, max_font_size):
    font_size = max_font_size
    while font_size > 10:
        font = ImageFont.truetype(font_path, font_size)
        bbox = font.getbbox(texto)
        text_width = bbox[2] - bbox[0]
        if text_width <= max_width:
            return font
        font_size -= 1
    return ImageFont.truetype(font_path, 10)

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
        hora = formatear_hora(resultado["hora"])
        if hora:
            return hora
    hora_scrapeo = resultado.get("hora_scrapeo")
    if hora_scrapeo:
        try:
            dt = datetime.strptime(hora_scrapeo, "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%I:%M").lstrip("0")
        except Exception as e:
            print(f"❌ Error al formatear hora_scrapeo: {hora_scrapeo} - {e}")
            return None
    return None

def generar_publicacion(nombre_loteria, numeros, hora, plantilla_path, salida_path):
    img = Image.open(plantilla_path).convert("RGBA")
    draw = ImageDraw.Draw(img)

    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
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

def obtener_resultados_de_hoy(api_url):
    hoy = datetime.now().strftime("%Y-%m-%d")
    response = requests.get(api_url, timeout=30)
    response.raise_for_status()
    data = response.json()
    resultados_utiles = []
    for resultado in data.get("resultados", []):
        nombre = resultado.get("loteria", "")
        fecha = resultado.get("fecha", "")
        if nombre in LOTERIAS_A_PUBLICAR and fecha == hoy:
            numeros = resultado.get("numeros", [])
            hora_legible = obtener_hora_legible(resultado)
            resultados_utiles.append((nombre, numeros, hora_legible))
    return resultados_utiles

# ========================
# MAIN
# ========================
if __name__ == "__main__":
    api_url = "https://omarghc.github.io/sync-phi72/resultados_combinados.json"
    script_dir = os.path.dirname(os.path.abspath(__file__))
    plantilla = "plantilla_bancard.png"

    resultados = obtener_resultados_de_hoy(api_url)

    if not resultados:
        print("⚠️ No hay resultados para hoy.")
    else:
        fecha_slug = datetime.now().strftime("%Y-%m-%d")
        for nombre, numeros, hora in resultados:
            # 1) generar imagen local
            nombre_archivo = f"post_{nombre.replace(' ', '_')}.png"
            generar_publicacion(
                nombre_loteria=nombre,
                numeros=numeros,
                hora=hora,
                plantilla_path=plantilla,
                salida_path=nombre_archivo
            )

            # 2) subir a GitHub (carpeta por día)
            gh_dest = f"posts/{fecha_slug}/{nombre_archivo}"
            try:
                public_url = github_put_file(nombre_archivo, gh_dest)
                print("🔗 URL pública:", public_url)
            except Exception as e:
                print("❌ Error subiendo a GitHub:", e)
                continue

            # 3) publicar en Instagram
            caption = (
                f"Resultados {nombre} — hoy {fecha_slug}\n"
                f"⏰ {hora if hora else 'hora no disponible'}\n"
                "Descarga BancaRD y recibe alertas en vivo.\n"
                "#BancaRD #ResultadosRD #LoteriasRD"
            )
            try:
                permalink = ig_publish_image(public_url, caption)
                print("📣 Publicado en IG:", permalink)
            except Exception as e:
                print("❌ Error publicando en IG:", e)


