import json
import os
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
        self.cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.conn.commit()

    def add_food(self, user_id, meal_type, name, grams, kcal, p, c, f):
        self.cursor.execute(
            """
            INSERT INTO food_log (user_id, meal_type, name, grams, kcal, p, c, f)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (user_id, meal_type, name, grams, kcal, p, c, f),
        )
        self.conn.commit()

    def get_today_foods(self, user_id):
        self.cursor.execute(
            """
            SELECT id, COALESCE(meal_type, 'Almuerzo'), name, grams, kcal, p, c, f
            FROM food_log
            WHERE user_id = ? AND date = DATE('now', 'localtime')
        """,
            (user_id,),
        )
        return self.cursor.fetchall()

    def delete_food(self, food_id):
        self.cursor.execute("DELETE FROM food_log WHERE id = ?", (food_id,))
        self.conn.commit()


# ==========================================
# FÓRMULA HARRIS-BENEDICT Y MACROS
# ==========================================
ACT_MULTIPLIERS = {
    "Sedentario": {"mult": 1.2, "desc": "Poco o ningún ejercicio", "icon": "🛋️"},
    "Ligero": {"mult": 1.375, "desc": "Entreno suave 1-3 días/sem", "icon": "🚶"},
    "Moderado": {"mult": 1.55, "desc": "Gimnasio 3-5 días/sem", "icon": "🏋️"},
    "Intenso": {"mult": 1.725, "desc": "Entreno fuerte 6-7 días/sem", "icon": "🔥"},
    "Atleta": {"mult": 1.9, "desc": "Doble sesión o trabajo físico", "icon": "🏆"},
}

GOALS_META = {
    "Definición": {"offset": -400.0, "desc": "Perder grasa (-400 kcal)", "icon": "⚡"},
    "Mantenimiento": {"offset": 0.0, "desc": "Mantener peso (0 kcal)", "icon": "⚖️"},
    "Volumen": {"offset": 350.0, "desc": "Ganar músculo (+350 kcal)", "icon": "💪"},
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
# APLICACIÓN MÓVIL MULTIUSUARIO
# ==========================================
def main(page: ft.Page):
    db = DatabaseManager()

    page.title = "GymMacro Pro"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = "#f1f5f9"
    page.padding = 0

    try:
        page.window.width = 420
        page.window.height = 880
    except Exception:
        pass

    OFF_HEADERS = {
        "User-Agent": "GymMacroProApp - Mobile - Version 3.0"
    }

    CARD_SHADOW = ft.BoxShadow(
        spread_radius=0, blur_radius=18, color="#14000000"
    )

    # Estado global de sesión
    current_user_id = [None]
    selected_meal_for_add = ["Desayuno"]
    expanded_meal = [None]

    MEALS_META = {
        "Desayuno": {"icon": "☕", "ratio": 0.30},
        "Almuerzo": {"icon": "🥪", "ratio": 0.40},
        "Cena": {"icon": "🥗", "ratio": 0.25},
        "Snacks": {"icon": "🍎", "ratio": 0.05},
    }

    # Banner de notificaciones flotante
    status_text = ft.Text("", color="white", size=13, weight="bold")
    status_banner = ft.Container(
        content=ft.Row([status_text], alignment=ft.MainAxisAlignment.CENTER),
        bgcolor="#10b981",
        padding=12,
        visible=False,
    )

    def notify(msg, color="#1d8cf8"):
        status_text.value = msg
        status_banner.bgcolor = color
        status_banner.visible = True
        page.update()

        def hide_later():
            time.sleep(2.4)
            status_banner.visible = False
            page.update()

        threading.Thread(target=hide_later, daemon=True).start()

    def make_button(text, bg_color, on_click_fn, text_color="white", expand=False):
        return ft.Container(
            bgcolor=bg_color,
            border_radius=16,
            padding=15,
            expand=expand,
            on_click=on_click_fn,
            content=ft.Row(
                [ft.Text(text, color=text_color, weight="bold", size=15)],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
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
                    border_radius=20,
                    padding=16,
                    shadow=CARD_SHADOW,
                    on_click=lambda e, i=uid: login_as_user(i),
                    content=ft.Row(
                        [
                            ft.Container(
                                width=56,
                                height=56,
                                border_radius=28,
                                bgcolor="#eff6ff",
                                content=ft.Row(
                                    [ft.Text(uavatar, size=28)],
                                    alignment=ft.MainAxisAlignment.CENTER,
                                ),
                            ),
                            ft.Container(width=6),
                            ft.Column(
                                [
                                    ft.Text(
                                        uname,
                                        size=18,
                                        weight="bold",
                                        color="#1e293b",
                                    ),
                                    ft.Text(
                                        f"{ugoal} • {int(ukcal)} kcal/día",
                                        size=13,
                                        color="#1d8cf8",
                                        weight="bold",
                                    ),
                                    ft.Text(
                                        f"{uage} años • {int(uw)} kg • {int(uh)} cm",
                                        size=12,
                                        color="#64748b",
                                    ),
                                ],
                                spacing=2,
                                expand=True,
                            ),
                            ft.Container(
                                padding=8,
                                on_click=lambda e, i=uid: delete_user_profile(i),
                                content=ft.Text("🗑️", size=18),
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
                    ft.Text("👋 ¡Hola!", size=16, color="#1d8cf8", weight="bold"),
                    ft.Text(
                        "¿Quién entrena hoy?",
                        size=28,
                        weight="bold",
                        color="#1e293b",
                    ),
                    ft.Text(
                        "Selecciona tu perfil o crea uno nuevo para calcular tus calorías.",
                        size=13,
                        color="#64748b",
                    ),
                    ft.Container(height=8),
                    cards_col,
                    make_button(
                        "➕ Crear Nuevo Usuario",
                        "#1d8cf8",
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
        notify("Perfil eliminado", "#ef4444")
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
            value=0.25, color="#1d8cf8", bgcolor="#e2e8f0", height=8
        )
        step_title_lbl = ft.Text("Paso 1 de 4", size=13, color="#1d8cf8", weight="bold")

        tf_wiz_name = ft.TextField(
            label="Tu nombre o apodo",
            value=wiz_state["name"],
            bgcolor="#ffffff",
            border_radius=14,
            border_color="#cbd5e1",
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
                border_radius=20,
                padding=16,
                shadow=CARD_SHADOW,
                content=ft.Column(
                    [
                        ft.Text(title, size=13, color="#64748b", weight="bold"),
                        ft.Row(
                            [
                                ft.Container(
                                    bgcolor="#f1f5f9",
                                    border_radius=12,
                                    padding=10,
                                    on_click=lambda e: change_metric(
                                        key, -5, min_v, max_v
                                    ),
                                    content=ft.Text(
                                        "-5", weight="bold", color="#475569"
                                    ),
                                ),
                                ft.Container(
                                    bgcolor="#eff6ff",
                                    border_radius=12,
                                    padding=12,
                                    on_click=lambda e: change_metric(
                                        key, -1, min_v, max_v
                                    ),
                                    content=ft.Text(
                                        "−",
                                        size=18,
                                        weight="bold",
                                        color="#1d8cf8",
                                    ),
                                ),
                                ft.Column(
                                    [
                                        ft.Text(
                                            val_str,
                                            size=28,
                                            weight="bold",
                                            color="#1e293b",
                                        ),
                                        ft.Text(unit, size=12, color="#64748b"),
                                    ],
                                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                    spacing=0,
                                    expand=True,
                                ),
                                ft.Container(
                                    bgcolor="#eff6ff",
                                    border_radius=12,
                                    padding=12,
                                    on_click=lambda e: change_metric(
                                        key, 1, min_v, max_v
                                    ),
                                    content=ft.Text(
                                        "+",
                                        size=18,
                                        weight="bold",
                                        color="#1d8cf8",
                                    ),
                                ),
                                ft.Container(
                                    bgcolor="#f1f5f9",
                                    border_radius=12,
                                    padding=10,
                                    on_click=lambda e: change_metric(
                                        key, 5, min_v, max_v
                                    ),
                                    content=ft.Text(
                                        "+5", weight="bold", color="#475569"
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
                            border_radius=25,
                            bgcolor="#1d8cf8" if active else "#ffffff",
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
                for g_name, g_icon in [("Hombre", "👨"), ("Mujer", "👩")]:
                    active = wiz_state["gender"] == g_name
                    gender_cards.controls.append(
                        ft.Container(
                            expand=True,
                            padding=20,
                            border_radius=20,
                            bgcolor="#1d8cf8" if active else "#ffffff",
                            shadow=CARD_SHADOW,
                            on_click=lambda e, gn=g_name: (
                                wiz_state.update({"gender": gn}),
                                render_wizard_step(),
                            ),
                            content=ft.Column(
                                [
                                    ft.Text(g_icon, size=36),
                                    ft.Text(
                                        g_name,
                                        size=16,
                                        weight="bold",
                                        color="white" if active else "#1e293b",
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
                            "¡Vamos a crear tu perfil!",
                            size=26,
                            weight="bold",
                            color="#1e293b",
                        ),
                        ft.Text(
                            "Personaliza tu cuenta para adaptar el cálculo metabólico.",
                            size=13,
                            color="#64748b",
                        ),
                        ft.Container(height=6),
                        tf_wiz_name,
                        ft.Text(
                            "Elige tu icono:",
                            size=13,
                            weight="bold",
                            color="#64748b",
                        ),
                        avatar_row,
                        ft.Text(
                            "Selecciona tu sexo biológico:",
                            size=13,
                            weight="bold",
                            color="#64748b",
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
                            weight="bold",
                            color="#1e293b",
                        ),
                        ft.Text(
                            "Ajusta rápidamente tu edad, peso y altura con los botones.",
                            size=13,
                            color="#64748b",
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
                            bgcolor="#1d8cf8" if active else "#ffffff",
                            padding=16,
                            border_radius=18,
                            shadow=CARD_SHADOW,
                            on_click=lambda e, an=act_name: (
                                wiz_state.update({"activity": an}),
                                render_wizard_step(),
                            ),
                            content=ft.Row(
                                [
                                    ft.Text(meta["icon"], size=28),
                                    ft.Container(width=8),
                                    ft.Column(
                                        [
                                            ft.Text(
                                                act_name,
                                                size=16,
                                                weight="bold",
                                                color="white"
                                                if active
                                                else "#1e293b",
                                            ),
                                            ft.Text(
                                                meta["desc"],
                                                size=12,
                                                color="#e0f2fe"
                                                if active
                                                else "#64748b",
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
                            weight="bold",
                            color="#1e293b",
                        ),
                        ft.Text(
                            "¿Cuánto te mueves durante la semana?",
                            size=13,
                            color="#64748b",
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
                            bgcolor="#1d8cf8" if active else "#ffffff",
                            padding=14,
                            border_radius=18,
                            shadow=CARD_SHADOW,
                            on_click=lambda e, gn=g_name: (
                                wiz_state.update({"goal": gn}),
                                render_wizard_step(),
                            ),
                            content=ft.Row(
                                [
                                    ft.Text(meta["icon"], size=26),
                                    ft.Container(width=8),
                                    ft.Column(
                                        [
                                            ft.Text(
                                                g_name,
                                                size=16,
                                                weight="bold",
                                                color="white"
                                                if active
                                                else "#1e293b",
                                            ),
                                            ft.Text(
                                                meta["desc"],
                                                size=12,
                                                color="#e0f2fe"
                                                if active
                                                else "#64748b",
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
                    bgcolor="#1e293b",
                    padding=20,
                    border_radius=22,
                    content=ft.Column(
                        [
                            ft.Text(
                                "🎯 TU PLAN NUTRICIONAL DIARIO",
                                size=12,
                                color="#38bdf8",
                                weight="bold",
                            ),
                            ft.Text(
                                f"{int(tkcal):,} kcal".replace(",", "."),
                                size=34,
                                weight="bold",
                                color="white",
                            ),
                            ft.Divider(color="#334155"),
                            ft.Row(
                                [
                                    ft.Column(
                                        [
                                            ft.Text(
                                                f"{int(tc)}g",
                                                size=18,
                                                weight="bold",
                                                color="#38bdf8",
                                            ),
                                            ft.Text(
                                                "Carbos",
                                                size=12,
                                                color="#94a3b8",
                                            ),
                                        ],
                                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                    ),
                                    ft.Column(
                                        [
                                            ft.Text(
                                                f"{int(tp)}g",
                                                size=18,
                                                weight="bold",
                                                color="#10b981",
                                            ),
                                            ft.Text(
                                                "Proteína",
                                                size=12,
                                                color="#94a3b8",
                                            ),
                                        ],
                                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                    ),
                                    ft.Column(
                                        [
                                            ft.Text(
                                                f"{int(tf)}g",
                                                size=18,
                                                weight="bold",
                                                color="#f59e0b",
                                            ),
                                            ft.Text(
                                                "Grasas",
                                                size=12,
                                                color="#94a3b8",
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
                            weight="bold",
                            color="#1e293b",
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
                        "#e2e8f0",
                        lambda e: prev_step(),
                        text_color="#334155",
                        expand=True,
                    )
                )
            elif not is_first_user:
                nav_row_controls.append(
                    make_button(
                        "✖ Cancelar",
                        "#e2e8f0",
                        lambda e: show_user_selector(),
                        text_color="#334155",
                        expand=True,
                    )
                )

            if s < 3:
                nav_row_controls.append(
                    make_button(
                        "Siguiente ➡",
                        "#1d8cf8",
                        lambda e: next_step(),
                        expand=True,
                    )
                )
            else:
                nav_row_controls.append(
                    make_button(
                        "🚀 ¡Empezar Ahora!",
                        "#10b981",
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
                    notify("Escribe tu nombre para continuar", "#ef4444")
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
                notify("Perfil actualizado con éxito", "#10b981")
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
                notify(f"¡Bienvenido/a {name_clean}!", "#10b981")

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
        header_user_txt = ft.Text("", size=14, weight="bold", color="#1e293b")
        header_avatar_txt = ft.Text("", size=18)

        top_bar = ft.Container(
            bgcolor="#ffffff",
            padding=ft.Padding(left=16, right=16, top=18, bottom=10),
            content=ft.Row(
                [
                    ft.Row(
                        [
                            ft.Text("GymMacro", size=20, weight="bold", color="#1e293b"),
                            ft.Text("PRO", size=20, weight="bold", color="#1d8cf8"),
                        ],
                        spacing=4,
                    ),
                    ft.Container(
                        bgcolor="#f1f5f9",
                        border_radius=20,
                        padding=ft.Padding(left=12, right=12, top=6, bottom=6),
                        on_click=show_user_selector,
                        content=ft.Row(
                            [
                                header_avatar_txt,
                                header_user_txt,
                                ft.Text("🔄", size=12),
                            ],
                            spacing=6,
                        ),
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
        )

        # ----- CAPA 1: PANTALLA "HOY" -----
        lbl_consumed = ft.Text("0", size=20, weight="bold", color="#1e293b")
        lbl_remaining = ft.Text("0", size=28, weight="bold", color="#1e293b")
        lbl_burned = ft.Text("244", size=20, weight="bold", color="#1e293b")

        ring_main = ft.ProgressRing(
            value=0.0,
            width=135,
            height=135,
            stroke_width=10,
            color="#5cd6c0",
            bgcolor="#edf1f5",
        )

        def create_macro_col(label_title, bar_color):
            bar = ft.ProgressBar(
                value=0.0, color=bar_color, bgcolor="#edf1f5", height=7
            )
            val_txt = ft.Text("0 / 0 g", size=13, weight="bold", color="#1e293b")
            col = ft.Column(
                [
                    ft.Text(label_title, size=12, color="#64748b"),
                    ft.Container(content=bar, width=85, border_radius=4, padding=6),
                    val_txt,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=2,
                expand=True,
            )
            return col, bar, val_txt

        col_c, bar_c, lbl_c = create_macro_col("Carbohidratos", "#38abfd")
        col_p, bar_p, lbl_p = create_macro_col("Proteínas", "#10b981")
        col_f, bar_f, lbl_f = create_macro_col("Grasas", "#f59e0b")

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
            notify("Alimento eliminado", "#ef4444")

        def refresh_dashboard():
            u = db.get_user(current_user_id[0])
            if not u:
                return
            _, uname, uavatar, _, _, _, _, _, _, _, tkcal, tp, tc, tf, burned = u
            header_user_txt.value = uname
            header_avatar_txt.value = uavatar

            foods = db.get_today_foods(current_user_id[0])

            skcal = sum(x[4] for x in foods)
            sp = sum(x[5] for x in foods)
            sc = sum(x[6] for x in foods)
            sf = sum(x[7] for x in foods)

            lbl_consumed.value = f"{int(skcal):,}".replace(",", ".")
            lbl_burned.value = f"{int(burned):,}".replace(",", ".")

            rem_kcal = max(0, int(tkcal - skcal))
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
                            color="#38abfd",
                            bgcolor="#edf1f5",
                        ),
                        ft.Container(
                            width=50,
                            height=50,
                            content=ft.Row(
                                [ft.Text(meta["icon"], size=20)],
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
                                        weight="bold",
                                        color="#1e293b",
                                    ),
                                    ft.Text(
                                        f"{int(m_kcal)} / {int(m_target)} kcal",
                                        size=13,
                                        color="#64748b",
                                    ),
                                ],
                                spacing=2,
                                expand=True,
                            ),
                            ft.Container(
                                width=36,
                                height=36,
                                border_radius=18,
                                bgcolor="#1d8cf8",
                                on_click=lambda e, mn=m_name: go_to_add_for_meal(mn),
                                content=ft.Row(
                                    [
                                        ft.Text(
                                            "+",
                                            size=20,
                                            weight="bold",
                                            color="white",
                                        )
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
                                bgcolor="#f8fafc",
                                padding=12,
                                content=ft.Text(
                                    "Sin alimentos registrados. Pulsa '+' para añadir.",
                                    size=12,
                                    color="#94a3b8",
                                ),
                            )
                        )
                    else:
                        for fid, _, fname, fgrams, fkcal, fp, fc_val, ff in m_foods:
                            meals_card_col.controls.append(
                                ft.Container(
                                    bgcolor="#f8fafc",
                                    padding=10,
                                    content=ft.Row(
                                        [
                                            ft.Column(
                                                [
                                                    ft.Text(
                                                        f"• {fname} ({int(fgrams)}g)",
                                                        size=13,
                                                        weight="bold",
                                                        color="#334155",
                                                    ),
                                                    ft.Text(
                                                        f"{int(fkcal)} kcal | C: {fc_val:.1f}g • P: {fp:.1f}g • G: {ff:.1f}g",
                                                        size=11,
                                                        color="#64748b",
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
                                                content=ft.Text(
                                                    "✕",
                                                    color="#ef4444",
                                                    weight="bold",
                                                    size=14,
                                                ),
                                            ),
                                        ]
                                    ),
                                )
                            )

                if idx < len(meal_names) - 1:
                    meals_card_col.controls.append(
                        ft.Divider(height=1, color="#f1f5f9")
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
                            ft.Text("Restantes", size=12, color="#64748b"),
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=0,
                    ),
                ),
            ],
        )

        view_home = ft.Container(
            padding=16,
            expand=True,
            content=ft.Column(
                [
                    ft.Container(
                        bgcolor="#ffffff",
                        border_radius=24,
                        padding=20,
                        shadow=CARD_SHADOW,
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
                                                    color="#64748b",
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
                                                    color="#64748b",
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
                    ft.Container(
                        bgcolor="#ffffff",
                        border_radius=24,
                        padding=8,
                        shadow=CARD_SHADOW,
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
                        bgcolor="#1d8cf8" if active else "#ffffff",
                        padding=10,
                        border_radius=16,
                        on_click=lambda e, mn=m_name: (
                            selected_meal_for_add.__setitem__(0, mn),
                            render_meal_selector_pills(),
                            page.update(),
                        ),
                        content=ft.Text(
                            f"{meta['icon']} {m_name}",
                            size=12,
                            weight="bold",
                            color="white" if active else "#64748b",
                        ),
                    )
                )

        render_meal_selector_pills()

        tf_search = ft.TextField(
            label="Buscar (ej. Pollo, Arroz, Avena, Huevo)",
            bgcolor="#ffffff",
            border_radius=14,
            border_color="#e2e8f0",
            expand=True,
        )
        search_results = ft.Column(spacing=6)

        tf_name = ft.TextField(
            label="Nombre del alimento",
            bgcolor="#ffffff",
            border_radius=14,
            border_color="#e2e8f0",
        )
        tf_grams = ft.TextField(
            label="Cantidad consumida (gramos)",
            value="100",
            bgcolor="#ffffff",
            border_radius=14,
            border_color="#e2e8f0",
        )
        tf_kcal = ft.TextField(
            label="Kcal / 100g",
            bgcolor="#ffffff",
            border_radius=14,
            border_color="#e2e8f0",
            expand=True,
        )
        tf_p = ft.TextField(
            label="Proteína / 100g",
            bgcolor="#ffffff",
            border_radius=14,
            border_color="#e2e8f0",
            expand=True,
        )
        tf_c = ft.TextField(
            label="Carbos / 100g",
            bgcolor="#ffffff",
            border_radius=14,
            border_color="#e2e8f0",
            expand=True,
        )
        tf_f = ft.TextField(
            label="Grasas / 100g",
            bgcolor="#ffffff",
            border_radius=14,
            border_color="#e2e8f0",
            expand=True,
        )

        def fill_food_form(name, kcal, p, c, f):
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
                ft.Text("Buscando...", color="#1d8cf8", size=13)
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
                                border_radius=14,
                                shadow=CARD_SHADOW,
                                on_click=lambda e, n=item["name"], k=item["kcal"], p=item["p"], c=item["c"], f=item["f"]: fill_food_form(
                                    n, k, p, c, f
                                ),
                                content=ft.Row(
                                    [
                                        ft.Column(
                                            [
                                                ft.Text(
                                                    f"⭐ {item['name']}",
                                                    weight="bold",
                                                    size=14,
                                                    color="#1e293b",
                                                ),
                                                ft.Text(
                                                    f"100g: {item['kcal']} kcal | C: {item['c']}g | P: {item['p']}g | G: {item['f']}g",
                                                    size=11,
                                                    color="#64748b",
                                                ),
                                            ],
                                            expand=True,
                                        ),
                                        ft.Text("Elegir", color="#1d8cf8", weight="bold"),
                                    ]
                                ),
                            )
                        )

            try:
                url = f"https://world.openfoodfacts.org/cgi/search.pl?search_terms={query}&search_simple=1&action=process&json=1&page_size=4"
                resp = requests.get(url, headers=OFF_HEADERS, timeout=6).json()
                for prod in resp.get("products", []):
                    pname = prod.get("product_name", "Alimento")
                    nutr = prod.get("nutriments", {})
                    k = nutr.get("energy-kcal_100g", 0)
                    p = nutr.get("proteins_100g", 0)
                    c = nutr.get("carbohydrates_100g", 0)
                    f = nutr.get("fat_100g", 0)
                    search_results.controls.append(
                        ft.Container(
                            bgcolor="#ffffff",
                            padding=12,
                            border_radius=14,
                            on_click=lambda e, n=pname, k=k, p=p, c=c, f=f: fill_food_form(
                                n, k, p, c, f
                            ),
                            content=ft.Row(
                                [
                                    ft.Column(
                                        [
                                            ft.Text(
                                                pname,
                                                weight="bold",
                                                size=13,
                                                color="#334155",
                                            ),
                                            ft.Text(
                                                f"100g: {int(k or 0)} kcal | C: {c or 0}g | P: {p or 0}g | G: {f or 0}g",
                                                size=11,
                                                color="#64748b",
                                            ),
                                        ],
                                        expand=True,
                                    ),
                                    ft.Text("Elegir", color="#38abfd", size=12),
                                ]
                            ),
                        )
                    )
            except Exception:
                pass

            if not search_results.controls:
                search_results.controls.append(
                    ft.Text("Sin resultados.", color="#64748b")
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
                db.add_food(current_user_id[0], meal_dest, name, grams, k, p, c, f)
                for field in (tf_name, tf_kcal, tf_p, tf_c, tf_f):
                    field.value = ""
                tf_grams.value = "100"
                expanded_meal[0] = meal_dest
                notify(f"Añadido a {meal_dest}", "#10b981")
                switch_tab(0)
            except ValueError:
                notify("Rellena los gramos y macros con números", "#ef4444")

        view_add = ft.Container(
            padding=18,
            expand=True,
            content=ft.Column(
                [
                    ft.Text("Añadir Alimento", size=24, weight="bold", color="#1e293b"),
                    meal_selector_row,
                    ft.Row(
                        [
                            tf_search,
                            ft.Container(
                                bgcolor="#1d8cf8",
                                padding=14,
                                border_radius=14,
                                on_click=search_openfoodfacts,
                                content=ft.Text("🔍", size=18),
                            ),
                        ]
                    ),
                    search_results,
                    ft.Divider(color="#e2e8f0"),
                    tf_name,
                    tf_grams,
                    ft.Row([tf_kcal, tf_p]),
                    ft.Row([tf_c, tf_f]),
                    make_button("➕ Registrar Alimento", "#1d8cf8", save_food_entry),
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
            border_radius=14,
            border_color="#e2e8f0",
        )

        camera_holder = ft.Container(visible=False)

        def process_code_string(code_str):
            code_str = (code_str or "").strip()
            if not code_str:
                notify("Introduce o escanea un código primero", "#ef4444")
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
                        name = prod.get("product_name", "Producto Escaneado")
                        nutr = prod.get("nutriments", {})
                        fill_food_form(
                            name,
                            nutr.get("energy-kcal_100g", 0),
                            nutr.get("proteins_100g", 0),
                            nutr.get("carbohydrates_100g", 0),
                            nutr.get("fat_100g", 0),
                        )
                        return
                    else:
                        notify("Código no encontrado en OpenFoodFacts", "#ef4444")
                except Exception:
                    notify("Error al conectar con OpenFoodFacts", "#ef4444")
            else:
                notify("Formato de código no reconocido", "#ef4444")

        def open_camera_inline(e):
            try:
                cam = fc.Camera(expand=True, preview_enabled=True)
            except Exception as ex:
                notify(f"Cámara no disponible: {ex}", "#ef4444")
                return

            def capture_and_decode(ev):
                try:
                    image_bytes = cam.get_image_bytes()
                    if not image_bytes:
                        notify("No se pudo capturar el fotograma", "#ef4444")
                        return
                    nparr = np.frombuffer(image_bytes, np.uint8)
                    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if img is None:
                        notify("Imagen inválida", "#ef4444")
                        return
                    detector = cv2.QRCodeDetector()
                    data, _, _ = detector.detectAndDecode(img)
                    if data:
                        tf_barcode.value = data
                        close_camera_inline(None)
                        process_code_string(data)
                    else:
                        notify("No se detectó ningún QR. Acerca más el código.", "#f59e0b")
                except Exception:
                    notify("Error al procesar la imagen de la cámara", "#ef4444")

            def close_camera_inline(ev):
                camera_holder.visible = False
                camera_holder.content = None
                page.update()

            camera_holder.content = ft.Container(
                bgcolor="#ffffff",
                padding=14,
                border_radius=20,
                shadow=CARD_SHADOW,
                content=ft.Column(
                    [
                        ft.Container(
                            height=280,
                            border_radius=16,
                            bgcolor="#0f172a",
                            content=cam,
                        ),
                        ft.Row(
                            [
                                make_button(
                                    "📸 Capturar QR",
                                    "#1d8cf8",
                                    capture_and_decode,
                                    expand=True,
                                ),
                                make_button(
                                    "✖ Cerrar",
                                    "#ef4444",
                                    close_camera_inline,
                                    expand=True,
                                ),
                            ],
                            spacing=10,
                        ),
                    ],
                    spacing=10,
                ),
            )
            camera_holder.visible = True
            page.update()

        view_scan = ft.Container(
            padding=18,
            expand=True,
            content=ft.Column(
                [
                    ft.Text("Escáner", size=24, weight="bold", color="#1e293b"),
                    camera_holder,
                    ft.Container(
                        bgcolor="#ffffff",
                        padding=22,
                        border_radius=24,
                        shadow=CARD_SHADOW,
                        content=ft.Column(
                            [
                                ft.Text("📷", size=48),
                                ft.Text(
                                    "Escanear Código QR",
                                    size=18,
                                    weight="bold",
                                    color="#1e293b",
                                ),
                                ft.Text(
                                    "Abre la cámara integrada para leer un código QR nutricional o escribe el código de barras debajo.",
                                    text_align="center",
                                    color="#64748b",
                                    size=13,
                                ),
                                make_button(
                                    "Abrir Cámara QR", "#1d8cf8", open_camera_inline
                                ),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=10,
                        ),
                    ),
                    ft.Text(
                        "Código de Barras (EAN-13) o JSON:",
                        weight="bold",
                        color="#64748b",
                        size=13,
                    ),
                    tf_barcode,
                    make_button(
                        "🔍 Buscar Producto",
                        "#10b981",
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
                    border_radius=24,
                    shadow=CARD_SHADOW,
                    content=ft.Column(
                        [
                            ft.Text(uav, size=48),
                            ft.Text(uname, size=22, weight="bold", color="#1e293b"),
                            ft.Text(
                                f"{ugen} • {uage} años • {int(uw)} kg • {int(uh)} cm",
                                size=13,
                                color="#64748b",
                            ),
                            ft.Divider(color="#f1f5f9"),
                            ft.Text(
                                f"Actividad: {uact}  |  Objetivo: {ugoal}",
                                size=13,
                                weight="bold",
                                color="#1d8cf8",
                            ),
                            ft.Text(
                                f"Meta diaria: {int(ukcal)} kcal (P: {int(up)}g • C: {int(uc)}g • G: {int(uf)}g)",
                                size=13,
                                color="#334155",
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=6,
                    ),
                ),
                make_button(
                    "⚡ Recalibrar Edad, Peso y Calorías",
                    "#1d8cf8",
                    lambda e: show_onboarding_wizard(
                        is_first_user=False, edit_user_data=u
                    ),
                ),
                make_button(
                    "👥 Cambiar o Añadir Usuario",
                    "#334155",
                    show_user_selector,
                ),
            ]
            page.update()

        view_profile = ft.Container(
            padding=18,
            expand=True,
            content=ft.Column(
                [
                    ft.Text("Mi Perfil", size=24, weight="bold", color="#1e293b"),
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
        views_list = [view_home, view_add, view_scan, view_profile]
        nav_buttons = []

        def switch_tab(index):
            nav_index[0] = index
            content_area.content = views_list[index]
            for i, btn in enumerate(nav_buttons):
                active = nav_index[0] == i
                btn.content.controls[0].color = "#1d8cf8" if active else "#94a3b8"
                btn.content.controls[1].color = "#1d8cf8" if active else "#94a3b8"
            if index == 0:
                refresh_dashboard()
            elif index == 3:
                refresh_profile_tab()
            page.update()

        for i, (icon, label) in enumerate(
            [("🏠", "Hoy"), ("➕", "Añadir"), ("📷", "Escáner"), ("👤", "Perfil")]
        ):
            nav_buttons.append(
                ft.Container(
                    expand=True,
                    padding=8,
                    on_click=lambda e, idx=i: switch_tab(idx),
                    content=ft.Column(
                        [
                            ft.Text(icon, size=20, color="#94a3b8"),
                            ft.Text(label, size=11, color="#94a3b8", weight="bold"),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=2,
                    ),
                )
            )

        nav_bar = ft.Container(
            bgcolor="#ffffff",
            padding=ft.Padding(left=6, right=6, top=6, bottom=18),
            content=ft.Row(nav_buttons, alignment=ft.MainAxisAlignment.SPACE_AROUND),
        )

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