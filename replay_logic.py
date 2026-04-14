"""
Логика danser!droid: разбор replay и рендер видео.
"""

import os
import time
import zipfile

import cv2
import numpy as np

from cursor_processing import (
    get_cursor_time_bounds,
    init_cursor_states,
    init_group_state,
    render_cursor_layer,
    update_group_state,
)
from map_processing import prepare_map_data, render_hit_objects


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


def render_cursor_data_to_video(cursor_data, video_path, osu_path=None, progress_cb=None, status_cb=None):
    if status_cb:
        status_cb("Загрузка данных...")

    data = cursor_data

    if not data or all(len(g) == 0 for g in data):
        if status_cb:
            status_cb("Ошибка: нет данных курсора")
        return False

    hit_objects = []
    preempt = 600.0
    fade_in_time = 400.0
    circle_r = 30.0

    if osu_path and os.path.exists(osu_path):
        map_data = prepare_map_data(osu_path)
        hit_objects = map_data["hit_objects"]
        preempt = map_data["preempt"]
        fade_in_time = map_data["fade_in_time"]
        circle_r = map_data["circle_r"]

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

    group_state = init_group_state(data)
    cursor_states = init_cursor_states(group_state)

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
