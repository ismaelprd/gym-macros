import json
import sqlite3
import threading
import cv2
import flet as ft
import requests


# ==========================================
# BASE DE DATOS LOCAL (SQLite)
# ==========================================
class DatabaseManager:

    def __init__(self, db_name="gym_macros_mobile.db"):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS profile (
                id INTEGER PRIMARY KEY,
                weight REAL, height REAL, age INTEGER, gender TEXT,
                activity TEXT, goal_offset REAL,
                target_kcal REAL, target_p REAL, target_c REAL, target_f REAL,
                burned_kcal REAL DEFAULT 244
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS food_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meal_type TEXT DEFAULT 'Almuerzo',
                name TEXT, grams REAL, kcal REAL, p REAL, c REAL, f REAL,
                date DATE DEFAULT (DATE('now', 'localtime'))
            )
        """)
        try:
            self.cursor.execute(
                "ALTER TABLE profile ADD COLUMN burned_kcal REAL DEFAULT 244"
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
        if not self.get_profile():
            self.save_profile(
                (
                    75.0,
                    178.0,
                    25,
                    "Hombre",
                    "Moderado",
                    0.0,
                    2117.0,
                    103.0,
                    258.0,
                    68.0,
                    244.0,
                )
            )

    def save_profile(self, data):
        self.cursor.execute("DELETE FROM profile")
        self.cursor.execute(
            """
            INSERT INTO profile (
                id, weight, height, age, gender, activity, goal_offset,
                target_kcal, target_p, target_c, target_f, burned_kcal
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            data,
        )
        self.conn.commit()

    def get_profile(self):
        self.cursor.execute(
            "SELECT id, weight, height, age, gender, activity, goal_offset, target_kcal, target_p, target_c, target_f, COALESCE(burned_kcal, 244) FROM profile WHERE id = 1"
        )
        return self.cursor.fetchone()

    def add_food(self, meal_type, name, grams, kcal, p, c, f):
        self.cursor.execute(
            """
            INSERT INTO food_log (meal_type, name, grams, kcal, p, c, f)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (meal_type, name, grams, kcal, p, c, f),
        )
        self.conn.commit()

    def get_today_foods(self):
        self.cursor.execute("""
            SELECT id, COALESCE(meal_type, 'Almuerzo'), name, grams, kcal, p, c, f
            FROM food_log 
            WHERE date = DATE('now', 'localtime')
        """)
        return self.cursor.fetchall()

    def delete_food(self, food_id):
        self.cursor.execute("DELETE FROM food_log WHERE id = ?", (food_id,))
        self.conn.commit()


# ==========================================
# APLICACIÓN MÓVIL ESTILO CLEAN LIGHT
# ==========================================
def main(page: ft.Page):
    db = DatabaseManager()

    page.title = "GymMacro Pro"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = "#f5f7fa"
    page.padding = 0

    try:
        page.window.width = 420
        page.window.height = 860
    except Exception:
        pass

    OFF_HEADERS = {
        "User-Agent": "GymMacroProApp - Windows - Version 2.0 (Contact: user@gymmacro.com)"
    }

    CARD_SHADOW = ft.BoxShadow(
        spread_radius=0, blur_radius=18, color="#12000000"
    )

    status_text = ft.Text("", color="white", size=13, weight="bold")
    status_banner = ft.Container(
        content=ft.Row([status_text], alignment=ft.MainAxisAlignment.CENTER),
        bgcolor="#10b981",
        padding=10,
        visible=False,
    )

    def notify(msg, color="#1d8cf8"):
        status_text.value = msg
        status_banner.bgcolor = color
        status_banner.visible = True
        page.update()

        def hide_later():
            import time

            time.sleep(2.5)
            status_banner.visible = False
            page.update()

        threading.Thread(target=hide_later, daemon=True).start()

    def make_button(text, bg_color, on_click_fn):
        return ft.Container(
            bgcolor=bg_color,
            border_radius=14,
            padding=14,
            on_click=on_click_fn,
            content=ft.Row(
                [ft.Text(text, color="white", weight="bold", size=15)],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
        )

    MEALS_META = {
        "Desayuno": {"icon": "☕", "ratio": 0.30},
        "Almuerzo": {"icon": "🥪", "ratio": 0.40},
        "Cena": {"icon": "🥗", "ratio": 0.25},
        "Snacks": {"icon": "🍎", "ratio": 0.05},
    }
    selected_meal_for_add = ["Desayuno"]
    expanded_meal = [None]

    # ==========================================
    # CAPA 1: PANTALLA "HOY"
    # ==========================================
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

    def create_macro_col(label_title):
        bar = ft.ProgressBar(
            value=0.0, color="#38abfd", bgcolor="#edf1f5", height=7
        )
        val_txt = ft.Text(
            "0 / 0 g", size=13, weight="bold", color="#1e293b"
        )
        col = ft.Column(
            [
                ft.Text(label_title, size=13, color="#64748b"),
                ft.Container(
                    content=bar,
                    width=85,
                    border_radius=4,
                    padding=8,  # Usando padding numérico directo seguro
                ),
                val_txt,
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=3,
            expand=True,
        )
        return col, bar, val_txt

    col_c, bar_c, lbl_c = create_macro_col("Carbohidratos")
    col_p, bar_p, lbl_p = create_macro_col("Proteínas")
    col_f, bar_f, lbl_f = create_macro_col("Grasas")

    meals_card_col = ft.Column(spacing=0)

    def go_to_add_for_meal(meal_name):
        selected_meal_for_add[0] = meal_name
        render_meal_selector_pills()
        switch_tab(1)

    def toggle_meal_details(meal_name):
        if expanded_meal[0] == meal_name:
            expanded_meal[0] = None
        else:
            expanded_meal[0] = meal_name
        refresh_dashboard()

    def delete_food_item(fid):
        db.delete_food(fid)
        refresh_dashboard()
        notify("Alimento eliminado", "#ef4444")

    def refresh_dashboard():
        prof = db.get_profile()
        _, _, _, _, _, _, _, tkcal, tp, tc, tf, burned = prof
        foods = db.get_today_foods()

        skcal = sum(x[4] for x in foods)
        sp = sum(x[5] for x in foods)
        sc = sum(x[6] for x in foods)
        sf = sum(x[7] for x in foods)

        lbl_consumed.value = f"{int(skcal):,}".replace(",", ".")
        lbl_burned.value = f"{int(burned):,}".replace(",", ".")

        rem_kcal = max(0, int(tkcal - skcal))
        lbl_remaining.value = f"{rem_kcal:,}".replace(",", ".")
        ring_main.value = min(1.0, skcal / max(1.0, tkcal))

        lbl_c.value = f"{int(sc)} / {int(tc)} g"
        bar_c.value = min(1.0, sc / max(1.0, tc))

        lbl_p.value = f"{int(sp)} / {int(tp)} g"
        bar_p.value = min(1.0, sp / max(1.0, tp))

        lbl_f.value = f"{int(sf)} / {int(tf)} g"
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
                width=52,
                height=52,
                controls=[
                    ft.ProgressRing(
                        value=m_prog,
                        width=52,
                        height=52,
                        stroke_width=4,
                        color="#38abfd",
                        bgcolor="#edf1f5",
                    ),
                    ft.Container(
                        width=52,
                        height=52,
                        content=ft.Row(
                            [ft.Text(meta["icon"], size=22)],
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
                                    size=17,
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
                            on_click=lambda e, mn=m_name: go_to_add_for_meal(
                                mn
                            ),
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
                                "Sin alimentos en esta comida. Pulsa '+' para añadir.",
                                size=12,
                                color="#94a3b8",
                            ),
                        )
                    )
                else:
                    for fid, _, fname, fgrams, fkcal, fp, fc, ff in m_foods:
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
                                                    f"{int(fkcal)} kcal  |  C: {fc:.1f}g  •  P: {fp:.1f}g  •  G: {ff:.1f}g",
                                                    size=11,
                                                    color="#64748b",
                                                ),
                                            ],
                                            spacing=1,
                                            expand=True,
                                        ),
                                        ft.Container(
                                            padding=6,
                                            on_click=lambda e, i=fid: (
                                                delete_food_item(i)
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
                        ft.Text("Restantes", size=13, color="#64748b"),
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
                ft.Text("Hoy", size=34, weight="bold", color="#1e293b"),
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
                                                size=13,
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
                                                size=13,
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
                            ft.Container(height=10),
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
                    padding=10,
                    shadow=CARD_SHADOW,
                    content=meals_card_col,
                ),
                ft.Text(
                    "💡 Pulsa '+' para añadir o toca una comida para ver su desglose.",
                    size=11,
                    color="#94a3b8",
                    text_align="center",
                ),
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            spacing=16,
        ),
    )

    # ==========================================
    # CAPA 2: AÑADIR ALIMENTO Y BUSCADOR
    # ==========================================
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
                    on_click=lambda e, mn=m_name: set_meal_for_add(mn),
                    content=ft.Text(
                        f"{meta['icon']} {m_name}",
                        size=12,
                        weight="bold",
                        color="white" if active else "#64748b",
                    ),
                )
            )

    def set_meal_for_add(mn):
        selected_meal_for_add[0] = mn
        render_meal_selector_pills()
        page.update()

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
            {
                "name": "Pechuga de Pollo (Plancha / Cruda)",
                "kcal": 105,
                "p": 23.0,
                "c": 0.0,
                "f": 1.2,
            },
            {
                "name": "Muslo de Pollo",
                "kcal": 177,
                "p": 20.0,
                "c": 0.0,
                "f": 10.5,
            },
        ],
        "arroz": [
            {
                "name": "Arroz Blanco (Cocido)",
                "kcal": 130,
                "p": 2.7,
                "c": 28.0,
                "f": 0.3,
            },
            {
                "name": "Arroz Blanco (En crudo)",
                "kcal": 350,
                "p": 7.0,
                "c": 78.0,
                "f": 1.0,
            },
        ],
        "avena": [
            {
                "name": "Copos de Avena",
                "kcal": 389,
                "p": 16.9,
                "c": 66.3,
                "f": 6.9,
            }
        ],
        "atun": [
            {
                "name": "Atún al Natural",
                "kcal": 103,
                "p": 24.0,
                "c": 0.0,
                "f": 1.0,
            },
            {
                "name": "Atún en Aceite de Oliva",
                "kcal": 190,
                "p": 22.0,
                "c": 0.0,
                "f": 11.0,
            },
        ],
        "huevo": [
            {
                "name": "Huevo Entero",
                "kcal": 155,
                "p": 13.0,
                "c": 1.1,
                "f": 11.0,
            },
            {
                "name": "Claras de Huevo",
                "kcal": 52,
                "p": 11.0,
                "c": 0.7,
                "f": 0.2,
            },
        ],
        "patata": [
            {
                "name": "Patata Cocida / Asada",
                "kcal": 87,
                "p": 2.0,
                "c": 20.0,
                "f": 0.1,
            }
        ],
        "ternera": [
            {
                "name": "Ternera Magra (Plancha)",
                "kcal": 135,
                "p": 22.0,
                "c": 0.0,
                "f": 5.0,
            }
        ],
        "pavo": [
            {
                "name": "Pechuga de Pavo",
                "kcal": 105,
                "p": 21.0,
                "c": 1.0,
                "f": 1.5,
            }
        ],
        "salmon": [
            {
                "name": "Salmón Fresco",
                "kcal": 208,
                "p": 20.0,
                "c": 0.0,
                "f": 13.0,
            }
        ],
        "platano": [
            {
                "name": "Plátano",
                "kcal": 89,
                "p": 1.1,
                "c": 22.8,
                "f": 0.3,
            }
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
                            on_click=lambda e, n=item["name"], k=item["kcal"], p=item[
                                "p"
                            ], c=item["c"], f=item["f"]: fill_food_form(
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
                                    ft.Text(
                                        "Elegir",
                                        color="#1d8cf8",
                                        weight="bold",
                                    ),
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
                        on_click=lambda e, n=pname, k=k, p=p, c=c, f=f: (
                            fill_food_form(n, k, p, c, f)
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
                                ft.Text(
                                    "Elegir", color="#38abfd", size=12
                                ),
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
            db.add_food(meal_dest, name, grams, k, p, c, f)
            for field in (tf_name, tf_kcal, tf_p, tf_c, tf_f):
                field.value = ""
            tf_grams.value = "100"
            expanded_meal[0] = meal_dest
            notify(f"Añadido a {meal_dest}", "#10b981")
            switch_tab(0)
        except ValueError:
            notify("Rellena los gramos y macros con números", "#ef4444")

    view_add = ft.Container(
        padding=20,
        expand=True,
        content=ft.Column(
            [
                ft.Text(
                    "Añadir Alimento", size=28, weight="bold", color="#1e293b"
                ),
                ft.Text(
                    "Selecciona la comida:",
                    size=13,
                    color="#64748b",
                    weight="bold",
                ),
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
                ft.Text(
                    "Valores por cada 100g:",
                    size=14,
                    weight="bold",
                    color="#64748b",
                ),
                tf_name,
                tf_grams,
                ft.Row([tf_kcal, tf_p]),
                ft.Row([tf_c, tf_f]),
                make_button(
                    "➕ Registrar Alimento", "#1d8cf8", save_food_entry
                ),
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            spacing=12,
        ),
    )

    # ==========================================
    # CAPA 3: ESCÁNER QR
    # ==========================================
    tf_barcode = ft.TextField(
        label="Código de barras (EAN-13) o JSON QR",
        bgcolor="#ffffff",
        border_radius=14,
        border_color="#e2e8f0",
    )

    def process_code_string(code_str):
        code_str = (code_str or "").strip()
        if not code_str:
            notify("Introduce o escanea un código primero", "#ef4444")
            return

        try:
            data = json.loads(code_str)
            if all(k in data for k in ("name", "kcal", "p", "c", "f")):
                fill_food_form(
                    data["name"],
                    data["kcal"],
                    data["p"],
                    data["c"],
                    data["f"],
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

    def open_camera_scanner(e):
        def cam_worker():
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                notify("No se pudo abrir la cámara web", "#ef4444")
                return
            detector = cv2.QRCodeDetector()
            found = None
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                cv2.putText(
                    frame,
                    "Apunta al QR (Pulsa 'Q' para salir)",
                    (20, 35),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (0, 200, 100),
                    2,
                )
                data, _, _ = detector.detectAndDecode(frame)
                if data:
                    found = data
                    break
                cv2.imshow("Escaner QR - GymMacro", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            cap.release()
            cv2.destroyAllWindows()
            if found:
                tf_barcode.value = found
                process_code_string(found)

        threading.Thread(target=cam_worker, daemon=True).start()

    view_scan = ft.Container(
        padding=20,
        expand=True,
        content=ft.Column(
            [
                ft.Text("Escáner", size=28, weight="bold", color="#1e293b"),
                ft.Container(
                    bgcolor="#ffffff",
                    padding=25,
                    border_radius=24,
                    shadow=CARD_SHADOW,
                    content=ft.Column(
                        [
                            ft.Text("📷", size=54),
                            ft.Text(
                                "Escanear Código QR",
                                size=18,
                                weight="bold",
                                color="#1e293b",
                            ),
                            ft.Text(
                                "Apunta con tu cámara al QR del alimento o introduce debajo el código de barras del envase.",
                                text_align="center",
                                color="#64748b",
                                size=13,
                            ),
                            make_button(
                                "Abrir Cámara QR",
                                "#1d8cf8",
                                open_camera_scanner,
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=12,
                    ),
                ),
                ft.Container(height=6),
                ft.Text(
                    "Código de Barras Manual o QR JSON:",
                    weight="bold",
                    color="#64748b",
                ),
                tf_barcode,
                make_button(
                    "🔍 Buscar Producto",
                    "#5cd6c0",
                    lambda e: process_code_string(tf_barcode.value),
                ),
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            spacing=14,
        ),
    )

    # ==========================================
    # CAPA 4: PERFIL Y HARRIS-BENEDICT
    # ==========================================
    prof_data = db.get_profile()
    _, pw, ph, pa, pgen, pact, poff, ptkcal, ptp, ptc, ptf, pburned = prof_data

    selected_gender = [pgen if pgen in ("Hombre", "Mujer") else "Hombre"]
    act_multipliers = {
        "Sedentario": 1.2,
        "Ligero": 1.375,
        "Moderado": 1.55,
        "Intenso": 1.725,
        "Atleta": 1.9,
    }
    selected_act = [pact if pact in act_multipliers else "Moderado"]

    tf_w = ft.TextField(
        label="Peso (kg)",
        value=str(pw),
        bgcolor="#ffffff",
        border_radius=14,
        border_color="#e2e8f0",
        expand=True,
    )
    tf_h = ft.TextField(
        label="Altura (cm)",
        value=str(ph),
        bgcolor="#ffffff",
        border_radius=14,
        border_color="#e2e8f0",
        expand=True,
    )
    tf_a = ft.TextField(
        label="Edad",
        value=str(pa),
        bgcolor="#ffffff",
        border_radius=14,
        border_color="#e2e8f0",
        expand=True,
    )

    gender_row = ft.Row(spacing=10)
    act_row = ft.Row(spacing=6, wrap=True)

    def render_gender_pills():
        gender_row.controls.clear()
        for g in ("Hombre", "Mujer"):
            active = selected_gender[0] == g
            gender_row.controls.append(
                ft.Container(
                    bgcolor="#1d8cf8" if active else "#ffffff",
                    padding=12,
                    border_radius=14,
                    expand=True,
                    on_click=lambda e, val=g: set_gender(val),
                    content=ft.Row(
                        [
                            ft.Text(
                                g,
                                color="white" if active else "#64748b",
                                weight="bold",
                            )
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                )
            )

    def set_gender(val):
        selected_gender[0] = val
        render_gender_pills()
        page.update()

    def render_act_pills():
        act_row.controls.clear()
        for k in act_multipliers.keys():
            active = selected_act[0] == k
            act_row.controls.append(
                ft.Container(
                    bgcolor="#1d8cf8" if active else "#ffffff",
                    padding=10,
                    border_radius=12,
                    on_click=lambda e, val=k: set_act(val),
                    content=ft.Text(
                        k,
                        size=12,
                        color="white" if active else "#64748b",
                        weight="bold",
                    ),
                )
            )

    def set_act(val):
        selected_act[0] = val
        render_act_pills()
        page.update()

    render_gender_pills()
    render_act_pills()

    lbl_offset = ft.Text(
        f"Déficit / Superávit: {int(poff):+d} kcal",
        weight="bold",
        color="#1e293b",
    )

    def on_slider_change(e):
        val = int(e.control.value)
        lbl_offset.value = f"Déficit / Superávit: {val:+d} kcal"
        page.update()

    sld_offset = ft.Slider(
        min=-1000,
        max=1000,
        divisions=40,
        value=poff,
        active_color="#1d8cf8",
        on_change=on_slider_change,
    )

    lbl_bmr_info = ft.Text(
        "Calcula tu TMB con la ecuación de Harris-Benedict",
        color="#1d8cf8",
        size=13,
    )

    tf_tkcal = ft.TextField(
        label="Objetivo Kcal",
        value=str(int(ptkcal)),
        bgcolor="#ffffff",
        border_radius=14,
        border_color="#e2e8f0",
        expand=True,
    )
    tf_burned = ft.TextField(
        label="Kcal Quemadas",
        value=str(int(pburned)),
        bgcolor="#ffffff",
        border_radius=14,
        border_color="#e2e8f0",
        expand=True,
    )
    tf_tc = ft.TextField(
        label="Carbohidratos (g)",
        value=str(int(ptc)),
        bgcolor="#ffffff",
        border_radius=14,
        border_color="#e2e8f0",
        expand=True,
    )
    tf_tp = ft.TextField(
        label="Proteínas (g)",
        value=str(int(ptp)),
        bgcolor="#ffffff",
        border_radius=14,
        border_color="#e2e8f0",
        expand=True,
    )
    tf_tf = ft.TextField(
        label="Grasas (g)",
        value=str(int(ptf)),
        bgcolor="#ffffff",
        border_radius=14,
        border_color="#e2e8f0",
        expand=True,
    )

    def calc_harris_benedict(e):
        try:
            w, h, a = float(tf_w.value), float(tf_h.value), int(tf_a.value)
            if selected_gender[0] == "Hombre":
                bmr = 66.473 + (13.7516 * w) + (5.0033 * h) - (6.755 * a)
            else:
                bmr = 655.0955 + (9.5634 * w) + (1.8496 * h) - (4.6756 * a)

            tdee = bmr * act_multipliers[selected_act[0]]
            target = tdee + float(sld_offset.value)

            p_g = round(w * 2.0)
            f_g = round(w * 0.9)
            c_g = max(0, round((target - (p_g * 4) - (f_g * 9)) / 4))

            lbl_bmr_info.value = (
                f"TMB: {int(bmr)} kcal  |  Mantenimiento: {int(tdee)} kcal"
            )
            tf_tkcal.value = str(int(target))
            tf_tp.value = str(p_g)
            tf_tc.value = str(c_g)
            tf_tf.value = str(f_g)
            notify("Calculado con Harris-Benedict")
        except ValueError:
            notify("Revisa Peso, Altura y Edad", "#ef4444")

    def save_profile_changes(e):
        try:
            db.save_profile(
                (
                    float(tf_w.value),
                    float(tf_h.value),
                    int(tf_a.value),
                    selected_gender[0],
                    selected_act[0],
                    float(sld_offset.value),
                    float(tf_tkcal.value),
                    float(tf_tp.value),
                    float(tf_tc.value),
                    float(tf_tf.value),
                    float(tf_burned.value),
                )
            )
            notify("¡Objetivos actualizados!", "#10b981")
            switch_tab(0)
        except ValueError:
            notify("Revisa que todos los límites sean números", "#ef4444")

    view_profile = ft.Container(
        padding=20,
        expand=True,
        content=ft.Column(
            [
                ft.Text("Mi Perfil", size=28, weight="bold", color="#1e293b"),
                ft.Row([tf_w, tf_h, tf_a]),
                ft.Text("Sexo:", size=13, color="#64748b", weight="bold"),
                gender_row,
                ft.Text(
                    "Nivel de Ejercicio Físico:",
                    size=13,
                    color="#64748b",
                    weight="bold",
                ),
                act_row,
                lbl_offset,
                sld_offset,
                make_button(
                    "⚡ Calcular con Harris-Benedict",
                    "#1d8cf8",
                    calc_harris_benedict,
                ),
                lbl_bmr_info,
                ft.Divider(color="#e2e8f0"),
                ft.Text(
                    "Tus Límites Diarios (Editables):",
                    size=14,
                    weight="bold",
                    color="#64748b",
                ),
                ft.Row([tf_tkcal, tf_burned]),
                ft.Row([tf_tc, tf_tp, tf_tf]),
                make_button(
                    "💾 Guardar Objetivos", "#5cd6c0", save_profile_changes
                ),
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            spacing=11,
        ),
    )

    # ==========================================
    # BARRA DE NAVEGACIÓN INFERIOR
    # ==========================================
    body_container = ft.Container(content=view_home, expand=True)
    nav_row = ft.Row(alignment=ft.MainAxisAlignment.SPACE_AROUND)

    tabs_meta = [
        ("📊", "Hoy", view_home),
        ("➕", "Añadir", view_add),
        ("📷", "Escáner", view_scan),
        ("⚙️", "Perfil", view_profile),
    ]
    current_tab = [0]

    def render_nav_bar():
        nav_row.controls.clear()
        for idx, (emoji, title, _) in enumerate(tabs_meta):
            is_active = current_tab[0] == idx
            nav_row.controls.append(
                ft.Container(
                    padding=10,
                    border_radius=16,
                    bgcolor="#eff6ff" if is_active else "transparent",
                    on_click=lambda e, i=idx: switch_tab(i),
                    content=ft.Column(
                        [
                            ft.Text(emoji, size=19),
                            ft.Text(
                                title,
                                size=11,
                                weight="bold" if is_active else "normal",
                                color="#1d8cf8" if is_active else "#94a3b8",
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=2,
                    ),
                )
            )

    def switch_tab(index):
        current_tab[0] = index
        if index == 0:
            refresh_dashboard()
        body_container.content = tabs_meta[index][2]
        render_nav_bar()
        page.update()

    bottom_bar = ft.Container(
        bgcolor="#ffffff",
        padding=6,
        shadow=ft.BoxShadow(
            spread_radius=0, blur_radius=12, color="#0d000000"
        ),
        content=nav_row,
    )

    render_nav_bar()
    page.add(
        ft.Column(
            [status_banner, body_container, bottom_bar],
            expand=True,
            spacing=0,
        )
    )
    refresh_dashboard()

if __name__ == "__main__":
    import os
    # Render usa la variable PORT y requiere host 0.0.0.0
    port = int(os.getenv("PORT", 8550))
    ft.app(target=main, view=ft.AppView.WEB_BROWSER, port=port, host="0.0.0.0")