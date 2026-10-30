import asyncio
import json
import os
import re
import sqlite3
import threading
import time
import cv2
import numpy as np
import flet as ft
import flet_camera as fc
import requests


# ==========================================
# BASE DE DATOS MULTIUSUARIO (SQLite)
# ==========================================
class DatabaseManager:

    def __init__(self, db_name="gym_macros_mobile.db"):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()

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
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS saved_meals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                name TEXT,
                meal_type TEXT,
                items TEXT
            )
        """)
        self.conn.commit()

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
        self.conn.commit()
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
        self.conn.commit()

    def delete_user(self, user_id):
        self.cursor.execute("DELETE FROM food_log WHERE user_id = ?", (user_id,))
        self.cursor.execute("DELETE FROM saved_meals WHERE user_id = ?", (user_id,))
        self.cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.conn.commit()

    def add_food(self, user_id, meal_type, name, grams, kcal, p, c, f, image=None):
        self.cursor.execute(
            """
            INSERT INTO food_log (user_id, meal_type, name, grams, kcal, p, c, f, image)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (user_id, meal_type, name, grams, kcal, p, c, f, image),
        )
        self.conn.commit()

    def get_today_foods(self, user_id):
        self.cursor.execute(
            """
            SELECT id, COALESCE(meal_type, 'Almuerzo'), name, grams, kcal, p, c, f, image
            FROM food_log
            WHERE user_id = ? AND date = DATE('now', 'localtime')
        """,
            (user_id,),
        )
        return self.cursor.fetchall()

    def delete_food(self, food_id):
        self.cursor.execute("DELETE FROM food_log WHERE id = ?", (food_id,))
        self.conn.commit()

    # ----- Comidas fijas -----
    def save_meal(self, user_id, name, meal_type, items):
        self.cursor.execute(
            "INSERT INTO saved_meals (user_id, name, meal_type, items) VALUES (?, ?, ?, ?)",
            (user_id, name, meal_type, json.dumps(items, ensure_ascii=False)),
        )
        self.conn.commit()

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
        self.conn.commit()


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


def compute_macros(weight, height, age, gender, activity, goal_name):
    if gender == "Hombre":
        bmr = 88.362 + (13.397 * weight) + (4.799 * height) - (5.677 * age)
    else:
        bmr = 447.593 + (9.247 * weight) + (3.098 * height) - (4.330 * age)

    mult = ACT_MULTIPLIERS.get(activity, ACT_MULTIPLIERS["Moderado"])["mult"]
    offset = GOALS_META.get(goal_name, GOALS_META["Mantenimiento"])["offset"]

    tdee = bmr * mult
    target_kcal = max(1200.0, round(tdee + offset))
    target_p = round(weight * 2.0)  # 2.0g/kg proteína
    target_f = round(weight * 0.9)  # 0.9g/kg grasas
    target_c = max(0.0, round((target_kcal - (target_p * 4) - (target_f * 9)) / 4))

    return target_kcal, target_p, target_c, target_f, offset


# ==========================================
# IA (Gemini) + UTILIDADES
# ==========================================
AI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")


def ask_ai(system_prompt, user_text, max_tokens=1800):
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Falta la variable GEMINI_API_KEY en Render")
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{AI_MODEL}:generateContent",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": max_tokens,
                "temperature": 0.4,
            },
        },
        timeout=45,
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

SUGGEST_PROMPT = (
    "Eres un nutricionista deportivo. Propón EXACTAMENTE 3 opciones (comida, snack o mini-receta) "
    "para que el usuario complete su día SIN SUPERAR las calorías restantes. Devuelve SOLO JSON: "
    '{"options":[{"title":"...","description":"una frase","grams":0,"kcal":0,"p":0,"c":0,"f":0,'
    '"ingredients":["cantidad + ingrediente"],"steps":["paso corto"]}]}. Reglas: las kcal de cada '
    "opción deben ser MENORES O IGUALES que las calorías restantes (idealmente entre el 70% y el "
    "100%); prioriza cubrir la proteína restante sin pasarte de carbohidratos ni grasas restantes; "
    "ingredientes comunes y baratos; máximo 4 pasos breves; los macros son totales de la opción."
)


def ai_parse_foods(description):
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
        raise ValueError("No pude identificar alimentos. Prueba a describirlo de otra forma.")
    return items


def ai_suggest(remaining, eaten_names, meal_name):
    user_text = (
        f"Tipo de comida: {meal_name}. Restante hoy: {int(remaining['kcal'])} kcal, "
        f"{int(remaining['p'])} g proteína, {int(remaining['c'])} g carbohidratos, "
        f"{int(remaining['f'])} g grasa. Ya ha comido hoy: {', '.join(eaten_names) or 'nada aún'}."
    )
    data = extract_json(ask_ai(SUGGEST_PROMPT, user_text, max_tokens=2200))
    options = []
    for o in data.get("options", []):
        kcal = _num(o.get("kcal"))
        # Garantía de "sin pasarte": descartamos lo que supere lo restante
        if o.get("title") and 0 < kcal <= remaining["kcal"]:
            options.append(
                {
                    "title": str(o["title"]),
                    "description": str(o.get("description", "")),
                    "grams": round(_num(o.get("grams")) or 100),
                    "kcal": round(kcal, 1),
                    "p": round(_num(o.get("p")), 1),
                    "c": round(_num(o.get("c")), 1),
                    "f": round(_num(o.get("f")), 1),
                    "ingredients": [str(x) for x in o.get("ingredients", [])][:10],
                    "steps": [str(x) for x in o.get("steps", [])][:5],
                }
            )
    return options


BTN_ICONS = {
    "➕": ft.Icons.ADD, "✅": ft.Icons.CHECK, "⭐": ft.Icons.STAR_OUTLINE,
    "🔍": ft.Icons.SEARCH, "📸": ft.Icons.PHOTO_CAMERA_OUTLINED, "✖": ft.Icons.CLOSE,
    "⬅": ft.Icons.ARROW_BACK, "➡": ft.Icons.ARROW_FORWARD, "💡": ft.Icons.LIGHTBULB_OUTLINE,
    "✨": ft.Icons.AUTO_AWESOME_OUTLINED, "⚡": ft.Icons.TUNE, "👥": ft.Icons.SWAP_HORIZ,
    "🚀": ft.Icons.CHECK,
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
    selected_meal_for_add = ["Desayuno"]
    expanded_meal = [None]

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
                        "¿Quién entrena hoy?",
                        size=28,
                        weight=ft.FontWeight.W_600,
                        color="#1C1B18",
                    ),
                    ft.Text(
                        "Elige un perfil o crea uno nuevo.",
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
                    ft.Container(height=12),
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
            "goal": edit_user_data[8] if edit_user_data else "Mantenimiento",
        }

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
                # PASO 3: Nivel de Actividad
                act_col = ft.Column(spacing=10)
                for act_name, meta in ACT_MULTIPLIERS.items():
                    active = wiz_state["activity"] == act_name
                    act_col.controls.append(
                        ft.Container(
                            bgcolor="#24543F" if active else "#ffffff",
                            padding=16,
                            border_radius=12,
                            shadow=CARD_SHADOW, border=CARD_BORDER,
                            on_click=lambda e, an=act_name: (
                                wiz_state.update({"activity": an}),
                                render_wizard_step(),
                            ),
                            content=ft.Row(
                                [
                                    ft.Icon(meta["icon"], size=24, color="white" if active else "#24543F"),
                                    ft.Container(width=8),
                                    ft.Column(
                                        [
                                            ft.Text(
                                                act_name,
                                                size=16,
                                                weight=ft.FontWeight.W_600,
                                                color="white"
                                                if active
                                                else "#1C1B18",
                                            ),
                                            ft.Text(
                                                meta["desc"],
                                                size=12,
                                                color="#CFE0D5"
                                                if active
                                                else "#77736A",
                                            ),
                                        ],
                                        spacing=2,
                                        expand=True,
                                    ),
                                ]
                            ),
                        )
                    )

                wiz_body.content = ft.Column(
                    [
                        ft.Text(
                            "Nivel de actividad",
                            size=26,
                            weight=ft.FontWeight.W_600,
                            color="#1C1B18",
                        ),
                        ft.Text(
                            "¿Cuánto te mueves durante la semana?",
                            size=13,
                            color="#77736A",
                        ),
                        act_col,
                    ],
                    scroll=ft.ScrollMode.AUTO,
                    spacing=12,
                )

            elif s == 3:
                # PASO 4: Objetivo y Cálculo Instantáneo
                tkcal, tp, tc, tf, _ = compute_macros(
                    wiz_state["weight"],
                    wiz_state["height"],
                    wiz_state["age"],
                    wiz_state["gender"],
                    wiz_state["activity"],
                    wiz_state["goal"],
                )

                goals_col = ft.Column(spacing=10)
                for g_name, meta in GOALS_META.items():
                    active = wiz_state["goal"] == g_name
                    goals_col.controls.append(
                        ft.Container(
                            bgcolor="#24543F" if active else "#ffffff",
                            padding=14,
                            border_radius=12,
                            shadow=CARD_SHADOW, border=CARD_BORDER,
                            on_click=lambda e, gn=g_name: (
                                wiz_state.update({"goal": gn}),
                                render_wizard_step(),
                            ),
                            content=ft.Row(
                                [
                                    ft.Icon(meta["icon"], size=24, color="white" if active else "#24543F"),
                                    ft.Container(width=8),
                                    ft.Column(
                                        [
                                            ft.Text(
                                                g_name,
                                                size=16,
                                                weight=ft.FontWeight.W_600,
                                                color="white"
                                                if active
                                                else "#1C1B18",
                                            ),
                                            ft.Text(
                                                meta["desc"],
                                                size=12,
                                                color="#CFE0D5"
                                                if active
                                                else "#77736A",
                                            ),
                                        ],
                                        spacing=2,
                                        expand=True,
                                    ),
                                ]
                            ),
                        )
                    )

                summary_card = ft.Container(
                    bgcolor="#1C1B18",
                    padding=20,
                    border_radius=14,
                    content=ft.Column(
                        [
                            ft.Text(
                                "OBJETIVO DIARIO",
                                size=12,
                                color="#E3B965",
                                weight=ft.FontWeight.W_600,
                            ),
                            ft.Text(
                                f"{int(tkcal):,} kcal".replace(",", "."),
                                size=34,
                                weight=ft.FontWeight.W_600,
                                color="white",
                            ),
                            ft.Divider(color="#3A3832"),
                            ft.Row(
                                [
                                    ft.Column(
                                        [
                                            ft.Text(
                                                f"{int(tc)}g",
                                                size=18,
                                                weight=ft.FontWeight.W_600,
                                                color="#E3B965",
                                            ),
                                            ft.Text(
                                                "Carbos",
                                                size=12,
                                                color="#A9A59B",
                                            ),
                                        ],
                                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                    ),
                                    ft.Column(
                                        [
                                            ft.Text(
                                                f"{int(tp)}g",
                                                size=18,
                                                weight=ft.FontWeight.W_600,
                                                color="#3F7D58",
                                            ),
                                            ft.Text(
                                                "Proteína",
                                                size=12,
                                                color="#A9A59B",
                                            ),
                                        ],
                                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                    ),
                                    ft.Column(
                                        [
                                            ft.Text(
                                                f"{int(tf)}g",
                                                size=18,
                                                weight=ft.FontWeight.W_600,
                                                color="#C9763F",
                                            ),
                                            ft.Text(
                                                "Grasas",
                                                size=12,
                                                color="#A9A59B",
                                            ),
                                        ],
                                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                    ),
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_AROUND,
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=6,
                    ),
                )

                wiz_body.content = ft.Column(
                    [
                        ft.Text(
                            "¿Cuál es tu objetivo?",
                            size=26,
                            weight=ft.FontWeight.W_600,
                            color="#1C1B18",
                        ),
                        goals_col,
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
            step[0] += 1
            render_wizard_step()

        def prev_step():
            step[0] -= 1
            render_wizard_step()

        def finish_wizard():
            name_clean = (wiz_state["name"] or "Atleta").strip()
            tkcal, tp, tc, tf, offset = compute_macros(
                wiz_state["weight"],
                wiz_state["height"],
                wiz_state["age"],
                wiz_state["gender"],
                wiz_state["activity"],
                wiz_state["goal"],
            )
            if wiz_state["id"] is not None:
                db.update_user(
                    wiz_state["id"],
                    name_clean,
                    wiz_state["avatar"],
                    wiz_state["weight"],
                    wiz_state["height"],
                    wiz_state["age"],
                    wiz_state["gender"],
                    wiz_state["activity"],
                    wiz_state["goal"],
                    offset,
                    tkcal,
                    tp,
                    tc,
                    tf,
                )
                uid = wiz_state["id"]
                notify("Perfil actualizado con éxito", "#3F7D58")
            else:
                uid = db.create_user(
                    name_clean,
                    wiz_state["avatar"],
                    wiz_state["weight"],
                    wiz_state["height"],
                    wiz_state["age"],
                    wiz_state["gender"],
                    wiz_state["activity"],
                    wiz_state["goal"],
                    offset,
                    tkcal,
                    tp,
                    tc,
                    tf,
                )
                notify(f"¡Bienvenido/a {name_clean}!", "#3F7D58")

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

        def refresh_dashboard():
            u = db.get_user(current_user_id[0])
            if not u:
                return
            _, uname, uavatar, _, _, _, _, _, _, _, tkcal, tp, tc, tf, burned = u
            header_user_txt.value = uname
            header_avatar_txt.value = (uname[:1] or "?").upper()

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
                        for fid, _, fname, fgrams, fkcal, fp, fc_val, ff, fimg in m_foods:
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
                                                on_click=lambda e, i=fid: delete_food_item(
                                                    i
                                                ),
                                                content=ft.Icon(ft.Icons.CLOSE, size=18, color="#B8453A"),
                                            ),
                                        ]
                                    ),
                                )
                            )
                        meals_card_col.controls.append(
                            ft.Container(
                                bgcolor="#FAF8F4",
                                padding=ft.Padding(left=10, right=10, top=0, bottom=10),
                                content=make_button(
                                    "⭐ Guardar como comida fija",
                                    "#C9763F",
                                    lambda e, mn=m_name, mf=m_foods: save_fixed_dialog(
                                        [
                                            {
                                                "name": x[2], "grams": x[3], "kcal": x[4],
                                                "p": x[5], "c": x[6], "f": x[7], "image": x[8],
                                            }
                                            for x in mf
                                        ],
                                        f"{mn} habitual",
                                        mn,
                                    ),
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

        suggest_btn = make_button(
            "💡 ¿Qué como para llegar a mis calorías?", "#1C1B18", _go_sugg
        )

        view_home = ft.Container(
            padding=16,
            expand=True,
            content=ft.Column(
                [
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

        def render_meal_selector_pills():
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
            label="Buscar (ej. Pollo, Arroz, Avena, Huevo)",
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

        def fill_food_form(name, kcal, p, c, f, image=None):
            set_food_image(image)
            tf_name.value = name
            tf_grams.value = "100"
            tf_kcal.value = str(round(float(kcal or 0), 1))
            tf_p.value = str(round(float(p or 0), 1))
            tf_c.value = str(round(float(c or 0), 1))
            tf_f.value = str(round(float(f or 0), 1))
            search_results.controls.clear()
            switch_tab(1)
            notify(f"Cargado: {name}")

        GYM_FOODS_DB = {
            "pollo": [
                {"name": "Pechuga de Pollo", "kcal": 105, "p": 23.0, "c": 0.0, "f": 1.2},
                {"name": "Muslo de Pollo", "kcal": 177, "p": 20.0, "c": 0.0, "f": 10.5},
            ],
            "arroz": [
                {"name": "Arroz Blanco (Cocido)", "kcal": 130, "p": 2.7, "c": 28.0, "f": 0.3},
                {"name": "Arroz Blanco (Crudo)", "kcal": 350, "p": 7.0, "c": 78.0, "f": 1.0},
            ],
            "avena": [
                {"name": "Copos de Avena", "kcal": 389, "p": 16.9, "c": 66.3, "f": 6.9}
            ],
            "atun": [
                {"name": "Atún al Natural", "kcal": 103, "p": 24.0, "c": 0.0, "f": 1.0},
                {"name": "Atún en Aceite", "kcal": 190, "p": 22.0, "c": 0.0, "f": 11.0},
            ],
            "huevo": [
                {"name": "Huevo Entero", "kcal": 155, "p": 13.0, "c": 1.1, "f": 11.0},
                {"name": "Claras de Huevo", "kcal": 52, "p": 11.0, "c": 0.7, "f": 0.2},
            ],
            "platano": [
                {"name": "Plátano", "kcal": 89, "p": 1.1, "c": 22.8, "f": 0.3}
            ],
        }

        def search_openfoodfacts(e):
            query = (tf_search.value or "").strip().lower()
            if not query:
                return
            search_results.controls.clear()
            search_results.controls.append(
                ft.Text("Buscando...", color="#24543F", size=13)
            )
            page.update()
            search_results.controls.clear()

            for key, items in GYM_FOODS_DB.items():
                if key in query or query in key:
                    for item in items:
                        search_results.controls.append(
                            ft.Container(
                                bgcolor="#ffffff",
                                padding=12,
                                border_radius=10,
                                shadow=CARD_SHADOW, border=CARD_BORDER,
                                on_click=lambda e, n=item["name"], k=item["kcal"], p=item["p"], c=item["c"], f=item["f"]: fill_food_form(
                                    n, k, p, c, f
                                ),
                                content=ft.Row(
                                    [
                                        ft.Column(
                                            [
                                                ft.Text(
                                                    item['name'],
                                                    weight=ft.FontWeight.W_600,
                                                    size=14,
                                                    color="#1C1B18",
                                                ),
                                                ft.Text(
                                                    f"100g: {item['kcal']} kcal | C: {item['c']}g | P: {item['p']}g | G: {item['f']}g",
                                                    size=11,
                                                    color="#77736A",
                                                ),
                                            ],
                                            expand=True,
                                        ),
                                        ft.Text("Elegir", color="#24543F", weight=ft.FontWeight.W_600),
                                    ]
                                ),
                            )
                        )

            try:
                url = f"https://world.openfoodfacts.org/cgi/search.pl?search_terms={query}&search_simple=1&action=process&json=1&page_size=4"
                resp = requests.get(url, headers=OFF_HEADERS, timeout=6).json()
                for prod in resp.get("products", []):
                    pname = prod.get("product_name") or "Alimento"
                    img = prod.get("image_front_small_url") or prod.get("image_url")
                    nutr = prod.get("nutriments", {})
                    k = nutr.get("energy-kcal_100g", 0)
                    p = nutr.get("proteins_100g", 0)
                    c = nutr.get("carbohydrates_100g", 0)
                    f = nutr.get("fat_100g", 0)
                    search_results.controls.append(
                        ft.Container(
                            bgcolor="#ffffff",
                            padding=12,
                            border_radius=10,
                            on_click=lambda e, n=pname, k=k, p=p, c=c, f=f, im=img: fill_food_form(
                                n, k, p, c, f, im
                            ),
                            content=ft.Row(
                                [
                                    food_thumb(img),
                                    ft.Column(
                                        [
                                            ft.Text(
                                                pname,
                                                weight=ft.FontWeight.W_600,
                                                size=13,
                                                color="#3A3832",
                                            ),
                                            ft.Text(
                                                f"100g: {int(k or 0)} kcal | C: {c or 0}g | P: {p or 0}g | G: {f or 0}g",
                                                size=11,
                                                color="#77736A",
                                            ),
                                        ],
                                        expand=True,
                                    ),
                                    ft.Text("Elegir", color="#D9A441", size=12),
                                ]
                            ),
                        )
                    )
            except Exception:
                pass

            if not search_results.controls:
                search_results.controls.append(
                    ft.Text("Sin resultados.", color="#77736A")
                )
            page.update()

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
                for field in (tf_name, tf_kcal, tf_p, tf_c, tf_f):
                    field.value = ""
                tf_grams.value = "100"
                set_food_image(None)
                expanded_meal[0] = meal_dest
                notify(f"Añadido a {meal_dest}", "#3F7D58")
                switch_tab(0)
            except ValueError:
                notify("Rellena los gramos y macros con números", "#B8453A")

        # ----- COMIDAS FIJAS -----
        saved_col = ft.Column(spacing=8)

        def add_items_to_log(items, meal_dest):
            for it in items:
                db.add_food(
                    current_user_id[0], meal_dest, it["name"], it["grams"],
                    it["kcal"], it["p"], it["c"], it["f"], it.get("image"),
                )
            expanded_meal[0] = meal_dest

        def save_fixed_dialog(items, default_name="Mi comida", meal_type=None):
            if not items:
                notify("No hay alimentos que guardar", "#B8453A")
                return
            name_field = ft.TextField(label="Nombre", value=default_name, autofocus=True)

            def do_save(e):
                nm = (name_field.value or "").strip() or default_name
                db.save_meal(current_user_id[0], nm, meal_type or selected_meal_for_add[0], items)
                page.pop_dialog()
                notify(f"'{nm}' guardada en comidas fijas", "#3F7D58")

            page.show_dialog(
                ft.AlertDialog(
                    modal=True,
                    title=ft.Text("Guardar como comida fija"),
                    content=name_field,
                    actions=[
                        ft.TextButton("Cancelar", on_click=lambda e: page.pop_dialog()),
                        ft.TextButton("Guardar", on_click=do_save),
                    ],
                )
            )

        def use_saved_meal(sid):
            for s_id, _, _, items in db.get_saved_meals(current_user_id[0]):
                if s_id == sid:
                    meal_dest = selected_meal_for_add[0]
                    add_items_to_log(items, meal_dest)
                    notify(f"Añadido a {meal_dest}", "#3F7D58")
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
                        "Aún no tienes comidas fijas. Guárdalas desde 'Hoy' o desde la IA.",
                        size=12, color="#A9A59B",
                    )
                )
            for sid, sname, _, items in meals:
                tk = sum(i["kcal"] for i in items)
                tp_ = sum(i["p"] for i in items)
                tc_ = sum(i["c"] for i in items)
                tf_ = sum(i["f"] for i in items)
                saved_col.controls.append(
                    ft.Container(
                        bgcolor="#ffffff", border_radius=10, padding=12, shadow=CARD_SHADOW, border=CARD_BORDER,
                        content=ft.Row(
                            [
                                ft.Column(
                                    [
                                        ft.Text(sname, size=14, weight=ft.FontWeight.W_600, color="#1C1B18"),
                                        ft.Text(
                                            f"{int(tk)} kcal • P {int(tp_)}g • C {int(tc_)}g • G {int(tf_)}g",
                                            size=11, color="#77736A",
                                        ),
                                        ft.Text(", ".join(i["name"] for i in items), size=11, color="#A9A59B"),
                                    ],
                                    spacing=2, expand=True,
                                ),
                                ft.Container(
                                    padding=10, bgcolor="#24543F", border_radius=8,
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

        view_add = ft.Container(
            padding=18,
            expand=True,
            content=ft.Column(
                [
                    ft.Text("Añadir alimento", size=24, weight=ft.FontWeight.W_600, color="#1C1B18"),
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
                    search_results,
                    ft.Divider(color="#E4DFD3"),
                    food_preview,
                    tf_name,
                    tf_grams,
                    ft.Row([tf_kcal, tf_p]),
                    ft.Row([tf_c, tf_f]),
                    make_button("Registrar alimento", "#24543F", save_food_entry),
                    ft.Divider(color="#E4DFD3"),
                    ft.Text("Mis comidas fijas", size=18, weight=ft.FontWeight.W_600, color="#1C1B18"),
                    ft.Text("Pulsa + para añadirlas a la comida seleccionada arriba.", size=12, color="#77736A"),
                    saved_col,
                ],
                scroll=ft.ScrollMode.AUTO,
                expand=True,
                spacing=10,
            ),
        )

        # ----- CAPA IA: registrar por texto/voz + sugerencias -----
        ai_state = {"items": []}
        tf_ai = ft.TextField(
            label="¿Qué has comido?",
            hint_text="Ej: 2 rebanadas de pan integral, 50 g de pollo y 2 cucharadas de queso cottage",
            multiline=True, min_lines=3, max_lines=6,
            bgcolor="#ffffff", border_radius=10, border_color="#E4DFD3",
        )
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

        def discard_ai(e=None):
            ai_state["items"] = []
            render_ai_items()
            page.update()

        def register_ai(e=None):
            meal_dest = selected_meal_for_add[0]
            add_items_to_log(ai_state["items"], meal_dest)
            n = len(ai_state["items"])
            ai_state["items"] = []
            tf_ai.value = ""
            render_ai_items()
            notify(f"{n} alimento(s) añadidos a {meal_dest}", "#3F7D58")
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
                make_button(f"✅ Registrar en {selected_meal_for_add[0]}", "#3F7D58", register_ai)
            )
            ai_result_col.controls.append(
                make_button(
                    "⭐ Guardar como comida fija", "#C9763F",
                    lambda e: save_fixed_dialog(list(ai_state["items"]), "Mi comida habitual"),
                )
            )
            ai_result_col.controls.append(
                make_button("Descartar", "#E4DFD3", discard_ai, text_color="#3A3832")
            )

        async def analyze_text(e=None):
            text = (tf_ai.value or "").strip()
            if not text:
                notify("Escribe o dicta lo que has comido", "#B8453A")
                return
            set_busy(True, "Calculando pesos y macros…")
            try:
                ai_state["items"] = await asyncio.to_thread(ai_parse_foods, text)
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

        def add_suggestion(o):
            meal_dest = selected_meal_for_add[0]
            add_items_to_log(
                [{"name": o["title"], "grams": o["grams"], "kcal": o["kcal"], "p": o["p"], "c": o["c"], "f": o["f"]}],
                meal_dest,
            )
            notify(f"Añadido a {meal_dest}", "#3F7D58")
            switch_tab(0)

        def render_suggestions(options, rem_kcal):
            ai_suggest_col.controls.clear()
            if not options:
                ai_suggest_col.controls.append(
                    ft.Text("La IA no encontró opciones que encajen sin pasarte. Inténtalo de nuevo.", size=12, color="#77736A")
                )
                return
            ai_suggest_col.controls.append(
                ft.Text(f"Te quedan {int(rem_kcal)} kcal. Todas estas opciones caben sin pasarte:", size=12, color="#77736A")
            )
            for o in options:
                steps = "\n".join(f"{i + 1}. {st}" for i, st in enumerate(o["steps"]))
                ai_suggest_col.controls.append(
                    ft.Container(
                        bgcolor="#ffffff", border_radius=12, padding=14, shadow=CARD_SHADOW, border=CARD_BORDER,
                        content=ft.Column(
                            [
                                ft.Text(o["title"], size=15, weight=ft.FontWeight.W_600, color="#1C1B18"),
                                ft.Text(o["description"], size=12, color="#77736A"),
                                ft.Text(macro_line(o), size=12, weight=ft.FontWeight.W_600, color="#24543F"),
                                ft.Text("Ingredientes: " + ", ".join(o["ingredients"]), size=11, color="#3A3832") if o["ingredients"] else ft.Container(),
                                ft.Text(steps, size=11, color="#5A574F") if steps else ft.Container(),
                                ft.Row(
                                    [
                                        make_button("➕ Registrar", "#3F7D58", lambda e, oo=o: add_suggestion(oo), expand=True),
                                        make_button(
                                            "⭐ Guardar", "#C9763F",
                                            lambda e, oo=o: save_fixed_dialog(
                                                [{"name": oo["title"], "grams": oo["grams"], "kcal": oo["kcal"], "p": oo["p"], "c": oo["c"], "f": oo["f"]}],
                                                oo["title"],
                                            ),
                                            expand=True,
                                        ),
                                    ],
                                    spacing=8,
                                ),
                            ],
                            spacing=6,
                        ),
                    )
                )

        async def run_suggestions(e=None):
            rem, eaten = today_remaining()
            if rem["kcal"] < 60:
                ai_suggest_col.controls = [ft.Text("Ya has llegado a tus calorías de hoy.", size=13, color="#3F7D58", weight=ft.FontWeight.W_600)]
                page.update()
                return
            set_busy(True, "Buscando opciones para ti…")
            try:
                options = await asyncio.to_thread(ai_suggest, rem, eaten, selected_meal_for_add[0])
            except Exception as ex:
                set_busy(False)
                notify(f"IA: {ex}", "#B8453A")
                return
            render_suggestions(options, rem["kcal"])
            set_busy(False)

        async def go_suggestions(e=None):
            switch_tab(2)
            await run_suggestions()

        view_ai = ft.Container(
            padding=18,
            expand=True,
            content=ft.Column(
                [
                    ft.Text("Asistente", size=24, weight=ft.FontWeight.W_600, color="#1C1B18"),
                    ft.Text("Escribe o dicta lo que has comido. Estimo pesos y macros.", size=13, color="#77736A"),
                    ft.Text("Se registrará en:", size=12, weight=ft.FontWeight.W_600, color="#77736A"),
                    ai_meal_row,
                    tf_ai,
                    ft.Text("Para dictar, usa el micrófono del teclado.", size=11, color="#A9A59B"),
                    make_button("✨ Calcular macros", "#1C1B18", analyze_text),
                    ai_loading,
                    ai_result_col,
                    ft.Divider(color="#E4DFD3"),
                    ft.Text("Completar el día", size=18, weight=ft.FontWeight.W_600, color="#1C1B18"),
                    ft.Text("Si te faltan calorías, propone opciones y recetas sin pasarte.", size=12, color="#77736A"),
                    make_button("Ver opciones para llegar", "#24543F", run_suggestions),
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

        def process_code_string(code_str):
            code_str = (code_str or "").strip()
            if not code_str:
                notify("Introduce o escanea un código primero", "#B8453A")
                return

            try:
                data = json.loads(code_str)
                if all(k in data for k in ("name", "kcal", "p", "c", "f")):
                    fill_food_form(
                        data["name"], data["kcal"], data["p"], data["c"], data["f"]
                    )
                    return
            except Exception:
                pass

            if code_str.isdigit():
                try:
                    url = f"https://world.openfoodfacts.org/api/v0/product/{code_str}.json"
                    resp = requests.get(url, headers=OFF_HEADERS, timeout=6).json()
                    if resp.get("status") == 1:
                        prod = resp["product"]
                        name = prod.get("product_name") or "Producto Escaneado"
                        nutr = prod.get("nutriments", {})
                        fill_food_form(
                            name,
                            nutr.get("energy-kcal_100g", 0),
                            nutr.get("proteins_100g", 0),
                            nutr.get("carbohydrates_100g", 0),
                            nutr.get("fat_100g", 0),
                            prod.get("image_front_url") or prod.get("image_url"),
                        )
                        return
                    else:
                        notify("Código no encontrado en OpenFoodFacts", "#B8453A")
                except Exception:
                    notify("Error al conectar con OpenFoodFacts", "#B8453A")
            else:
                notify("Formato de código no reconocido", "#B8453A")

        scan_state = {"active": False}

        def decode_frame(image_bytes):
            """Devuelve el texto de un QR o código de barras, o '' si no hay."""
            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return ""
            # 1) QR
            try:
                data, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
                if data:
                    return data
            except Exception:
                pass
            # 2) Código de barras EAN/UPC (si tu OpenCV lo incluye)
            try:
                bd = cv2.barcode.BarcodeDetector()
                ok, infos, _, _ = bd.detectAndDecodeWithType(img)
                if ok:
                    for d in infos:
                        if d:
                            return d
            except Exception:
                pass
            return ""

        def close_camera_inline(ev=None):
            scan_state["active"] = False
            camera_holder.visible = False
            camera_holder.content = None
            page.update()

        async def open_camera_inline(e):
            try:
                cam = fc.Camera(expand=True, preview_enabled=True)
            except Exception as ex:
                notify(f"Cámara no disponible: {ex}", "#B8453A")
                return

            async def capture_and_decode(ev=None):
                try:
                    image_bytes = await cam.take_picture()
                    if not image_bytes:
                        return False
                    data = decode_frame(image_bytes)
                    if data:
                        scan_state["active"] = False
                        tf_barcode.value = data
                        close_camera_inline()
                        process_code_string(data)
                        return True
                except Exception as ex:
                    print("Error captura:", ex)
                return False

            async def manual_capture(ev):
                ok = await capture_and_decode()
                if not ok:
                    notify("No se detectó ningún código. Acércalo más.", "#C9763F")

            async def auto_scan_loop():
                import asyncio
                scan_state["active"] = True
                await asyncio.sleep(1.0)
                while scan_state["active"]:
                    if await capture_and_decode():
                        break
                    await asyncio.sleep(0.7)

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
                        ft.Text(
                            "Escaneando automáticamente… apunta al código",
                            size=12,
                            color="#77736A",
                        ),
                        ft.Row(
                            [
                                make_button("📸 Capturar", "#24543F", manual_capture, expand=True),
                                make_button("✖ Cerrar", "#B8453A", close_camera_inline, expand=True),
                            ],
                            spacing=10,
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
                                    "Abre la cámara integrada para leer un código QR nutricional o escribe el código de barras debajo.",
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
                    make_button(
                        "Buscar producto",
                        "#3F7D58",
                        lambda e: process_code_string(tf_barcode.value),
                    ),
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
            uid, uname, uav, uw, uh, uage, ugen, uact, ugoal, _, ukcal, up, uc, uf, _ = u
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
                                f"Actividad: {uact}  |  Objetivo: {ugoal}",
                                size=13,
                                weight=ft.FontWeight.W_600,
                                color="#24543F",
                            ),
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

        # ----- NAVEGACIÓN INFERIOR -----
        nav_index = [0]
        content_area = ft.Container(expand=True)
        views_list = [view_home, view_add, view_ai, view_scan, view_profile]
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
                ft.NavigationBarDestination(icon=ft.Icons.PERSON_OUTLINE, selected_icon=ft.Icons.PERSON, label="Perfil"),
            ],
        )

        def switch_tab(index):
            nav_index[0] = index
            content_area.content = views_list[index]
            nav_bar.selected_index = index
            if index != 3 and scan_state["active"]:
                close_camera_inline()
            if index == 0:
                refresh_dashboard()
            elif index == 1:
                render_saved_meals()
            elif index == 4:
                refresh_profile_tab()
            page.update()

        root_container.content = ft.Column(
            [top_bar, content_area, nav_bar],
            spacing=0,
            expand=True,
        )
        switch_tab(0)

    # ==========================================
    # MONTAJE INICIAL
    # ==========================================
    page.add(status_banner, root_container)
    show_user_selector()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8550))
    ft.run(main, view=ft.AppView.WEB_BROWSER, port=port, host="0.0.0.0")