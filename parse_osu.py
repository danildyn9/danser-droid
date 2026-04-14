"""
parse_osu.py — парсер .osu файлов для danser!droid
Поддерживает: хит-кружки, линейные слайдеры (L|), кривые Безье (B|), спиннеры
"""


def parse_osu(filepath):
    """
    Читает .osu файл и возвращает dict:
    {
        "difficulty": { cs, ar, od, hp, slider_multiplier, slider_tick_rate },
        "timing_points": [ { time, beat_length, inherited } ],
        "hit_objects": [ { type, x, y, time, ... } ]
    }
    """
    with open(filepath, "r", encoding="utf-8-sig") as f:
        lines = [l.rstrip("\r\n") for l in f.readlines()]

    section = None
    difficulty = {}
    timing_points = []
    hit_objects = []

    for line in lines:
        # Определяем секцию
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue

        if not line or line.startswith("//"):
            continue

        # --- Difficulty ---
        if section == "Difficulty":
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip()
                val = val.strip()
                if key == "CircleSize":
                    difficulty["cs"] = float(val)
                elif key == "ApproachRate":
                    difficulty["ar"] = float(val)
                elif key == "OverallDifficulty":
                    difficulty["od"] = float(val)
                elif key == "HPDrainRate":
                    difficulty["hp"] = float(val)
                elif key == "SliderMultiplier":
                    difficulty["slider_multiplier"] = float(val)
                elif key == "SliderTickRate":
                    difficulty["slider_tick_rate"] = float(val)

        # --- TimingPoints ---
        elif section == "TimingPoints":
            parts = line.split(",")
            if len(parts) < 2:
                continue
            tp_time = float(parts[0])
            beat_len = float(parts[1])
            inherited = len(parts) < 7 or parts[6].strip() == "1"
            timing_points.append({
                "time": tp_time,
                "beat_length": beat_len,
                "inherited": inherited,  # True = базовая, False = унаследованная (velocity)
            })

        # --- HitObjects ---
        elif section == "HitObjects":
            parts = line.split(",")
            if len(parts) < 5:
                continue

            x    = int(parts[0])
            y    = int(parts[1])
            time = int(parts[2])
            raw_type = int(parts[3])

            # Тип объекта из битовой маски
            is_circle  = bool(raw_type & 1)
            is_slider  = bool(raw_type & 2)
            is_spinner = bool(raw_type & 8)
            new_combo  = bool(raw_type & 4)

            if is_circle:
                hit_objects.append({
                    "type": "circle",
                    "x": x, "y": y,
                    "time": time,
                    "new_combo": new_combo,
                })

            elif is_slider:
                # Формат: x,y,time,type,hitSound,curveType|cx:cy|...,slides,length,...
                if len(parts) < 8:
                    continue
                curve_raw = parts[5]
                slides    = int(parts[6])
                length    = float(parts[7])

                curve_type = curve_raw[0]  # L, B, P, C
                # Контрольные точки включая стартовую
                ctrl_raw = curve_raw[2:].split("|")
                control_points = [(x, y)]  # старт = позиция объекта
                for cp in ctrl_raw:
                    cx, cy = cp.split(":")
                    control_points.append((int(cx), int(cy)))

                hit_objects.append({
                    "type": "slider",
                    "x": x, "y": y,
                    "time": time,
                    "new_combo": new_combo,
                    "curve_type": curve_type,
                    "control_points": control_points,
                    "slides": slides,
                    "length": length,
                })

            elif is_spinner:
                end_time = int(parts[5]) if len(parts) > 5 else time + 1000
                hit_objects.append({
                    "type": "spinner",
                    "x": 256, "y": 192,  # всегда по центру
                    "time": time,
                    "end_time": end_time,
                    "new_combo": new_combo,
                })

    return {
        "difficulty": difficulty,
        "timing_points": timing_points,
        "hit_objects": hit_objects,
    }


def calc_circle_radius(cs):
    """Радиус хит-кружка в osu!-координатах (поле 512x384)."""
    return 54.4 - 4.48 * cs


def calc_preempt(ar):
    """
    Время появления approach circle до момента удара (мс).
    AR=10 → 450ms, AR=5 → 1200ms, AR=0 → 1800ms
    """
    if ar < 5:
        return 1800 - 120 * ar
    elif ar == 5:
        return 1200
    else:
        return 1200 - 150 * (ar - 5)


def calc_fade_in(ar):
    """Время фейда появления объекта (мс)."""
    if ar < 5:
        return 800 - 64 * ar
    elif ar == 5:
        return 800
    else:
        return 800 - 100 * (ar - 5)


def get_beat_length_at(timing_points, time):
    """Возвращает длину бита (мс) для заданного момента времени."""
    base_beat = 500.0
    for tp in timing_points:
        if tp["time"] > time:
            break
        if tp["inherited"]:
            base_beat = tp["beat_length"]
    return base_beat


def get_slider_velocity_at(timing_points, time, slider_multiplier):
    """Возвращает скорость слайдера (osu пикс/мс) для заданного момента."""
    base_beat = 500.0
    velocity_mult = 1.0
    for tp in timing_points:
        if tp["time"] > time:
            break
        if tp["inherited"]:
            base_beat = tp["beat_length"]
        else:
            # Унаследованная точка: beat_length = -100 / velocity%
            velocity_mult = -100.0 / tp["beat_length"]
    # osu пикс/мс
    return (slider_multiplier * 100.0 * velocity_mult) / base_beat


def calc_slider_duration(hit_obj, timing_points, slider_multiplier):
    """Длительность слайдера в мс."""
    sv = get_slider_velocity_at(timing_points, hit_obj["time"], slider_multiplier)
    if sv <= 0:
        sv = slider_multiplier * 100.0 / 500.0
    duration_one = hit_obj["length"] / sv
    return duration_one * hit_obj["slides"]


def build_slider_path(control_points, curve_type, length, steps=50):
    """
    Строит список точек пути слайдера.
    Поддерживает: L (linear), B (bezier), P (perfect circle), C (catmull).
    Возвращает список (x, y) длиной steps+1.
    """
    if curve_type == "L":
        return _linear_path(control_points, length, steps)
    elif curve_type == "B":
        return _bezier_path(control_points, length, steps)
    elif curve_type == "P":
        return _perfect_arc_path(control_points, length, steps)
    else:
        # Fallback — линейный
        return _linear_path(control_points, length, steps)


def _linear_path(pts, length, steps):
    """Линейный путь по контрольным точкам."""
    if len(pts) < 2:
        return [pts[0]] * (steps + 1)

    # Суммарная длина сегментов
    seg_lengths = []
    for i in range(len(pts) - 1):
        dx = pts[i+1][0] - pts[i][0]
        dy = pts[i+1][1] - pts[i][1]
        seg_lengths.append((dx**2 + dy**2) ** 0.5)
    total = sum(seg_lengths)
    if total == 0:
        return [pts[0]] * (steps + 1)

    dist_limit = min(length, total)
    result = []
    for s in range(steps + 1):
        t_dist = dist_limit * s / steps
        remaining = t_dist
        for i, seg_len in enumerate(seg_lengths):
            if remaining <= seg_len or i == len(seg_lengths) - 1:
                if seg_len == 0:
                    result.append(pts[i])
                else:
                    frac = remaining / seg_len
                    x = pts[i][0] + frac * (pts[i+1][0] - pts[i][0])
                    y = pts[i][1] + frac * (pts[i+1][1] - pts[i][1])
                    result.append((x, y))
                break
            remaining -= seg_len
    return result


def _bezier_point(pts, t):
    """Точка на кривой Безье при параметре t ∈ [0,1]."""
    p = list(pts)
    while len(p) > 1:
        p = [
            (p[i][0] * (1 - t) + p[i+1][0] * t,
             p[i][1] * (1 - t) + p[i+1][1] * t)
            for i in range(len(p) - 1)
        ]
    return p[0]


def _bezier_path(ctrl_pts, length, steps):
    """
    Путь Безье. osu! разбивает сегменты по дублированным точкам.
    """
    # Разбиваем на сегменты по дублированным точкам
    segments = []
    seg = [ctrl_pts[0]]
    for i in range(1, len(ctrl_pts)):
        seg.append(ctrl_pts[i])
        if i < len(ctrl_pts) - 1 and ctrl_pts[i] == ctrl_pts[i+1]:
            segments.append(seg)
            seg = [ctrl_pts[i]]
    segments.append(seg)

    # Сэмплируем каждый сегмент
    raw = []
    for seg in segments:
        seg_steps = max(steps // len(segments), 10)
        for s in range(seg_steps + 1):
            t = s / seg_steps
            raw.append(_bezier_point(seg, t))

    # Обрезаем по length
    return _trim_path(raw, length, steps)


def _perfect_arc_path(pts, length, steps):
    """Дуга через 3 точки (Perfect circle curve)."""
    if len(pts) < 3:
        return _linear_path(pts, length, steps)
    ax, ay = pts[0]
    bx, by = pts[1]
    cx, cy = pts[2]

    # Центр описанной окружности через 3 точки
    D = 2 * (ax*(by - cy) + bx*(cy - ay) + cx*(ay - by))
    if abs(D) < 1e-7:
        return _linear_path(pts, length, steps)
    ux = ((ax**2+ay**2)*(by-cy) + (bx**2+by**2)*(cy-ay) + (cx**2+cy**2)*(ay-by)) / D
    uy = ((ax**2+ay**2)*(cx-bx) + (bx**2+by**2)*(ax-cx) + (cx**2+cy**2)*(bx-ax)) / D
    r = ((ax-ux)**2 + (ay-uy)**2) ** 0.5

    import math
    a_start = math.atan2(ay - uy, ax - ux)
    a_end   = math.atan2(cy - uy, cx - ux)
    arc_len = abs(r * (a_end - a_start))
    if arc_len > length:
        a_end = a_start + (length / r) * (1 if a_end > a_start else -1)

    result = []
    for s in range(steps + 1):
        angle = a_start + (a_end - a_start) * s / steps
        result.append((ux + r * math.cos(angle), uy + r * math.sin(angle)))
    return result


def _trim_path(raw, length, steps):
    """Обрезает путь до нужной длины и ресэмплирует."""
    # Кумулятивные длины
    dists = [0.0]
    for i in range(1, len(raw)):
        dx = raw[i][0] - raw[i-1][0]
        dy = raw[i][1] - raw[i-1][1]
        dists.append(dists[-1] + (dx**2+dy**2)**0.5)
    total = dists[-1]
    dist_limit = min(length, total)
    if dist_limit == 0:
        return [raw[0]] * (steps + 1)

    result = []
    for s in range(steps + 1):
        target = dist_limit * s / steps
        # Бинарный поиск
        lo, hi = 0, len(dists) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if dists[mid] < target:
                lo = mid
            else:
                hi = mid
        seg_len = dists[hi] - dists[lo]
        if seg_len == 0:
            result.append(raw[lo])
        else:
            frac = (target - dists[lo]) / seg_len
            x = raw[lo][0] + frac * (raw[hi][0] - raw[lo][0])
            y = raw[lo][1] + frac * (raw[hi][1] - raw[lo][1])
            result.append((x, y))
    return result


if __name__ == "__main__":
    import sys, json
    if len(sys.argv) < 2:
        print("Usage: python parse_osu.py map.osu")
        sys.exit(1)
    data = parse_osu(sys.argv[1])
    print(f"Difficulty: {data['difficulty']}")
    print(f"TimingPoints: {len(data['timing_points'])}")
    print(f"HitObjects: {len(data['hit_objects'])}")
    types = {}
    for obj in data['hit_objects']:
        types[obj['type']] = types.get(obj['type'], 0) + 1
    print(f"Types: {types}")
    print("\nFirst 3 objects:")
    for obj in data['hit_objects'][:3]:
        print(" ", obj)
