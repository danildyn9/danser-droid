"""
Обработка карты (.osu) для danser!droid.
"""

import math

import cv2

from parse_osu import (
    build_slider_path,
    calc_circle_radius,
    calc_fade_in,
    calc_preempt,
    calc_slider_duration,
    parse_osu,
)


def prepare_map_data(osu_path):
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

    return {
        "hit_objects": osu_data["hit_objects"],
        "preempt": preempt,
        "fade_in_time": fade_in_time,
        "circle_r": circle_r,
    }


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