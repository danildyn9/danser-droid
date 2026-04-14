"""
Логика danser!droid: разбор replay и рендер видео.
"""

import os
import math
import time
import zipfile
from collections import deque

import cv2
import numpy as np


# Пытаемся импортировать библиотеку для разбора реплеев
try:
    import osudroid_api_wrapper as od

    ODR_AVAILABLE = True
except ImportError:
    ODR_AVAILABLE = False


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


def _is_valid_occurrence(occ):
    if not occ:
        return False
    pos = occ.get("position")
    if not pos:
        return False
    return occ.get("time") is not None and pos.get("x") is not None and pos.get("y") is not None


def _advance_cursor_keep(gs, cursor):
    gs["stroke_idx"], gs["phase"], gs["move_idx"] = cursor


def _peek_next_occurrence(group, gs):
    s_idx = gs["stroke_idx"]
    phase = gs["phase"]
    move_idx = gs["move_idx"]

    while s_idx < len(group):
        stroke = group[s_idx]
        down = stroke.get("down")
        moves = stroke.get("moves") or []
        up = stroke.get("up")

        if phase == "down":
            keep = (s_idx, "down", move_idx)
            if _is_valid_occurrence(down):
                consume = (s_idx, "move", 0)
                return down, "down", keep, consume
            phase = "move"
            move_idx = 0
            continue

        if phase == "move":
            if move_idx < len(moves):
                move_occ = moves[move_idx]
                keep = (s_idx, "move", move_idx)
                if _is_valid_occurrence(move_occ):
                    consume = (s_idx, "move", move_idx + 1)
                    return move_occ, "move", keep, consume
                move_idx += 1
                continue
            phase = "up"
            continue

        keep = (s_idx, "up", move_idx)
        if _is_valid_occurrence(up):
            consume = (s_idx + 1, "down", 0)
            return up, "up", keep, consume

        s_idx += 1
        phase = "down"
        move_idx = 0

    return None, None, (s_idx, "down", 0), (s_idx, "down", 0)


def get_cursor_time_bounds(cursor_data):
    t_min = None
    t_max = None

    for group in cursor_data:
        for stroke in group:
            down = stroke.get("down")
            moves = stroke.get("moves") or []
            up = stroke.get("up")
            for occ in [down, *moves, up]:
                if not _is_valid_occurrence(occ):
                    continue
                t = occ["time"]
                if t_min is None or t < t_min:
                    t_min = t
                if t_max is None or t > t_max:
                    t_max = t

    return t_min, t_max


def render_hit_objects(frame, t, hit_objects, preempt, fade_in_time, scr_r, to_screen):
    combo_palette = [
        (255, 191, 128),
        (128, 220, 255),
        (100, 255, 180),
        (180, 130, 255),
    ]

    def combo_color(idx):
        return combo_palette[idx % len(combo_palette)]

    visible = []
    for obj in hit_objects:
        t_appear = obj["time"] - preempt
        if obj["type"] == "spinner":
            t_gone = obj["time"] + obj.get("duration", 1000) + 200
        elif obj["type"] == "slider":
            t_gone = obj["time"] + obj.get("duration", 500) + 200
        else:
            t_gone = obj["time"] + 200
        if t_appear <= t <= t_gone:
            visible.append(obj)

    for obj in sorted(visible, key=lambda o: -o["time"]):
        cx, cy = to_screen(obj["x"], obj["y"])
        col = combo_color(obj.get("combo_idx", 0))
        t_fade_end = obj["time"] - preempt + fade_in_time
        if t < t_fade_end:
            vis_alpha = max(0.0, (t - (obj["time"] - preempt)) / fade_in_time)
        else:
            vis_alpha = 1.0

        if obj["type"] == "circle":
            cv2.circle(frame, (cx, cy), scr_r, (20, 20, 30), -1, cv2.LINE_AA)
            thick_ring = max(2, scr_r // 8)
            cv2.circle(frame, (cx, cy), scr_r, col, thick_ring, cv2.LINE_AA)
            cv2.circle(frame, (cx, cy), scr_r - thick_ring - 1, (200, 200, 200), 1, cv2.LINE_AA)

            if t < obj["time"]:
                approach_t = 1.0 - (obj["time"] - t) / preempt
                ap_r = int(scr_r * (3.0 - 2.0 * approach_t))
                if ap_r > scr_r:
                    ap_alpha = vis_alpha * 0.9
                    ov = frame.copy()
                    cv2.circle(ov, (cx, cy), ap_r, col, max(1, scr_r // 10), cv2.LINE_AA)
                    cv2.addWeighted(ov, ap_alpha, frame, 1 - ap_alpha, 0, frame)

            num_str = str(obj.get("combo_num", ""))
            font_scale = max(0.4, scr_r / 28.0)
            (tw, th), _ = cv2.getTextSize(num_str, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 2)
            cv2.putText(
                frame,
                num_str,
                (cx - tw // 2, cy + th // 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                font_scale,
                (220, 220, 220),
                2,
                cv2.LINE_AA,
            )

        elif obj["type"] == "slider":
            path = obj.get("path", [])
            if len(path) >= 2:
                pts_scr = [to_screen(p[0], p[1]) for p in path]
                body_col = tuple(max(0, c - 80) for c in col)
                for i in range(1, len(pts_scr)):
                    cv2.line(frame, pts_scr[i - 1], pts_scr[i], body_col, scr_r * 2, cv2.LINE_AA)
                for i in range(1, len(pts_scr)):
                    cv2.line(frame, pts_scr[i - 1], pts_scr[i], (200, 200, 200), max(1, scr_r * 2 - 6), cv2.LINE_AA)
                cv2.circle(frame, pts_scr[0], scr_r, col, max(2, scr_r // 8), cv2.LINE_AA)
                cv2.circle(frame, pts_scr[-1], int(scr_r * 0.8), (160, 160, 160), 2, cv2.LINE_AA)

                if t < obj["time"]:
                    approach_t = 1.0 - (obj["time"] - t) / preempt
                    ap_r = int(scr_r * (3.0 - 2.0 * approach_t))
                    if ap_r > scr_r:
                        ov = frame.copy()
                        cv2.circle(ov, pts_scr[0], ap_r, col, max(1, scr_r // 10), cv2.LINE_AA)
                        cv2.addWeighted(ov, vis_alpha * 0.9, frame, 1 - vis_alpha * 0.9, 0, frame)

                if obj["time"] <= t <= obj["time"] + obj.get("duration", 500):
                    prog = (t - obj["time"]) / obj["duration"]
                    slides = obj.get("slides", 1)
                    local_prog = prog * slides % 1.0
                    if int(prog * slides) % 2 == 1:
                        local_prog = 1.0 - local_prog
                    pidx = min(int(local_prog * (len(path) - 1)), len(path) - 1)
                    bx, by = to_screen(path[pidx][0], path[pidx][1])
                    cv2.circle(frame, (bx, by), int(scr_r * 0.7), (240, 240, 240), -1, cv2.LINE_AA)
                    cv2.circle(frame, (bx, by), int(scr_r * 0.7), col, 2, cv2.LINE_AA)

        elif obj["type"] == "spinner":
            cx2, cy2 = to_screen(256, 192)
            spin_r = int(scr_r * 5)
            prog = max(0.0, (t - obj["time"]) / max(1, obj.get("duration", 1000)))
            angle = prog * math.pi * 20
            for ri, alpha_val in [(spin_r, 0.6), (spin_r // 2, 0.4)]:
                ov = frame.copy()
                cv2.ellipse(ov, (cx2, cy2), (ri, ri), 0, 0, 360, col, 2, cv2.LINE_AA)
                mx = int(cx2 + ri * math.cos(angle))
                my = int(cy2 + ri * math.sin(angle))
                cv2.circle(ov, (mx, my), 8, (255, 255, 255), -1)
                cv2.addWeighted(ov, alpha_val, frame, 1 - alpha_val, 0, frame)


def update_group_state(group_state, t):
    for gs in group_state:
        while True:
            occ, kind, keep_cursor, consume_cursor = _peek_next_occurrence(gs["group"], gs)
            _advance_cursor_keep(gs, keep_cursor)
            if not occ or occ["time"] > t:
                break
            gs["x"] = occ["position"]["x"]
            gs["y"] = occ["position"]["y"]
            if kind == "down":
                gs["active"] = True
            elif kind == "up":
                gs["active"] = False
            _advance_cursor_keep(gs, consume_cursor)


def render_cursor_layer(frame, group_state, to_screen, cursor_states):
    for g_idx, gs in enumerate(group_state):
        state = cursor_states[g_idx]
        trail = state["trail"]
        prev_pos = state["prev_pos"]

        if gs["x"] is not None and gs["active"]:
            sx, sy = to_screen(gs["x"], gs["y"])
            if prev_pos and np.linalg.norm(np.array([sx, sy]) - np.array(prev_pos)) > 300:
                trail.clear()
            trail.append((sx, sy))
            state["prev_pos"] = (sx, sy)

            pts = list(trail)
            for i in range(1, len(pts)):
                alpha = i / len(pts)
                thick = max(1, int(16 * alpha) + 2)
                c = (int(255 * alpha), int(180 * alpha), int(50 * alpha))
                cv2.line(frame, pts[i - 1], pts[i], c, thick, cv2.LINE_AA)

            cv2.circle(frame, (sx, sy), 14, (255, 255, 255), -1, cv2.LINE_AA)
            cv2.circle(frame, (sx, sy), 18, (255, 255, 255), 2, cv2.LINE_AA)
        elif not gs["active"]:
            trail.clear()
            state["prev_pos"] = None


def render_cursor_data_to_video(cursor_data, video_path, osu_path=None, progress_cb=None, status_cb=None):
    if status_cb:
        status_cb("Загрузка данных...")

    data = cursor_data

    if not data or all(len(g) == 0 for g in data):
        if status_cb:
            status_cb("Ошибка: нет данных курсора")
        return False

    osu_data = None
    hit_objects = []
    preempt = 600.0
    fade_in_time = 400.0
    circle_r = 30.0

    if osu_path and os.path.exists(osu_path):
        from parse_osu import (
            build_slider_path,
            calc_circle_radius,
            calc_fade_in,
            calc_preempt,
            calc_slider_duration,
            parse_osu,
        )

        osu_data = parse_osu(osu_path)
        diff = osu_data["difficulty"]
        circle_r = calc_circle_radius(diff.get("cs", 5))
        preempt = calc_preempt(diff.get("ar", 5))
        fade_in_time = calc_fade_in(diff.get("ar", 5))

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

        sm = diff.get("slider_multiplier", 1.4)
        for obj in osu_data["hit_objects"]:
            if obj["type"] == "slider":
                obj["path"] = build_slider_path(obj["control_points"], obj["curve_type"], obj["length"])
                obj["duration"] = calc_slider_duration(obj, osu_data["timing_points"], sm)
            elif obj["type"] == "spinner":
                obj["duration"] = obj["end_time"] - obj["time"]

        hit_objects = osu_data["hit_objects"]

    width, height = 1920, 1080
    fps = 60
    frame_ms = 1000.0 / fps
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    os.makedirs(os.path.dirname(video_path) or ".", exist_ok=True)
    out = cv2.VideoWriter(video_path, fourcc, fps, (width, height))

    scale = 2.1
    offset_x = (width - 512 * scale) / 2.0
    offset_y = (height - 384 * scale) / 2.0

    def to_screen(x, y):
        return (int(x * scale + offset_x), int(y * scale + offset_y))

    cursor_start, cursor_end = get_cursor_time_bounds(data)
    if cursor_start is None or cursor_end is None:
        return False

    if hit_objects:
        map_start = hit_objects[0]["time"] - preempt - 100
        start_ms = min(cursor_start, map_start)
    else:
        start_ms = cursor_start
    end_ms = cursor_end
    total_frames = int((end_ms - start_ms) / frame_ms) + 2

    group_state = []
    for group in data:
        group_state.append(
            {
                "group": group,
                "stroke_idx": 0,
                "phase": "down",
                "move_idx": 0,
                "active": False,
                "x": None,
                "y": None,
            }
        )

    cursor_states = [{"trail": deque(maxlen=22), "prev_pos": None} for _ in group_state]

    scr_r = int(circle_r * scale)

    if status_cb:
        status_cb("Рендеринг...")
    t_wall = time.time()

    for frame_idx in range(total_frames):
        t = start_ms + frame_idx * frame_ms
        frame = np.zeros((height, width, 3), dtype=np.uint8)

        pf_tl = to_screen(0, 0)
        pf_br = to_screen(512, 384)
        cv2.rectangle(frame, pf_tl, pf_br, (30, 30, 40), 1)

        if hit_objects:
            render_hit_objects(frame, t, hit_objects, preempt, fade_in_time, scr_r, to_screen)

        update_group_state(group_state, t)
        render_cursor_layer(frame, group_state, to_screen, cursor_states)

        out.write(frame)

        if frame_idx % 60 == 0 and progress_cb:
            pct = int(frame_idx / total_frames * 100)
            progress_cb(pct)

    out.release()
    elapsed = time.time() - t_wall
    if progress_cb:
        progress_cb(100)
    return elapsed


def render_replay_to_video(replay, video_path, osu_path=None, progress_cb=None, status_cb=None):
    return render_cursor_data_to_video(
        replay.cursor_data,
        video_path,
        osu_path=osu_path,
        progress_cb=progress_cb,
        status_cb=status_cb,
    )
