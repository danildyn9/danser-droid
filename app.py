"""
danser!droid — GUI приложение для рендера osu!droid реплеев.
Зависимости: customtkinter, opencv-python, numpy, osudroid-api-wrapper
Сборка в .exe: pyinstaller --onefile --windowed --name "danser!droid" app.py
"""

import customtkinter as ctk
import threading
import os
import json
import time
import sys
import zipfile
from tkinter import filedialog, messagebox
from collections import deque

import cv2
import numpy as np
from PIL import Image


def get_assets_dir():
    """Работает и при запуске из .py, и из собранного .exe (PyInstaller)."""
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, "assets")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

ASSETS_DIR = get_assets_dir()

# Пытаемся импортировать библиотеку для разбора реплеев
try:
    import osudroid_api_wrapper as od
    ODR_AVAILABLE = True
except ImportError:
    ODR_AVAILABLE = False


# =============================================================================
# ЛОГИКА РАЗБОРА РЕПЛЕЯ (.odr → .json)
# =============================================================================

def unpack_replay(replay_file_path):
    if not ODR_AVAILABLE:
        return None, None, "Библиотека osudroid_api_wrapper не установлена"
    if replay_file_path.endswith('.odr'):
        replay = od.Replay().load(replay_file_path)
        return replay, replay_file_path, None
    try:
        opened_replay = zipfile.ZipFile(replay_file_path, 'r')
        for file in opened_replay.namelist():
            if file.endswith('.odr'):
                opened_replay.extract(file, os.path.dirname(replay_file_path))
                replay_file_path = os.path.join(os.path.dirname(replay_file_path), file)
                break
        replay = od.Replay().load(replay_file_path)
    except zipfile.BadZipFile:
        return None, None, "Не удалось открыть файл реплея"
    return replay, replay_file_path, None


def extract_json_from_replay(odr_path, output_dir="output"):
    os.makedirs(output_dir, exist_ok=True)
    replay, replay_file_path, err = unpack_replay(odr_path)
    if err:
        return None, err
    cursor_data = replay.cursor_data
    out_name = os.path.basename(replay_file_path) + ".json"
    out_path = os.path.join(output_dir, out_name)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cursor_data, f, default=lambda o: o.to_dict, indent=4)
    return out_path, None


# =============================================================================
# ЛОГИКА РЕНДЕРА (.json → .mp4)
# =============================================================================

def build_group_events(group):
    events = []
    for stroke in group:
        down = stroke["down"]
        events.append({"t": down["time"], "x": down["position"]["x"], "y": down["position"]["y"], "kind": "down"})
        for m in stroke.get("moves", []):
            events.append({"t": m["time"], "x": m["position"]["x"], "y": m["position"]["y"], "kind": "move"})
        if "up" in stroke:
            up = stroke["up"]
            events.append({"t": up["time"], "x": up["position"]["x"], "y": up["position"]["y"], "kind": "up"})
    events.sort(key=lambda e: e["t"])
    return events


def render_json_to_video(json_path, video_path, osu_path=None,
                         progress_cb=None, status_cb=None):
    if status_cb:
        status_cb("Загрузка данных...")

    # --- Курсор ---
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not data or all(len(g) == 0 for g in data):
        if status_cb:
            status_cb("Ошибка: нет данных курсора")
        return False

    # --- Карта (опционально) ---
    osu_data      = None
    hit_objects   = []
    preempt       = 600.0    # fallback
    fade_in_time  = 400.0
    circle_r      = 30.0     # osu-пикс, fallback
    combo_colors  = [(128, 191, 255)]   # BGR fallback

    if osu_path and os.path.exists(osu_path):
        from parse_osu import (parse_osu, calc_circle_radius,
                               calc_preempt, calc_fade_in,
                               calc_slider_duration, build_slider_path)
        osu_data    = parse_osu(osu_path)
        diff        = osu_data["difficulty"]
        circle_r    = calc_circle_radius(diff.get("cs", 5))
        preempt     = calc_preempt(diff.get("ar", 5))
        fade_in_time= calc_fade_in(diff.get("ar", 5))

        # Присваиваем номера комбо каждому объекту
        combo_num = 0
        combo_idx = 0
        for obj in osu_data["hit_objects"]:
            if obj.get("new_combo"):
                combo_num = 1
                combo_idx += 1
            else:
                combo_num += 1
            obj["combo_num"] = combo_num
            obj["combo_idx"] = combo_idx

        # Строим пути слайдеров заранее
        sm = diff.get("slider_multiplier", 1.4)
        for obj in osu_data["hit_objects"]:
            if obj["type"] == "slider":
                obj["path"] = build_slider_path(
                    obj["control_points"], obj["curve_type"], obj["length"])
                obj["duration"] = calc_slider_duration(
                    obj, osu_data["timing_points"], sm)
            elif obj["type"] == "spinner":
                obj["duration"] = obj["end_time"] - obj["time"]

        hit_objects = osu_data["hit_objects"]

    # --- Видеопараметры ---
    width, height = 1920, 1080
    fps       = 60
    frame_ms  = 1000.0 / fps
    fourcc    = cv2.VideoWriter_fourcc(*"mp4v")
    os.makedirs(os.path.dirname(video_path) or ".", exist_ok=True)
    out = cv2.VideoWriter(video_path, fourcc, fps, (width, height))

    # Масштаб: поле osu! 512×384 → экран
    scale    = 2.1
    offset_x = (width  - 512 * scale) / 2.0
    offset_y = (height - 384 * scale) / 2.0

    def to_screen(x, y):
        return (int(x * scale + offset_x), int(y * scale + offset_y))

    # --- Временная шкала ---
    # И курсор, и .osu используют одну шкалу: мс от старта аудио.
    group_events = [build_group_events(group) for group in data]
    all_times    = [ev["t"] for evs in group_events for ev in evs]
    if not all_times:
        return False

    cursor_start = min(all_times)
    cursor_end   = max(all_times)

    # Начинаем чуть раньше чтобы approach circle первых объектов были видны
    if hit_objects:
        map_start = hit_objects[0]["time"] - preempt - 100
        start_ms  = min(cursor_start, map_start)
    else:
        start_ms  = cursor_start
    end_ms       = cursor_end
    total_frames = int((end_ms - start_ms) / frame_ms) + 2

    # --- Состояние курсора ---
    group_state = []
    for evs in group_events:
        group_state.append({"events": evs, "ev_idx": 0,
                            "active": False, "x": None, "y": None})

    cursor_trail  = deque(maxlen=22)
    prev_trail_pos = None

    # --- Радиус кружка в экранных пикселях ---
    scr_r = int(circle_r * scale)

    # --- Цвет одного комбо (BGR) ---
    COMBO_PALETTE = [
        (255, 191, 128),   # голубой (как в карте)
        (128, 220, 255),   # жёлтый
        (100, 255, 180),   # зелёный
        (180, 130, 255),   # розовый
    ]

    def combo_color(idx):
        return COMBO_PALETTE[idx % len(COMBO_PALETTE)]

    def overlay_circle(frame, cx, cy, r, color, alpha, filled=True, thickness=2):
        """Рисует круг с прозрачностью через overlay."""
        if r <= 0:
            return
        overlay = frame.copy()
        if filled:
            cv2.circle(overlay, (cx, cy), r, color, -1, cv2.LINE_AA)
        cv2.circle(overlay, (cx, cy), r, color, thickness, cv2.LINE_AA)
        cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)

    if status_cb:
        status_cb("Рендеринг...")
    t_wall = time.time()

    for frame_idx in range(total_frames):
        t     = start_ms + frame_idx * frame_ms
        frame = np.zeros((height, width, 3), dtype=np.uint8)

        # ── 1. Playfield boundary (тонкий прямоугольник) ──────────────────
        pf_tl = to_screen(0,   0)
        pf_br = to_screen(512, 384)
        cv2.rectangle(frame, pf_tl, pf_br, (30, 30, 40), 1)

        # ── 2. Карта ───────────────────────────────────────────────────────
        if hit_objects:
            # Собираем видимые объекты; рисуем в обратном порядке (более
            # поздние — снизу, более ранние — сверху).
            visible = []
            for obj in hit_objects:
                t_appear = obj["time"] - preempt
                if obj["type"] == "spinner":
                    t_gone = obj["time"] + obj.get("duration", 1000) + 200
                elif obj["type"] == "slider":
                    t_gone = obj["time"] + obj.get("duration", 500) + 200
                else:
                    t_gone = obj["time"] + 200   # grace после удара
                if t_appear <= t <= t_gone:
                    visible.append(obj)

            # Рисуем от самых поздних к самым ранним (z-order)
            for obj in sorted(visible, key=lambda o: -o["time"]):
                cx, cy = to_screen(obj["x"], obj["y"])
                col    = combo_color(obj.get("combo_idx", 0))
                # alpha появления
                t_fade_end = obj["time"] - preempt + fade_in_time
                if t < t_fade_end:
                    vis_alpha = max(0.0, (t - (obj["time"] - preempt)) / fade_in_time)
                else:
                    vis_alpha = 1.0

                if obj["type"] == "circle":
                    # Тело кружка
                    cv2.circle(frame, (cx, cy), scr_r, (20, 20, 30), -1, cv2.LINE_AA)
                    # Цветная обводка
                    thick_ring = max(2, scr_r // 8)
                    cv2.circle(frame, (cx, cy), scr_r,     col, thick_ring, cv2.LINE_AA)
                    cv2.circle(frame, (cx, cy), scr_r - thick_ring - 1,
                               (200, 200, 200), 1, cv2.LINE_AA)

                    # Approach circle (сужается от 3x до 1x)
                    if t < obj["time"]:
                        approach_t = 1.0 - (obj["time"] - t) / preempt   # 0→1
                        ap_r = int(scr_r * (3.0 - 2.0 * approach_t))
                        if ap_r > scr_r:
                            ap_alpha = vis_alpha * 0.9
                            ov = frame.copy()
                            cv2.circle(ov, (cx, cy), ap_r, col, max(1, scr_r // 10), cv2.LINE_AA)
                            cv2.addWeighted(ov, ap_alpha, frame, 1 - ap_alpha, 0, frame)

                    # Номер комбо
                    num_str = str(obj.get("combo_num", ""))
                    font_scale = max(0.4, scr_r / 28.0)
                    (tw, th), _ = cv2.getTextSize(num_str, cv2.FONT_HERSHEY_SIMPLEX,
                                                  font_scale, 2)
                    cv2.putText(frame, num_str,
                                (cx - tw // 2, cy + th // 2),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                font_scale, (220, 220, 220), 2, cv2.LINE_AA)

                elif obj["type"] == "slider":
                    path = obj.get("path", [])
                    if len(path) >= 2:
                        # Тело слайдера
                        pts_scr = [to_screen(p[0], p[1]) for p in path]
                        body_col = tuple(max(0, c - 80) for c in col)
                        for i in range(1, len(pts_scr)):
                            cv2.line(frame, pts_scr[i-1], pts_scr[i],
                                     body_col, scr_r * 2, cv2.LINE_AA)
                        # Белая обводка поверх
                        for i in range(1, len(pts_scr)):
                            cv2.line(frame, pts_scr[i-1], pts_scr[i],
                                     (200, 200, 200), max(1, scr_r * 2 - 6), cv2.LINE_AA)
                        # Головной кружок
                        cv2.circle(frame, pts_scr[0], scr_r, col, max(2, scr_r // 8), cv2.LINE_AA)
                        # Конечный кружок
                        cv2.circle(frame, pts_scr[-1], int(scr_r * 0.8),
                                   (160, 160, 160), 2, cv2.LINE_AA)

                        # Approach circle слайдера
                        if t < obj["time"]:
                            approach_t = 1.0 - (obj["time"] - t) / preempt
                            ap_r = int(scr_r * (3.0 - 2.0 * approach_t))
                            if ap_r > scr_r:
                                ov = frame.copy()
                                cv2.circle(ov, pts_scr[0], ap_r, col,
                                           max(1, scr_r // 10), cv2.LINE_AA)
                                cv2.addWeighted(ov, vis_alpha * 0.9,
                                                frame, 1 - vis_alpha * 0.9, 0, frame)

                        # Шарик слайдера (движется по пути)
                        if obj["time"] <= t <= obj["time"] + obj.get("duration", 500):
                            prog = (t - obj["time"]) / obj["duration"]
                            # Учитываем slides (туда-обратно)
                            slides = obj.get("slides", 1)
                            local_prog = prog * slides % 1.0
                            if int(prog * slides) % 2 == 1:
                                local_prog = 1.0 - local_prog
                            pidx = min(int(local_prog * (len(path) - 1)),
                                       len(path) - 1)
                            bx, by = to_screen(path[pidx][0], path[pidx][1])
                            cv2.circle(frame, (bx, by), int(scr_r * 0.7),
                                       (240, 240, 240), -1, cv2.LINE_AA)
                            cv2.circle(frame, (bx, by), int(scr_r * 0.7),
                                       col, 2, cv2.LINE_AA)

                elif obj["type"] == "spinner":
                    import math
                    cx2, cy2 = to_screen(256, 192)
                    spin_r = int(scr_r * 5)
                    prog   = max(0.0, (t - obj["time"]) / max(1, obj.get("duration", 1000)))
                    angle  = prog * math.pi * 20
                    for ri, alpha_val in [(spin_r, 0.6), (spin_r // 2, 0.4)]:
                        ov = frame.copy()
                        cv2.ellipse(ov, (cx2, cy2), (ri, ri), 0,
                                    0, 360, col, 2, cv2.LINE_AA)
                        # Вращающийся маркер
                        mx = int(cx2 + ri * math.cos(angle))
                        my = int(cy2 + ri * math.sin(angle))
                        cv2.circle(ov, (mx, my), 8, (255, 255, 255), -1)
                        cv2.addWeighted(ov, alpha_val, frame, 1 - alpha_val, 0, frame)

        # ── 3. Курсор ──────────────────────────────────────────────────────
        for gs in group_state:
            had_down = False
            while gs["ev_idx"] < len(gs["events"]):
                ev = gs["events"][gs["ev_idx"]]
                if ev["t"] > t:
                    break
                gs["x"] = ev["x"]
                gs["y"] = ev["y"]
                if ev["kind"] == "down":
                    gs["active"] = True
                    had_down = True
                elif ev["kind"] == "up":
                    gs["active"] = False
                gs["ev_idx"] += 1
            gs["had_down"] = had_down

        gs0 = group_state[0]
        if gs0["x"] is not None and gs0["active"]:
            sx, sy = to_screen(gs0["x"], gs0["y"])
            if prev_trail_pos and np.linalg.norm(
                    np.array([sx, sy]) - np.array(prev_trail_pos)) > 300:
                cursor_trail.clear()
            cursor_trail.append((sx, sy))
            prev_trail_pos = (sx, sy)

            pts = list(cursor_trail)
            for i in range(1, len(pts)):
                alpha = i / len(pts)
                thick = max(1, int(16 * alpha) + 2)
                c = (int(255 * alpha), int(180 * alpha), int(50 * alpha))
                cv2.line(frame, pts[i-1], pts[i], c, thick, cv2.LINE_AA)

            cv2.circle(frame, (sx, sy), 14, (255, 255, 255), -1, cv2.LINE_AA)
            cv2.circle(frame, (sx, sy), 18, (255, 255, 255),  2, cv2.LINE_AA)
        elif not gs0["active"]:
            cursor_trail.clear()
            prev_trail_pos = None

        # Тап-пальцы (группы 1+)
        for g_idx in range(1, len(group_state)):
            gs = group_state[g_idx]
            if gs["x"] is None:
                continue
            if gs["active"] or gs["had_down"]:
                sx, sy = to_screen(gs["x"], gs["y"])
                color = (180, 180, 180) if gs["active"] else (140, 140, 255)
                cv2.circle(frame, (sx, sy), 10, color, -1, cv2.LINE_AA)
                cv2.circle(frame, (sx, sy), 13, color,  2, cv2.LINE_AA)

        out.write(frame)

        if frame_idx % 60 == 0 and progress_cb:
            pct = int(frame_idx / total_frames * 100)
            progress_cb(pct)

    out.release()
    elapsed = time.time() - t_wall
    if progress_cb:
        progress_cb(100)
    return elapsed


# =============================================================================
# GUI
# =============================================================================

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

PINK       = "#ff66aa"
PINK_DARK  = "#cc4488"
BG         = "#0d0d14"
CARD       = "#14141f"
CARD2      = "#1a1a2e"
TEXT       = "#ffffff"
TEXT_DIM   = "#888899"
GREEN      = "#44ffaa"
FONT_TITLE = ("Trebuchet MS", 22, "bold")
FONT_LABEL = ("Trebuchet MS", 13)
FONT_SMALL = ("Trebuchet MS", 11)
FONT_BTN   = ("Trebuchet MS", 13, "bold")
FONT_STATUS= ("Trebuchet MS", 12)

# =============================================================================
# ПЕРЕВОДЫ
# =============================================================================

LANGS = {
    "ru": {
        "replay_label":    "Реплей (.odr)",
        "json_label":      ".json файл",
        "choose_file":     "Выбрать файл",
        "fetch_json":      "→ JSON",
        "render_btn":      "Render!",
        "rendering_btn":   "Rendering...",
        "status_idle":     "Выберите файл реплея или .json",
        "status_json_hint":"Нажми → JSON чтобы извлечь данные",
        "status_json_ready":"JSON готов — нажми Render!",
        "status_ready":    "Готово к рендеру — нажми Render!",
        "status_extracting":"Извлекаю данные из реплея...",
        "status_preparing":"Подготовка...",
        "status_done":     "Готово за {t:.1f}секунд ✓",
        "status_error":    "Ошибка: {e}",
        "status_render_err":"Ошибка рендера",
        "status_rendering":"Rendering... {p}%",
        "status_rendering_eta":"Rendering... {p}%  (осталось ~{e}секунд)",
        "no_file_title":   "Нет файла",
        "no_odr_msg":      "Сначала выбери .odr файл реплея",
        "no_json_msg":     "Выбери .json файл или извлеки его из реплея",
        "no_lib_title":    "Нет библиотеки",
        "no_lib_msg":      "Установи osudroid-api-wrapper:\npip install osudroid-api-wrapper",
        "not_selected":    "не выбран",
        "subtitle":        "osu!droid replay renderer",
        "osu_label":       ".osu карта",
        "osu_hint":        "(опционально — для рендера нот)",
    },
    "en": {
        "replay_label":    "Replay (.odr)",
        "json_label":      ".json file",
        "choose_file":     "Choose file",
        "fetch_json":      "→ JSON",
        "render_btn":      "Render!",
        "rendering_btn":   "Rendering...",
        "status_idle":     "Choose a replay or .json file",
        "status_json_hint":"Press → JSON to extract data",
        "status_json_ready":"JSON ready — press Render!",
        "status_ready":    "Ready to render — press Render!",
        "status_extracting":"Extracting replay data...",
        "status_preparing":"Preparing...",
        "status_done":     "Done in {t:.1f}seconds ✓",
        "status_error":    "Error: {e}",
        "status_render_err":"Render error",
        "status_rendering":"Rendering... {p}%",
        "status_rendering_eta":"Rendering... {p}%  (~{e}seconds left)",
        "no_file_title":   "No file",
        "no_odr_msg":      "Please choose a .odr replay file first",
        "no_json_msg":     "Choose a .json file or extract it from a replay",
        "no_lib_title":    "Library missing",
        "no_lib_msg":      "Install osudroid-api-wrapper:\npip install osudroid-api-wrapper",
        "not_selected":    "not selected",
        "subtitle":        "osu!droid replay renderer",
        "osu_label":       ".osu map",
        "osu_hint":        "(optional — renders hit objects)",
    },
}



class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("danser!droid")
        self.geometry("520x650")
        self.resizable(False, False)
        self.configure(fg_color=BG)

        self._odr_path  = None   # путь к выбранному .odr файлу
        self._json_path = None   # путь к выбранному .json файлу
        self._osu_path  = None   # путь к .osu файлу карты (опционально)
        self._rendering = False
        self._lang         = "ru"
        self._status_key   = "status_idle"
        self._status_params= {}
        self._status_color = TEXT_DIM

        self._build_ui()

    # -------------------------------------------------------------------------
    def _build_ui(self):
        # --- Логотип + заголовок ---
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(pady=(28, 0))

        logo_path = os.path.join(ASSETS_DIR, "logo.png")
        if os.path.exists(logo_path):
            logo_img = ctk.CTkImage(
                light_image=Image.open(logo_path),
                dark_image=Image.open(logo_path),
                size=(72, 72)
            )
            ctk.CTkLabel(top, image=logo_img, text="", fg_color="transparent", bg_color="transparent").pack()
        else:
            # Fallback — оригинальный d!d если logo.png не найден
            logo_canvas = ctk.CTkCanvas(top, width=72, height=72,
                                        bg=BG, highlightthickness=0)
            logo_canvas.pack()
            logo_canvas.create_oval(4, 4, 68, 68, outline=PINK, width=2)
            logo_canvas.create_oval(14, 14, 58, 58, fill=PINK_DARK, outline="")
            logo_canvas.create_text(36, 36, text="d!d", fill=TEXT,
                                    font=("Trebuchet MS", 14, "bold"))

        ctk.CTkLabel(top, text="danser!droid",
                     font=FONT_TITLE, text_color=PINK).pack(pady=(6, 0))
        self._subtitle_label = ctk.CTkLabel(top, text="osu!droid replay renderer",
                     font=FONT_SMALL, text_color=TEXT_DIM)
        self._subtitle_label.pack()

        # --- Разделитель ---
        ctk.CTkFrame(self, height=1, fg_color=CARD2).pack(fill="x", padx=30, pady=18)

        # --- Карточка с файлами ---
        card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=14)
        card.pack(padx=30, fill="x")

        # Replay (.odr) строка
        self._build_file_row(
            card,
            label_key="replay_label",
            btn_cmd=self._pick_odr,
            attr_name="_odr_label",
            btn_attr="_odr_choose_btn",
            extra_btn_attr="_fetch_json_btn",
            extra_btn_cmd=self._fetch_json,
            row=0
        )

        ctk.CTkFrame(card, height=1, fg_color=CARD2).pack(fill="x", padx=16, pady=4)

        # .json строка
        self._build_file_row(
            card,
            label_key="json_label",
            btn_cmd=self._pick_json,
            attr_name="_json_label",
            btn_attr="_json_choose_btn",
            row=1
        )

        ctk.CTkFrame(card, height=1, fg_color=CARD2).pack(fill="x", padx=16, pady=4)

        # .osu строка
        self._build_file_row(
            card,
            label_key="osu_label",
            btn_cmd=self._pick_osu,
            attr_name="_osu_label",
            btn_attr="_osu_choose_btn",
            row=2
        )

        # --- Разделитель ---
        ctk.CTkFrame(self, height=1, fg_color=CARD2).pack(fill="x", padx=30, pady=18)

        # --- Статус + прогресс ---
        status_area = ctk.CTkFrame(self, fg_color="transparent")
        status_area.pack(padx=30, fill="x")

        self._status_label = ctk.CTkLabel(
            status_area, text="Выберите файл реплея или .json",
            font=FONT_STATUS, text_color=TEXT_DIM, anchor="w"
        )
        self._status_label.pack(fill="x")

        self._progress_bar = ctk.CTkProgressBar(
            status_area, fg_color=CARD2, progress_color=PINK,
            height=6, corner_radius=3
        )
        self._progress_bar.set(0)
        self._progress_bar.pack(fill="x", pady=(6, 0))
        self._progress_bar.pack_forget()  # скрыта до начала рендера

        # --- Кнопка Render ---
        self._render_btn = ctk.CTkButton(
            self,
            text="Render!",
            font=FONT_BTN,
            fg_color=PINK,
            hover_color=PINK_DARK,
            text_color="#ffffff",
            corner_radius=10,
            height=46,
            command=self._start_render
        )
        self._render_btn.pack(padx=30, pady=(18, 10), fill="x")

        # --- Нижняя панель: путь к видео + кнопка Language ---
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(padx=30, pady=(0, 16), fill="x")

        self._output_label = ctk.CTkLabel(
            bottom, text="", font=FONT_SMALL, text_color=TEXT_DIM, anchor="w"
        )
        self._output_label.pack(side="left", fill="x", expand=True)

        self._lang_btn = ctk.CTkButton(
            bottom,
            text="🌐 RU",
            font=FONT_SMALL,
            fg_color=CARD2,
            hover_color="#2a2a44",
            text_color=TEXT_DIM,
            corner_radius=8,
            height=26,
            width=64,
            command=self._toggle_lang
        )
        self._lang_btn.pack(side="right")

    # -------------------------------------------------------------------------
    def _build_file_row(self, parent, label_key, btn_cmd,
                        attr_name, btn_attr,
                        extra_btn_attr=None, extra_btn_cmd=None, row=0):
        s = LANGS[self._lang]
        row_frame = ctk.CTkFrame(parent, fg_color="transparent")
        row_frame.pack(fill="x", padx=16, pady=10)

        row_label = ctk.CTkLabel(row_frame, text=s[label_key], font=FONT_LABEL,
                     text_color=TEXT, width=100, anchor="w")
        row_label.pack(side="left")
        setattr(self, "_lbl_" + label_key, row_label)

        right = ctk.CTkFrame(row_frame, fg_color="transparent")
        right.pack(side="right", fill="x", expand=True)

        # Имя выбранного файла
        file_label = ctk.CTkLabel(
            right, text=s["not_selected"], font=FONT_SMALL,
            text_color=TEXT_DIM, anchor="w"
        )
        file_label.pack(fill="x", pady=(0, 4))
        setattr(self, attr_name, file_label)

        btn_row = ctk.CTkFrame(right, fg_color="transparent")
        btn_row.pack(fill="x")

        choose_btn = ctk.CTkButton(
            btn_row, text=s["choose_file"], font=FONT_SMALL,
            fg_color=CARD2, hover_color="#2a2a44",
            text_color=TEXT, corner_radius=8, height=32,
            command=btn_cmd
        )
        choose_btn.pack(side="left")
        setattr(self, btn_attr, choose_btn)

        if extra_btn_attr:
            extra_btn = ctk.CTkButton(
                btn_row, text=s["fetch_json"], font=FONT_SMALL,
                fg_color="transparent", hover_color=CARD2,
                text_color=PINK, border_color=PINK, border_width=1,
                corner_radius=8, height=32, width=70,
                command=extra_btn_cmd
            )
            extra_btn.pack(side="left", padx=(8, 0))
            setattr(self, extra_btn_attr, extra_btn)

    # -------------------------------------------------------------------------
    def _pick_odr(self):
        path = filedialog.askopenfilename(
            title="Выбери файл реплея",
            filetypes=[("osu!droid replay", "*.odr"), ("All files", "*.*")]
        )
        if path:
            self._odr_path = path
            self._odr_label.configure(
                text=os.path.basename(path), text_color=TEXT
            )
            self._set_status_key("status_json_hint", TEXT_DIM)

    def _pick_json(self):
        path = filedialog.askopenfilename(
            title="Выбери .json файл курсора",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")]
        )
        if path:
            self._json_path = path
            self._json_label.configure(
                text=os.path.basename(path), text_color=TEXT
            )
            self._set_status_key("status_ready", GREEN)

    def _pick_osu(self):
        s = LANGS[self._lang]
        path = filedialog.askopenfilename(
            title="Выбери .osu файл карты",
            filetypes=[("osu! beatmap", "*.osu"), ("All files", "*.*")]
        )
        if path:
            self._osu_path = path
            self._osu_label.configure(
                text=os.path.basename(path), text_color=TEXT
            )

    # -------------------------------------------------------------------------
    def _fetch_json(self):
        if not self._odr_path:
            messagebox.showwarning(LANGS[self._lang]["no_file_title"], LANGS[self._lang]["no_odr_msg"])
            return
        if not ODR_AVAILABLE:
            messagebox.showerror(LANGS[self._lang]["no_lib_title"], LANGS[self._lang]["no_lib_msg"])
            return

        self._set_status_key("status_extracting", TEXT_DIM)
        self._render_btn.configure(state="disabled")

        def worker():
            out_path, err = extract_json_from_replay(self._odr_path)
            def done():
                self._render_btn.configure(state="normal")
                if err:
                    self._set_status_key("status_error", "#ff4466", e=err)
                else:
                    self._json_path = out_path
                    self._json_label.configure(
                        text=os.path.basename(out_path), text_color=TEXT
                    )
                    self._set_status_key("status_json_ready", GREEN)
            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    # -------------------------------------------------------------------------
    def _start_render(self):
        if self._rendering:
            return
        if not self._json_path:
            messagebox.showwarning(LANGS[self._lang]["no_file_title"], LANGS[self._lang]["no_json_msg"])
            return

        out_dir = "video_render"
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.basename(self._json_path).replace(".json", "")
        video_path = os.path.join(out_dir, base + ".mp4")

        self._rendering = True
        self._render_btn.configure(text=LANGS[self._lang]["rendering_btn"], state="disabled",
                                   fg_color="#444455")
        self._progress_bar.pack(fill="x", pady=(6, 0))
        self._progress_bar.set(0)
        self._set_status_key("status_preparing", TEXT_DIM)
        self._output_label.configure(text="")

        t_start = time.time()

        def on_progress(pct):
            self.after(0, lambda: self._progress_bar.set(pct / 100))
            elapsed = time.time() - t_start
            if pct > 0:
                eta = elapsed / pct * (100 - pct)
                self.after(0, lambda p=pct, e=eta: self._set_status(
                    LANGS[self._lang]["status_rendering_eta"].format(p=p, e=int(e)), TEXT_DIM
                ))
            else:
                self.after(0, lambda p=pct: self._set_status(
                    LANGS[self._lang]["status_rendering"].format(p=p), TEXT_DIM
                ))

        def on_status(msg):
            self.after(0, lambda: self._set_status(msg, TEXT_DIM))

        def worker():
            result = render_json_to_video(
                self._json_path, video_path,
                osu_path=self._osu_path,
                progress_cb=on_progress,
                status_cb=on_status
            )
            def done():
                self._rendering = False
                self._render_btn.configure(
                    text=LANGS[self._lang]["render_btn"], state="normal", fg_color=PINK
                )
                if result is False:
                    self._set_status_key("status_render_err", "#ff4466")
                else:
                    elapsed = result
                    self._set_status_key("status_done", GREEN, t=elapsed)
                    self._progress_bar.set(1)
                    abs_path = os.path.abspath(video_path)
                    self._output_label.configure(
                        text=f"📁 {abs_path}", text_color=TEXT_DIM
                    )
            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    # -------------------------------------------------------------------------
    def _toggle_lang(self):
        self._lang = "en" if self._lang == "ru" else "ru"
        self._apply_lang()

    def _apply_lang(self):
        s = LANGS[self._lang]
        flag = "🌐 RU" if self._lang == "ru" else "🌐 EN"
        self._lang_btn.configure(text=flag)
        self._subtitle_label.configure(text=s["subtitle"])
        # Лейблы строк
        self._lbl_replay_label.configure(text=s["replay_label"])
        self._lbl_json_label.configure(text=s["json_label"])
        # Кнопки выбора файлов
        self._odr_choose_btn.configure(text=s["choose_file"])
        self._json_choose_btn.configure(text=s["choose_file"])
        self._osu_choose_btn.configure(text=s["choose_file"])
        self._fetch_json_btn.configure(text=s["fetch_json"])
        self._lbl_osu_label.configure(text=s["osu_label"])
        if not self._osu_path:
            self._osu_label.configure(text=s["not_selected"])
        # Кнопка render (только если не рендерится)
        if not self._rendering:
            self._render_btn.configure(text=s["render_btn"])
        # Статус — только если файл не выбран
        if not self._json_path and not self._odr_path:
            self._set_status_key("status_idle", TEXT_DIM)
        # not_selected для незаполненных полей
        if not self._odr_path:
            self._odr_label.configure(text=s["not_selected"])
        if not self._json_path:
            self._json_label.configure(text=s["not_selected"])
        # Перерисовать статус на новом языке
        self._redraw_status()

    # -------------------------------------------------------------------------
    def _set_status_key(self, key, color=None, **params):
        """Устанавливает статус по ключу перевода — перерисовывается при смене языка."""
        self._status_key    = key
        self._status_params = params
        self._status_color  = color or TEXT_DIM
        self._redraw_status()

    def _set_status(self, text, color=None):
        """Произвольный текст (прогресс и т.д.) — не переводится."""
        self._status_key   = None
        self._status_color = color or TEXT_DIM
        self._status_label.configure(text=text, text_color=self._status_color)

    def _redraw_status(self):
        if self._status_key is None:
            return
        s = LANGS[self._lang]
        if self._status_key not in s:
            return
        text = s[self._status_key]
        if self._status_params:
            try:
                text = text.format(**self._status_params)
            except KeyError:
                pass
        self._status_label.configure(text=text, text_color=self._status_color)


# =============================================================================
if __name__ == "__main__":
    app = App()
    app.mainloop()
