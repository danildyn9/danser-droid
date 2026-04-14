"""
Обработка данных курсора для danser!droid.
"""

from collections import deque

import cv2
import numpy as np


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


def init_group_state(cursor_data):
    group_state = []
    for group in cursor_data:
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
    return group_state


def init_cursor_states(group_state, trail_maxlen=22):
    return [{"trail": deque(maxlen=trail_maxlen), "prev_pos": None} for _ in group_state]


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