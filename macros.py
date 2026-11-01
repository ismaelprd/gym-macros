import asyncio
import base64
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
import cv2
import numpy as np
import flet as ft
import flet_camera as fc
import requests
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo

    APP_TZ = ZoneInfo(os.getenv("APP_TZ", "Europe/Madrid"))
except Exception:  # sin base de zonas horarias: se usa la hora local del equipo
    APP_TZ = None

DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
DIA_INICIAL = ["L", "M", "X", "J", "V", "S", "D"]


def now_local():
    """Hora actual en la zona del usuario (por defecto Europe/Madrid; cámbiala con APP_TZ)."""
    return datetime.now(APP_TZ) if APP_TZ else datetime.now()


def today_str():
    return now_local().strftime("%Y-%m-%d")


def date_es_long(d):
    return f"{DIAS_ES[d.weekday()].capitalize()}, {d.day} de {MESES_ES[d.month - 1]} de {d.year}"


def date_es_short(d):
    return f"{DIAS_ES[d.weekday()]} {d.day} {MESES_ES[d.month - 1][:3]}"


try:
    import libsql_experimental as libsql
except ImportError:
    libsql = None


# ==========================================
# BASE DE DATOS MULTIUSUARIO (SQLite + Turso)
# ==========================================
class DatabaseManager:

    def __init__(self, db_name="gym_macros_mobile.db"):
        turso_url = os.getenv("TURSO_DATABASE_URL")
        turso_token = os.getenv("TURSO_AUTH_TOKEN")

        if libsql and turso_url and turso_token:
            self.conn = libsql.connect(
                database=db_name,
                sync_url=turso_url,
                auth_token=turso_token,
            )
            self.conn.sync()
        else:
            self.conn = sqlite3.connect(db_name, check_same_thread=False)

        self.cursor = self.conn.cursor()
        self.create_tables()

    def _save(self):
        self.conn.commit()
        if hasattr(self.conn, "sync"):
            try:
                self.conn.sync()
            except Exception as e:
                print("Error sincronizando con Turso:", e)

    def create_tables(self):
        # Tabla de usuarios (Multiusuario)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT DEFAULT 'Atleta',
                avatar TEXT DEFAULT '💪',
                weight REAL,
                height REAL,
                age INTEGER,
                gender TEXT,
                activity TEXT,
                goal_name TEXT DEFAULT 'Mantenimiento',
                goal_offset REAL DEFAULT 0.0,
                target_kcal REAL,
                target_p REAL,
                target_c REAL,
                target_f REAL,
                burned_kcal REAL DEFAULT 244
            )
        """)

        # Tabla de registro de alimentos vinculada a user_id
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS food_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER DEFAULT 1,
                meal_type TEXT DEFAULT 'Almuerzo',
                name TEXT,
                grams REAL,
                kcal REAL,
                p REAL,
                c REAL,
                f REAL,
                date DATE DEFAULT (DATE('now', 'localtime'))
            )
        """)

        # Migraciones seguras por si ya existía la base de datos anterior
        try:
            self.cursor.execute(
                "ALTER TABLE food_log ADD COLUMN user_id INTEGER DEFAULT 1"
            )
        except Exception:
            pass
        try:
            self.cursor.execute(
                "ALTER TABLE food_log ADD COLUMN meal_type TEXT DEFAULT 'Almuerzo'"
            )
        except Exception:
            pass

        try:
            self.cursor.execute("ALTER TABLE food_log ADD COLUMN image TEXT")
        except Exception:
            pass
        # Alimentos añadidos como conjunto (Mis comidas): comparten group_id y group_name
        for _col in ("group_id TEXT", "group_name TEXT"):
            try:
                self.cursor.execute(f"ALTER TABLE food_log ADD COLUMN {_col}")
            except Exception:
                pass
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS saved_meals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                name TEXT,
                meal_type TEXT,
                items TEXT
            )
        """)
        # Migraciones: actividad física descrita por el usuario + factor calculado por la IA
        for col_sql in (
            "ALTER TABLE users ADD COLUMN activity_text TEXT",
            "ALTER TABLE users ADD COLUMN activity_mult REAL",
        ):
            try:
                self.cursor.execute(col_sql)
            except Exception:
                pass
        self._save()

    def get_all_users(self):
        self.cursor.execute("""
            SELECT id, name, avatar, weight, height, age, gender, activity,
                   goal_name, goal_offset, target_kcal, target_p, target_c, target_f,
                   COALESCE(burned_kcal, 244)
            FROM users
            ORDER BY id ASC
        """)
        return self.cursor.fetchall()

    def get_user(self, user_id):
        self.cursor.execute(
            """
            SELECT id, name, avatar, weight, height, age, gender, activity,
                   goal_name, goal_offset, target_kcal, target_p, target_c, target_f,
                   COALESCE(burned_kcal, 244)
            FROM users
            WHERE id = ?
        """,
            (user_id,),
        )
        return self.cursor.fetchone()

    def create_user(
        self,
        name,
        avatar,
        weight,
        height,
        age,
        gender,
        activity,
        goal_name,
        goal_offset,
        target_kcal,
        target_p,
        target_c,
        target_f,
        burned_kcal=244.0,
    ):
        self.cursor.execute(
            """
            INSERT INTO users (
                name, avatar, weight, height, age, gender, activity,
                goal_name, goal_offset, target_kcal, target_p, target_c, target_f, burned_kcal
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                name,
                avatar,
                weight,
                height,
                age,
                gender,
                activity,
                goal_name,
                goal_offset,
                target_kcal,
                target_p,
                target_c,
                target_f,
                burned_kcal,
            ),
        )
        self._save()
        return self.cursor.lastrowid

    def update_user(
        self,
        user_id,
        name,
        avatar,
        weight,
        height,
        age,
        gender,
        activity,
        goal_name,
        goal_offset,
        target_kcal,
        target_p,
        target_c,
        target_f,
        burned_kcal=244.0,
    ):
        self.cursor.execute(
            """
            UPDATE users
            SET name=?, avatar=?, weight=?, height=?, age=?, gender=?, activity=?,
                goal_name=?, goal_offset=?, target_kcal=?, target_p=?, target_c=?, target_f=?, burned_kcal=?
            WHERE id=?
        """,
            (
                name,
                avatar,
                weight,
                height,
                age,
                gender,
                activity,
                goal_name,
                goal_offset,
                target_kcal,
                target_p,
                target_c,
                target_f,
                burned_kcal,
                user_id,
            ),
        )
        self._save()

    def delete_user(self, user_id):
        self.cursor.execute("DELETE FROM food_log WHERE user_id = ?", (user_id,))
        self.cursor.execute("DELETE FROM saved_meals WHERE user_id = ?", (user_id,))
        self.cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self._save()

    def get_activity_extra(self, user_id):
        """Devuelve (texto de actividad, factor de actividad o None)."""
        self.cursor.execute(
            "SELECT activity_text, activity_mult FROM users WHERE id = ?", (user_id,)
        )
        row = self.cursor.fetchone()
        if not row:
            return "", None
        return row[0] or "", row[1]

    def set_activity_extra(self, user_id, text, mult):
        self.cursor.execute(
            "UPDATE users SET activity_text = ?, activity_mult = ? WHERE id = ?",
            (text or None, mult, user_id),
        )
        self._save()

    def add_food(self, user_id, meal_type, name, grams, kcal, p, c, f, image=None,
                 group_id=None, group_name=None):
        self.cursor.execute(
            """
            INSERT INTO food_log (user_id, meal_type, name, grams, kcal, p, c, f, image, date,
                                  group_id, group_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (user_id, meal_type, name, grams, kcal, p, c, f, image, today_str(), group_id, group_name),
        )
        self._save()

    def get_today_foods(self, user_id):
        self.cursor.execute(
            """
            SELECT id, COALESCE(meal_type, 'Almuerzo'), name, grams, kcal, p, c, f, image,
                   group_id, group_name
            FROM food_log
            WHERE user_id = ? AND date = ?
            ORDER BY id ASC
        """,
            (user_id, today_str()),
        )
        return self.cursor.fetchall()

    def get_recent_foods(self, user_id, limit=10):
        """Últimos alimentos distintos añadidos, con sus valores por 100 g y los gramos usados."""
        self.cursor.execute(
            """
            SELECT name, grams, kcal, p, c, f, image
            FROM food_log
            WHERE id IN (
                SELECT MAX(id) FROM food_log WHERE user_id = ? GROUP BY LOWER(name)
            )
            ORDER BY id DESC
            LIMIT ?
        """,
            (user_id, limit),
        )
        out = []
        for name, grams, kcal, p, c, f, image in self.cursor.fetchall():
            g = float(grams or 0)
            if g <= 0:
                continue
            k100 = 100.0 / g
            out.append(
                {
                    "name": name, "grams": g, "image": image,
                    "kcal": kcal * k100, "p": p * k100, "c": c * k100, "f": f * k100,
                }
            )
        return out

    def get_foods_between(self, user_id, start, end):
        """Alimentos entre dos fechas (YYYY-MM-DD, ambas incluidas)."""
        self.cursor.execute(
            """
            SELECT id, date, COALESCE(meal_type, 'Almuerzo'), name, grams, kcal, p, c, f, image,
                   group_id, group_name
            FROM food_log
            WHERE user_id = ? AND date BETWEEN ? AND ?
            ORDER BY date ASC, id ASC
        """,
            (user_id, start, end),
        )
        return self.cursor.fetchall()

    def delete_group(self, user_id, group_id):
        self.cursor.execute(
            "DELETE FROM food_log WHERE user_id = ? AND group_id = ?", (user_id, group_id)
        )
        self._save()

    def delete_meal_today(self, user_id, meal_type):
        """Borra todos los alimentos de hoy de una comida (desayuno, almuerzo…)."""
        self.cursor.execute(
            "DELETE FROM food_log WHERE user_id = ? AND date = ? AND COALESCE(meal_type, 'Almuerzo') = ?",
            (user_id, today_str(), meal_type),
        )
        self._save()

    def delete_food(self, food_id):
        self.cursor.execute("DELETE FROM food_log WHERE id = ?", (food_id,))
        self._save()

    # ----- Comidas fijas -----
    def save_meal(self, user_id, name, meal_type, items):
        self.cursor.execute(
            "INSERT INTO saved_meals (user_id, name, meal_type, items) VALUES (?, ?, ?, ?)",
            (user_id, name, meal_type, json.dumps(items, ensure_ascii=False)),
        )
        self._save()

    def get_saved_meals(self, user_id):
        self.cursor.execute(
            "SELECT id, name, meal_type, items FROM saved_meals WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        )
        out = []
        for sid, name, mt, items in self.cursor.fetchall():
            try:
                out.append((sid, name, mt, json.loads(items)))
            except Exception:
                pass
        return out

    def delete_saved_meal(self, saved_id):
        self.cursor.execute("DELETE FROM saved_meals WHERE id = ?", (saved_id,))
        self._save()


# ==========================================
# FÓRMULA HARRIS-BENEDICT Y MACROS
# ==========================================
ACT_MULTIPLIERS = {
    "Sedentario": {"mult": 1.2, "desc": "Poco o ningún ejercicio", "icon": ft.Icons.WEEKEND_OUTLINED},
    "Ligero": {"mult": 1.375, "desc": "Entreno suave 1-3 días/sem", "icon": ft.Icons.DIRECTIONS_WALK},
    "Moderado": {"mult": 1.55, "desc": "Gimnasio 3-5 días/sem", "icon": ft.Icons.FITNESS_CENTER},
    "Intenso": {"mult": 1.725, "desc": "Entreno fuerte 6-7 días/sem", "icon": ft.Icons.LOCAL_FIRE_DEPARTMENT_OUTLINED},
    "Atleta": {"mult": 1.9, "desc": "Doble sesión o trabajo físico", "icon": ft.Icons.EMOJI_EVENTS_OUTLINED},
}

GOALS_META = {
    "Definición": {"offset": -400.0, "desc": "Perder grasa (-400 kcal)", "icon": ft.Icons.TRENDING_DOWN},
    "Mantenimiento": {"offset": 0.0, "desc": "Mantener peso (0 kcal)", "icon": ft.Icons.BALANCE},
    "Volumen": {"offset": 350.0, "desc": "Ganar músculo (+350 kcal)", "icon": ft.Icons.TRENDING_UP},
}


def compute_bmr(weight, height, age, gender):
    if gender == "Hombre":
        return 88.362 + (13.397 * weight) + (4.799 * height) - (5.677 * age)
    return 447.593 + (9.247 * weight) + (3.098 * height) - (4.330 * age)


def activity_multiplier(activity, act_mult=None):
    """Factor de actividad: el calculado por la IA si existe; si no, el del nivel rápido."""
    if act_mult:
        return float(act_mult)
    return ACT_MULTIPLIERS.get(activity, ACT_MULTIPLIERS["Moderado"])["mult"]


def nearest_activity_level(mult):
    return min(ACT_MULTIPLIERS, key=lambda k: abs(ACT_MULTIPLIERS[k]["mult"] - mult))


def estimate_exercise_kcal(weight, height, age, gender, mult):
    """Gasto medio diario por actividad (por encima del reposo)."""
    return max(0, round(compute_bmr(weight, height, age, gender) * (mult - 1.2)))


# Repartos de macronutrientes (% de las kcal: proteína, carbohidratos, grasas).
# Valores orientativos basados en la literatura; no sustituyen a un dietista.
MACRO_PRESETS = {
    "auto": {
        "name": "Automático",
        "split": None,
        "desc": "2 g/kg de proteína, 0,9 g/kg de grasa y el resto en carbohidratos.",
        "ref": "Morton et al., 2018 (Br J Sports Med); Jäger et al., 2017 (ISSN)",
    },
    "amdr": {
        "name": "Equilibrado",
        "split": (20, 50, 30),
        "desc": "Reparto dentro de los rangos recomendados para adultos sanos.",
        "ref": "IOM/NAM, Dietary Reference Intakes, 2005: P 10-35 %, C 45-65 %, G 20-35 %",
    },
    "cut": {
        "name": "Definición",
        "split": (35, 40, 25),
        "desc": "Más proteína para conservar el músculo mientras estás en déficit.",
        "ref": "Helms et al., 2014 (JISSN); Morton et al., 2018",
    },
    "bulk": {
        "name": "Volumen",
        "split": (25, 50, 25),
        "desc": "Carbohidratos altos para rendir y recuperar mejor en superávit.",
        "ref": "Burke et al., 2011 (J Sports Sci); Kerksick et al., 2018 (ISSN)",
    },
    "lowcarb": {
        "name": "Low carb",
        "split": (30, 25, 45),
        "desc": "Menos carbohidratos y más grasa; buena saciedad si te sienta bien.",
        "ref": "Hall & Guo, 2017 (Gastroenterology): a igual proteína y kcal, la diferencia en pérdida de grasa es pequeña",
    },
    "custom": {
        "name": "Personalizado",
        "split": None,
        "desc": "Elige tú el porcentaje de proteína y carbohidratos; la grasa es lo que sobra.",
        "ref": "Referencia de rangos: IOM/NAM, 2005",
    },
}


def recommended_preset(offset):
    """Preset sugerido según el ajuste calórico del objetivo."""
    if offset <= -50:
        return "cut"
    if offset >= 50:
        return "bulk"
    return "amdr"


def compute_macros(weight, height, age, gender, activity, goal_name, offset=None, act_mult=None, split=None):
    bmr = compute_bmr(weight, height, age, gender)
    mult = activity_multiplier(activity, act_mult)
    if offset is None:
        offset = GOALS_META.get(goal_name, GOALS_META["Mantenimiento"])["offset"]

    tdee = bmr * mult
    target_kcal = max(1200.0, round(tdee + offset))
    if split:
        # Reparto por porcentajes de las kcal totales (P, C, G)
        pp, pc, pf = split
        target_p = round(target_kcal * pp / 100.0 / 4)
        target_c = round(target_kcal * pc / 100.0 / 4)
        target_f = round(target_kcal * pf / 100.0 / 9)
    else:
        target_p = round(weight * 2.0)  # 2.0g/kg proteína
        target_f = round(weight * 0.9)  # 0.9g/kg grasas
        target_c = max(0.0, round((target_kcal - (target_p * 4) - (target_f * 9)) / 4))

    return target_kcal, target_p, target_c, target_f, offset


_OFFSET_ADVICE = {
    ("-", "ligero"): "Pérdida lenta y sostenible; es lo que mejor conserva el músculo.",
    ("-", "moderado"): "El punto dulce para la mayoría de la gente.",
    ("-", "agresivo"): "Rápido, pero difícil de sostener y con riesgo de perder músculo.",
    ("-", "muy agresivo"): "No recomendable salvo con supervisión profesional.",
    ("+", "ligero"): "Ganancia limpia, con muy poca grasa.",
    ("+", "moderado"): "Buen ritmo de volumen para la mayoría.",
    ("+", "agresivo"): "Ganarás músculo, pero también bastante grasa.",
    ("+", "muy agresivo"): "Suele ser excesivo: mucha grasa acumulada.",
}


def describe_offset(offset):
    """(nombre, color, consejo) de un ajuste calórico respecto al mantenimiento."""
    a = abs(offset)
    if a < 50:
        return "Mantenimiento", "#3F7D58", "Mantienes tu peso actual."
    if a <= 250:
        lvl, col = "ligero", "#3F7D58"
    elif a <= 500:
        lvl, col = "moderado", "#D9A441"
    elif a <= 750:
        lvl, col = "agresivo", "#C9763F"
    else:
        lvl, col = "muy agresivo", "#B8453A"
    sign = "-" if offset < 0 else "+"
    name = ("Déficit " if offset < 0 else "Superávit ") + lvl
    return name, col, _OFFSET_ADVICE[(sign, lvl)]


def weekly_change_kg(offset):
    """Cambio de peso aproximado por semana (7700 kcal ≈ 1 kg)."""
    return offset * 7 / 7700.0


# ==========================================
# IA (Gemini) + UTILIDADES
# ==========================================
AI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")


def ask_ai(system_prompt, user_text, max_tokens=1800, image_bytes=None):
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Falta la variable GEMINI_API_KEY en Render")
    user_parts = []
    if image_bytes:
        user_parts.append(
            {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(image_bytes).decode("ascii")}}
        )
    user_parts.append({"text": user_text})
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{AI_MODEL}:generateContent",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": user_parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": max_tokens,
                "temperature": 0.4,
            },
        },
        timeout=60 if image_bytes else 45,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Error {resp.status_code}: {resp.text[:150]}")
    data = resp.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except Exception:
        raise RuntimeError("Gemini no devolvió respuesta (¿contenido bloqueado?)")
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def extract_json(text):
    text = re.sub(r"```(?:json)?", "", text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    i, j = text.find("{"), text.rfind("}")
    if i != -1 and j > i:
        return json.loads(text[i : j + 1])
    raise ValueError("La IA no devolvió un resultado válido")


def _num(v):
    try:
        return max(0.0, float(v))
    except Exception:
        return 0.0


FOOD_PARSE_PROMPT = (
    "Eres un nutricionista experto. El usuario describe en español lo que ha comido. "
    'Devuelve SOLO un JSON con este formato exacto: {"items":[{"name":"...","grams":123,'
    '"kcal":0,"p":0,"c":0,"f":0}]}. Reglas: un elemento por alimento; "grams" es el peso '
    "estimado en gramos de la porción realmente comida (si el usuario da gramos, respétalos; "
    "si usa medidas caseras como rebanadas, cucharadas, piezas, vasos o puñados, conviértelas "
    "a gramos con equivalencias típicas); kcal, p (proteína), c (carbohidratos) y f (grasa) "
    "son los TOTALES de esa porción, no por 100 g; valores realistas; nada de texto fuera del JSON."
)

FOOD_PHOTO_PROMPT = (
    "Eres un nutricionista experto. El usuario te envía una FOTO de un plato, bocadillo o comida, "
    "y a veces un texto con aclaraciones (ingredientes que no se ven, cantidades, marcas, cómo está "
    "cocinado). Identifica cada alimento y estima el peso en gramos de la porción que se ve, usando "
    "referencias visuales (tamaño del plato, cubiertos, manos, envase, grosor). Si es un plato "
    "combinado (por ejemplo macarrones con chorizo o un bocadillo), desglósalo en sus ingredientes "
    "principales, cada uno con su peso. Si el texto del usuario dice ingredientes o gramos, tiene "
    "PRIORIDAD sobre lo que veas en la imagen. Si la foto no contiene comida reconocible y no hay "
    'texto útil, devuelve {"items":[]}. Devuelve SOLO un JSON con este formato exacto: '
    '{"items":[{"name":"...","grams":123,"kcal":0,"p":0,"c":0,"f":0}]}. Reglas: un elemento por '
    'alimento o ingrediente; "grams" es el peso estimado de la porción; kcal, p (proteína), c '
    "(carbohidratos) y f (grasa) son los TOTALES de esa porción, no por 100 g; valores realistas; "
    "nada de texto fuera del JSON."
)


def prepare_photo(image_bytes, max_side=1024, quality=82):
    """Reduce la foto (lado máx. 1024 px) y la convierte a JPEG para enviarla ligera a la IA."""
    try:
        img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return image_bytes
        h, w = img.shape[:2]
        scale = max_side / float(max(h, w))
        if scale < 1:
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return buf.tobytes() if ok else image_bytes
    except Exception:
        return image_bytes


ACTIVITY_PROMPT = (
    "Eres un fisiólogo del ejercicio. El usuario describe en español qué actividad física hace y con "
    "qué frecuencia. Estima su factor de actividad (PAL): el número por el que se multiplica su "
    "metabolismo basal para obtener el gasto diario total medio, contando el día a día (trabajo, "
    "pasos) y el ejercicio planificado, promediado sobre la semana. Devuelve SOLO un JSON: "
    '{"multiplier":1.55,"summary":"una frase explicando el cálculo","exercise_kcal_day":250}. '
    "Reglas: multiplier entre 1.2 (sedentario total) y 2.1 (atleta con doble sesión o trabajo físico "
    "duro), con 2 decimales; sé conservador, la gente suele sobreestimar su actividad; "
    "exercise_kcal_day son las kcal medias diarias gastadas SOLO en ejercicio, promediadas sobre 7 "
    "días; summary en español, máximo 25 palabras."
)

PLAN_TOLERANCE = 50  # margen ± kcal por comida
MAX_MEAL_KCAL = 800  # a partir de aquí se reparte en varias comidas
MIN_MEAL_KCAL = 150  # no tiene sentido una comida más pequeña

PLAN_PROMPT = (
    "Eres un nutricionista deportivo y cocinero. El usuario debe completar las calorías del día con "
    "una o varias comidas. Para CADA comida pedida crea UNA receta sencilla (ingredientes comunes y "
    "baratos, máximo 20 minutos) cuyas calorías se acerquen lo máximo posible al OBJETIVO de esa "
    "comida; prioriza la proteína, no repitas el ingrediente principal entre comidas y respeta "
    "alergias y preferencias. Devuelve SOLO JSON con este formato: "
    '{"meals":[{"title":"...","description":"una frase","ingredients":[{"name":"...","grams":0,'
    '"p":0,"c":0,"f":0}],"steps":["paso corto"]}]}. Reglas: un elemento de "meals" por cada comida '
    "pedida y en el mismo orden; en cada ingrediente, \"name\" va SIN cantidades ni medidas, "
    "\"grams\" es el peso en gramos de la cantidad usada y p, c, f son los gramos TOTALES de "
    "proteína, carbohidratos y grasa de esa cantidad (no por 100 g), con valores nutricionales "
    "realistas; 4*p + 4*c + 9*f sumado de todos los ingredientes debe dar las kcal objetivo; los "
    "pasos NO mencionan cantidades; máximo 4 pasos."
)

def ai_parse_foods(description, image_bytes=None):
    if image_bytes:
        txt = (
            f"Aclaraciones del usuario: {description}"
            if description
            else "El usuario no ha escrito nada: identifica los alimentos solo por la foto."
        )
        data = extract_json(ask_ai(FOOD_PHOTO_PROMPT, txt, image_bytes=image_bytes))
    else:
        data = extract_json(ask_ai(FOOD_PARSE_PROMPT, description))
    items = []
    for it in data.get("items", []):
        name = str(it.get("name", "")).strip()
        grams = _num(it.get("grams"))
        if name and grams > 0:
            items.append(
                {
                    "name": name,
                    "grams": round(grams),
                    "kcal": round(_num(it.get("kcal")), 1),
                    "p": round(_num(it.get("p")), 1),
                    "c": round(_num(it.get("c")), 1),
                    "f": round(_num(it.get("f")), 1),
                }
            )
    if not items:
        raise ValueError(
            "No pude identificar alimentos en la foto. Prueba con otra más cercana o añade texto."
            if image_bytes
            else "No pude identificar alimentos. Prueba a describirlo de otra forma."
        )
    return items


DAY_REVIEW_PROMPT = (
    "Eres un nutricionista deportivo cercano y honesto. Valoras UN día de comidas de un usuario que "
    "cuenta macros con fines de fitness. Recibes su objetivo, lo consumido y cada alimento con sus "
    "gramos. Sé FLEXIBLE y proporcionado: un capricho pequeño (una ración normal de dulce, snack o "
    "procesado, aprox. por debajo del 10-15 % de las kcal del día) NO es un problema y no lo señales; "
    "tampoco regañes por un día imperfecto. Solo marca un alimento cuando la CANTIDAD sea claramente "
    "excesiva (por ejemplo 300 g de sobrasada, medio kilo de embutido, litros de refresco, bollería "
    "abundante, mucho alcohol) o cuando el conjunto del día sea claramente pobre (casi todo "
    "ultraprocesado, sin verdura, fruta ni proteína de calidad). Para cada problema real: di qué "
    "cantidad sería razonable (en gramos) y da 2-3 alternativas saludables concretas y realistas que "
    "se parezcan en sabor o uso. Si el día fue bueno, dilo con claridad y con un detalle concreto "
    "(proteína, fibra, variedad…). Si el día está incompleto, no juzgues lo que aún falta por comer. "
    'Devuelve SOLO un JSON: {"verdict":"muy_bien|bien|mejorable|mal","title":"frase corta",'
    '"summary":"2-3 frases","flags":[{"food":"nombre","issue":"por qué, 1 frase",'
    '"suggested_grams":0,"alternatives":["...","..."]}],"tips":["consejo corto"]}. Reglas: flags vacío '
    "si no hay nada grave; máximo 3 flags y 2 tips; todo en español, tono cercano, sin sermones ni "
    "culpa; no inventes alimentos que no estén en la lista; kcal y gramos solo como referencia."
)


def ai_review_day(context):
    data = extract_json(ask_ai(DAY_REVIEW_PROMPT, context, max_tokens=1400))
    verdict = str(data.get("verdict", "bien")).lower().strip()
    if verdict not in ("muy_bien", "bien", "mejorable", "mal"):
        verdict = "bien"
    flags = []
    for fl in (data.get("flags") or [])[:3]:
        if not isinstance(fl, dict):
            continue
        food = str(fl.get("food", "")).strip()
        issue = str(fl.get("issue", "")).strip()
        alts = [str(a).strip() for a in (fl.get("alternatives") or []) if str(a).strip()][:3]
        if food and (issue or alts):
            flags.append(
                {"food": food, "issue": issue, "grams": round(_num(fl.get("suggested_grams"))), "alternatives": alts}
            )
    tips = [str(t).strip() for t in (data.get("tips") or []) if str(t).strip()][:2]
    return {
        "verdict": verdict,
        "title": str(data.get("title", "")).strip(),
        "summary": str(data.get("summary", "")).strip(),
        "flags": flags,
        "tips": tips,
    }


def ai_estimate_activity(text, weight, height, age, gender):
    """La IA interpreta la actividad descrita y devuelve factor de actividad y gasto por ejercicio."""
    user_text = f"Sexo: {gender}. Edad: {age} años. Peso: {weight} kg. Altura: {height} cm. Actividad: {text}"
    data = extract_json(ask_ai(ACTIVITY_PROMPT, user_text, max_tokens=1200))
    try:
        mult = float(data.get("multiplier"))
    except Exception:
        raise ValueError("La IA no pudo estimar tu actividad. Descríbela con más detalle.")
    mult = max(1.2, min(2.1, mult))
    return {
        "mult": round(mult, 3),
        "summary": str(data.get("summary", "")).strip()[:220],
        "exercise_kcal": int(min(1500, _num(data.get("exercise_kcal_day")))),
    }


def split_calories(total, n):
    """Reparte `total` kcal en `n` partes enteras que suman exactamente `total`."""
    total, n = int(round(total)), max(1, int(n))
    base, extra = divmod(total, n)
    return [base + (1 if i < extra else 0) for i in range(n)]


def _plan_from_ai(m, target):
    """Valida una receta de la IA y ajusta las cantidades para clavar las kcal objetivo.

    Las kcal se calculan SIEMPRE a partir de los macros (4/4/9) y las cantidades se escalan
    proporcionalmente, así que el resultado queda dentro del margen aunque la IA calcule mal.
    """
    if not isinstance(m, dict) or not str(m.get("title", "")).strip():
        return None
    ings = []
    for ing in m.get("ingredients") or []:
        if not isinstance(ing, dict):
            continue
        name = str(ing.get("name", "")).strip()
        g = _num(ing.get("grams"))
        if name and g > 0:
            ings.append(
                {"name": name, "grams": g, "p": _num(ing.get("p")), "c": _num(ing.get("c")), "f": _num(ing.get("f"))}
            )
    if not ings:
        return None
    raw = sum(4 * i["p"] + 4 * i["c"] + 9 * i["f"] for i in ings)
    if raw <= 0:
        return None
    factor = target["kcal"] / raw
    if not 0.5 <= factor <= 2.0:  # la receta de la IA queda demasiado lejos: mejor pedir otra
        return None
    final, tp, tc, tf, grams_total = [], 0.0, 0.0, 0.0, 0
    for i in ings:
        g2 = max(1, round(i["grams"] * factor))
        r = g2 / i["grams"]
        tp, tc, tf = tp + i["p"] * r, tc + i["c"] * r, tf + i["f"] * r
        grams_total += g2
        final.append({"name": i["name"], "grams": g2})
    kcal = 4 * tp + 4 * tc + 9 * tf
    if abs(kcal - target["kcal"]) > PLAN_TOLERANCE:
        return None
    return {
        "meal": target["meal"],
        "title": str(m["title"]).strip(),
        "description": str(m.get("description", "")).strip(),
        "target_kcal": target["kcal"],
        "kcal": round(kcal),
        "p": round(tp, 1),
        "c": round(tc, 1),
        "f": round(tf, 1),
        "grams": grams_total,
        "ingredients": final,
        "steps": [str(x) for x in (m.get("steps") or [])][:5],
    }


def ai_plan(targets, eaten_names, prefs=""):
    """Una receta por objetivo. Devuelve una lista (None donde no se pudo ajustar)."""
    plans = [None] * len(targets)
    for attempt in range(2):
        pending = [i for i, p in enumerate(plans) if p is None]
        if not pending:
            break
        lines = []
        for n, i in enumerate(pending, 1):
            t = targets[i]
            macros = ", ".join(
                f"~{t[k]} g {lbl}" for k, lbl in (("p", "proteína"), ("c", "carbohidratos"), ("f", "grasa")) if t[k] > 0
            )
            lines.append(f"Comida {n} ({t['meal']}): OBJETIVO {t['kcal']} kcal" + (f", {macros}" if macros else ""))
        user_text = (
            f"Comidas a crear ({len(pending)}): " + "; ".join(lines) + ". "
            f"Ya ha comido hoy: {', '.join(eaten_names) or 'nada aún'}. "
            f"Preferencias o alergias: {prefs or 'ninguna'}."
        )
        if attempt:
            user_text += (
                " IMPORTANTE: en el intento anterior las calorías quedaron lejos del objetivo. "
                "Ajusta las cantidades para que 4*p + 4*c + 9*f de los ingredientes sume justo las kcal pedidas."
            )
        try:
            data = extract_json(ask_ai(PLAN_PROMPT, user_text, max_tokens=1000 * len(pending) + 1500))
        except Exception:
            if attempt == 0:
                raise
            break
        meals = data.get("meals") if isinstance(data, dict) else data
        for idx, m in zip(pending, meals or []):
            plans[idx] = _plan_from_ai(m, targets[idx])
    return plans

BTN_ICONS = {
    "➕": ft.Icons.ADD, "✅": ft.Icons.CHECK, "⭐": ft.Icons.STAR_OUTLINE,
    "🔍": ft.Icons.SEARCH, "📸": ft.Icons.PHOTO_CAMERA_OUTLINED, "✖": ft.Icons.CLOSE,
    "⬅": ft.Icons.ARROW_BACK, "➡": ft.Icons.ARROW_FORWARD, "💡": ft.Icons.LIGHTBULB_OUTLINE,
    "✨": ft.Icons.AUTO_AWESOME_OUTLINED, "⚡": ft.Icons.TUNE, "👥": ft.Icons.SWAP_HORIZ,
    "🚀": ft.Icons.CHECK, "🔄": ft.Icons.REFRESH,
}


def split_btn_icon(text):
    """Separa un posible emoji inicial/final del texto y lo cambia por un icono."""
    t = (text or "").replace("\ufe0f", "").strip()
    for em, ic in BTN_ICONS.items():
        if t.startswith(em):
            return ic, t[len(em):].strip(), False
        if t.endswith(em):
            return ic, t[: -len(em)].strip(), True
    return None, t, False


def avatar_chip(name, size=44):
    initial = (name or "?").strip()[:1].upper() or "?"
    return ft.Container(
        width=size,
        height=size,
        border_radius=size * 0.3,
        bgcolor="#E3ECE6",
        alignment=ft.Alignment.CENTER,
        content=ft.Text(initial, size=size * 0.42, weight=ft.FontWeight.W_600, color="#24543F"),
    )


def food_thumb(url, size=44):
    placeholder = ft.Container(
        width=size,
        height=size,
        border_radius=10,
        bgcolor="#F4F1EA",
        alignment=ft.Alignment.CENTER,
        content=ft.Icon(ft.Icons.RESTAURANT, size=size * 0.4, color="#A9A59B"),
    )
    if not url:
        return placeholder
    return ft.Image(
        src=url,
        width=size,
        height=size,
        fit=ft.BoxFit.COVER,
        border_radius=10,
        error_content=placeholder,
    )


# ==========================================
# LECTURA DE CÓDIGOS (QR / EAN) Y OPENFOODFACTS
# ==========================================
try:  # opcional: pyzbar lee muy bien EAN/UPC si está instalado
    from pyzbar import pyzbar as _pyzbar
except Exception:
    _pyzbar = None


def barcode_engines():
    """(OpenCV con lector de barras, pyzbar) disponibles en este servidor."""
    return hasattr(cv2, "barcode"), _pyzbar is not None


def valid_ean(code):
    """Comprueba longitud y dígito de control de EAN-8 / UPC-A / EAN-13 / GTIN-14."""
    if not code or not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    digits = [int(x) for x in code]
    body = digits[:-1][::-1]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(body))
    return (10 - total % 10) % 10 == digits[-1]


def _cv_barcode_read(det, img):
    """Lee códigos con el detector de OpenCV sin depender de la firma exacta de cada versión."""
    try:
        res = det.detectAndDecodeWithType(img)
    except Exception:
        try:
            res = det.detectAndDecode(img)
        except Exception:
            return []
    out = []
    for part in res if isinstance(res, tuple) else (res,):
        if isinstance(part, str):
            out.append(part)
        elif isinstance(part, (tuple, list)):
            out += [x for x in part if isinstance(x, str)]
    return [s.strip() for s in out if s.strip().isdigit()]


def _scaled(img, s):
    if s == 1.0:
        return img
    return cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)


def _barcode_variants(img, deep=True):
    """Versiones de un fotograma (de la más probable a la más costosa) para facilitar la lectura.

    El detector de OpenCV solo lee códigos cuyo ancho ronda los 150-300 px, así que se prueba
    una pirámide de escalas. En modo rápido (deep=False) solo las tres escalas más habituales;
    el modo profundo añade recortes ampliados, realce de contraste/nitidez y giro de 90°.
    """
    h, w = img.shape[:2]
    for sc in (1.0, 0.5, 0.7) if not deep else (1.0, 0.5, 0.35, 0.7, 0.25, 0.18):
        yield _scaled(img, sc)
    if not deep:
        return
    center = img[int(h * 0.2) : int(h * 0.8), int(w * 0.1) : int(w * 0.9)]  # el código suele ir centrado
    for sc in (1.5, 2.0):  # código pequeño/lejos
        yield _scaled(center, sc)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    eq = cv2.cvtColor(cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray), cv2.COLOR_GRAY2BGR)
    blur = cv2.GaussianBlur(gray, (0, 0), 3)
    sharp = cv2.cvtColor(cv2.addWeighted(gray, 1.8, blur, -0.8, 0), cv2.COLOR_GRAY2BGR)
    for enhanced in (eq, sharp):  # poca luz / desenfoque
        for sc in (1.0, 0.5):
            yield _scaled(enhanced, sc)
    rot = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)  # códigos en vertical
    for sc in (1.0, 0.5, 0.35):
        yield _scaled(rot, sc)


def decode_frame(image_bytes, deep=True, max_side=1280):
    """Texto de un QR o de un código de barras VÁLIDO (con dígito de control), o '' si no hay.

    deep=False es la pasada rápida del escaneo automático (se alterna con pasadas profundas);
    el botón Capturar usa deep=True y una resolución mayor.
    """
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return ""
    big = max(img.shape[:2])
    if big > max_side:  # los fotogramas de 4000 px hacían lentísima cada pasada
        img = _scaled(img, max_side / float(big))
    if _pyzbar is not None:  # el más rápido con EAN/UPC: se prueba primero
        try:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            for b in _pyzbar.decode(gray):
                txt = b.data.decode("utf-8", "ignore").strip()
                if valid_ean(txt) or txt.startswith("{"):
                    return txt
        except Exception:
            pass
    try:
        data, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
        if data:
            return data
    except Exception:
        pass
    if hasattr(cv2, "barcode"):
        try:
            det = cv2.barcode.BarcodeDetector()
        except Exception:
            det = None
        if det is not None:
            for variant in _barcode_variants(img, deep):
                for code in _cv_barcode_read(det, variant):
                    if valid_ean(code):
                        return code
    return ""


def off_nutri(nutr):
    """(kcal, proteína, carbos, grasa) por 100 g desde OpenFoodFacts; si faltan kcal usa los kJ."""
    nutr = nutr or {}
    kcal = nutr.get("energy-kcal_100g")
    if kcal in (None, ""):
        kj = nutr.get("energy_100g")
        kcal = _num(kj) / 4.184 if kj not in (None, "") else 0
    return (
        _num(kcal),
        _num(nutr.get("proteins_100g")),
        _num(nutr.get("carbohydrates_100g")),
        _num(nutr.get("fat_100g")),
    )


# Alimentos básicos (por 100 g): (nombre, kcal, proteína, carbohidratos, grasa, sinónimos)
# Valores medios orientativos (tablas BEDCA/USDA); los cocidos van marcados como tales.
BASIC_FOODS = [
    # ---- Frutas ----
    ("Mandarina", 53, 0.8, 13.3, 0.3, "clementina satsuma mandarinas"),
    ("Naranja", 47, 0.9, 11.8, 0.1, "naranjas"),
    ("Limón", 29, 1.1, 9.3, 0.3, "limon limones"),
    ("Pomelo", 42, 0.8, 10.7, 0.1, ""),
    ("Manzana", 52, 0.3, 13.8, 0.2, "manzanas"),
    ("Pera", 57, 0.4, 15.2, 0.1, "peras"),
    ("Plátano", 89, 1.1, 22.8, 0.3, "platano banana bananas"),
    ("Uvas", 69, 0.7, 18.1, 0.2, "uva"),
    ("Fresas", 32, 0.7, 7.7, 0.3, "fresa freson"),
    ("Arándanos", 57, 0.7, 14.5, 0.3, "arandanos arandano"),
    ("Frambuesas", 52, 1.2, 11.9, 0.7, "frambuesa"),
    ("Melocotón", 39, 0.9, 9.5, 0.3, "melocoton durazno"),
    ("Nectarina", 44, 1.1, 10.6, 0.3, "paraguayo"),
    ("Albaricoque", 48, 1.4, 11.1, 0.4, "damasco"),
    ("Ciruela", 46, 0.7, 11.4, 0.3, "ciruelas"),
    ("Cerezas", 63, 1.1, 16.0, 0.2, "cereza"),
    ("Kiwi", 61, 1.1, 14.7, 0.5, ""),
    ("Piña", 50, 0.5, 13.1, 0.1, "pina ananas"),
    ("Mango", 60, 0.8, 15.0, 0.4, ""),
    ("Sandía", 30, 0.6, 7.6, 0.2, "sandia"),
    ("Melón", 34, 0.8, 8.2, 0.2, "melon"),
    ("Papaya", 43, 0.5, 10.8, 0.3, ""),
    ("Aguacate", 160, 2.0, 8.5, 14.7, "palta"),
    ("Granada", 83, 1.7, 18.7, 1.2, ""),
    ("Higos", 74, 0.8, 19.2, 0.3, "higo"),
    ("Dátiles", 282, 2.5, 75.0, 0.4, "datiles datil"),
    ("Pasas", 299, 3.1, 79.2, 0.5, "uvas pasas"),
    # ---- Verduras y hortalizas ----
    ("Tomate", 18, 0.9, 3.9, 0.2, "tomates"),
    ("Lechuga", 15, 1.4, 2.9, 0.2, "ensalada"),
    ("Espinacas", 23, 2.9, 3.6, 0.4, "espinaca"),
    ("Brócoli", 34, 2.8, 6.6, 0.4, "brocoli"),
    ("Coliflor", 25, 1.9, 5.0, 0.3, ""),
    ("Zanahoria", 41, 0.9, 9.6, 0.2, "zanahorias"),
    ("Pepino", 15, 0.7, 3.6, 0.1, ""),
    ("Pimiento rojo", 31, 1.0, 6.0, 0.3, "pimiento"),
    ("Pimiento verde", 20, 0.9, 4.6, 0.2, "pimiento"),
    ("Cebolla", 40, 1.1, 9.3, 0.1, ""),
    ("Ajo", 149, 6.4, 33.0, 0.5, ""),
    ("Calabacín", 17, 1.2, 3.1, 0.3, "calabacin"),
    ("Berenjena", 25, 1.0, 5.9, 0.2, ""),
    ("Champiñones", 22, 3.1, 3.3, 0.3, "champinones setas hongos"),
    ("Judías verdes", 31, 1.8, 7.0, 0.2, "judias verdes ejotes"),
    ("Espárragos", 20, 2.2, 3.9, 0.1, "esparragos"),
    ("Alcachofa", 47, 3.3, 10.5, 0.2, "alcachofas"),
    ("Puerro", 61, 1.5, 14.2, 0.3, ""),
    ("Calabaza", 26, 1.0, 6.5, 0.1, ""),
    ("Remolacha", 43, 1.6, 9.6, 0.2, "betabel"),
    ("Repollo", 25, 1.3, 5.8, 0.1, "col"),
    ("Coles de Bruselas", 43, 3.4, 9.0, 0.3, "col de bruselas"),
    ("Apio", 16, 0.7, 3.0, 0.2, ""),
    ("Maíz dulce", 86, 3.3, 19.0, 1.4, "maiz"),
    ("Guisantes", 81, 5.4, 14.5, 0.4, "chicharos arvejas"),
    ("Patata cocida", 87, 1.9, 20.1, 0.1, "papa patatas cocidas"),
    ("Patata cruda", 77, 2.0, 17.5, 0.1, "papa patatas"),
    ("Boniato", 86, 1.6, 20.1, 0.1, "batata camote"),
    ("Tomate triturado", 24, 1.3, 4.2, 0.2, "salsa de tomate"),
    ("Aceitunas", 145, 1.0, 3.8, 15.3, "aceituna olivas"),
    # ---- Cereales, pan y pasta ----
    ("Arroz blanco (cocido)", 130, 2.7, 28.0, 0.3, "arroz"),
    ("Arroz blanco (crudo)", 350, 7.0, 78.0, 1.0, "arroz"),
    ("Arroz integral (cocido)", 112, 2.3, 23.5, 0.8, "arroz"),
    ("Pasta (cocida)", 158, 5.8, 30.9, 0.9, "macarrones espaguetis fideos tallarines"),
    ("Pasta (cruda)", 359, 12.5, 74.0, 1.5, "macarrones espaguetis fideos tallarines"),
    ("Pan blanco", 265, 9.0, 49.0, 3.2, "barra pan"),
    ("Pan integral", 247, 13.0, 41.0, 3.4, "pan"),
    ("Pan de molde", 265, 8.0, 49.0, 3.0, "pan bimbo"),
    ("Copos de Avena", 389, 16.9, 66.3, 6.9, "avena"),
    ("Cuscús (cocido)", 112, 3.8, 23.2, 0.2, "cuscus"),
    ("Quinoa (cocida)", 120, 4.4, 21.3, 1.9, ""),
    ("Harina de trigo", 364, 10.0, 76.0, 1.0, ""),
    ("Cereales de desayuno", 357, 7.5, 84.0, 0.4, "corn flakes cereales"),
    ("Galletas María", 430, 7.0, 75.0, 11.0, "galletas"),
    ("Tortilla de trigo (wrap)", 310, 8.0, 52.0, 7.0, "wrap tortita"),
    ("Tortitas de arroz", 387, 8.0, 82.0, 2.8, ""),
    ("Palomitas", 387, 13.0, 78.0, 4.5, ""),
    # ---- Legumbres y vegetales proteicos ----
    ("Lentejas (cocidas)", 116, 9.0, 20.0, 0.4, "lenteja"),
    ("Garbanzos (cocidos)", 164, 8.9, 27.4, 2.6, "garbanzo"),
    ("Alubias (cocidas)", 127, 8.7, 22.8, 0.5, "judias frijoles habichuelas"),
    ("Hummus", 166, 8.0, 14.3, 9.6, "humus"),
    ("Tofu", 76, 8.0, 1.9, 4.8, ""),
    ("Tempeh", 192, 20.0, 7.6, 11.0, ""),
    ("Seitán", 120, 25.0, 4.0, 1.9, "seitan"),
    # ---- Carnes y embutidos ----
    ("Pechuga de Pollo", 105, 23.0, 0.0, 1.2, "pollo"),
    ("Muslo de Pollo", 177, 20.0, 0.0, 10.5, "pollo"),
    ("Pechuga de Pavo", 104, 24.0, 0.0, 1.0, "pavo"),
    ("Ternera magra", 143, 21.4, 0.0, 6.0, "ternera filete vacuno carne"),
    ("Carne picada", 215, 18.0, 0.0, 15.0, "picada ternera hamburguesa"),
    ("Lomo de cerdo", 143, 21.0, 0.0, 6.0, "cerdo"),
    ("Cordero (pierna)", 190, 20.0, 0.0, 12.0, ""),
    ("Conejo", 136, 20.0, 0.0, 5.5, ""),
    ("Jamón serrano", 241, 31.0, 0.0, 13.0, "jamon"),
    ("Jamón cocido", 126, 19.0, 1.0, 5.0, "jamon york"),
    ("Chorizo", 455, 24.0, 2.0, 38.0, ""),
    ("Bacon", 417, 12.6, 1.4, 40.0, "beicon"),
    ("Salchichas", 290, 11.0, 3.0, 26.0, "salchicha frankfurt"),
    # ---- Pescados y mariscos ----
    ("Salmón", 208, 20.0, 0.0, 13.0, "salmon"),
    ("Atún al Natural", 103, 24.0, 0.0, 1.0, "atun lata"),
    ("Atún en Aceite", 190, 22.0, 0.0, 11.0, "atun lata"),
    ("Atún fresco", 144, 23.0, 0.0, 4.9, "atun"),
    ("Merluza", 82, 17.8, 0.0, 0.9, "pescado blanco"),
    ("Bacalao", 82, 18.0, 0.0, 0.7, "pescado blanco"),
    ("Lubina", 97, 18.0, 0.0, 2.0, "pescado"),
    ("Dorada", 100, 19.0, 0.0, 2.5, "pescado"),
    ("Trucha", 119, 20.0, 0.0, 3.5, "pescado"),
    ("Caballa", 205, 19.0, 0.0, 14.0, "pescado"),
    ("Sardinas en aceite", 208, 24.6, 0.0, 11.5, "sardina lata"),
    ("Anchoas en aceite", 210, 29.0, 0.0, 10.0, "anchoa boqueron"),
    ("Gambas", 99, 24.0, 0.2, 0.3, "gamba langostino marisco"),
    ("Mejillones", 86, 12.0, 3.7, 2.2, "marisco"),
    ("Calamares", 92, 15.6, 3.1, 1.4, "calamar chipirones marisco"),
    ("Pulpo", 82, 15.0, 2.2, 1.0, "marisco"),
    # ---- Huevos y lácteos ----
    ("Huevo Entero", 155, 13.0, 1.1, 11.0, "huevos"),
    ("Claras de Huevo", 52, 11.0, 0.7, 0.2, "clara huevo"),
    ("Yema de Huevo", 322, 16.0, 3.6, 27.0, "yema huevo"),
    ("Leche entera", 61, 3.2, 4.8, 3.3, "leche"),
    ("Leche semidesnatada", 46, 3.3, 4.8, 1.6, "leche"),
    ("Leche desnatada", 34, 3.4, 5.0, 0.1, "leche"),
    ("Yogur natural", 61, 3.5, 4.7, 3.3, "yogurt"),
    ("Yogur griego", 97, 9.0, 4.0, 5.0, "yogurt"),
    ("Yogur desnatado", 45, 4.3, 6.0, 0.2, "yogurt"),
    ("Skyr", 63, 11.0, 4.0, 0.2, "yogur proteico"),
    ("Queso cottage", 98, 11.0, 3.4, 4.3, ""),
    ("Queso fresco", 174, 12.0, 3.0, 12.5, "burgos"),
    ("Mozzarella", 280, 18.0, 2.2, 22.0, ""),
    ("Queso curado", 400, 26.0, 0.5, 33.0, "manchego"),
    ("Queso emmental", 380, 28.0, 0.5, 29.0, ""),
    ("Parmesano", 431, 38.0, 4.1, 29.0, "queso"),
    ("Queso crema", 342, 6.0, 4.0, 34.0, "philadelphia"),
    ("Mantequilla", 717, 0.9, 0.1, 81.0, ""),
    ("Bebida de soja", 33, 3.3, 1.0, 1.8, "leche de soja"),
    ("Bebida de almendras", 24, 0.9, 0.1, 1.9, "leche de almendras"),
    ("Bebida de avena", 43, 0.3, 7.0, 1.5, "leche de avena"),
    # ---- Frutos secos y semillas ----
    ("Almendras", 579, 21.0, 22.0, 50.0, "frutos secos"),
    ("Nueces", 654, 15.0, 14.0, 65.0, "frutos secos"),
    ("Cacahuetes", 567, 26.0, 16.0, 49.0, "mani frutos secos"),
    ("Avellanas", 628, 15.0, 17.0, 61.0, "frutos secos"),
    ("Pistachos", 560, 20.0, 28.0, 45.0, "frutos secos"),
    ("Anacardos", 553, 18.0, 30.0, 44.0, "frutos secos"),
    ("Mantequilla de cacahuete", 588, 25.0, 20.0, 50.0, "crema de cacahuete"),
    ("Semillas de chía", 486, 17.0, 42.0, 31.0, "chia"),
    ("Semillas de lino", 534, 18.0, 29.0, 42.0, "linaza"),
    ("Pipas de girasol", 584, 21.0, 20.0, 51.0, "semillas"),
    # ---- Aceites, salsas y dulces ----
    ("Aceite de oliva", 884, 0.0, 0.0, 100.0, "aove"),
    ("Aceite de girasol", 884, 0.0, 0.0, 100.0, ""),
    ("Mayonesa", 680, 1.0, 0.6, 75.0, ""),
    ("Ketchup", 112, 1.7, 26.0, 0.1, "kétchup"),
    ("Azúcar", 387, 0.0, 100.0, 0.0, "azucar"),
    ("Miel", 304, 0.3, 82.0, 0.0, ""),
    ("Mermelada", 250, 0.4, 60.0, 0.1, "confitura"),
    ("Chocolate negro", 598, 7.8, 45.9, 42.6, "chocolate 70"),
    ("Chocolate con leche", 535, 7.6, 59.0, 30.0, ""),
    ("Cacao en polvo", 228, 19.6, 57.9, 13.7, "cacao"),
    ("Proteína whey (polvo)", 400, 80.0, 8.0, 6.0, "proteina suero batido"),
    # ---- Bebidas y otros ----
    ("Zumo de naranja", 45, 0.7, 10.4, 0.2, "zumo jugo"),
    ("Cerveza", 43, 0.5, 3.6, 0.0, ""),
    ("Vino tinto", 85, 0.1, 2.6, 0.0, "vino"),
    ("Refresco de cola", 42, 0.0, 10.6, 0.0, "coca cola"),
    ("Café solo", 2, 0.1, 0.0, 0.0, "cafe"),
    ("Tortilla de patatas", 170, 6.5, 12.0, 11.0, "tortilla española"),
    ("Patatas fritas (bolsa)", 536, 7.0, 53.0, 34.0, "papas chips"),
    ("Pizza margarita", 266, 11.0, 33.0, 10.0, ""),
]


def norm_text(txt):
    """Minúsculas y sin acentos, para comparar búsquedas ('platano' = 'Plátano')."""
    import unicodedata

    t = unicodedata.normalize("NFD", str(txt or "").lower())
    return "".join(ch for ch in t if unicodedata.category(ch) != "Mn").strip()


def _word_match(tok, words):
    """El término coincide con el inicio de una palabra ('pollo' no coincide con 'repollo');
    también acepta plurales ('mandarinas' coincide con 'mandarina')."""
    return any(w.startswith(tok) or (len(w) >= 4 and tok.startswith(w)) for w in words)


def search_basic_foods(query, limit=10):
    """Alimentos básicos que coinciden con la búsqueda, los más exactos primero."""
    q = norm_text(query)
    if not q:
        return []
    toks = q.split()
    out = []
    for idx, (name, k, p, c, f, alias) in enumerate(BASIC_FOODS):
        n = norm_text(name)
        name_words = n.replace("(", " ").replace(")", " ").split()
        alias_words = norm_text(alias).split()
        if not all(_word_match(t, name_words + alias_words) for t in toks):
            continue
        in_name = all(_word_match(t, name_words) for t in toks)
        if n == q or (len(toks) == 1 and name_words[:1] == toks):
            score = 100 if n == q else 90
        elif n.startswith(q):
            score = 80
        elif in_name:
            score = 60
        else:
            score = 30  # solo coincide por sinónimo
        out.append((score - idx * 0.001, {"name": name, "kcal": k, "p": p, "c": c, "f": f}))
    out.sort(key=lambda x: -x[0])
    return [o[1] for o in out[:limit]]


# Palabras que delatan productos elaborados: se penalizan salvo que el usuario las busque
OFF_PENALTY = {
    "zumo", "jugo", "nectar", "bebida", "refresco", "mermelada", "yogur", "yogurt", "helado", "batido",
    "sirope", "licor", "gominolas", "gominola", "caramelo", "caramelos", "galleta", "galletas",
    "bizcocho", "tarta", "compota", "sabor", "aroma", "gelatina", "postre", "snack", "barrita",
    "mermeladas", "cereales", "infusion", "te", "smoothie", "pure", "conserva", "almibar",
}


def rank_off_products(query, products, limit=6):
    """Ordena resultados de OpenFoodFacts: nombres que contienen la búsqueda, cortos y sin elaborados."""
    q = norm_text(query)
    toks = q.split()
    penalty = OFF_PENALTY - set(toks)
    scored, seen = [], set()
    for prod in products:
        name = (prod.get("product_name_es") or prod.get("product_name") or "").strip()
        if not name:
            continue
        nutr = off_nutri(prod.get("nutriments"))
        if nutr[0] <= 0 and sum(nutr[1:]) <= 0:
            continue
        n = norm_text(name)
        if n in seen:
            continue
        seen.add(n)
        words = n.split()
        has_all = all(t in n for t in toks)
        score = 0
        if n == q:
            score += 100
        elif n.startswith(q):
            score += 40
        if words and words[0] == toks[0]:
            score += 20
        if has_all:
            score += 30
        if any(w in penalty for w in words):
            score -= 60
        score -= len(words)
        scored.append((has_all, score, prod, name, nutr))
    scored.sort(key=lambda x: (-int(x[0]), -x[1]))
    chosen = [x for x in scored if x[0]] or scored[:3]
    return [
        {
            "name": name,
            "kcal": nutr[0], "p": nutr[1], "c": nutr[2], "f": nutr[3],
            "image": prod.get("image_front_small_url") or prod.get("image_url"),
        }
        for _, _, prod, name, nutr in chosen[:limit]
    ]


def off_search(query):
    """Busca en OpenFoodFacts (productos de España primero) y devuelve los mejores resultados."""
    base = {
        "search_terms": query, "search_simple": 1, "action": "process", "json": 1,
        "page_size": 30, "sort_by": "unique_scans_n", "lc": "es",
        "fields": "product_name,product_name_es,nutriments,image_front_small_url,image_url",
    }
    headers = {"User-Agent": "GymMacroProApp - Mobile - Version 3.0"}
    results = []
    for extra in (
        {"tagtype_0": "countries", "tag_contains_0": "contains", "tag_0": "spain"},
        {},
    ):
        try:
            resp = requests.get(
                "https://world.openfoodfacts.org/cgi/search.pl",
                params={**base, **extra}, headers=headers, timeout=7,
            ).json()
            results = rank_off_products(query, resp.get("products", []))
        except Exception:
            results = []
        if len(results) >= 3:
            break
    return results


def lookup_product(code):
    """Busca un código en OpenFoodFacts: dict del producto, None si no existe (o error de red)."""
    cands = [code]
    if len(code) == 12:
        cands.append("0" + code)  # UPC-A guardado como EAN-13
    if len(code) == 13 and code.startswith("0"):
        cands.append(code[1:])
    for cand in cands:
        resp = requests.get(
            f"https://world.openfoodfacts.org/api/v2/product/{cand}.json",
            params={"fields": "product_name,nutriments,image_front_url,image_url"},
            headers={"User-Agent": "GymMacroProApp - Mobile - Version 3.0"},
            timeout=8,
        )
        data = resp.json()
        if data.get("status") == 1 and data.get("product"):
            prod = data["product"]
            k, p, c, f = off_nutri(prod.get("nutriments"))
            return {
                "name": prod.get("product_name") or "Producto escaneado",
                "kcal": k, "p": p, "c": c, "f": f,
                "image": prod.get("image_front_url") or prod.get("image_url"),
            }
    return None


# ==========================================
# APLICACIÓN MÓVIL MULTIUSUARIO
# ==========================================
def main(page: ft.Page):
    db = DatabaseManager(os.getenv("DB_PATH", "gym_macros_mobile.db"))

    page.title = "GymMacro"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = "#F4F1EA"
    page.theme = ft.Theme(color_scheme=ft.ColorScheme(primary="#24543F"))
    page.padding = 0

    try:
        page.window.width = 420
        page.window.height = 880
    except Exception:
        pass

    OFF_HEADERS = {
        "User-Agent": "GymMacroProApp - Mobile - Version 3.0"
    }

    CARD_SHADOW = None  # tarjetas planas: solo borde fino
    CARD_BORDER = ft.Border.all(1, "#E4DFD3")

    # Estado global de sesión
    current_user_id = [None]
    on_new_day = [None]  # callback que se ejecuta cuando cambia el día (00:00)
    selected_meal_for_add = ["Desayuno"]
    expanded_meal = [None]
    expanded_groups = set()  # ids de grupos (Mis comidas) con el desglose abierto en Hoy

    MEALS_META = {
        "Desayuno": {"icon": ft.Icons.FREE_BREAKFAST_OUTLINED, "ratio": 0.30},
        "Almuerzo": {"icon": ft.Icons.LUNCH_DINING_OUTLINED, "ratio": 0.40},
        "Cena": {"icon": ft.Icons.DINNER_DINING_OUTLINED, "ratio": 0.25},
        "Snacks": {"icon": ft.Icons.COOKIE_OUTLINED, "ratio": 0.05},
    }

    # Banner de notificaciones flotante
    status_text = ft.Text("", color="white", size=13, weight=ft.FontWeight.W_600)
    status_banner = ft.Container(
        content=ft.Row([status_text], alignment=ft.MainAxisAlignment.CENTER),
        bgcolor="#3F7D58",
        padding=12,
        visible=False,
    )

    def notify(msg, color="#24543F"):
        status_text.value = msg
        status_banner.bgcolor = color
        status_banner.visible = True
        page.update()

        def hide_later():
            time.sleep(2.4)
            status_banner.visible = False
            page.update()

        threading.Thread(target=hide_later, daemon=True).start()

    def make_button(text, bg_color, on_click_fn, text_color="white", expand=False, icon=None):
        ic, label, trailing = split_btn_icon(text)
        ic = icon or ic
        parts = [ft.Text(label, color=text_color, weight=ft.FontWeight.W_600, size=14)]
        if ic:
            icon_ctl = ft.Icon(ic, size=18, color=text_color)
            parts = parts + [icon_ctl] if trailing else [icon_ctl] + parts
        return ft.Container(
            bgcolor=bg_color,
            border_radius=10,
            padding=ft.Padding(left=16, right=16, top=14, bottom=14),
            expand=expand,
            on_click=on_click_fn,
            ink=True,
            content=ft.Row(parts, alignment=ft.MainAxisAlignment.CENTER, spacing=8),
        )

    # Contenedor raíz que alterna entre: Selector de Usuarios / Onboarding / App Principal
    root_container = ft.Container(expand=True)

    # ==========================================
    # PANTALLA A: SELECTOR DE PERFILES (MULTIUSUARIO)
    # ==========================================
    def show_user_selector(e=None):
        on_new_day[0] = None
        users = db.get_all_users()
        if not users:
            # Si no hay usuarios creados, abrimos directamente el asistente dinámico
            show_onboarding_wizard(is_first_user=True)
            return

        cards_col = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)

        for u in users:
            uid, uname, uavatar, uw, uh, uage, ugen, uact, ugoal, _, ukcal, _, _, _, _ = u
            cards_col.controls.append(
                ft.Container(
                    bgcolor="#ffffff",
                    border_radius=12,
                    padding=16,
                    shadow=CARD_SHADOW, border=CARD_BORDER,
                    on_click=lambda e, i=uid: login_as_user(i),
                    content=ft.Row(
                        [
                            avatar_chip(uname, 52),
                            ft.Container(width=6),
                            ft.Column(
                                [
                                    ft.Text(
                                        uname,
                                        size=18,
                                        weight=ft.FontWeight.W_600,
                                        color="#1C1B18",
                                    ),
                                    ft.Text(
                                        f"{ugoal} • {int(ukcal)} kcal/día",
                                        size=13,
                                        color="#24543F",
                                        weight=ft.FontWeight.W_600,
                                    ),
                                    ft.Text(
                                        f"{uage} años • {int(uw)} kg • {int(uh)} cm",
                                        size=12,
                                        color="#77736A",
                                    ),
                                ],
                                spacing=2,
                                expand=True,
                            ),
                            ft.Container(
                                padding=8,
                                on_click=lambda e, i=uid: delete_user_profile(i),
                                content=ft.Icon(ft.Icons.DELETE_OUTLINE, size=20, color="#A9A59B"),
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                )
            )

        root_container.content = ft.Container(
            padding=24,
            expand=True,
            content=ft.Column(
                [
                    ft.Container(height=20),
                    ft.Text("PERFILES", size=11, color="#77736A", weight=ft.FontWeight.W_600),
                    ft.Text(
                        "Elige tu perfil",
                        size=28,
                        weight=ft.FontWeight.W_600,
                        color="#1C1B18",
                    ),
                    ft.Text(
                        "Toca tu perfil para entrar o crea uno nuevo.",
                        size=13,
                        color="#77736A",
                    ),
                    ft.Container(height=8),
                    cards_col,
                    make_button(
                        "Crear perfil",
                        "#24543F",
                        lambda e: show_onboarding_wizard(is_first_user=False),
                    ),
                    ft.Container(
                        alignment=ft.Alignment.CENTER,
                        content=ft.Text("Creado por ismael_prd", size=11, color="#A9A59B"),
                    ),
                    ft.Container(height=8),
                ],
                expand=True,
                spacing=8,
            ),
        )
        page.update()

    def delete_user_profile(uid):
        db.delete_user(uid)
        notify("Perfil eliminado", "#B8453A")
        show_user_selector()

    def login_as_user(uid):
        current_user_id[0] = uid
        open_main_app()

    # ==========================================
    # PANTALLA B: ONBOARDING SUPERDINÁMICO PASO A PASO
    # ==========================================
    def show_onboarding_wizard(is_first_user=False, edit_user_data=None):
        step = [0]  # 0: Identidad, 1: Biometría, 2: Actividad, 3: Objetivo + Resumen

        act_text0, act_mult0 = (
            db.get_activity_extra(edit_user_data[0]) if edit_user_data else ("", None)
        )

        # Estado temporal del formulario dinámico
        wiz_state = {
            "id": edit_user_data[0] if edit_user_data else None,
            "name": edit_user_data[1] if edit_user_data else "",
            "avatar": edit_user_data[2] if edit_user_data else "💪",
            "weight": float(edit_user_data[3]) if edit_user_data else 72.0,
            "height": float(edit_user_data[4]) if edit_user_data else 175.0,
            "age": int(edit_user_data[5]) if edit_user_data else 24,
            "gender": edit_user_data[6] if edit_user_data else "Hombre",
            "activity": edit_user_data[7] if edit_user_data else "Moderado",
            # Ajuste calórico (kcal) sobre el mantenimiento: se elige con la barra deslizante
            "offset": float(edit_user_data[9] or 0.0) if edit_user_data else 0.0,
            # Actividad descrita con palabras y analizada por la IA
            "act_text": act_text0,
            "act_mult": act_mult0,
            "act_kcal": None,
            "act_summary": "",
            "act_busy": False,
            # Reparto de macros (porcentajes): preset elegido o personalizado
            "macro_preset": None,  # None = sugerido según el objetivo
            "split_p": 30,
            "split_c": 40,
        }

        def current_preset():
            return wiz_state["macro_preset"] or recommended_preset(wiz_state["offset"])

        def current_split():
            key = current_preset()
            if key == "custom":
                p, c = wiz_state["split_p"], wiz_state["split_c"]
                return (p, c, 100 - p - c)
            return MACRO_PRESETS[key]["split"]

        AVATARS = ["💪", "🔥", "⚡", "🏋️", "🧘", "🏃", "🥗", "🏆"]

        wiz_body = ft.Container(expand=True)
        progress_bar = ft.ProgressBar(
            value=0.25, color="#24543F", bgcolor="#E4DFD3", height=8
        )
        step_title_lbl = ft.Text("Paso 1 de 4", size=13, color="#24543F", weight=ft.FontWeight.W_600)

        tf_wiz_name = ft.TextField(
            label="Tu nombre o apodo",
            value=wiz_state["name"],
            bgcolor="#ffffff",
            border_radius=10,
            border_color="#D6D0C2",
            on_change=lambda e: wiz_state.update({"name": e.control.value}),
        )

        tf_wiz_act = ft.TextField(
            label="¿Qué actividad haces y con qué frecuencia?",
            hint_text="Ej: Gimnasio 4 días/semana (1 h de pesas), camino 30 min al día, trabajo sentado",
            value=wiz_state["act_text"] or "",
            multiline=True,
            min_lines=3,
            max_lines=5,
            bgcolor="#ffffff",
            border_radius=10,
            border_color="#D6D0C2",
            on_change=lambda e: wiz_state.update({"act_text": e.control.value}),
        )

        def change_metric(key, delta, min_v, max_v):
            val = wiz_state[key] + delta
            wiz_state[key] = max(min_v, min(max_v, val))
            render_wizard_step()

        def create_stepper_card(title, unit, key, min_v, max_v, is_int=False):
            val_str = (
                f"{int(wiz_state[key])}"
                if is_int
                else f"{wiz_state[key]:.1f}".replace(".0", "")
            )
            return ft.Container(
                bgcolor="#ffffff",
                border_radius=12,
                padding=16,
                shadow=CARD_SHADOW, border=CARD_BORDER,
                content=ft.Column(
                    [
                        ft.Text(title, size=13, color="#77736A", weight=ft.FontWeight.W_600),
                        ft.Row(
                            [
                                ft.Container(
                                    bgcolor="#F4F1EA",
                                    border_radius=8,
                                    padding=10,
                                    on_click=lambda e: change_metric(
                                        key, -5, min_v, max_v
                                    ),
                                    content=ft.Text(
                                        "-5", weight=ft.FontWeight.W_600, color="#5A574F"
                                    ),
                                ),
                                ft.Container(
                                    bgcolor="#E3ECE6",
                                    border_radius=8,
                                    padding=12,
                                    on_click=lambda e: change_metric(
                                        key, -1, min_v, max_v
                                    ),
                                    content=ft.Text(
                                        "−",
                                        size=18,
                                        weight=ft.FontWeight.W_600,
                                        color="#24543F",
                                    ),
                                ),
                                ft.Column(
                                    [
                                        ft.Text(
                                            val_str,
                                            size=28,
                                            weight=ft.FontWeight.W_600,
                                            color="#1C1B18",
                                        ),
                                        ft.Text(unit, size=12, color="#77736A"),
                                    ],
                                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                    spacing=0,
                                    expand=True,
                                ),
                                ft.Container(
                                    bgcolor="#E3ECE6",
                                    border_radius=8,
                                    padding=12,
                                    on_click=lambda e: change_metric(
                                        key, 1, min_v, max_v
                                    ),
                                    content=ft.Text(
                                        "+",
                                        size=18,
                                        weight=ft.FontWeight.W_600,
                                        color="#24543F",
                                    ),
                                ),
                                ft.Container(
                                    bgcolor="#F4F1EA",
                                    border_radius=8,
                                    padding=10,
                                    on_click=lambda e: change_metric(
                                        key, 5, min_v, max_v
                                    ),
                                    content=ft.Text(
                                        "+5", weight=ft.FontWeight.W_600, color="#5A574F"
                                    ),
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=8,
                ),
            )

        def render_wizard_step():
            s = step[0]
            progress_bar.value = (s + 1) / 4.0
            step_title_lbl.value = f"Paso {s + 1} de 4"

            if s == 0:
                # PASO 1: Nombre, Avatar y Sexo
                avatar_row = ft.Row(spacing=8, wrap=True)
                for av in AVATARS:
                    active = wiz_state["avatar"] == av
                    avatar_row.controls.append(
                        ft.Container(
                            width=50,
                            height=50,
                            border_radius=12,
                            bgcolor="#24543F" if active else "#ffffff",
                            on_click=lambda e, a=av: (
                                wiz_state.update({"avatar": a}),
                                render_wizard_step(),
                            ),
                            content=ft.Row(
                                [ft.Text(av, size=24)],
                                alignment=ft.MainAxisAlignment.CENTER,
                            ),
                        )
                    )

                gender_cards = ft.Row(spacing=12)
                for g_name, g_icon in [("Hombre", ft.Icons.MALE), ("Mujer", ft.Icons.FEMALE)]:
                    active = wiz_state["gender"] == g_name
                    gender_cards.controls.append(
                        ft.Container(
                            expand=True,
                            padding=20,
                            border_radius=12,
                            bgcolor="#24543F" if active else "#ffffff",
                            shadow=CARD_SHADOW, border=CARD_BORDER,
                            on_click=lambda e, gn=g_name: (
                                wiz_state.update({"gender": gn}),
                                render_wizard_step(),
                            ),
                            content=ft.Column(
                                [
                                    ft.Icon(g_icon, size=30, color="white" if active else "#1C1B18"),
                                    ft.Text(
                                        g_name,
                                        size=16,
                                        weight=ft.FontWeight.W_600,
                                        color="white" if active else "#1C1B18",
                                    ),
                                ],
                                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                spacing=6,
                            ),
                        )
                    )

                wiz_body.content = ft.Column(
                    [
                        ft.Text(
                            "Tu perfil",
                            size=26,
                            weight=ft.FontWeight.W_600,
                            color="#1C1B18",
                        ),
                        ft.Text(
                            "Con estos datos calculamos tus calorías y macros.",
                            size=13,
                            color="#77736A",
                        ),
                        ft.Container(height=6),
                        tf_wiz_name,
                        ft.Text(
                            "Selecciona tu sexo biológico:",
                            size=13,
                            weight=ft.FontWeight.W_600,
                            color="#77736A",
                        ),
                        gender_cards,
                    ],
                    scroll=ft.ScrollMode.AUTO,
                    spacing=14,
                )

            elif s == 1:
                # PASO 2: Edad, Peso y Altura superdinámicos
                wiz_body.content = ft.Column(
                    [
                        ft.Text(
                            "Tus medidas corporales",
                            size=26,
                            weight=ft.FontWeight.W_600,
                            color="#1C1B18",
                        ),
                        ft.Text(
                            "Ajusta tus medidas.",
                            size=13,
                            color="#77736A",
                        ),
                        create_stepper_card(
                            "EDAD", "años", "age", 12, 95, is_int=True
                        ),
                        create_stepper_card(
                            "PESO ACTUAL", "kg", "weight", 35.0, 220.0
                        ),
                        create_stepper_card(
                            "ALTURA", "cm", "height", 120.0, 230.0, is_int=True
                        ),
                    ],
                    scroll=ft.ScrollMode.AUTO,
                    spacing=12,
                )

            elif s == 2:
                # PASO 3: Actividad física (texto libre analizado por la IA, o niveles rápidos)
                custom = bool(wiz_state["act_mult"])

                def pick_level(an):
                    wiz_state.update(
                        {"activity": an, "act_mult": None, "act_kcal": None, "act_summary": "", "act_text": ""}
                    )
                    tf_wiz_act.value = ""
                    render_wizard_step()

                act_col = ft.Column(spacing=10)
                for act_name, meta in ACT_MULTIPLIERS.items():
                    active = (not custom) and wiz_state["activity"] == act_name
                    act_col.controls.append(
                        ft.Container(
                            bgcolor="#24543F" if active else "#ffffff",
                            padding=14,
                            border_radius=12,
                            shadow=CARD_SHADOW, border=CARD_BORDER,
                            on_click=lambda e, an=act_name: pick_level(an),
                            content=ft.Row(
                                [
                                    ft.Icon(meta["icon"], size=22, color="white" if active else "#24543F"),
                                    ft.Container(width=8),
                                    ft.Column(
                                        [
                                            ft.Text(
                                                act_name, size=15, weight=ft.FontWeight.W_600,
                                                color="white" if active else "#1C1B18",
                                            ),
                                            ft.Text(
                                                meta["desc"], size=12,
                                                color="#CFE0D5" if active else "#77736A",
                                            ),
                                        ],
                                        spacing=2,
                                        expand=True,
                                    ),
                                ]
                            ),
                        )
                    )

                ai_card = ft.Container()
                if custom:
                    ai_card = ft.Container(
                        bgcolor="#E3ECE6",
                        border_radius=12,
                        padding=14,
                        content=ft.Column(
                            [
                                ft.Text(
                                    f"Factor de actividad calculado: ×{wiz_state['act_mult']:.2f}",
                                    size=15, weight=ft.FontWeight.W_600, color="#24543F",
                                ),
                                ft.Text(wiz_state["act_summary"] or "Calculado a partir de tu descripción.", size=12, color="#3A3832"),
                                ft.Text(
                                    f"Gasto medio por ejercicio: ~{int(wiz_state['act_kcal'] or 0)} kcal/día"
                                    if wiz_state["act_kcal"] is not None
                                    else "Se usará para calcular tus calorías.",
                                    size=12, color="#77736A",
                                ),
                            ],
                            spacing=4,
                        ),
                    )

                wiz_body.content = ft.Column(
                    [
                        ft.Text("Tu actividad física", size=26, weight=ft.FontWeight.W_600, color="#1C1B18"),
                        ft.Text(
                            "Cuéntame qué haces y cada cuánto. La IA lo tendrá en cuenta para calcular tus calorías.",
                            size=13, color="#77736A",
                        ),
                        tf_wiz_act,
                        make_button("✨ Calcular con IA", "#1C1B18", analyze_activity),
                        ft.Row(
                            [
                                ft.ProgressRing(width=18, height=18, stroke_width=3),
                                ft.Text("Analizando tu actividad…", size=13, color="#77736A"),
                            ],
                            visible=bool(wiz_state["act_busy"]),
                        ),
                        ai_card,
                        ft.Text("O elige un nivel rápido:", size=13, weight=ft.FontWeight.W_600, color="#77736A"),
                        act_col,
                    ],
                    scroll=ft.ScrollMode.AUTO,
                    spacing=12,
                )

            elif s == 3:
                # PASO 4: Objetivo con barra deslizante + cálculo instantáneo
                w_, h_, a_, g_ = wiz_state["weight"], wiz_state["height"], wiz_state["age"], wiz_state["gender"]
                bmr = compute_bmr(w_, h_, a_, g_)
                mult = activity_multiplier(wiz_state["activity"], wiz_state["act_mult"])
                tdee = bmr * mult

                lbl_goal = ft.Text("", size=22, weight=ft.FontWeight.W_600)
                lbl_goal_off = ft.Text("", size=13, weight=ft.FontWeight.W_600, color="#3A3832")
                lbl_goal_week = ft.Text("", size=12, color="#77736A")
                lbl_goal_adv = ft.Text("", size=12, color="#77736A")
                lbl_warn = ft.Text("", size=12, color="#B8453A", visible=False)
                lbl_kcal = ft.Text("", size=34, weight=ft.FontWeight.W_600, color="white")
                lbl_tdee = ft.Text("", size=12, color="#A9A59B")
                lbl_tc = ft.Text("", size=18, weight=ft.FontWeight.W_600, color="#E3B965")
                lbl_tp = ft.Text("", size=18, weight=ft.FontWeight.W_600, color="#3F7D58")
                lbl_tf = ft.Text("", size=18, weight=ft.FontWeight.W_600, color="#C9763F")
                lbl_split = ft.Text("", size=12, color="#A9A59B")
                lbl_pkg = ft.Text("", size=12, color="#A9A59B")
                lbl_macro_warn = ft.Text("", size=12, color="#B8453A", visible=False)

                def refresh_goal():
                    off = wiz_state["offset"]
                    tkcal, tp, tc, tf, _ = compute_macros(
                        w_, h_, a_, g_, wiz_state["activity"], "", offset=off,
                        act_mult=wiz_state["act_mult"], split=current_split(),
                    )
                    pct_p = round(tp * 4 * 100 / tkcal) if tkcal else 0
                    pct_c = round(tc * 4 * 100 / tkcal) if tkcal else 0
                    pct_f = max(0, 100 - pct_p - pct_c)
                    lbl_split.value = f"Carbos {pct_c}% • Proteína {pct_p}% • Grasas {pct_f}%"
                    lbl_pkg.value = f"Proteína: {tp / w_:.1f} g/kg • Grasas: {tf / w_:.1f} g/kg"
                    warns = []
                    if pct_f < 20:
                        warns.append("Grasa por debajo del 20 %: fuera del rango recomendado (IOM, 2005).")
                    if tp / w_ < 1.4:
                        warns.append("Proteína por debajo de 1,4 g/kg: baja para quien entrena fuerza.")
                    if tp / w_ > 3.0:
                        warns.append("Proteína por encima de 3 g/kg: no hay beneficio extra demostrado.")
                    lbl_macro_warn.value = " ".join(warns)
                    lbl_macro_warn.visible = bool(warns)
                    title, color, advice = describe_offset(off)
                    lbl_goal.value = title
                    lbl_goal.color = color
                    lbl_goal_off.value = (
                        "Sin ajuste sobre tu mantenimiento" if abs(off) < 50 else f"{int(off):+d} kcal sobre tu mantenimiento"
                    )
                    wk = weekly_change_kg(off)
                    lbl_goal_week.value = (
                        "" if abs(off) < 50
                        else f"≈ {abs(wk):.2f} kg/semana {'perdiendo' if off < 0 else 'ganando'}"
                    )
                    lbl_goal_week.visible = bool(lbl_goal_week.value)
                    lbl_goal_adv.value = advice
                    slider.active_color = color
                    lbl_kcal.value = f"{int(tkcal):,} kcal".replace(",", ".")
                    lbl_tdee.value = f"Mantenimiento: {int(round(tdee)):,} kcal/día".replace(",", ".")
                    lbl_tc.value, lbl_tp.value, lbl_tf.value = f"{int(tc)}g", f"{int(tp)}g", f"{int(tf)}g"
                    if tdee + off < 1200:
                        lbl_warn.value = "Se aplica el mínimo de seguridad de 1.200 kcal/día."
                    elif tkcal < bmr:
                        lbl_warn.value = f"Por debajo de tu metabolismo basal (~{int(bmr)} kcal): difícil de sostener."
                    else:
                        lbl_warn.value = ""
                    lbl_warn.visible = bool(lbl_warn.value)

                def on_slide(e):
                    old_rec = recommended_preset(wiz_state["offset"])
                    wiz_state["offset"] = float(round(float(e.control.value) / 50.0) * 50)
                    if wiz_state["macro_preset"] is None and recommended_preset(wiz_state["offset"]) != old_rec:
                        render_wizard_step()  # cambia el preset sugerido: redibuja la tarjeta
                        return
                    refresh_goal()
                    page.update()

                slider = ft.Slider(
                    min=-1000, max=1000, divisions=40,
                    value=max(-1000.0, min(1000.0, wiz_state["offset"])),
                    label="{value} kcal",
                    on_change=on_slide,
                )

                def set_offset(v):
                    wiz_state["offset"] = float(v)
                    render_wizard_step()

                def quick_chip(text, v):
                    return ft.Container(
                        bgcolor="#ffffff", border=CARD_BORDER, border_radius=10,
                        padding=ft.Padding(left=12, right=12, top=9, bottom=9),
                        on_click=lambda e, v=v: set_offset(v),
                        content=ft.Text(text, size=12, weight=ft.FontWeight.W_600, color="#24543F"),
                    )

                refresh_goal()

                goal_card = ft.Container(
                    bgcolor="#ffffff", border_radius=14, padding=16, shadow=CARD_SHADOW, border=CARD_BORDER,
                    content=ft.Column(
                        [
                            lbl_goal,
                            lbl_goal_off,
                            slider,
                            ft.Row(
                                [
                                    ft.Text("Déficit", size=11, color="#A9A59B"),
                                    ft.Text("Mantener", size=11, color="#A9A59B"),
                                    ft.Text("Superávit", size=11, color="#A9A59B"),
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            ),
                            lbl_goal_week,
                            lbl_goal_adv,
                            lbl_warn,
                        ],
                        spacing=6,
                    ),
                )

                # ---- Reparto de macros según objetivo (con referencia bibliográfica) ----
                def set_preset(key):
                    wiz_state["macro_preset"] = key
                    render_wizard_step()

                active_key = current_preset()
                rec_key = recommended_preset(wiz_state["offset"])

                def preset_chip(key):
                    meta = MACRO_PRESETS[key]
                    sel = key == active_key
                    label = meta["name"] + (" ★" if key == rec_key else "")
                    return ft.Container(
                        bgcolor="#24543F" if sel else "#ffffff",
                        border=CARD_BORDER,
                        border_radius=10,
                        padding=ft.Padding(left=12, right=12, top=9, bottom=9),
                        on_click=lambda e, k=key: set_preset(k),
                        content=ft.Text(
                            label, size=12, weight=ft.FontWeight.W_600,
                            color="#ffffff" if sel else "#24543F",
                        ),
                    )

                lbl_custom = ft.Text("", size=12, weight=ft.FontWeight.W_600, color="#3A3832")

                def refresh_custom_lbl():
                    p, c = wiz_state["split_p"], wiz_state["split_c"]
                    lbl_custom.value = f"Proteína {p}% • Carbos {c}% • Grasas {100 - p - c}%"

                def on_slide_p(e):
                    p = int(round(float(e.control.value)))
                    wiz_state["split_p"] = p
                    if wiz_state["split_c"] > 90 - p:  # deja siempre un 10 % mínimo para grasa
                        wiz_state["split_c"] = 90 - p
                    refresh_custom_lbl()
                    refresh_goal()
                    page.update()

                def on_slide_c(e):
                    c = int(round(float(e.control.value)))
                    wiz_state["split_c"] = min(c, 90 - wiz_state["split_p"])
                    refresh_custom_lbl()
                    refresh_goal()
                    page.update()

                refresh_custom_lbl()
                custom_box = ft.Column(
                    [
                        lbl_custom,
                        ft.Text("Proteína (%)", size=11, color="#A9A59B"),
                        ft.Slider(min=10, max=50, divisions=40, value=wiz_state["split_p"],
                                  label="{value} %", on_change=on_slide_p),
                        ft.Text("Carbohidratos (%)", size=11, color="#A9A59B"),
                        ft.Slider(min=5, max=70, divisions=65, value=wiz_state["split_c"],
                                  label="{value} %", on_change=on_slide_c),
                    ],
                    spacing=2,
                    visible=(active_key == "custom"),
                )

                macro_card = ft.Container(
                    bgcolor="#ffffff", border_radius=14, padding=16, shadow=CARD_SHADOW, border=CARD_BORDER,
                    content=ft.Column(
                        [
                            ft.Text("Reparto de macros", size=16, weight=ft.FontWeight.W_600, color="#1C1B18"),
                            ft.Text(
                                "★ = recomendado para tu objetivo. Toca otro para cambiarlo.",
                                size=12, color="#77736A",
                            ),
                            ft.Row([preset_chip(k) for k in MACRO_PRESETS], spacing=8, run_spacing=8, wrap=True),
                            ft.Text(MACRO_PRESETS[active_key]["desc"], size=13, color="#3A3832"),
                            ft.Text("Ref.: " + MACRO_PRESETS[active_key]["ref"], size=11, color="#A9A59B", italic=True),
                            custom_box,
                            lbl_macro_warn,
                            ft.Text(
                                "Valores orientativos; no sustituyen el consejo de un dietista o médico.",
                                size=11, color="#A9A59B",
                            ),
                        ],
                        spacing=6,
                    ),
                )

                summary_card = ft.Container(
                    bgcolor="#1C1B18",
                    padding=20,
                    border_radius=14,
                    content=ft.Column(
                        [
                            ft.Text("OBJETIVO DIARIO", size=12, color="#E3B965", weight=ft.FontWeight.W_600),
                            lbl_kcal,
                            lbl_tdee,
                            ft.Divider(color="#3A3832"),
                            ft.Row(
                                [
                                    ft.Column([lbl_tc, ft.Text("Carbos", size=12, color="#A9A59B")],
                                              horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                                    ft.Column([lbl_tp, ft.Text("Proteína", size=12, color="#A9A59B")],
                                              horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                                    ft.Column([lbl_tf, ft.Text("Grasas", size=12, color="#A9A59B")],
                                              horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_AROUND,
                            ),
                            lbl_split,
                            lbl_pkg,
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=6,
                    ),
                )

                refresh_goal()  # ya existen todas las etiquetas (porcentajes, g/kg, avisos)

                wiz_body.content = ft.Column(
                    [
                        ft.Text("¿Cuánto quieres recortar o sumar?", size=26, weight=ft.FontWeight.W_600, color="#1C1B18"),
                        ft.Text(
                            "Desliza la barra: a la izquierda déficit (perder grasa), a la derecha superávit (ganar).",
                            size=13, color="#77736A",
                        ),
                        goal_card,
                        ft.Row(
                            [quick_chip("Definición −400", -400), quick_chip("Mantener 0", 0), quick_chip("Volumen +350", 350)],
                            spacing=8, wrap=True,
                        ),
                        macro_card,
                        summary_card,
                    ],
                    scroll=ft.ScrollMode.AUTO,
                    spacing=12,
                )

            # Botones de navegación del Wizard
            nav_row_controls = []
            if s > 0:
                nav_row_controls.append(
                    make_button(
                        "⬅ Atrás",
                        "#E4DFD3",
                        lambda e: prev_step(),
                        text_color="#3A3832",
                        expand=True,
                    )
                )
            elif not is_first_user:
                nav_row_controls.append(
                    make_button(
                        "Cancelar",
                        "#E4DFD3",
                        lambda e: show_user_selector(),
                        text_color="#3A3832",
                        expand=True,
                    )
                )

            if s < 3:
                nav_row_controls.append(
                    make_button(
                        "Siguiente ➡",
                        "#24543F",
                        lambda e: next_step(),
                        expand=True,
                    )
                )
            else:
                nav_row_controls.append(
                    make_button(
                        "Empezar",
                        "#3F7D58",
                        lambda e: finish_wizard(),
                        expand=True,
                    )
                )

            wiz_footer.controls = nav_row_controls
            page.update()

        def next_step():
            if step[0] == 0:
                name_clean = (tf_wiz_name.value or "").strip()
                if not name_clean:
                    notify("Escribe tu nombre para continuar", "#B8453A")
                    return
                wiz_state["name"] = name_clean
            elif step[0] == 2:
                if wiz_state["act_busy"]:
                    notify("Espera a que termine el análisis de tu actividad", "#C9763F")
                    return
                if (tf_wiz_act.value or "").strip() and not wiz_state["act_mult"]:
                    notify("Pulsa «Calcular con IA» para usar tu descripción, o elige un nivel rápido", "#C9763F")
                    return
            step[0] += 1
            render_wizard_step()

        def prev_step():
            step[0] -= 1
            render_wizard_step()

        async def analyze_activity(e=None):
            txt = (tf_wiz_act.value or "").strip()
            if not txt:
                notify("Cuéntame qué actividad haces y cada cuánto", "#B8453A")
                return
            wiz_state["act_text"] = txt
            wiz_state["act_busy"] = True
            render_wizard_step()
            try:
                res = await asyncio.to_thread(
                    ai_estimate_activity, txt, wiz_state["weight"], wiz_state["height"],
                    wiz_state["age"], wiz_state["gender"],
                )
            except Exception as ex:
                wiz_state["act_busy"] = False
                render_wizard_step()
                notify(f"IA: {ex}", "#B8453A")
                return
            wiz_state.update(
                {
                    "act_busy": False,
                    "act_mult": res["mult"],
                    "act_kcal": res["exercise_kcal"],
                    "act_summary": res["summary"],
                    "activity": nearest_activity_level(res["mult"]),
                }
            )
            render_wizard_step()

        def finish_wizard():
            name_clean = (wiz_state["name"] or "Atleta").strip()
            tkcal, tp, tc, tf, offset = compute_macros(
                wiz_state["weight"],
                wiz_state["height"],
                wiz_state["age"],
                wiz_state["gender"],
                wiz_state["activity"],
                "",
                offset=float(wiz_state["offset"]),
                act_mult=wiz_state["act_mult"],
                split=current_split(),
            )
            goal_name = describe_offset(offset)[0]
            mult = activity_multiplier(wiz_state["activity"], wiz_state["act_mult"])
            burned = (
                float(wiz_state["act_kcal"])
                if wiz_state["act_kcal"] is not None
                else float(
                    estimate_exercise_kcal(
                        wiz_state["weight"], wiz_state["height"], wiz_state["age"], wiz_state["gender"], mult
                    )
                )
            )
            args = (
                name_clean,
                wiz_state["avatar"],
                wiz_state["weight"],
                wiz_state["height"],
                wiz_state["age"],
                wiz_state["gender"],
                wiz_state["activity"],
                goal_name,
                offset,
                tkcal,
                tp,
                tc,
                tf,
                burned,
            )
            if wiz_state["id"] is not None:
                db.update_user(wiz_state["id"], *args)
                uid = wiz_state["id"]
                notify("Perfil actualizado con éxito", "#3F7D58")
            else:
                uid = db.create_user(*args)
                notify(f"¡Bienvenido/a {name_clean}!", "#3F7D58")
            db.set_activity_extra(
                uid,
                (wiz_state["act_text"] or "").strip() if wiz_state["act_mult"] else "",
                wiz_state["act_mult"],
            )

            login_as_user(uid)

        wiz_footer = ft.Row(spacing=10)

        root_container.content = ft.Container(
            padding=22,
            expand=True,
            content=ft.Column(
                [
                    ft.Container(height=12),
                    step_title_lbl,
                    ft.Container(
                        content=progress_bar, border_radius=6, clip_behavior=ft.ClipBehavior.HARD_EDGE
                    ),
                    ft.Container(height=4),
                    wiz_body,
                    wiz_footer,
                    ft.Container(height=10),
                ],
                expand=True,
                spacing=10,
            ),
        )
        render_wizard_step()

    # ==========================================
    # PANTALLA C: APLICACIÓN PRINCIPAL (HOY, AÑADIR, ESCÁNER, PERFIL)
    # ==========================================
    def open_main_app():
        user = db.get_user(current_user_id[0])
        if not user:
            show_user_selector()
            return

        # Cabecera Superior con Selector Rápido de Usuario
        header_user_txt = ft.Text("", size=14, weight=ft.FontWeight.W_600, color="#1C1B18")
        header_avatar_txt = ft.Text("", size=12, weight=ft.FontWeight.W_600, color="#24543F")

        top_bar = ft.Container(
            bgcolor="#ffffff",
            padding=ft.Padding(left=16, right=16, top=18, bottom=10),
            border=ft.Border(bottom=ft.BorderSide(1, "#E4DFD3")),
            content=ft.Row(
                [
                    ft.Text("GymMacro", size=18, weight=ft.FontWeight.W_600, color="#1C1B18"),
                    ft.Container(
                        bgcolor="#F4F1EA",
                        border_radius=12,
                        padding=ft.Padding(left=12, right=12, top=6, bottom=6),
                        on_click=show_user_selector,
                        content=ft.Row(
                            [
                                ft.Container(
                                    width=26, height=26, border_radius=8, bgcolor="#E3ECE6",
                                    alignment=ft.Alignment.CENTER, content=header_avatar_txt,
                                ),
                                header_user_txt,
                                ft.Icon(ft.Icons.UNFOLD_MORE, size=16, color="#77736A"),
                            ],
                            spacing=6,
                        ),
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
        )

        # ----- CAPA 1: PANTALLA "HOY" -----
        lbl_consumed = ft.Text("0", size=20, weight=ft.FontWeight.W_600, color="#1C1B18")
        lbl_remaining = ft.Text("0", size=28, weight=ft.FontWeight.W_600, color="#1C1B18")
        lbl_burned = ft.Text("244", size=20, weight=ft.FontWeight.W_600, color="#1C1B18")

        ring_main = ft.ProgressRing(
            value=0.0,
            width=135,
            height=135,
            stroke_width=10,
            color="#24543F",
            bgcolor="#ECE8DE",
        )

        def create_macro_col(label_title, bar_color):
            bar = ft.ProgressBar(
                value=0.0, color=bar_color, bgcolor="#ECE8DE", height=7
            )
            val_txt = ft.Text("0 / 0 g", size=13, weight=ft.FontWeight.W_600, color="#1C1B18")
            col = ft.Column(
                [
                    ft.Text(label_title, size=12, color="#77736A"),
                    ft.Container(content=bar, width=85, border_radius=4, padding=6),
                    val_txt,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=2,
                expand=True,
            )
            return col, bar, val_txt

        col_c, bar_c, lbl_c = create_macro_col("Carbohidratos", "#D9A441")
        col_p, bar_p, lbl_p = create_macro_col("Proteínas", "#3F7D58")
        col_f, bar_f, lbl_f = create_macro_col("Grasas", "#C9763F")

        meals_card_col = ft.Column(spacing=0)

        def go_to_add_for_meal(meal_name):
            selected_meal_for_add[0] = meal_name
            render_meal_selector_pills()
            switch_tab(1)

        def toggle_meal_details(meal_name):
            expanded_meal[0] = None if expanded_meal[0] == meal_name else meal_name
            refresh_dashboard()

        def delete_food_item(fid):
            db.delete_food(fid)
            refresh_dashboard()
            notify("Alimento eliminado", "#B8453A")

        def toggle_group(gid):
            if gid in expanded_groups:
                expanded_groups.discard(gid)
            else:
                expanded_groups.add(gid)
            refresh_dashboard()

        def delete_group_item(gid):
            db.delete_group(current_user_id[0], gid)
            expanded_groups.discard(gid)
            refresh_dashboard()
            notify("Comida eliminada", "#B8453A")

        def confirm_delete_meal(meal_name):
            def do_delete(e):
                db.delete_meal_today(current_user_id[0], meal_name)
                page.pop_dialog()
                refresh_dashboard()
                notify(f"{meal_name} de hoy vaciado", "#B8453A")

            page.show_dialog(
                ft.AlertDialog(
                    modal=True,
                    title=ft.Text(f"¿Borrar todo de {meal_name}?"),
                    content=ft.Text("Se eliminarán todos los alimentos de hoy en esta comida. No se puede deshacer."),
                    actions=[
                        ft.TextButton("Cancelar", on_click=lambda e: page.pop_dialog()),
                        ft.TextButton("Borrar todo", on_click=do_delete),
                    ],
                )
            )

        def refresh_dashboard():
            u = db.get_user(current_user_id[0])
            if not u:
                return
            _, uname, uavatar, _, _, _, _, _, _, _, tkcal, tp, tc, tf, burned = u
            header_user_txt.value = uname
            header_avatar_txt.value = (uname[:1] or "?").upper()
            lbl_date.value = date_es_long(now_local().date())

            foods = db.get_today_foods(current_user_id[0])

            skcal = sum(x[4] for x in foods)
            sp = sum(x[5] for x in foods)
            sc = sum(x[6] for x in foods)
            sf = sum(x[7] for x in foods)

            lbl_consumed.value = f"{int(skcal):,}".replace(",", ".")
            lbl_burned.value = f"{int(burned):,}".replace(",", ".")

            rem_kcal = max(0, int(tkcal - skcal))
            suggest_btn.visible = rem_kcal >= 60
            lbl_remaining.value = f"{rem_kcal:,}".replace(",", ".")
            ring_main.value = min(1.0, skcal / max(1.0, tkcal))

            # Valoración con IA: automática al llegar a las calorías del día, manual cuando quieras
            day_review["cur_sig"] = tuple(x[0] for x in foods)
            eval_btn.visible = bool(foods)
            render_review()
            if (
                foods
                and rem_kcal < 60
                and not day_review["busy"]
                and day_review["sig"] != day_review["cur_sig"]
                and day_review["fail_sig"] != day_review["cur_sig"]
            ):
                page.run_task(run_day_review, True)

            lbl_c.value = f"{int(sc)} / {int(tc)}g"
            bar_c.value = min(1.0, sc / max(1.0, tc))

            lbl_p.value = f"{int(sp)} / {int(tp)}g"
            bar_p.value = min(1.0, sp / max(1.0, tp))

            lbl_f.value = f"{int(sf)} / {int(tf)}g"
            bar_f.value = min(1.0, sf / max(1.0, tf))

            meals_card_col.controls.clear()
            meal_names = list(MEALS_META.keys())

            for idx, m_name in enumerate(meal_names):
                meta = MEALS_META[m_name]
                m_target = max(1, round(tkcal * meta["ratio"]))
                m_foods = [f for f in foods if f[1] == m_name]
                m_kcal = sum(f[4] for f in m_foods)
                m_prog = min(1.0, m_kcal / m_target)

                icon_stack = ft.Stack(
                    width=50,
                    height=50,
                    controls=[
                        ft.ProgressRing(
                            value=m_prog,
                            width=50,
                            height=50,
                            stroke_width=4,
                            color="#D9A441",
                            bgcolor="#ECE8DE",
                        ),
                        ft.Container(
                            width=50,
                            height=50,
                            content=ft.Row(
                                [ft.Icon(meta["icon"], size=20, color="#1C1B18")],
                                alignment=ft.MainAxisAlignment.CENTER,
                            ),
                        ),
                    ],
                )

                row_item = ft.Container(
                    padding=14,
                    on_click=lambda e, mn=m_name: toggle_meal_details(mn),
                    content=ft.Row(
                        [
                            icon_stack,
                            ft.Container(width=6),
                            ft.Column(
                                [
                                    ft.Text(
                                        m_name,
                                        size=16,
                                        weight=ft.FontWeight.W_600,
                                        color="#1C1B18",
                                    ),
                                    ft.Text(
                                        f"{int(m_kcal)} / {int(m_target)} kcal",
                                        size=13,
                                        color="#77736A",
                                    ),
                                ],
                                spacing=2,
                                expand=True,
                            ),
                            ft.Container(
                                width=36,
                                height=36,
                                border_radius=100,
                                bgcolor="#24543F",
                                on_click=lambda e, mn=m_name: go_to_add_for_meal(mn),
                                content=ft.Row(
                                    [
                                        ft.Icon(ft.Icons.ADD, size=20, color="white")
                                    ],
                                    alignment=ft.MainAxisAlignment.CENTER,
                                ),
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                )
                meals_card_col.controls.append(row_item)

                if expanded_meal[0] == m_name:
                    if not m_foods:
                        meals_card_col.controls.append(
                            ft.Container(
                                bgcolor="#FAF8F4",
                                padding=12,
                                content=ft.Text(
                                    "Sin alimentos registrados. Pulsa '+' para añadir.",
                                    size=12,
                                    color="#A9A59B",
                                ),
                            )
                        )
                    else:
                        done_groups = set()
                        for r in m_foods:
                            fid, _, fname, fgrams, fkcal, fp, fc_val, ff, fimg, gid, gname = r
                            if gid:
                                # Conjunto de «Mis comidas»: una sola fila; al pulsar se ve el desglose
                                if gid in done_groups:
                                    continue
                                done_groups.add(gid)
                                members = [x for x in m_foods if x[9] == gid]
                                gk = sum(x[4] for x in members)
                                gp = sum(x[5] for x in members)
                                gc = sum(x[6] for x in members)
                                gf = sum(x[7] for x in members)
                                g_open = gid in expanded_groups
                                meals_card_col.controls.append(
                                    ft.Container(
                                        bgcolor="#FAF8F4",
                                        padding=10,
                                        on_click=lambda e, g=gid: toggle_group(g),
                                        content=ft.Row(
                                            [
                                                ft.Container(
                                                    width=38, height=38, border_radius=10, bgcolor="#E3ECE6",
                                                    alignment=ft.Alignment.CENTER,
                                                    content=ft.Icon(ft.Icons.RESTAURANT_MENU, size=18, color="#24543F"),
                                                ),
                                                ft.Column(
                                                    [
                                                        ft.Text(
                                                            f"• {gname}", size=13,
                                                            weight=ft.FontWeight.W_600, color="#3A3832",
                                                        ),
                                                        ft.Text(
                                                            f"{len(members)} alimentos • {int(gk)} kcal | "
                                                            f"C: {gc:.1f}g • P: {gp:.1f}g • G: {gf:.1f}g",
                                                            size=11, color="#77736A",
                                                        ),
                                                    ],
                                                    spacing=1, expand=True,
                                                ),
                                                ft.Icon(
                                                    ft.Icons.EXPAND_LESS if g_open else ft.Icons.EXPAND_MORE,
                                                    size=20, color="#A9A59B",
                                                ),
                                                ft.Container(
                                                    padding=6,
                                                    tooltip="Quitar toda la comida",
                                                    on_click=lambda e, g=gid: delete_group_item(g),
                                                    content=ft.Icon(ft.Icons.CLOSE, size=18, color="#B8453A"),
                                                ),
                                            ],
                                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                        ),
                                    )
                                )
                                if g_open:
                                    for x in members:
                                        meals_card_col.controls.append(
                                            ft.Container(
                                                bgcolor="#F4F1EA",
                                                padding=ft.Padding(left=30, right=10, top=7, bottom=7),
                                                content=ft.Row(
                                                    [
                                                        ft.Column(
                                                            [
                                                                ft.Text(
                                                                    f"– {x[2]} ({int(x[3])}g)", size=12,
                                                                    weight=ft.FontWeight.W_600, color="#3A3832",
                                                                ),
                                                                ft.Text(
                                                                    f"{int(x[4])} kcal | C: {x[6]:.1f}g • P: {x[5]:.1f}g • G: {x[7]:.1f}g",
                                                                    size=11, color="#77736A",
                                                                ),
                                                            ],
                                                            spacing=1, expand=True,
                                                        ),
                                                        ft.Container(
                                                            padding=4,
                                                            on_click=lambda e, i=x[0]: delete_food_item(i),
                                                            content=ft.Icon(ft.Icons.CLOSE, size=16, color="#B8453A"),
                                                        ),
                                                    ],
                                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                                ),
                                            )
                                        )
                                continue
                            meals_card_col.controls.append(
                                ft.Container(
                                    bgcolor="#FAF8F4",
                                    padding=10,
                                    content=ft.Row(
                                        [
                                            food_thumb(fimg, 38),
                                            ft.Column(
                                                [
                                                    ft.Text(
                                                        f"• {fname} ({int(fgrams)}g)",
                                                        size=13,
                                                        weight=ft.FontWeight.W_600,
                                                        color="#3A3832",
                                                    ),
                                                    ft.Text(
                                                        f"{int(fkcal)} kcal | C: {fc_val:.1f}g • P: {fp:.1f}g • G: {ff:.1f}g",
                                                        size=11,
                                                        color="#77736A",
                                                    ),
                                                ],
                                                spacing=1,
                                                expand=True,
                                            ),
                                            ft.Container(
                                                padding=6,
                                                on_click=lambda e, i=fid: delete_food_item(i),
                                                content=ft.Icon(ft.Icons.CLOSE, size=18, color="#B8453A"),
                                            ),
                                        ]
                                    ),
                                )
                            )
                        meals_card_col.controls.append(
                            ft.Container(
                                bgcolor="#FAF8F4",
                                padding=ft.Padding(left=10, right=10, top=4, bottom=10),
                                content=ft.Row(
                                    [
                                        ft.Container(
                                            padding=ft.Padding(left=10, right=10, top=6, bottom=6),
                                            border_radius=8, bgcolor="#F6E4E1",
                                            on_click=lambda e, mn=m_name: confirm_delete_meal(mn),
                                            content=ft.Row(
                                                [
                                                    ft.Icon(ft.Icons.DELETE_OUTLINE, size=16, color="#B8453A"),
                                                    ft.Text(
                                                        f"Borrar todo de {m_name}", size=12,
                                                        weight=ft.FontWeight.W_600, color="#B8453A",
                                                    ),
                                                ],
                                                spacing=5, tight=True,
                                            ),
                                        )
                                    ]
                                ),
                            )
                        )

                if idx < len(meal_names) - 1:
                    meals_card_col.controls.append(
                        ft.Divider(height=1, color="#F4F1EA")
                    )

            page.update()

        center_ring_stack = ft.Stack(
            width=135,
            height=135,
            controls=[
                ring_main,
                ft.Container(
                    width=135,
                    height=135,
                    content=ft.Column(
                        [
                            lbl_remaining,
                            ft.Text("Restantes", size=12, color="#77736A"),
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=0,
                    ),
                ),
            ],
        )

        async def _go_sugg(e):
            await go_suggestions(e)

        # ----- VALORACIÓN DEL DÍA CON IA -----
        day_review = {"sig": None, "cur_sig": None, "data": None, "busy": False, "fail_sig": None}
        review_card = ft.Container(visible=False)
        review_loading = ft.Row(
            [ft.ProgressRing(width=18, height=18, stroke_width=3), ft.Text("Valorando tu día…", size=13, color="#77736A")],
            visible=False,
        )
        _warn_icon = getattr(ft.Icons, "WARNING_AMBER_ROUNDED", ft.Icons.LIGHTBULB_OUTLINE)
        VERDICT_STYLE = {
            "muy_bien": ("¡Muy bien!", "#3F7D58", ft.Icons.CHECK),
            "bien": ("Bien", "#3F7D58", ft.Icons.CHECK),
            "mejorable": ("Se puede mejorar", "#D9A441", ft.Icons.LIGHTBULB_OUTLINE),
            "mal": ("Ojo con esto", "#C9763F", _warn_icon),
        }

        def build_day_context(u, foods):
            tk, tp_, tc_, tf_ = u[10], u[11], u[12], u[13]
            lines = [
                f"Objetivo del usuario: {u[8]} (ajuste {u[9]:+.0f} kcal). Peso: {u[3]} kg.",
                f"Objetivo diario: {int(tk)} kcal, P {int(tp_)} g, C {int(tc_)} g, G {int(tf_)} g.",
                f"Consumido hoy: {int(sum(x[4] for x in foods))} kcal, P {int(sum(x[5] for x in foods))} g, "
                f"C {int(sum(x[6] for x in foods))} g, G {int(sum(x[7] for x in foods))} g.",
                "Alimentos de hoy:",
            ]
            for m in MEALS_META:
                items = [x for x in foods if x[1] == m]
                if items:
                    lines.append(
                        f"{m}: "
                        + "; ".join(
                            f"{x[2]} {int(x[3])} g ({int(x[4])} kcal, P {x[5]:.0f}, C {x[6]:.0f}, G {x[7]:.0f})"
                            for x in items
                        )
                    )
            return "\n".join(lines)

        def render_review():
            d = day_review["data"]
            if not d or day_review["sig"] != day_review["cur_sig"]:
                review_card.visible = False
                review_card.content = None
                return
            label, color, icon = VERDICT_STYLE[d["verdict"]]
            body = [
                ft.Row(
                    [
                        ft.Container(
                            width=38, height=38, border_radius=19, bgcolor=color, alignment=ft.Alignment.CENTER,
                            content=ft.Icon(icon, size=20, color="white"),
                        ),
                        ft.Column(
                            [
                                ft.Text(label.upper(), size=11, weight=ft.FontWeight.W_600, color=color),
                                ft.Text(d["title"] or "Valoración del día", size=15,
                                        weight=ft.FontWeight.W_600, color="#1C1B18"),
                            ],
                            spacing=1, expand=True,
                        ),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Text(d["summary"], size=13, color="#3A3832"),
            ]
            for fl in d["flags"]:
                lines = [ft.Text(fl["food"], size=13, weight=ft.FontWeight.W_600, color="#1C1B18")]
                if fl["issue"]:
                    lines.append(ft.Text(fl["issue"], size=12, color="#3A3832"))
                if fl["grams"] > 0:
                    lines.append(
                        ft.Text(f"Cantidad más razonable: ~{fl['grams']} g", size=12,
                                weight=ft.FontWeight.W_600, color="#C9763F")
                    )
                if fl["alternatives"]:
                    lines.append(
                        ft.Text("Alternativas: " + ", ".join(fl["alternatives"]), size=12, color="#24543F")
                    )
                body.append(
                    ft.Container(bgcolor="#FFF6EC", border_radius=10, padding=10, content=ft.Column(lines, spacing=3))
                )
            for t in d["tips"]:
                body.append(ft.Text("• " + t, size=12, color="#3A3832"))
            body.append(
                ft.Text("Valoración orientativa de la IA; no sustituye a un dietista.", size=10, color="#A9A59B")
            )
            review_card.content = ft.Container(
                bgcolor="#ffffff", border_radius=14, padding=16, border=CARD_BORDER,
                content=ft.Column(body, spacing=8),
            )
            review_card.visible = True

        async def run_day_review(auto=False):
            if day_review["busy"]:
                return
            u = db.get_user(current_user_id[0])
            foods = db.get_today_foods(current_user_id[0])
            if not u or not foods:
                if not auto:
                    notify("Registra algo primero para poder valorar el día", "#B8453A")
                return
            sig = tuple(x[0] for x in foods)
            day_review["busy"] = True
            review_loading.visible = True
            page.update()
            try:
                data = await asyncio.to_thread(ai_review_day, build_day_context(u, foods))
            except Exception as ex:
                day_review["busy"] = False
                day_review["fail_sig"] = sig  # no reintentar solo en bucle
                review_loading.visible = False
                page.update()
                if not auto:
                    notify(f"IA: {ex}", "#B8453A")
                return
            day_review.update(sig=sig, data=data, busy=False)
            review_loading.visible = False
            render_review()
            page.update()

        async def _review_click(e):
            await run_day_review(False)

        eval_btn = make_button("✨ Valorar mi día con IA", "#24543F", _review_click)

        suggest_btn = make_button(
            "💡 Plan de recetas para llegar a mis calorías", "#1C1B18", _go_sugg
        )

        lbl_date = ft.Text(
            date_es_long(now_local().date()), size=14, weight=ft.FontWeight.W_600, color="#3A3832"
        )

        view_home = ft.Container(
            padding=16,
            expand=True,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.CALENDAR_TODAY_OUTLINED, size=16, color="#24543F"),
                            lbl_date,
                        ],
                        spacing=8,
                    ),
                    ft.Container(
                        bgcolor="#ffffff",
                        border_radius=14,
                        padding=20,
                        shadow=CARD_SHADOW, border=CARD_BORDER,
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Column(
                                            [
                                                lbl_consumed,
                                                ft.Text(
                                                    "Consumidas",
                                                    size=12,
                                                    color="#77736A",
                                                ),
                                            ],
                                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                            spacing=2,
                                            expand=True,
                                        ),
                                        center_ring_stack,
                                        ft.Column(
                                            [
                                                lbl_burned,
                                                ft.Text(
                                                    "Quemadas",
                                                    size=12,
                                                    color="#77736A",
                                                ),
                                            ],
                                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                            spacing=2,
                                            expand=True,
                                        ),
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                ft.Container(height=8),
                                ft.Row(
                                    [col_c, col_p, col_f],
                                    alignment=ft.MainAxisAlignment.SPACE_AROUND,
                                ),
                            ],
                            spacing=8,
                        ),
                    ),
                    suggest_btn,
                    eval_btn,
                    review_loading,
                    review_card,
                    ft.Container(
                        bgcolor="#ffffff",
                        border_radius=14,
                        padding=8,
                        shadow=CARD_SHADOW, border=CARD_BORDER,
                        content=meals_card_col,
                    ),
                ],
                scroll=ft.ScrollMode.AUTO,
                expand=True,
                spacing=14,
            ),
        )

        # ----- CAPA 2: AÑADIR ALIMENTO -----
        meal_selector_row = ft.Row(spacing=6, wrap=True)
        lbl_meal_dest = ft.Text("", size=12, weight=ft.FontWeight.W_600, color="#77736A")

        def default_meal_for_now():
            """Comida que toca según la hora (hora de España): así no se apunta todo al desayuno."""
            now = now_local()
            h = now.hour + now.minute / 60.0
            if h < 5:
                return "Cena"
            if h < 11.5:
                return "Desayuno"
            if h < 16.5:
                return "Almuerzo"
            if h < 19.5:
                return "Snacks"
            return "Cena"

        selected_meal_for_add[0] = default_meal_for_now()

        def render_meal_selector_pills():
            lbl_meal_dest.value = f"Se registrará en: {selected_meal_for_add[0]}  (toca otra para cambiar)"
            meal_selector_row.controls.clear()
            for m_name, meta in MEALS_META.items():
                active = selected_meal_for_add[0] == m_name
                meal_selector_row.controls.append(
                    ft.Container(
                        bgcolor="#24543F" if active else "#ffffff",
                        padding=10,
                        border_radius=10,
                        on_click=lambda e, mn=m_name: (
                            selected_meal_for_add.__setitem__(0, mn),
                            render_meal_selector_pills(),
                            page.update(),
                        ),
                        content=ft.Text(
                            m_name,
                            size=12,
                            weight=ft.FontWeight.W_600,
                            color="white" if active else "#77736A",
                        ),
                    )
                )

        # Mismas pastillas de comida también en la pestaña IA
        ai_meal_row = ft.Row(spacing=6, wrap=True)
        _render_add_pills = render_meal_selector_pills

        def render_meal_selector_pills():
            _render_add_pills()
            ai_meal_row.controls.clear()
            for m_name, meta in MEALS_META.items():
                active = selected_meal_for_add[0] == m_name
                ai_meal_row.controls.append(
                    ft.Container(
                        bgcolor="#24543F" if active else "#ffffff",
                        padding=10,
                        border_radius=10,
                        on_click=lambda e, mn=m_name: (
                            selected_meal_for_add.__setitem__(0, mn),
                            render_meal_selector_pills(),
                            page.update(),
                        ),
                        content=ft.Text(
                            m_name,
                            size=12,
                            weight=ft.FontWeight.W_600,
                            color="white" if active else "#77736A",
                        ),
                    )
                )

        render_meal_selector_pills()

        tf_search = ft.TextField(
            label="Buscar (ej. mandarina, pollo, arroz, huevo)",
            bgcolor="#ffffff",
            border_radius=10,
            border_color="#E4DFD3",
            expand=True,
        )
        search_results = ft.Column(spacing=6)

        tf_name = ft.TextField(
            label="Nombre del alimento",
            bgcolor="#ffffff",
            border_radius=10,
            border_color="#E4DFD3",
        )
        tf_grams = ft.TextField(
            label="Cantidad consumida (gramos)",
            value="100",
            bgcolor="#ffffff",
            border_radius=10,
            border_color="#E4DFD3",
        )
        tf_kcal = ft.TextField(
            label="Kcal / 100g",
            bgcolor="#ffffff",
            border_radius=10,
            border_color="#E4DFD3",
            expand=True,
        )
        tf_p = ft.TextField(
            label="Proteína / 100g",
            bgcolor="#ffffff",
            border_radius=10,
            border_color="#E4DFD3",
            expand=True,
        )
        tf_c = ft.TextField(
            label="Carbos / 100g",
            bgcolor="#ffffff",
            border_radius=10,
            border_color="#E4DFD3",
            expand=True,
        )
        tf_f = ft.TextField(
            label="Grasas / 100g",
            bgcolor="#ffffff",
            border_radius=10,
            border_color="#E4DFD3",
            expand=True,
        )

        current_food_image = [None]
        food_preview = ft.Row(visible=False, alignment=ft.MainAxisAlignment.CENTER)

        def set_food_image(url):
            current_food_image[0] = url or None
            if url:
                food_preview.controls = [
                    ft.Container(
                        border_radius=12,
                        shadow=CARD_SHADOW, border=CARD_BORDER,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                        content=food_thumb(url, 160),
                    )
                ]
                food_preview.visible = True
            else:
                food_preview.controls = []
                food_preview.visible = False

        search_state = {"gen": 0}

        def fill_food_form(name, kcal, p, c, f, image=None, grams=100):
            search_state["gen"] += 1  # descarta búsquedas en curso
            set_food_image(image)
            tf_name.value = name
            tf_grams.value = str(grams)
            tf_kcal.value = str(round(float(kcal or 0), 1))
            tf_p.value = str(round(float(p or 0), 1))
            tf_c.value = str(round(float(c or 0), 1))
            tf_f.value = str(round(float(f or 0), 1))
            search_results.controls.clear()
            switch_tab(1)
            notify(f"Cargado: {name}")

        def food_result_card(name, k, p, c, f, img=None, tag="Elegir", tag_color="#24543F", grams=100):
            return ft.Container(
                bgcolor="#ffffff", padding=12, border_radius=10, border=CARD_BORDER,
                on_click=lambda e, n=name, k=k, p=p, c=c, f=f, im=img, g=grams: fill_food_form(n, k, p, c, f, im, g),
                content=ft.Row(
                    ([food_thumb(img)] if img else [])
                    + [
                        ft.Column(
                            [
                                ft.Text(name, weight=ft.FontWeight.W_600, size=14, color="#1C1B18"),
                                ft.Text(
                                    f"100g: {int(k)} kcal | C: {c:.1f}g | P: {p:.1f}g | G: {f:.1f}g",
                                    size=11, color="#77736A",
                                ),
                            ],
                            spacing=2, expand=True,
                        ),
                        ft.Text(tag, color=tag_color, size=11, weight=ft.FontWeight.W_600),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

        # Desplegable de alimentos añadidos hace poco (se ve al enfocar o escribir en el buscador)
        recents_box = ft.Container(visible=False)

        def render_recents(filter_text=""):
            rows = db.get_recent_foods(current_user_id[0], 12) if current_user_id[0] else []
            q = norm_text(filter_text)
            if q:
                rows = [r for r in rows if q in norm_text(r["name"])]
            rows = rows[:6]
            if not rows or search_results.controls:
                recents_box.visible = False
                recents_box.content = None
                return
            cards = [ft.Text("Recientes", size=12, weight=ft.FontWeight.W_600, color="#77736A")]
            for r in rows:
                cards.append(
                    food_result_card(
                        r["name"], r["kcal"], r["p"], r["c"], r["f"], r["image"],
                        "Reciente", "#C9763F", round(r["grams"]),
                    )
                )
            recents_box.content = ft.Column(cards, spacing=6)
            recents_box.visible = True

        def on_search_focus(e=None):
            render_recents(tf_search.value or "")
            page.update()

        def on_search_change(e=None):
            search_results.controls.clear()  # los resultados anteriores ya no corresponden
            render_recents(tf_search.value or "")
            page.update()

        async def search_openfoodfacts(e=None):
            query = (tf_search.value or "").strip()
            if not query:
                render_recents("")
                page.update()
                return
            search_state["gen"] += 1
            my = search_state["gen"]
            search_results.controls.clear()
            recents_box.visible = False
            # 1) Alimentos básicos al instante (mandarina, pollo, arroz…)
            for it in search_basic_foods(query):
                search_results.controls.append(
                    food_result_card(it["name"], it["kcal"], it["p"], it["c"], it["f"], None, "Básico")
                )
            loading = ft.Text("Buscando más productos…", color="#24543F", size=12)
            search_results.controls.append(loading)
            page.update()
            # 2) Productos de OpenFoodFacts, filtrados y ordenados, en segundo plano
            try:
                prods = await asyncio.to_thread(off_search, query)
            except Exception:
                prods = []
            if search_state["gen"] != my:
                return
            try:
                search_results.controls.remove(loading)
            except ValueError:
                pass
            for it in prods:
                search_results.controls.append(
                    food_result_card(it["name"], it["kcal"], it["p"], it["c"], it["f"], it["image"], "Producto", "#D9A441")
                )
            if not search_results.controls:
                search_results.controls.append(
                    ft.Text("Sin resultados. Prueba otra palabra o descríbelo en la pestaña IA.", color="#77736A")
                )
            page.update()

        tf_search.on_focus = on_search_focus
        tf_search.on_change = on_search_change
        tf_search.on_submit = search_openfoodfacts


        def save_food_entry(e):
            try:
                name = (tf_name.value or "").strip() or "Alimento"
                grams = float(tf_grams.value)
                factor = grams / 100.0
                k = float(tf_kcal.value) * factor
                p = float(tf_p.value) * factor
                c = float(tf_c.value) * factor
                f = float(tf_f.value) * factor

                meal_dest = selected_meal_for_add[0]
                db.add_food(current_user_id[0], meal_dest, name, grams, k, p, c, f, current_food_image[0])
                extra = ""
                for field in (tf_name, tf_kcal, tf_p, tf_c, tf_f):
                    field.value = ""
                tf_grams.value = "100"
                set_food_image(None)
                expanded_meal[0] = meal_dest
                notify(f"Añadido a {meal_dest}{extra}", "#3F7D58")
                switch_tab(0)
            except ValueError:
                notify("Rellena los gramos y macros con números", "#B8453A")

        # ----- MIS COMIDAS (comidas personalizadas que registras y añades cuando quieras) -----
        saved_col = ft.Column(spacing=8)
        mm_state = {"items": []}

        def add_items_to_log(items, meal_dest, group_name=None):
            """Añade alimentos a una comida. Con group_name (o gname en cada alimento) entran como
            un conjunto con ese nombre, con desglose al pulsarlo en Hoy."""
            gids = {}
            for it in items:
                gname = group_name or it.get("gname")
                gid = None
                if gname:
                    gid = gids.setdefault(it.get("gkey") or "_", uuid.uuid4().hex[:12])
                db.add_food(
                    current_user_id[0], meal_dest, it["name"], it["grams"],
                    it["kcal"], it["p"], it["c"], it["f"], it.get("image"),
                    group_id=gid, group_name=gname,
                )
            expanded_meal[0] = meal_dest

        def _mm_line(it):
            return f"{int(it['kcal'])} kcal • P {it['p']:.1f}g • C {it['c']:.1f}g • G {it['f']:.1f}g"

        def save_fixed_dialog(items, default_name="Mi comida", meal_type=None):
            """Guarda una lista de alimentos en Mis comidas pidiendo un nombre."""
            if not items:
                notify("No hay alimentos que guardar", "#B8453A")
                return
            name_field = ft.TextField(label="Nombre", value=default_name, autofocus=True)

            def do_save(e):
                nm = (name_field.value or "").strip() or default_name
                db.save_meal(current_user_id[0], nm, meal_type or selected_meal_for_add[0], items)
                page.pop_dialog()
                render_saved_meals()
                page.update()
                notify(f"'{nm}' guardada en Mis comidas", "#3F7D58")

            page.show_dialog(
                ft.AlertDialog(
                    modal=True,
                    title=ft.Text("Guardar en Mis comidas"),
                    content=name_field,
                    actions=[
                        ft.TextButton("Cancelar", on_click=lambda e: page.pop_dialog()),
                        ft.TextButton("Guardar", on_click=do_save),
                    ],
                )
            )

        def use_saved_meal(sid):
            for s_id, sname, _, items in db.get_saved_meals(current_user_id[0]):
                if s_id == sid:
                    meal_dest = selected_meal_for_add[0]
                    add_items_to_log(items, meal_dest, group_name=sname)
                    notify(f"'{sname}' añadida a {meal_dest}", "#3F7D58")
                    switch_tab(0)
                    return

        def delete_saved(sid):
            db.delete_saved_meal(sid)
            render_saved_meals()
            page.update()

        def render_saved_meals():
            saved_col.controls.clear()
            meals = db.get_saved_meals(current_user_id[0])
            if not meals:
                saved_col.controls.append(
                    ft.Text(
                        "Aún no tienes comidas. Pulsa «Crear comida» o guarda una desde el Historial.",
                        size=12, color="#A9A59B",
                    )
                )
            for sid, sname, mtype, items in meals:
                tot = {k: sum(i[k] for i in items) for k in ("kcal", "p", "c", "f")}
                saved_col.controls.append(
                    ft.Container(
                        bgcolor="#ffffff", border_radius=10, padding=12, shadow=CARD_SHADOW, border=CARD_BORDER,
                        content=ft.Row(
                            [
                                ft.Column(
                                    [
                                        ft.Text(sname, size=14, weight=ft.FontWeight.W_600, color="#1C1B18"),
                                        ft.Text(_mm_line(tot), size=11, color="#77736A"),
                                        ft.Text(
                                            ", ".join(f"{i['name']} {int(i['grams'])}g" for i in items),
                                            size=11, color="#A9A59B",
                                        ),
                                    ],
                                    spacing=2, expand=True,
                                ),
                                ft.Container(
                                    padding=10, bgcolor="#24543F", border_radius=8,
                                    tooltip="Añadir a la comida seleccionada",
                                    on_click=lambda e, s_=sid: use_saved_meal(s_),
                                    content=ft.Icon(ft.Icons.ADD, size=18, color="white"),
                                ),
                                ft.Container(
                                    padding=8,
                                    on_click=lambda e, s_=sid: delete_saved(s_),
                                    content=ft.Icon(ft.Icons.DELETE_OUTLINE, size=20, color="#A9A59B"),
                                ),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    )
                )

        # -- Constructor: crear una comida personalizada --
        _fld = dict(bgcolor="#ffffff", border_radius=10, border_color="#E4DFD3")
        tf_mm_name = ft.TextField(label="Nombre de la comida", hint_text="Ej: Mi desayuno de avena", **_fld)
        tf_mm_ai = ft.TextField(
            label="Descríbela y la IA calcula",
            hint_text="Ej: 60 g de avena, 1 plátano y 200 ml de leche",
            multiline=True, min_lines=2, max_lines=4, **_fld,
        )
        tf_mm_iname = ft.TextField(label="Alimento", **_fld)
        tf_mm_igrams = ft.TextField(label="Gramos", expand=True, keyboard_type=ft.KeyboardType.NUMBER, **_fld)
        tf_mm_ikcal = ft.TextField(label="Kcal", expand=True, keyboard_type=ft.KeyboardType.NUMBER, **_fld)
        tf_mm_ip = ft.TextField(label="Prot. g", expand=True, keyboard_type=ft.KeyboardType.NUMBER, **_fld)
        tf_mm_ic = ft.TextField(label="Carb. g", expand=True, keyboard_type=ft.KeyboardType.NUMBER, **_fld)
        tf_mm_if = ft.TextField(label="Grasa g", expand=True, keyboard_type=ft.KeyboardType.NUMBER, **_fld)
        mm_items_col = ft.Column(spacing=6)
        mm_total_txt = ft.Text("", size=12, weight=ft.FontWeight.W_600, color="#24543F")
        mm_loading = ft.Row(
            [ft.ProgressRing(width=18, height=18, stroke_width=3), ft.Text("Calculando…", size=13, color="#77736A")],
            visible=False,
        )

        def _num_or_zero(v):
            v = str(v or "").replace(",", ".").strip()
            return float(v) if v else 0.0

        def render_mm_items():
            mm_items_col.controls.clear()
            items = mm_state["items"]
            for idx, it in enumerate(items):
                mm_items_col.controls.append(
                    ft.Container(
                        bgcolor="#FAF8F4", border_radius=8, padding=ft.Padding(left=10, right=4, top=6, bottom=6),
                        content=ft.Row(
                            [
                                ft.Column(
                                    [
                                        ft.Text(f"{it['name']} ({int(it['grams'])} g)", size=13,
                                                weight=ft.FontWeight.W_600, color="#1C1B18"),
                                        ft.Text(_mm_line(it), size=11, color="#77736A"),
                                    ],
                                    spacing=1, expand=True,
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.CLOSE, icon_color="#B8453A", icon_size=18,
                                    on_click=lambda e, i=idx: remove_mm_item(i),
                                ),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    )
                )
            if items:
                tot = {k: sum(i[k] for i in items) for k in ("kcal", "p", "c", "f")}
                mm_total_txt.value = "Total: " + _mm_line(tot)
            else:
                mm_total_txt.value = "Aún no has añadido alimentos."

        def remove_mm_item(i):
            if 0 <= i < len(mm_state["items"]):
                mm_state["items"].pop(i)
            render_mm_items()
            page.update()

        def add_mm_manual(e=None):
            name = (tf_mm_iname.value or "").strip()
            try:
                grams = _num_or_zero(tf_mm_igrams.value)
                k, p, c, f = (_num_or_zero(x.value) for x in (tf_mm_ikcal, tf_mm_ip, tf_mm_ic, tf_mm_if))
            except ValueError:
                notify("Usa números en gramos y macros", "#B8453A")
                return
            if not name or grams <= 0:
                notify("Pon el nombre y los gramos del alimento", "#B8453A")
                return
            if k <= 0:  # si no pones kcal se calculan con los macros
                k = 4 * p + 4 * c + 9 * f
            mm_state["items"].append(
                {"name": name, "grams": round(grams), "kcal": round(k, 1), "p": round(p, 1),
                 "c": round(c, 1), "f": round(f, 1)}
            )
            for fld in (tf_mm_iname, tf_mm_igrams, tf_mm_ikcal, tf_mm_ip, tf_mm_ic, tf_mm_if):
                fld.value = ""
            render_mm_items()
            page.update()

        async def add_mm_ai(e=None):
            text = (tf_mm_ai.value or "").strip()
            if not text:
                notify("Escribe lo que lleva la comida", "#B8453A")
                return
            mm_loading.visible = True
            page.update()
            try:
                items = await asyncio.to_thread(ai_parse_foods, text)
            except Exception as ex:
                mm_loading.visible = False
                page.update()
                notify(f"IA: {ex}", "#B8453A")
                return
            mm_state["items"].extend(items)
            tf_mm_ai.value = ""
            mm_loading.visible = False
            render_mm_items()
            page.update()

        mm_builder = ft.Container(visible=False)

        def close_mm_builder(e=None):
            mm_builder.visible = False
            page.update()

        def save_mm(e=None):
            name = (tf_mm_name.value or "").strip()
            if not name:
                notify("Ponle un nombre a la comida", "#B8453A")
                return
            if not mm_state["items"]:
                notify("Añade al menos un alimento", "#B8453A")
                return
            db.save_meal(current_user_id[0], name, selected_meal_for_add[0], list(mm_state["items"]))
            mm_state["items"] = []
            tf_mm_name.value = ""
            mm_builder.visible = False
            render_saved_meals()
            notify(f"'{name}' guardada en Mis comidas", "#3F7D58")
            page.update()

        mm_builder.content = ft.Container(
            bgcolor="#ffffff", border_radius=12, padding=14, border=CARD_BORDER,
            content=ft.Column(
                [
                    ft.Text("Nueva comida", size=15, weight=ft.FontWeight.W_600, color="#1C1B18"),
                    tf_mm_name,
                    tf_mm_ai,
                    make_button("✨ Añadir con IA", "#1C1B18", add_mm_ai),
                    mm_loading,
                    ft.Text("…o añade un alimento a mano (valores totales de esa cantidad):", size=11, color="#77736A"),
                    tf_mm_iname,
                    ft.Row([tf_mm_igrams, tf_mm_ikcal], spacing=8),
                    ft.Row([tf_mm_ip, tf_mm_ic, tf_mm_if], spacing=8),
                    make_button("➕ Añadir alimento", "#24543F", add_mm_manual),
                    ft.Divider(color="#E4DFD3"),
                    mm_items_col,
                    mm_total_txt,
                    ft.Row(
                        [
                            make_button("✅ Guardar comida", "#3F7D58", save_mm, expand=True),
                            make_button("✖ Cancelar", "#E4DFD3", close_mm_builder, text_color="#3A3832", expand=True),
                        ],
                        spacing=8,
                    ),
                ],
                spacing=8,
            ),
        )

        def open_mm_builder(e=None):
            mm_state["items"] = []
            tf_mm_name.value = ""
            tf_mm_ai.value = ""
            render_mm_items()
            mm_builder.visible = True
            page.update()

        def save_today_meal_as_fixed(e=None):
            mn = selected_meal_for_add[0]
            foods = [x for x in db.get_today_foods(current_user_id[0]) if x[1] == mn]
            save_fixed_dialog(
                [
                    {"name": x[2], "grams": x[3], "kcal": x[4], "p": x[5], "c": x[6], "f": x[7], "image": x[8]}
                    for x in foods
                ],
                f"{mn} habitual",
                mn,
            )

        my_meals_section = ft.Column(
            [
                ft.Text("Mis comidas", size=18, weight=ft.FontWeight.W_600, color="#1C1B18"),
                ft.Text(
                    "Registra tus comidas personalizadas y añádelas cuando quieras con +. "
                    "Se suman a la comida seleccionada arriba.",
                    size=12, color="#77736A",
                ),
                make_button("➕ Crear comida", "#24543F", open_mm_builder),
                mm_builder,
                saved_col,
                make_button("⭐ Guardar lo de hoy como comida", "#C9763F", save_today_meal_as_fixed),
                ft.Text(
                    "Guarda lo que ya has registrado hoy en la comida seleccionada arriba. "
                    "También puedes guardar una comida desde el Historial.",
                    size=11, color="#A9A59B",
                ),
            ],
            spacing=10,
        )

        view_add = ft.Container(
            padding=18,
            expand=True,
            content=ft.Column(
                [
                    ft.Text("Añadir alimento", size=24, weight=ft.FontWeight.W_600, color="#1C1B18"),
                    lbl_meal_dest,
                    meal_selector_row,
                    ft.Row(
                        [
                            tf_search,
                            ft.Container(
                                bgcolor="#24543F",
                                padding=14,
                                border_radius=10,
                                on_click=search_openfoodfacts,
                                content=ft.Icon(ft.Icons.SEARCH, size=20, color="white"),
                            ),
                        ]
                    ),
                    recents_box,
                    search_results,
                    ft.Divider(color="#E4DFD3"),
                    food_preview,
                    tf_name,
                    tf_grams,
                    ft.Row([tf_kcal, tf_p]),
                    ft.Row([tf_c, tf_f]),
                    make_button("Registrar alimento", "#24543F", save_food_entry),
                    ft.Divider(color="#E4DFD3"),
                    my_meals_section,
                ],
                scroll=ft.ScrollMode.AUTO,
                expand=True,
                spacing=10,
            ),
        )

        # ----- CAPA IA: registrar por texto/voz + sugerencias -----
        ai_state = {"items": [], "photo": None}
        ai_cam_btn = ft.IconButton(
            icon=ft.Icons.PHOTO_CAMERA_OUTLINED,
            icon_color="#24543F",
            tooltip="Hacer una foto a la comida",
        )
        _tf_ai_args = dict(
            label="¿Qué has comido?",
            hint_text=(
                "Ej: 2 rebanadas de pan integral, 50 g de pollo y 2 cucharadas de queso cottage. "
                "Con foto: añade lo que no se ve (p. ej. «los macarrones llevan chorizo»)"
            ),
            multiline=True, min_lines=3, max_lines=6,
            bgcolor="#ffffff", border_radius=10, border_color="#E4DFD3",
        )
        try:
            tf_ai = ft.TextField(**_tf_ai_args, suffix=ai_cam_btn)  # icono dentro del recuadro
            ai_input = tf_ai
        except TypeError:  # versión de Flet sin 'suffix': el icono va al lado
            tf_ai = ft.TextField(**_tf_ai_args, expand=True)
            ai_input = ft.Row([tf_ai, ai_cam_btn], vertical_alignment=ft.CrossAxisAlignment.START)
        ai_photo_box = ft.Container(visible=False)
        ai_camera_holder = ft.Container(visible=False)
        ai_loading_txt = ft.Text("Pensando…", size=13, color="#77736A")
        ai_loading = ft.Row(
            [ft.ProgressRing(width=18, height=18, stroke_width=3), ai_loading_txt],
            visible=False,
        )
        ai_result_col = ft.Column(spacing=8)
        ai_suggest_col = ft.Column(spacing=10)


        def set_busy(flag, msg="Pensando…"):
            ai_loading_txt.value = msg
            ai_loading.visible = flag
            page.update()

        def macro_line(it):
            return f"{int(it['kcal'])} kcal • P {it['p']:.1f}g • C {it['c']:.1f}g • G {it['f']:.1f}g"

        def remove_ai_item(idx):
            if 0 <= idx < len(ai_state["items"]):
                ai_state["items"].pop(idx)
            render_ai_items()
            page.update()

        def clear_ai_photo(e=None):
            ai_state["photo"] = None
            ai_photo_box.visible = False
            ai_photo_box.content = None
            page.update()

        def set_ai_photo(jpeg_bytes):
            ai_state["photo"] = jpeg_bytes
            thumb = ft.Image(
                src=base64.b64encode(jpeg_bytes).decode("ascii"),
                width=84, height=84, fit=ft.BoxFit.COVER, border_radius=10,
                error_content=ft.Icon(ft.Icons.IMAGE_OUTLINED, size=32, color="#A9A59B"),
            )
            ai_photo_box.content = ft.Container(
                bgcolor="#ffffff", border_radius=10, padding=10, border=CARD_BORDER,
                content=ft.Row(
                    [
                        thumb,
                        ft.Column(
                            [
                                ft.Text("Foto lista", size=14, weight=ft.FontWeight.W_600, color="#1C1B18"),
                                ft.Text(
                                    "Puedes añadir texto para aclarar ingredientes, o calcular solo con la foto.",
                                    size=11, color="#77736A",
                                ),
                            ],
                            spacing=2, expand=True,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.CLOSE, icon_color="#B8453A",
                            tooltip="Quitar foto", on_click=clear_ai_photo,
                        ),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )
            ai_photo_box.visible = True
            page.update()

        def close_ai_camera(e=None):
            ai_camera_holder.visible = False
            ai_camera_holder.content = None
            page.update()

        async def open_ai_camera(e=None):
            try:
                cam = fc.Camera(expand=True, preview_enabled=True)
            except Exception as ex:
                notify(f"Cámara no disponible: {ex}", "#B8453A")
                return

            async def snap(ev=None):
                try:
                    data = await cam.take_picture()
                except Exception as ex:
                    notify(f"No se pudo hacer la foto: {ex}", "#B8453A")
                    return
                if not data:
                    notify("No se obtuvo la foto, inténtalo otra vez", "#B8453A")
                    return
                close_ai_camera()
                set_ai_photo(await asyncio.to_thread(prepare_photo, data))

            ai_camera_holder.content = ft.Container(
                bgcolor="#ffffff", padding=14, border_radius=12, border=CARD_BORDER,
                content=ft.Column(
                    [
                        ft.Container(
                            height=320, border_radius=10, bgcolor="#1C1B18",
                            clip_behavior=ft.ClipBehavior.HARD_EDGE, content=cam,
                        ),
                        ft.Text("Encuadra el plato entero, con buena luz.", size=12, color="#77736A"),
                        ft.Row(
                            [
                                make_button("📸 Hacer foto", "#24543F", snap, expand=True),
                                make_button("✖ Cerrar", "#B8453A", close_ai_camera, expand=True),
                            ],
                            spacing=8,
                        ),
                    ],
                    spacing=10,
                ),
            )
            ai_camera_holder.visible = True
            page.update()
            try:
                cameras = await cam.get_available_cameras()
                if not cameras:
                    notify("No se encontró ninguna cámara", "#B8453A")
                    close_ai_camera()
                    return
                chosen = next(
                    (c for c in cameras if c.lens_direction == fc.CameraLensDirection.BACK),
                    cameras[0],
                )
                await cam.initialize(
                    description=chosen,
                    resolution_preset=fc.ResolutionPreset.HIGH,
                    enable_audio=False,
                )
            except Exception as ex:
                notify(f"No se pudo abrir la cámara: {ex}", "#B8453A")
                close_ai_camera()

        ai_cam_btn.on_click = open_ai_camera

        def discard_ai(e=None):
            ai_state["items"] = []
            render_ai_items()
            page.update()

        def register_ai(e=None):
            meal_dest = selected_meal_for_add[0]
            items = list(ai_state["items"])
            add_items_to_log(items, meal_dest)
            extra = ""
            ai_state["items"] = []
            tf_ai.value = ""
            ai_state["photo"] = None
            ai_photo_box.visible = False
            ai_photo_box.content = None
            render_ai_items()
            notify(f"{len(items)} alimento(s) añadidos a {meal_dest}{extra}", "#3F7D58")
            switch_tab(0)

        def render_ai_items():
            ai_result_col.controls.clear()
            items = ai_state["items"]
            if not items:
                return
            for idx, it in enumerate(items):
                ai_result_col.controls.append(
                    ft.Container(
                        bgcolor="#ffffff", border_radius=10, padding=12, shadow=CARD_SHADOW, border=CARD_BORDER,
                        content=ft.Row(
                            [
                                ft.Column(
                                    [
                                        ft.Text(f"{it['name']} (~{int(it['grams'])} g)", size=14, weight=ft.FontWeight.W_600, color="#1C1B18"),
                                        ft.Text(macro_line(it), size=11, color="#77736A"),
                                    ],
                                    spacing=2, expand=True,
                                ),
                                ft.Container(
                                    padding=6,
                                    on_click=lambda e, i=idx: remove_ai_item(i),
                                    content=ft.Icon(ft.Icons.CLOSE, size=18, color="#B8453A"),
                                ),
                            ]
                        ),
                    )
                )
            tot = {k: sum(i[k] for i in items) for k in ("kcal", "p", "c", "f")}
            ai_result_col.controls.append(
                ft.Container(
                    bgcolor="#1C1B18", border_radius=10, padding=12,
                    content=ft.Text(f"TOTAL: {macro_line(tot)}", size=12, weight=ft.FontWeight.W_600, color="white"),
                )
            )
            ai_result_col.controls.append(
                make_button(
                    "⭐ Guardar en Mis comidas", "#C9763F",
                    lambda e: save_fixed_dialog(list(ai_state["items"]), "Mi comida", selected_meal_for_add[0]),
                )
            )
            ai_result_col.controls.append(
                make_button(f"✅ Registrar en {selected_meal_for_add[0]}", "#3F7D58", register_ai)
            )
            ai_result_col.controls.append(
                make_button("Descartar", "#E4DFD3", discard_ai, text_color="#3A3832")
            )

        async def analyze_text(e=None):
            text = (tf_ai.value or "").strip()
            photo = ai_state.get("photo")
            if not text and not photo:
                notify("Escribe lo que has comido o haz una foto", "#B8453A")
                return
            close_ai_camera()
            set_busy(True, "Analizando la foto y calculando macros…" if photo else "Calculando pesos y macros…")
            try:
                ai_state["items"] = await asyncio.to_thread(ai_parse_foods, text, photo)
            except Exception as ex:
                set_busy(False)
                notify(f"IA: {ex}", "#B8453A")
                return
            render_ai_items()
            set_busy(False)

        def today_remaining():
            u = db.get_user(current_user_id[0])
            foods = db.get_today_foods(current_user_id[0])
            rem = {
                "kcal": u[10] - sum(x[4] for x in foods),
                "p": max(0, u[11] - sum(x[5] for x in foods)),
                "c": max(0, u[12] - sum(x[6] for x in foods)),
                "f": max(0, u[13] - sum(x[7] for x in foods)),
            }
            return rem, [x[2] for x in foods]

        # ----- PLAN DE RECETAS PARA LLEGAR EXACTO A LAS CALORÍAS -----
        plan_state = {"n": 0, "plans": [], "targets": [], "rem": 0, "done": set(), "switches": {}}
        split_row = ft.Row(spacing=6, wrap=True)
        tf_prefs = ft.TextField(
            label="Preferencias o alergias (opcional)",
            hint_text="Ej: sin lactosa, no me gusta el pescado, algo rápido",
            bgcolor="#ffffff", border_radius=10, border_color="#E4DFD3",
        )
        plan_loading_txt = ft.Text("Creando recetas…", size=13, color="#77736A")
        plan_loading = ft.Row(
            [ft.ProgressRing(width=18, height=18, stroke_width=3), plan_loading_txt], visible=False
        )

        def set_plan_busy(flag, msg="Creando recetas…"):
            plan_loading_txt.value = msg
            plan_loading.visible = flag
            page.update()

        def render_split_pills():
            split_row.controls.clear()
            for val, lbl in ((0, "Auto"), (1, "1 comida"), (2, "2 comidas"), (3, "3 comidas"), (4, "4 comidas")):
                active = plan_state["n"] == val
                split_row.controls.append(
                    ft.Container(
                        bgcolor="#24543F" if active else "#ffffff",
                        padding=10,
                        border_radius=10,
                        on_click=lambda e, v=val: (
                            plan_state.__setitem__("n", v),
                            render_split_pills(),
                            page.update(),
                        ),
                        content=ft.Text(
                            lbl, size=12, weight=ft.FontWeight.W_600,
                            color="white" if active else "#77736A",
                        ),
                    )
                )

        render_split_pills()

        def pick_meal_slots(n, foods, tkcal):
            """Qué comidas del día rellenar: la elegida (1) o las que más les falta por cubrir."""
            if n == 1:
                return [selected_meal_for_add[0]]
            order = list(MEALS_META.keys())
            deficit = {
                m: tkcal * MEALS_META[m]["ratio"] - sum(f[4] for f in foods if f[1] == m) for m in order
            }
            pool = sorted([m for m in order if m != "Snacks"], key=lambda m: -deficit[m])
            if n > len(pool):
                pool.append("Snacks")
            chosen = pool[:n]
            return [m for m in order if m in chosen]

        def register_plan_item(i):
            plan = plan_state["plans"][i]
            if plan is None or i in plan_state["done"]:
                return
            item = {
                "name": plan["title"], "grams": plan["grams"], "kcal": plan["kcal"],
                "p": plan["p"], "c": plan["c"], "f": plan["f"],
            }
            add_items_to_log([item], plan["meal"])
            sw = plan_state["switches"].get(i)
            if sw is not None and sw.value:
                db.save_meal(current_user_id[0], plan["title"], plan["meal"], [item])
            plan_state["done"].add(i)

        def plan_pending():
            return [i for i, p in enumerate(plan_state["plans"]) if p is not None and i not in plan_state["done"]]

        def on_register_one(i):
            plan = plan_state["plans"][i]
            register_plan_item(i)
            if not plan_pending():
                notify(f"Añadido a {plan['meal']}", "#3F7D58")
                switch_tab(0)
            else:
                render_plan()
                notify(f"Añadido a {plan['meal']}", "#3F7D58")

        def on_register_all(e=None):
            pending = plan_pending()
            for i in pending:
                register_plan_item(i)
            notify(f"{len(pending)} comida(s) añadidas", "#3F7D58")
            switch_tab(0)

        def render_plan():
            plans, targets, R = plan_state["plans"], plan_state["targets"], plan_state["rem"]
            plan_state["switches"] = {}
            col = ai_suggest_col
            col.controls.clear()
            ok = [p for p in plans if p]
            tot = {k: sum(p[k] for p in ok) for k in ("kcal", "p", "c", "f")}
            n = len(targets)
            col.controls.append(
                ft.Container(
                    bgcolor="#1C1B18", border_radius=12, padding=14,
                    content=ft.Column(
                        [
                            ft.Text(
                                f"Te quedan {R} kcal · plan de {n} comida{'s' if n > 1 else ''}",
                                size=13, weight=ft.FontWeight.W_600, color="white",
                            ),
                            ft.Text(
                                f"Cada comida cuadra con su objetivo (margen ±{PLAN_TOLERANCE} kcal).",
                                size=11, color="#A9A59B",
                            ),
                            ft.Text(f"Plan completo: {macro_line(tot)}", size=11, color="#E3B965"),
                        ],
                        spacing=3,
                    ),
                )
            )
            for i, (plan, tgt) in enumerate(zip(plans, targets)):
                if plan is None:
                    col.controls.append(
                        ft.Container(
                            bgcolor="#ffffff", border_radius=12, padding=14, border=CARD_BORDER,
                            content=ft.Text(
                                f"{tgt['meal']}: no pude ajustar una receta a {tgt['kcal']} kcal. "
                                "Pulsa «Generar otro plan».",
                                size=12, color="#B8453A",
                            ),
                        )
                    )
                    continue
                within = abs(plan["kcal"] - plan["target_kcal"]) <= PLAN_TOLERANCE
                ings = "\n".join(f"• {x['grams']} g {x['name']}" for x in plan["ingredients"])
                steps = "\n".join(f"{k + 1}. {st}" for k, st in enumerate(plan["steps"]))
                if i in plan_state["done"]:
                    action = ft.Container(
                        bgcolor="#E3ECE6", border_radius=10, padding=10,
                        content=ft.Row(
                            [
                                ft.Icon(ft.Icons.CHECK, size=18, color="#24543F"),
                                ft.Text(f"Registrada en {plan['meal']}", size=13, weight=ft.FontWeight.W_600, color="#24543F"),
                            ],
                            alignment=ft.MainAxisAlignment.CENTER,
                        ),
                    )
                else:
                    sw = ft.Switch(label="Guardar también en Mis comidas", value=False)
                    plan_state["switches"][i] = sw
                    action = ft.Column(
                        [sw, make_button(f"➕ Registrar en {plan['meal']}", "#3F7D58", lambda e, k=i: on_register_one(k))],
                        spacing=4,
                    )
                col.controls.append(
                    ft.Container(
                        bgcolor="#ffffff", border_radius=12, padding=14, shadow=CARD_SHADOW, border=CARD_BORDER,
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Container(
                                            bgcolor="#E3ECE6", border_radius=8,
                                            padding=ft.Padding(left=8, right=8, top=3, bottom=3),
                                            content=ft.Text(plan["meal"].upper(), size=10, weight=ft.FontWeight.W_600, color="#24543F"),
                                        ),
                                        ft.Text(
                                            f"{plan['kcal']} / {plan['target_kcal']} kcal",
                                            size=12, weight=ft.FontWeight.W_600,
                                            color="#3F7D58" if within else "#C9763F",
                                        ),
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                                ft.Text(plan["title"], size=15, weight=ft.FontWeight.W_600, color="#1C1B18"),
                                ft.Text(plan["description"], size=12, color="#77736A") if plan["description"] else ft.Container(),
                                ft.Text(macro_line(plan), size=12, weight=ft.FontWeight.W_600, color="#24543F"),
                                ft.Text("Ingredientes", size=11, weight=ft.FontWeight.W_600, color="#77736A"),
                                ft.Text(ings, size=12, color="#3A3832"),
                                ft.Text(steps, size=11, color="#5A574F") if steps else ft.Container(),
                                action,
                            ],
                            spacing=6,
                        ),
                    )
                )
            if len(plan_pending()) > 1:
                col.controls.append(make_button("✅ Registrar todo el plan", "#24543F", on_register_all))
            col.controls.append(
                make_button("🔄 Generar otro plan", "#E4DFD3", run_suggestions, text_color="#3A3832")
            )

        async def run_suggestions(e=None):
            u = db.get_user(current_user_id[0])
            foods = db.get_today_foods(current_user_id[0])
            rem, eaten = today_remaining()
            R = int(round(rem["kcal"]))
            if R < 60:
                ai_suggest_col.controls = [
                    ft.Text("Ya has llegado a tus calorías de hoy.", size=13, color="#3F7D58", weight=ft.FontWeight.W_600)
                ]
                page.update()
                return
            # Si no cabe en una comida (> MAX_MEAL_KCAL) se reparte en varias; Auto decide cuántas.
            n = plan_state["n"] or math.ceil(R / MAX_MEAL_KCAL)
            n = max(1, min(4, n, R // MIN_MEAL_KCAL))
            slots = pick_meal_slots(n, foods, u[10])
            kcals = split_calories(R, n)
            targets = [
                {
                    "meal": slots[i], "kcal": kcals[i],
                    "p": round(rem["p"] / n), "c": round(rem["c"] / n), "f": round(rem["f"] / n),
                }
                for i in range(n)
            ]
            set_plan_busy(True, f"Creando {n} receta{'s' if n > 1 else ''} de ~{kcals[0]} kcal…")
            try:
                plans = await asyncio.to_thread(ai_plan, targets, eaten, (tf_prefs.value or "").strip())
            except Exception as ex:
                set_plan_busy(False)
                notify(f"IA: {ex}", "#B8453A")
                return
            plan_state.update(plans=plans, targets=targets, rem=R, done=set())
            render_plan()
            set_plan_busy(False)

        async def go_suggestions(e=None):
            switch_tab(2)
            await run_suggestions()

        view_ai = ft.Container(
            padding=18,
            expand=True,
            content=ft.Column(
                [
                    ft.Text("Asistente", size=24, weight=ft.FontWeight.W_600, color="#1C1B18"),
                    ft.Text(
                        "Escribe, dicta o haz una foto de lo que has comido. Estimo pesos y macros.",
                        size=13, color="#77736A",
                    ),
                    ft.Text("Se registrará en:", size=12, weight=ft.FontWeight.W_600, color="#77736A"),
                    ai_meal_row,
                    ai_input,
                    ai_camera_holder,
                    ai_photo_box,
                    ft.Text(
                        "Para dictar, usa el micrófono del teclado. La cámara 📷 del recuadro analiza un plato; "
                        "el texto sirve para aclarar lo que no se ve.",
                        size=11, color="#A9A59B",
                    ),
                    make_button("✨ Calcular macros", "#1C1B18", analyze_text),
                    ai_loading,
                    ai_result_col,
                    ft.Divider(color="#E4DFD3"),
                    ft.Text("Completar el día", size=18, weight=ft.FontWeight.W_600, color="#1C1B18"),
                    ft.Text(
                        f"Te creo recetas para llegar justo a tus calorías de hoy (±{PLAN_TOLERANCE} kcal por comida). "
                        "Si son demasiadas para una sola comida, las reparto en varias.",
                        size=12, color="#77736A",
                    ),
                    ft.Text("Repartir en:", size=12, weight=ft.FontWeight.W_600, color="#77736A"),
                    split_row,
                    tf_prefs,
                    make_button("Generar plan de recetas", "#24543F", run_suggestions),
                    plan_loading,
                    ai_suggest_col,
                ],
                scroll=ft.ScrollMode.AUTO,
                expand=True,
                spacing=10,
            ),
        )

        # ----- CAPA 3: ESCÁNER QR Y CÓDIGO DE BARRAS (SIN ERRORES EN FLET) -----
        tf_barcode = ft.TextField(
            label="Código de barras (EAN-13) o JSON QR",
            bgcolor="#ffffff",
            border_radius=10,
            border_color="#E4DFD3",
        )

        camera_holder = ft.Container(visible=False)

        def parse_qr_json(code_str):
            try:
                data = json.loads(code_str)
                if isinstance(data, dict) and all(k in data for k in ("name", "kcal", "p", "c", "f")):
                    return data
            except Exception:
                pass
            return None

        async def process_code_string(code_str):
            code_str = (code_str or "").strip()
            if not code_str:
                notify("Introduce o escanea un código primero", "#B8453A")
                return
            data = parse_qr_json(code_str)
            if data:
                fill_food_form(data["name"], data["kcal"], data["p"], data["c"], data["f"])
                return
            if not code_str.isdigit():
                notify("Formato de código no reconocido", "#B8453A")
                return
            notify("Buscando producto…", "#24543F")
            try:
                prod = await asyncio.to_thread(lookup_product, code_str)
            except Exception:
                notify("Error al conectar con OpenFoodFacts", "#B8453A")
                return
            if not prod:
                notify("Código no encontrado en OpenFoodFacts", "#B8453A")
                return
            fill_food_form(prod["name"], prod["kcal"], prod["p"], prod["c"], prod["f"], prod["image"])

        async def search_barcode(e=None):
            await process_code_string(tf_barcode.value)

        scan_state = {"active": False, "gen": 0, "capturing": False}
        has_cv_barcode, has_zbar = barcode_engines()

        def close_camera_inline(ev=None):
            scan_state["active"] = False
            scan_state["gen"] += 1  # invalida cualquier bucle de escaneo en marcha
            camera_holder.visible = False
            camera_holder.content = None
            page.update()

        async def open_camera_inline(e):
            if not (has_cv_barcode or has_zbar):
                notify(
                    "Este servidor no lee códigos de barras (falta OpenCV ≥ 4.8 o pyzbar). Escribe el número.",
                    "#B8453A",
                )
            try:
                cam = fc.Camera(expand=True, preview_enabled=True)
            except Exception as ex:
                notify(f"Cámara no disponible: {ex}", "#B8453A")
                return

            scan_state["gen"] += 1
            my_gen = scan_state["gen"]
            status_lbl = ft.Text(
                "Apunta al código: se lee solo. Si tarda, pulsa Capturar con el código bien centrado.",
                size=12, color="#77736A",
            )

            async def handle_found(data):
                if not (scan_state["active"] and scan_state["gen"] == my_gen):
                    return
                scan_state["active"] = False
                tf_barcode.value = data
                close_camera_inline()
                await process_code_string(data)

            async def decode_and_handle(image_bytes, deep):
                data = await asyncio.to_thread(decode_frame, image_bytes, deep)
                if data:
                    await handle_found(data)

            async def auto_scan_loop():
                """Hace fotos sin parar y las decodifica en segundo plano (sin esperar a la anterior)."""
                if scan_state["gen"] != my_gen:
                    return
                scan_state["active"] = True
                await asyncio.sleep(0.4)
                tries = 0
                inflight = None
                while scan_state["active"] and scan_state["gen"] == my_gen:
                    if scan_state["capturing"]:
                        await asyncio.sleep(0.2)
                        continue
                    tries += 1
                    try:
                        image_bytes = await cam.take_picture()
                        if image_bytes and (inflight is None or inflight.done()):
                            # pasada rápida casi siempre; cada 4ª, profunda
                            inflight = asyncio.create_task(decode_and_handle(image_bytes, tries % 4 == 0))
                    except Exception as ex:
                        print("Error escaneo:", ex)
                    if tries == 30 and scan_state["gen"] == my_gen and not scan_state["capturing"]:
                        status_lbl.value = "Aún nada… acerca o aleja el código, con buena luz, y pulsa Capturar"
                        page.update()
                    await asyncio.sleep(0.05)

            async def capture_now(ev=None):
                """Captura manual: foto fija y lectura profunda (como el escáner de antes)."""
                if scan_state["capturing"]:
                    return
                scan_state["capturing"] = True
                status_lbl.value = "Leyendo el código…"
                page.update()
                try:
                    found = ""
                    for _ in range(2):
                        image_bytes = await cam.take_picture()
                        if image_bytes:
                            found = await asyncio.to_thread(decode_frame, image_bytes, True, 2000)
                            if found:
                                break
                        await asyncio.sleep(0.15)
                    if found:
                        await handle_found(found)
                    else:
                        status_lbl.value = (
                            "No pude leerlo. Céntralo, evita reflejos, ajusta la distancia y pulsa Capturar otra vez"
                        )
                        page.update()
                except Exception as ex:
                    status_lbl.value = f"Error al capturar: {ex}"
                    page.update()
                finally:
                    scan_state["capturing"] = False

            camera_holder.content = ft.Container(
                bgcolor="#ffffff",
                padding=14,
                border_radius=12,
                shadow=CARD_SHADOW, border=CARD_BORDER,
                content=ft.Column(
                    [
                        ft.Container(
                            height=280,
                            border_radius=10,
                            bgcolor="#1C1B18",
                            clip_behavior=ft.ClipBehavior.HARD_EDGE,
                            content=cam,
                        ),
                        status_lbl,
                        ft.Row(
                            [
                                make_button("📸 Capturar", "#24543F", capture_now, expand=True),
                                make_button("✖ Cerrar", "#B8453A", close_camera_inline, expand=True),
                            ],
                            spacing=8,
                        ),
                    ],
                    spacing=10,
                ),
            )
            camera_holder.visible = True
            page.update()

            # Inicializar la cámara (OBLIGATORIO antes de usarla)
            try:
                cameras = await cam.get_available_cameras()
                if not cameras:
                    notify("No se encontró ninguna cámara", "#B8453A")
                    close_camera_inline()
                    return
                # Preferir la cámara trasera
                chosen = next(
                    (c for c in cameras if c.lens_direction == fc.CameraLensDirection.BACK),
                    cameras[0],
                )
                await cam.initialize(
                    description=chosen,
                    resolution_preset=fc.ResolutionPreset.HIGH,
                    enable_audio=False,  # no pedir micrófono
                )
            except Exception as ex:
                notify(f"No se pudo abrir la cámara: {ex}", "#B8453A")
                close_camera_inline()
                return

            page.run_task(auto_scan_loop)

        view_scan = ft.Container(
            padding=18,
            expand=True,
            content=ft.Column(
                [
                    ft.Text("Escáner", size=24, weight=ft.FontWeight.W_600, color="#1C1B18"),
                    camera_holder,
                    ft.Container(
                        bgcolor="#ffffff",
                        padding=22,
                        border_radius=14,
                        shadow=CARD_SHADOW, border=CARD_BORDER,
                        content=ft.Column(
                            [
                                ft.Icon(ft.Icons.QR_CODE_SCANNER, size=44, color="#24543F"),
                                ft.Text(
                                    "Escanear código",
                                    size=18,
                                    weight=ft.FontWeight.W_600,
                                    color="#1C1B18",
                                ),
                                ft.Text(
                                    "Abre la cámara y apunta al código de barras o QR: se detecta solo, sin pulsar capturar. También puedes escribir el número debajo.",
                                    text_align="center",
                                    color="#77736A",
                                    size=13,
                                ),
                                make_button(
                                    "Abrir cámara", "#24543F", open_camera_inline
                                ),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=10,
                        ),
                    ),
                    ft.Text(
                        "Código de barras o JSON",
                        weight=ft.FontWeight.W_600,
                        color="#77736A",
                        size=13,
                    ),
                    tf_barcode,
                    make_button("Buscar producto", "#3F7D58", search_barcode),
                ],
                scroll=ft.ScrollMode.AUTO,
                expand=True,
                spacing=12,
            ),
        )

        # ----- CAPA 4: PERFIL DEL USUARIO ACTIVO -----
        profile_summary_col = ft.Column(spacing=12)

        def refresh_profile_tab():
            u = db.get_user(current_user_id[0])
            if not u:
                return
            uid, uname, uav, uw, uh, uage, ugen, uact, ugoal, uoff, ukcal, up, uc, uf, _ = u
            act_txt, act_mult = db.get_activity_extra(uid)
            act_label = f"{uact} (×{act_mult:.2f})" if act_mult else uact
            act_note = (
                ft.Text(f"“{act_txt}”", size=12, color="#77736A", italic=True, text_align="center")
                if act_txt
                else ft.Container()
            )
            profile_summary_col.controls = [
                ft.Container(
                    bgcolor="#ffffff",
                    padding=20,
                    border_radius=14,
                    shadow=CARD_SHADOW, border=CARD_BORDER,
                    content=ft.Column(
                        [
                            avatar_chip(uname, 64),
                            ft.Text(uname, size=22, weight=ft.FontWeight.W_600, color="#1C1B18"),
                            ft.Text(
                                f"{ugen} • {uage} años • {int(uw)} kg • {int(uh)} cm",
                                size=13,
                                color="#77736A",
                            ),
                            ft.Divider(color="#F4F1EA"),
                            ft.Text(
                                f"Actividad: {act_label}  |  Objetivo: {ugoal} ({(uoff or 0):+.0f} kcal)",
                                size=13,
                                weight=ft.FontWeight.W_600,
                                color="#24543F",
                            ),
                            act_note,
                            ft.Text(
                                f"Meta diaria: {int(ukcal)} kcal (P: {int(up)}g • C: {int(uc)}g • G: {int(uf)}g)",
                                size=13,
                                color="#3A3832",
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=6,
                    ),
                ),
                make_button(
                    "Recalcular objetivo",
                    "#24543F",
                    lambda e: show_onboarding_wizard(
                        is_first_user=False, edit_user_data=u
                    ),
                ),
                make_button(
                    "Cambiar de perfil",
                    "#3A3832",
                    show_user_selector,
                ),
                ft.Container(
                    alignment=ft.Alignment.CENTER,
                    content=ft.Text("Creado por ismael_prd", size=11, color="#A9A59B"),
                ),
            ]
            page.update()

        view_profile = ft.Container(
            padding=18,
            expand=True,
            content=ft.Column(
                [
                    ft.Text("Perfil", size=24, weight=ft.FontWeight.W_600, color="#1C1B18"),
                    profile_summary_col,
                ],
                scroll=ft.ScrollMode.AUTO,
                expand=True,
                spacing=12,
            ),
        )

        # ----- CAPA: HISTORIAL (ÚLTIMOS 7 DÍAS) -----
        history_open = [None]  # fecha (YYYY-MM-DD) del día desplegado
        history_chart = ft.Row(alignment=ft.MainAxisAlignment.SPACE_AROUND, vertical_alignment=ft.CrossAxisAlignment.END)
        history_summary = ft.Text("", size=13, color="#3A3832")
        history_col = ft.Column(spacing=10)

        def hist_items(m_foods):
            return [
                {"name": x[3], "grams": x[4], "kcal": x[5], "p": x[6], "c": x[7], "f": x[8], "image": x[9],
                 "gname": x[11], "gkey": x[10]}
                for x in m_foods
            ]

        def copy_meal_to_today(m_foods, m_name, label):
            add_items_to_log(hist_items(m_foods), m_name)
            notify(f"{m_name} de «{label}» copiado a hoy", "#3F7D58")
            refresh_history()

        def hist_btn(label, icon, cb):
            return ft.Container(
                padding=ft.Padding(left=10, right=10, top=6, bottom=6),
                border_radius=8, bgcolor="#E3ECE6", on_click=cb,
                content=ft.Row(
                    [
                        ft.Icon(icon, size=14, color="#24543F"),
                        ft.Text(label, size=12, weight=ft.FontWeight.W_600, color="#24543F"),
                    ],
                    spacing=5, tight=True,
                ),
            )

        def toggle_history_day(day_iso):
            history_open[0] = None if history_open[0] == day_iso else day_iso
            refresh_history()

        def refresh_history():
            u = db.get_user(current_user_id[0])
            if not u:
                return
            tkcal = max(1.0, float(u[10]))
            today = now_local().date()
            days = [today - timedelta(days=i) for i in range(6, -1, -1)]  # de más antiguo a hoy
            rows = db.get_foods_between(current_user_id[0], days[0].isoformat(), today.isoformat())
            by_day = {}
            for r in rows:
                by_day.setdefault(r[1], []).append(r)

            def tot(day_iso):
                fs = by_day.get(day_iso, [])
                return (sum(x[5] for x in fs), sum(x[6] for x in fs), sum(x[7] for x in fs), sum(x[8] for x in fs))

            # Gráfico de barras: kcal por día respecto a tu objetivo
            history_chart.controls = []
            for d in days:
                k = tot(d.isoformat())[0]
                h = 4 if k <= 0 else max(8, int(70 * min(1.3, k / tkcal)))
                color = "#E4DFD3" if k <= 0 else ("#3F7D58" if k <= tkcal * 1.05 else "#C9763F")
                history_chart.controls.append(
                    ft.Column(
                        [
                            ft.Container(width=22, height=h, bgcolor=color, border_radius=5),
                            ft.Text(
                                DIA_INICIAL[d.weekday()], size=12,
                                weight=ft.FontWeight.W_600 if d == today else ft.FontWeight.NORMAL,
                                color="#24543F" if d == today else "#77736A",
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=4,
                    )
                )

            logged = [d for d in days if by_day.get(d.isoformat())]
            if logged:
                n = len(logged)
                sk = sum(tot(d.isoformat())[0] for d in logged) / n
                sp = sum(tot(d.isoformat())[1] for d in logged) / n
                sc = sum(tot(d.isoformat())[2] for d in logged) / n
                sf = sum(tot(d.isoformat())[3] for d in logged) / n
                history_summary.value = (
                    f"Media de {n} {'día' if n == 1 else 'días'} con registro: {int(sk)} kcal • "
                    f"P {int(sp)}g • C {int(sc)}g • G {int(sf)}g"
                )
            else:
                history_summary.value = "Aún no hay registros en los últimos 7 días."

            # Tarjetas por día (hoy primero)
            history_col.controls = []
            for d in reversed(days):
                iso = d.isoformat()
                k, p_, c_, f_ = tot(iso)
                if d == today:
                    label = "Hoy"
                elif d == today - timedelta(days=1):
                    label = "Ayer"
                else:
                    label = date_es_short(d).capitalize()
                is_open = history_open[0] == iso
                has = bool(by_day.get(iso))
                header = ft.Container(
                    padding=14,
                    on_click=(lambda e, i=iso: toggle_history_day(i)) if has else None,
                    content=ft.Row(
                        [
                            ft.Column(
                                [
                                    ft.Text(label, size=15, weight=ft.FontWeight.W_600, color="#1C1B18"),
                                    ft.Text(
                                        f"P {int(p_)}g • C {int(c_)}g • G {int(f_)}g" if has else "Sin registros",
                                        size=12, color="#77736A",
                                    ),
                                ],
                                spacing=2, expand=True,
                            ),
                            ft.Text(
                                f"{int(k)} / {int(tkcal)} kcal" if has else "—",
                                size=13, weight=ft.FontWeight.W_600, color="#24543F",
                            ),
                            ft.Icon(
                                ft.Icons.EXPAND_LESS if is_open else ft.Icons.EXPAND_MORE,
                                size=20, color="#A9A59B", visible=has,
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                )
                card_items = [header]
                if is_open and has:
                    order = list(MEALS_META.keys())
                    for r in by_day[iso]:
                        if r[2] not in order:
                            order.append(r[2])
                    for m_name in order:
                        m_foods = [r for r in by_day[iso] if r[2] == m_name]
                        if not m_foods:
                            continue
                        card_items.append(
                            ft.Container(
                                bgcolor="#FAF8F4",
                                padding=ft.Padding(left=14, right=14, top=8, bottom=8),
                                content=ft.Column(
                                    [
                                        ft.Text(
                                            f"{m_name} • {int(sum(x[5] for x in m_foods))} kcal",
                                            size=12, weight=ft.FontWeight.W_600, color="#24543F",
                                        ),
                                        *[
                                            ft.Text(
                                                f"• {x[3]} ({int(x[4])}g) — {int(x[5])} kcal",
                                                size=12, color="#3A3832",
                                            )
                                            for x in m_foods
                                        ],
                                        ft.Row(
                                            ([] if d == today else [
                                                hist_btn(
                                                    "Copiar a hoy", ft.Icons.CONTENT_COPY,
                                                    lambda e, mf=m_foods, mn=m_name, lb=label: copy_meal_to_today(mf, mn, lb),
                                                )
                                            ])
                                            + [
                                                hist_btn(
                                                    "Guardar en Mis comidas", ft.Icons.STAR_OUTLINE,
                                                    lambda e, mf=m_foods, mn=m_name, lb=label: save_fixed_dialog(
                                                        hist_items(mf), f"{mn} ({lb})", mn
                                                    ),
                                                )
                                            ],
                                            spacing=8, wrap=True,
                                        ),
                                    ],
                                    spacing=3,
                                ),
                            )
                        )
                history_col.controls.append(
                    ft.Container(
                        bgcolor="#ffffff", border_radius=12, border=CARD_BORDER,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                        content=ft.Column(card_items, spacing=0),
                    )
                )
            page.update()

        view_history = ft.Container(
            padding=18,
            expand=True,
            content=ft.Column(
                [
                    ft.Text("Últimos 7 días", size=24, weight=ft.FontWeight.W_600, color="#1C1B18"),
                    ft.Container(
                        bgcolor="#ffffff", padding=16, border_radius=14, border=CARD_BORDER,
                        content=ft.Column(
                            [
                                history_chart,
                                ft.Text(
                                    "Verde: dentro de tu objetivo • Naranja: te pasaste",
                                    size=11, color="#A9A59B", text_align=ft.TextAlign.CENTER,
                                ),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=8,
                        ),
                    ),
                    history_summary,
                    history_col,
                ],
                scroll=ft.ScrollMode.AUTO,
                expand=True,
                spacing=12,
            ),
        )

        # ----- NAVEGACIÓN INFERIOR -----
        nav_index = [0]
        content_area = ft.Container(expand=True)
        views_list = [view_home, view_add, view_ai, view_scan, view_history, view_profile]
        nav_bar = ft.NavigationBar(
            selected_index=0,
            bgcolor="#FFFFFF",
            indicator_color="#E3ECE6",
            elevation=0,
            height=64,
            border=ft.Border(top=ft.BorderSide(1, "#E4DFD3")),
            label_behavior=ft.NavigationBarLabelBehavior.ALWAYS_SHOW,
            on_change=lambda e: switch_tab(e.control.selected_index),
            destinations=[
                ft.NavigationBarDestination(icon=ft.Icons.HOME_OUTLINED, selected_icon=ft.Icons.HOME, label="Hoy"),
                ft.NavigationBarDestination(icon=ft.Icons.ADD_CIRCLE_OUTLINE, selected_icon=ft.Icons.ADD_CIRCLE, label="Añadir"),
                ft.NavigationBarDestination(icon=ft.Icons.AUTO_AWESOME_OUTLINED, selected_icon=ft.Icons.AUTO_AWESOME, label="IA"),
                ft.NavigationBarDestination(icon=ft.Icons.QR_CODE_SCANNER, selected_icon=ft.Icons.QR_CODE_SCANNER, label="Escáner"),
                ft.NavigationBarDestination(icon=ft.Icons.HISTORY, selected_icon=ft.Icons.HISTORY, label="Historial"),
                ft.NavigationBarDestination(icon=ft.Icons.PERSON_OUTLINE, selected_icon=ft.Icons.PERSON, label="Perfil"),
            ],
        )

        def switch_tab(index):
            nav_index[0] = index
            content_area.content = views_list[index]
            nav_bar.selected_index = index
            if index != 3 and scan_state["active"]:
                close_camera_inline()
            if index != 2 and ai_camera_holder.visible:
                close_ai_camera()
            if index == 0:
                refresh_dashboard()
            elif index == 1:
                render_saved_meals()
                render_recents(tf_search.value or "")
            elif index == 4:
                refresh_history()
            elif index == 5:
                refresh_profile_tab()
            page.update()

        root_container.content = ft.Column(
            [top_bar, content_area, nav_bar],
            spacing=0,
            expand=True,
        )

        def _new_day():
            """Se ejecuta a las 00:00: nuevo día, contadores a cero y fecha actualizada."""
            if current_user_id[0] is None:
                return
            expanded_meal[0] = None
            history_open[0] = None
            refresh_dashboard()
            if nav_index[0] == 4:
                refresh_history()
            notify(f"Nuevo día: {date_es_short(now_local().date())}", "#24543F")

        on_new_day[0] = _new_day
        switch_tab(0)

    # ==========================================
    # MONTAJE INICIAL
    # ==========================================
    async def day_watcher():
        """Despierta justo después de las 00:00 y avisa a la pantalla abierta."""
        last = today_str()
        while True:
            now = now_local()
            nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=1, microsecond=0)
            await asyncio.sleep(min(30.0, max(1.0, (nxt - now).total_seconds())))
            cur = today_str()
            if cur != last:
                last = cur
                cb = on_new_day[0]
                if cb:
                    try:
                        cb()
                    except Exception:
                        pass

    page.add(status_banner, root_container)
    page.run_task(day_watcher)
    show_user_selector()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8550))
    ft.run(main, view=ft.AppView.WEB_BROWSER, port=port, host="0.0.0.0")