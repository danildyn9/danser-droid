"""
GUI для danser!droid.
"""

import os
import sys
import threading
import time

import customtkinter as ctk
from PIL import Image
from tkinter import filedialog, messagebox

from replay_logic import ODR_AVAILABLE, unpack_replay, render_replay_to_video


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

PINK = "#ff66aa"
PINK_DARK = "#cc4488"
BG = "#0d0d14"
CARD = "#14141f"
CARD2 = "#1a1a2e"
TEXT = "#ffffff"
TEXT_DIM = "#888899"
GREEN = "#44ffaa"
FONT_TITLE = ("Trebuchet MS", 22, "bold")
FONT_LABEL = ("Trebuchet MS", 13)
FONT_SMALL = ("Trebuchet MS", 11)
FONT_BTN = ("Trebuchet MS", 13, "bold")
FONT_STATUS = ("Trebuchet MS", 12)

LANGS = {
    "ru": {
        "replay_label": "Реплей (.odr)",
        "choose_file": "Выбрать файл",
        "render_btn": "Render!",
        "rendering_btn": "Rendering...",
        "status_idle": "Выберите файл реплея",
        "status_replay_ready": "Реплей выбран — нажми Render!",
        "status_extracting": "Читаю данные реплея...",
        "status_preparing": "Подготовка...",
        "status_done": "Готово за {t:.1f}секунд ✓",
        "status_error": "Ошибка: {e}",
        "status_render_err": "Ошибка рендера",
        "status_rendering": "Rendering... {p}%",
        "status_rendering_eta": "Rendering... {p}%  (осталось ~{e}секунд)",
        "no_file_title": "Нет файла",
        "no_odr_msg": "Сначала выбери .odr файл реплея",
        "no_lib_title": "Нет библиотеки",
        "no_lib_msg": "Установи osudroid-api-wrapper:\npip install osudroid-api-wrapper",
        "not_selected": "не выбран",
        "subtitle": "osu!droid replay renderer",
        "osu_label": ".osu карта",
    },
    "en": {
        "replay_label": "Replay (.odr)",
        "choose_file": "Choose file",
        "render_btn": "Render!",
        "rendering_btn": "Rendering...",
        "status_idle": "Choose a replay file",
        "status_replay_ready": "Replay selected — press Render!",
        "status_extracting": "Loading replay data...",
        "status_preparing": "Preparing...",
        "status_done": "Done in {t:.1f}seconds ✓",
        "status_error": "Error: {e}",
        "status_render_err": "Render error",
        "status_rendering": "Rendering... {p}%",
        "status_rendering_eta": "Rendering... {p}%  (~{e}seconds left)",
        "no_file_title": "No file",
        "no_odr_msg": "Please choose a .odr replay file first",
        "no_lib_title": "Library missing",
        "no_lib_msg": "Install osudroid-api-wrapper:\npip install osudroid-api-wrapper",
        "not_selected": "not selected",
        "subtitle": "osu!droid replay renderer",
        "osu_label": ".osu map",
    },
}


def get_assets_dir():
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "assets")
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "assets")


ASSETS_DIR = get_assets_dir()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("danser!droid")
        self.geometry("520x650")
        self.resizable(False, False)
        self.configure(fg_color=BG)

        self._odr_path = None
        self._osu_path = None
        self._rendering = False
        self._lang = "ru"
        self._status_key = "status_idle"
        self._status_params = {}
        self._status_color = TEXT_DIM

        self._build_ui()

    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(pady=(28, 0))

        logo_path = os.path.join(ASSETS_DIR, "logo.png")
        if os.path.exists(logo_path):
            logo_img = ctk.CTkImage(
                light_image=Image.open(logo_path), dark_image=Image.open(logo_path), size=(72, 72)
            )
            ctk.CTkLabel(top, image=logo_img, text="", fg_color="transparent", bg_color="transparent").pack()
        else:
            logo_canvas = ctk.CTkCanvas(top, width=72, height=72, bg=BG, highlightthickness=0)
            logo_canvas.pack()
            logo_canvas.create_oval(4, 4, 68, 68, outline=PINK, width=2)
            logo_canvas.create_oval(14, 14, 58, 58, fill=PINK_DARK, outline="")
            logo_canvas.create_text(36, 36, text="d!d", fill=TEXT, font=("Trebuchet MS", 14, "bold"))

        ctk.CTkLabel(top, text="danser!droid", font=FONT_TITLE, text_color=PINK).pack(pady=(6, 0))
        self._subtitle_label = ctk.CTkLabel(top, text="osu!droid replay renderer", font=FONT_SMALL, text_color=TEXT_DIM)
        self._subtitle_label.pack()

        ctk.CTkFrame(self, height=1, fg_color=CARD2).pack(fill="x", padx=30, pady=18)

        card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=14)
        card.pack(padx=30, fill="x")

        self._build_file_row(
            card,
            label_key="replay_label",
            btn_cmd=self._pick_odr,
            attr_name="_odr_label",
            btn_attr="_odr_choose_btn",
        )

        ctk.CTkFrame(card, height=1, fg_color=CARD2).pack(fill="x", padx=16, pady=4)

        self._build_file_row(
            card,
            label_key="osu_label",
            btn_cmd=self._pick_osu,
            attr_name="_osu_label",
            btn_attr="_osu_choose_btn",
        )

        ctk.CTkFrame(self, height=1, fg_color=CARD2).pack(fill="x", padx=30, pady=18)

        status_area = ctk.CTkFrame(self, fg_color="transparent")
        status_area.pack(padx=30, fill="x")

        self._status_label = ctk.CTkLabel(
            status_area,
            text="Выберите файл реплея",
            font=FONT_STATUS,
            text_color=TEXT_DIM,
            anchor="w",
        )
        self._status_label.pack(fill="x")

        self._progress_bar = ctk.CTkProgressBar(status_area, fg_color=CARD2, progress_color=PINK, height=6, corner_radius=3)
        self._progress_bar.set(0)
        self._progress_bar.pack(fill="x", pady=(6, 0))
        self._progress_bar.pack_forget()

        self._render_btn = ctk.CTkButton(
            self,
            text="Render!",
            font=FONT_BTN,
            fg_color=PINK,
            hover_color=PINK_DARK,
            text_color="#ffffff",
            corner_radius=10,
            height=46,
            command=self._start_render,
        )
        self._render_btn.pack(padx=30, pady=(18, 10), fill="x")

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(padx=30, pady=(0, 16), fill="x")

        self._output_label = ctk.CTkLabel(bottom, text="", font=FONT_SMALL, text_color=TEXT_DIM, anchor="w")
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
            command=self._toggle_lang,
        )
        self._lang_btn.pack(side="right")

    def _build_file_row(self, parent, label_key, btn_cmd, attr_name, btn_attr):
        s = LANGS[self._lang]
        row_frame = ctk.CTkFrame(parent, fg_color="transparent")
        row_frame.pack(fill="x", padx=16, pady=10)

        row_label = ctk.CTkLabel(row_frame, text=s[label_key], font=FONT_LABEL, text_color=TEXT, width=100, anchor="w")
        row_label.pack(side="left")
        setattr(self, "_lbl_" + label_key, row_label)

        right = ctk.CTkFrame(row_frame, fg_color="transparent")
        right.pack(side="right", fill="x", expand=True)

        file_label = ctk.CTkLabel(right, text=s["not_selected"], font=FONT_SMALL, text_color=TEXT_DIM, anchor="w")
        file_label.pack(fill="x", pady=(0, 4))
        setattr(self, attr_name, file_label)

        btn_row = ctk.CTkFrame(right, fg_color="transparent")
        btn_row.pack(fill="x")

        choose_btn = ctk.CTkButton(
            btn_row,
            text=s["choose_file"],
            font=FONT_SMALL,
            fg_color=CARD2,
            hover_color="#2a2a44",
            text_color=TEXT,
            corner_radius=8,
            height=32,
            command=btn_cmd,
        )
        choose_btn.pack(side="left")
        setattr(self, btn_attr, choose_btn)

    def _pick_odr(self):
        path = filedialog.askopenfilename(
            title="Выбери файл реплея", filetypes=[("osu!droid replay", "*.odr"), ("All files", "*.*")]
        )
        if path:
            self._odr_path = path
            self._odr_label.configure(text=os.path.basename(path), text_color=TEXT)
            self._set_status_key("status_replay_ready", GREEN)

    def _pick_osu(self):
        path = filedialog.askopenfilename(
            title="Выбери .osu файл карты", filetypes=[("osu! beatmap", "*.osu"), ("All files", "*.*")]
        )
        if path:
            self._osu_path = path
            self._osu_label.configure(text=os.path.basename(path), text_color=TEXT)

    def _start_render(self):
        if self._rendering:
            return
        if not self._odr_path:
            messagebox.showwarning(LANGS[self._lang]["no_file_title"], LANGS[self._lang]["no_odr_msg"])
            return
        if not ODR_AVAILABLE:
            messagebox.showerror(LANGS[self._lang]["no_lib_title"], LANGS[self._lang]["no_lib_msg"])
            return

        out_dir = "video_render"
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(self._odr_path))[0]
        video_path = os.path.join(out_dir, base + ".mp4")

        self._rendering = True
        self._render_btn.configure(text=LANGS[self._lang]["rendering_btn"], state="disabled", fg_color="#444455")
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
                self.after(0, lambda p=pct, e=eta: self._set_status(LANGS[self._lang]["status_rendering_eta"].format(p=p, e=int(e)), TEXT_DIM))
            else:
                self.after(0, lambda p=pct: self._set_status(LANGS[self._lang]["status_rendering"].format(p=p), TEXT_DIM))

        def on_status(msg):
            self.after(0, lambda: self._set_status(msg, TEXT_DIM))

        def worker():
            try:
                self.after(0, lambda: self._set_status_key("status_extracting", TEXT_DIM))
                replay, _, err = unpack_replay(self._odr_path)
                if err:
                    result = (False, err)
                else:
                    render_result = render_replay_to_video(
                        replay,
                        video_path,
                        osu_path=self._osu_path,
                        progress_cb=on_progress,
                        status_cb=on_status,
                    )
                    result = render_result
            except Exception as e:
                result = (False, str(e))

            def done():
                self._rendering = False
                self._render_btn.configure(text=LANGS[self._lang]["render_btn"], state="normal", fg_color=PINK)
                if isinstance(result, tuple) and result and result[0] is False:
                    err_text = result[1] if len(result) > 1 else LANGS[self._lang]["status_render_err"]
                    self._set_status_key("status_error", "#ff4466", e=err_text)
                elif result is False:
                    self._set_status_key("status_render_err", "#ff4466")
                else:
                    elapsed = result
                    self._set_status_key("status_done", GREEN, t=elapsed)
                    self._progress_bar.set(1)
                    abs_path = os.path.abspath(video_path)
                    self._output_label.configure(text=f"📁 {abs_path}", text_color=TEXT_DIM)

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _toggle_lang(self):
        self._lang = "en" if self._lang == "ru" else "ru"
        self._apply_lang()

    def _apply_lang(self):
        s = LANGS[self._lang]
        flag = "🌐 RU" if self._lang == "ru" else "🌐 EN"
        self._lang_btn.configure(text=flag)
        self._subtitle_label.configure(text=s["subtitle"])
        self._lbl_replay_label.configure(text=s["replay_label"])
        self._odr_choose_btn.configure(text=s["choose_file"])
        self._osu_choose_btn.configure(text=s["choose_file"])
        self._lbl_osu_label.configure(text=s["osu_label"])
        if not self._osu_path:
            self._osu_label.configure(text=s["not_selected"])
        if not self._rendering:
            self._render_btn.configure(text=s["render_btn"])
        if not self._odr_path:
            self._set_status_key("status_idle", TEXT_DIM)
        if not self._odr_path:
            self._odr_label.configure(text=s["not_selected"])
        self._redraw_status()

    def _set_status_key(self, key, color=None, **params):
        self._status_key = key
        self._status_params = params
        self._status_color = color or TEXT_DIM
        self._redraw_status()

    def _set_status(self, text, color=None):
        self._status_key = None
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


def run_app():
    app = App()
    app.mainloop()
