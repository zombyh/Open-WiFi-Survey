import math
import json
import base64
import io
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Constantes globais
# ---------------------------------------------------------------------------
AREA_PER_AP   = 100
HEATMAP_STEP  = 18
PATH_LOSS_EXP = 2.8

BAND_CONFIG = {
    "2.4GHz": {"radius": 12},
    "5GHz":   {"radius": 8},
}

SIGNAL_GOOD = 0.66
SIGNAL_FAIR = 0.33
SIGNAL_POOR = 0.10

WALL_TYPES = {
    "Drywall":  {"color": "#b0b0d8", "loss_db": 3,  "thick": 2},
    "Vidro":    {"color": "#60d8f8", "loss_db": 2,  "thick": 2},
    "Tijolo":   {"color": "#d07848", "loss_db": 8,  "thick": 3},
    "Concreto": {"color": "#909090", "loss_db": 15, "thick": 4},
    "Metal":    {"color": "#f0c840", "loss_db": 20, "thick": 4},
}

ZOOM_MIN  = 0.15
ZOOM_MAX  = 5.0
ZOOM_STEP = 1.12

# Fontes TrueType para exportação de imagem (suporte a acentos)
_FONT_CANDIDATES = [
    "/usr/share/fonts/TTF/DejaVuSans.ttf",          # Arch / CachyOS / Manjaro
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", # Ubuntu / Debian
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",                   # Windows
]

def _load_font(size: int) -> ImageFont.ImageFont:
    """Carrega fonte TTF com suporte a Unicode; fallback para bitmap se não encontrar."""
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()

# ---------------------------------------------------------------------------
# Cores da UI
# ---------------------------------------------------------------------------
DARK_BG    = "#16161e"
PANEL_BG   = "#1e1e2e"
TOOLBAR_BG = "#24243a"
ACCENT     = "#7aa2f7"
SUCCESS    = "#9ece6a"
WARN       = "#e0af68"
DANGER     = "#f7768e"
TEXT       = "#c0caf5"
MUTED      = "#565f89"
ENTRY_BG   = "#2a2a42"
WALL_MODE_COLOR = "#bb9af7"


# ===========================================================================
# Domínio
# ===========================================================================

class AccessPoint:
    _counter = 0

    def __init__(self, x, y, band="5GHz", label=None):
        self.x, self.y = x, y
        self.band = band
        if label is None:
            AccessPoint._counter += 1
            label = f"AP{AccessPoint._counter}"
        self.label = label

    def to_dict(self):
        return {"x": self.x, "y": self.y, "band": self.band, "label": self.label}

    @classmethod
    def from_dict(cls, d):
        return cls(d["x"], d["y"], d.get("band", "5GHz"), d.get("label"))


class Wall:
    def __init__(self, x1, y1, x2, y2, material="Drywall"):
        self.x1, self.y1 = x1, y1
        self.x2, self.y2 = x2, y2
        self.material = material

    # ---- Geometria --------------------------------------------------------

    def intersects(self, ax, ay, bx, by) -> bool:
        """Verifica se esta parede intersecta o segmento (ax,ay)→(bx,by)."""
        dx1, dy1 = self.x2 - self.x1, self.y2 - self.y1
        dx2, dy2 = bx - ax, by - ay
        denom = dx1 * dy2 - dy1 * dx2
        if abs(denom) < 1e-9:
            return False
        t = ((ax - self.x1) * dy2 - (ay - self.y1) * dx2) / denom
        u = ((ax - self.x1) * dy1 - (ay - self.y1) * dx1) / denom
        return 0.0 < t < 1.0 and 0.0 < u < 1.0

    def dist_to_point(self, px, py) -> float:
        dx, dy = self.x2 - self.x1, self.y2 - self.y1
        if dx == 0 and dy == 0:
            return math.hypot(px - self.x1, py - self.y1)
        t = max(0.0, min(1.0, ((px - self.x1) * dx + (py - self.y1) * dy) / (dx*dx + dy*dy)))
        return math.hypot(px - (self.x1 + t*dx), py - (self.y1 + t*dy))

    # ---- Serialização -----------------------------------------------------

    def to_dict(self):
        return {"x1": self.x1, "y1": self.y1,
                "x2": self.x2, "y2": self.y2,
                "material": self.material}

    @classmethod
    def from_dict(cls, d):
        return cls(d["x1"], d["y1"], d["x2"], d["y2"], d.get("material", "Drywall"))


class HeatmapEngine:
    """Propagação logarítmica com atenuação de paredes."""

    def compute_signal_free(self, dist_m: float, radius_m: float) -> float:
        if dist_m <= 0:
            return 1.0
        if dist_m > radius_m * 1.5:
            return 0.0
        ref = 1.0
        loss = 10 * PATH_LOSS_EXP * math.log10(max(dist_m, ref) / ref)
        max_loss = 10 * PATH_LOSS_EXP * math.log10(radius_m / ref)
        return max(0.0, min(1.0, 1.0 - loss / (max_loss * 1.5)))

    def wall_attenuation(self, ax, ay, px, py, walls) -> float:
        """Retorna fator multiplicativo de atenuação (0–1) pelas paredes cruzadas."""
        factor = 1.0
        for w in walls:
            if w.intersects(ax, ay, px, py):
                loss_db = WALL_TYPES[w.material]["loss_db"]
                factor *= 10 ** (-loss_db / 10.0)
        return factor

    def build_heatmap(self, width, height, aps, walls, scale, show_dead_zones=True):
        heatmap = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw    = ImageDraw.Draw(heatmap, "RGBA")

        for px in range(0, width, HEATMAP_STEP):
            for py in range(0, height, HEATMAP_STEP):
                max_sig = 0.0
                for ap in aps:
                    dist_px = math.hypot(ap.x - px, ap.y - py)
                    dist_m  = dist_px * scale
                    radius  = BAND_CONFIG[ap.band]["radius"]
                    sig     = self.compute_signal_free(dist_m, radius)

                    if sig > 0 and walls:
                        sig *= self.wall_attenuation(ap.x, ap.y, px, py, walls)

                    max_sig = max(max_sig, sig)

                if max_sig >= SIGNAL_GOOD:
                    color = (0, 210, 80, 55)
                elif max_sig >= SIGNAL_FAIR:
                    color = (255, 200, 0, 50)
                elif max_sig >= SIGNAL_POOR:
                    color = (255, 60, 0, 45)
                elif show_dead_zones and aps:
                    color = (60, 60, 80, 38)
                else:
                    continue

                draw.rectangle([px, py, px + HEATMAP_STEP, py + HEATMAP_STEP], fill=color)

        return heatmap


class ProjectModel:
    def __init__(self):
        self.image: Image.Image = None
        self.area      = ""
        self.real_width = ""
        self.band      = "5GHz"
        self.aps:   list[AccessPoint] = []
        self.walls: list[Wall]        = []

    def get_scale(self):
        try:
            w = float(self.real_width.replace(",", "."))
            if w <= 0 or self.image is None:
                raise ValueError
            return w / self.image.width
        except Exception:
            return None

    def calculate_ap_count(self):
        try:
            return max(1, math.ceil(float(self.area) / AREA_PER_AP))
        except Exception:
            return 1

    def to_dict(self):
        buf = io.BytesIO()
        self.image.save(buf, format="PNG")
        return {
            "version":      5,
            "image_base64": base64.b64encode(buf.getvalue()).decode(),
            "area":         self.area,
            "real_width":   self.real_width,
            "band":         self.band,
            "aps":          [a.to_dict() for a in self.aps],
            "walls":        [w.to_dict() for w in self.walls],
        }

    @classmethod
    def from_dict(cls, data):
        m = cls()
        img_data = base64.b64decode(data["image_base64"])
        m.image      = Image.open(io.BytesIO(img_data)).convert("RGBA")
        m.area       = data.get("area", "")
        m.real_width = data.get("real_width", data.get("width", ""))
        m.band       = data.get("band", "5GHz")

        raw_aps = data.get("aps", [])
        if raw_aps and isinstance(raw_aps[0], list):
            m.aps = [AccessPoint(x, y, m.band) for x, y in raw_aps]
        else:
            m.aps = [AccessPoint.from_dict(a) for a in raw_aps]

        m.walls = [Wall.from_dict(w) for w in data.get("walls", [])]
        return m


# ===========================================================================
# Interface
# ===========================================================================

class WifiPlannerApp:

    MODE_AP   = "ap"
    MODE_WALL = "wall"

    def __init__(self, root):
        self.root = root
        self.root.title("Open WiFi Survey")
        self.root.configure(bg=DARK_BG)
        self.root.geometry("1280x760")

        self.model  = ProjectModel()
        self.engine = HeatmapEngine()

        self.selected_ap_index = None
        self.band            = tk.StringVar(value="5GHz")
        self.wall_material   = tk.StringVar(value="Drywall")
        self.show_dead_zones = tk.BooleanVar(value=True)

        self._mode           = self.MODE_AP
        self._wall_start     = None   # (ix, iy) em coords de imagem
        self._wall_preview   = None   # id do item canvas de preview

        self._undo_stack = []
        self._redo_stack = []
        self._tk_image   = None

        self.zoom_level  = 1.0

        self._build_ui()
        self._bind_shortcuts()

    # -----------------------------------------------------------------------
    # Helpers de widgets
    # -----------------------------------------------------------------------

    def _btn(self, parent, text, cmd, bg=ACCENT, fg=DARK_BG, **kw):
        return tk.Button(
            parent, text=text, command=cmd,
            bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
            relief="flat", padx=10, pady=5, cursor="hand2",
            font=("Segoe UI", 9, "bold"), bd=0, **kw
        )

    def _label(self, parent, text, fg=MUTED, bg=TOOLBAR_BG, **kw):
        return tk.Label(parent, text=text, bg=bg, fg=fg, font=("Segoe UI", 9), **kw)

    def _entry(self, parent, width=10):
        return tk.Entry(parent, width=width, bg=ENTRY_BG, fg=TEXT,
                        insertbackground=TEXT, relief="flat",
                        font=("Segoe UI", 9), bd=4)

    # -----------------------------------------------------------------------
    # Construção da UI
    # -----------------------------------------------------------------------

    def _build_ui(self):
        # ── Toolbar principal ──────────────────────────────────────────────
        toolbar = tk.Frame(self.root, bg=TOOLBAR_BG, pady=8, padx=10)
        toolbar.pack(fill="x")

        self._btn(toolbar, "📂 Carregar Planta", self.load_image).pack(side="left", padx=3)
        self._btn(toolbar, "💾 Salvar Projeto",  self.save_project, bg=MUTED, fg=TEXT).pack(side="left", padx=3)
        self._btn(toolbar, "📁 Abrir Projeto",   self.load_project, bg=MUTED, fg=TEXT).pack(side="left", padx=3)
        self._btn(toolbar, "🖼 Exportar Imagem", self.save_image,   bg=MUTED, fg=TEXT).pack(side="left", padx=3)

        tk.Frame(toolbar, width=1, bg=MUTED).pack(side="left", padx=10, fill="y", pady=4)

        self._btn(toolbar, "↩", self.undo, bg="#303050", fg=TEXT).pack(side="left", padx=2)
        self._btn(toolbar, "↪", self.redo, bg="#303050", fg=TEXT).pack(side="left", padx=2)
        self._btn(toolbar, "⚡ Auto",  self.auto_place,  bg=SUCCESS,   fg=DARK_BG).pack(side="left", padx=3)
        self._btn(toolbar, "🗑 Limpar APs", self.clear_aps, bg=DANGER, fg=DARK_BG).pack(side="left", padx=3)
        self._btn(toolbar, "🧱 Limpar Paredes", self.clear_walls, bg=DANGER, fg=DARK_BG).pack(side="left", padx=3)

        # Zoom
        tk.Frame(toolbar, width=1, bg=MUTED).pack(side="left", padx=10, fill="y", pady=4)
        self._btn(toolbar, "🔍+", lambda: self._zoom(ZOOM_STEP), bg="#303050", fg=TEXT).pack(side="left", padx=2)
        self._btn(toolbar, "🔍−", lambda: self._zoom(1/ZOOM_STEP), bg="#303050", fg=TEXT).pack(side="left", padx=2)
        self._btn(toolbar, "1:1", self._zoom_reset, bg="#303050", fg=TEXT).pack(side="left", padx=2)
        self.zoom_label = tk.Label(toolbar, text="100%", bg=TOOLBAR_BG, fg=MUTED,
                                   font=("Segoe UI", 8))
        self.zoom_label.pack(side="left", padx=4)

        # ── Barra de configurações ─────────────────────────────────────────
        cfg = tk.Frame(self.root, bg=PANEL_BG, pady=7, padx=10)
        cfg.pack(fill="x")

        self._label(cfg, "Área (m²):", bg=PANEL_BG).pack(side="left")
        self.area_entry = self._entry(cfg, 9)
        self.area_entry.pack(side="left", padx=(3, 14))

        self._label(cfg, "Largura real (m):", bg=PANEL_BG).pack(side="left")
        self.width_entry = self._entry(cfg, 9)
        self.width_entry.pack(side="left", padx=(3, 14))

        self._label(cfg, "Banda:", bg=PANEL_BG).pack(side="left", padx=(8, 4))
        for val, lbl in [("2.4GHz", "2.4 GHz"), ("5GHz", "5 GHz")]:
            tk.Radiobutton(cfg, text=lbl, variable=self.band, value=val,
                           command=self.redraw,
                           bg=PANEL_BG, fg=TEXT, selectcolor=ENTRY_BG,
                           activebackground=PANEL_BG, activeforeground=TEXT,
                           font=("Segoe UI", 9)).pack(side="left", padx=2)

        tk.Checkbutton(cfg, text="Zonas mortas", variable=self.show_dead_zones,
                       command=self.redraw,
                       bg=PANEL_BG, fg=TEXT, selectcolor=ENTRY_BG,
                       activebackground=PANEL_BG, activeforeground=TEXT,
                       font=("Segoe UI", 9)).pack(side="left", padx=14)

        # Modo parede
        tk.Frame(cfg, width=1, bg=MUTED).pack(side="left", padx=10, fill="y", pady=2)
        self.mode_btn = self._btn(cfg, "🧱 Modo Parede", self._toggle_wall_mode,
                                  bg="#303050", fg=TEXT)
        self.mode_btn.pack(side="left", padx=4)

        self._label(cfg, "Material:", bg=PANEL_BG).pack(side="left", padx=(8, 3))
        mat_cb = ttk.Combobox(cfg, textvariable=self.wall_material,
                              values=list(WALL_TYPES.keys()), width=10,
                              state="readonly", font=("Segoe UI", 9))
        mat_cb.pack(side="left")

        # Contadores
        self.rec_label    = tk.Label(cfg, text="", bg=PANEL_BG, fg=MUTED, font=("Segoe UI", 9))
        self.rec_label.pack(side="right", padx=4)
        self.ap_count_lbl = tk.Label(cfg, text="APs: 0", bg=PANEL_BG, fg=SUCCESS,
                                     font=("Segoe UI", 9, "bold"))
        self.ap_count_lbl.pack(side="right", padx=8)

        # ── Área central ───────────────────────────────────────────────────
        center = tk.Frame(self.root, bg=DARK_BG)
        center.pack(fill="both", expand=True)

        canvas_frame = tk.Frame(center, bg=DARK_BG)
        canvas_frame.pack(side="left", fill="both", expand=True)

        h_scroll = tk.Scrollbar(canvas_frame, orient="horizontal", bg=PANEL_BG)
        h_scroll.pack(side="bottom", fill="x")
        v_scroll = tk.Scrollbar(canvas_frame, orient="vertical", bg=PANEL_BG)
        v_scroll.pack(side="right", fill="y")

        self.canvas = tk.Canvas(canvas_frame, bg="#0d0d14",
                                xscrollcommand=h_scroll.set,
                                yscrollcommand=v_scroll.set,
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        h_scroll.config(command=self.canvas.xview)
        v_scroll.config(command=self.canvas.yview)

        # ── Sidebar ────────────────────────────────────────────────────────
        sidebar = tk.Frame(center, bg=PANEL_BG, width=180, padx=12, pady=14)
        sidebar.pack(side="right", fill="y")
        sidebar.pack_propagate(False)

        self._sidebar_section(sidebar, "Sinal Wi-Fi")
        for color, lbl in [(SUCCESS, "Ótimo  > 66%"),
                           (WARN,    "Regular 33–66%"),
                           (DANGER,  "Fraco  < 33%"),
                           (MUTED,   "Sem cobertura")]:
            row = tk.Frame(sidebar, bg=PANEL_BG)
            row.pack(fill="x", pady=2)
            tk.Label(row, bg=color, width=3).pack(side="left", padx=(0, 7))
            tk.Label(row, text=lbl, bg=PANEL_BG, fg=TEXT, font=("Segoe UI", 8)).pack(side="left")

        self._sidebar_section(sidebar, "Materiais (perda dB)")
        for mat, cfg_w in WALL_TYPES.items():
            row = tk.Frame(sidebar, bg=PANEL_BG)
            row.pack(fill="x", pady=1)
            tk.Label(row, bg=cfg_w["color"], width=3).pack(side="left", padx=(0, 6))
            tk.Label(row, text=f"{mat}  −{cfg_w['loss_db']} dB",
                     bg=PANEL_BG, fg=TEXT, font=("Segoe UI", 8)).pack(side="left")

        self._sidebar_section(sidebar, "Atalhos")
        tips_ap = [
            "Modo AP:",
            "  Click → adicionar AP",
            "  Arrastar → mover AP",
            "  Clique dir. → remover AP",
            "",
            "Modo Parede:",
            "  Click+arrastar → parede",
            "  Clique dir. → remover parede",
            "",
            "Ctrl+scroll → zoom",
            "Ctrl+Z / Ctrl+Y → undo/redo",
        ]
        for tip in tips_ap:
            tk.Label(sidebar, text=tip, bg=PANEL_BG,
                     fg=ACCENT if tip.endswith(":") else MUTED,
                     font=("Segoe UI", 7), justify="left").pack(anchor="w")

        # Tooltip
        self._tooltip = tk.Toplevel(self.root)
        self._tooltip.withdraw()
        self._tooltip.overrideredirect(True)
        self._tooltip.configure(bg="#1a1b26")
        tk.Frame(self._tooltip, bg=ACCENT, height=2).pack(fill="x")
        self._tooltip_lbl = tk.Label(self._tooltip, bg="#1a1b26", fg=TEXT,
                                     font=("Segoe UI", 8), padx=8, pady=5, justify="left")
        self._tooltip_lbl.pack()

        # Binds
        self.canvas.bind("<Button-1>",  self._on_click)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>",  self._on_right_click)
        self.canvas.bind("<Motion>",    self._on_hover)
        self.canvas.bind("<Leave>",     lambda _e: self._tooltip.withdraw())
        self.canvas.bind("<Control-MouseWheel>", self._on_ctrl_scroll)
        # Linux
        self.canvas.bind("<Control-Button-4>",  lambda e: self._zoom(ZOOM_STEP))
        self.canvas.bind("<Control-Button-5>",  lambda e: self._zoom(1/ZOOM_STEP))

    def _sidebar_section(self, parent, title):
        tk.Frame(parent, height=1, bg=MUTED).pack(fill="x", pady=(12, 6))
        tk.Label(parent, text=title, bg=PANEL_BG, fg=ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")

    def _bind_shortcuts(self):
        self.root.bind("<Control-z>", lambda _e: self.undo())
        self.root.bind("<Control-y>", lambda _e: self.redo())

    # -----------------------------------------------------------------------
    # Modo parede
    # -----------------------------------------------------------------------

    def _toggle_wall_mode(self):
        if self._mode == self.MODE_AP:
            self._mode = self.MODE_WALL
            self.mode_btn.config(bg=WALL_MODE_COLOR, fg=DARK_BG, text="✏️ Modo AP")
            self.canvas.config(cursor="crosshair")
        else:
            self._mode = self.MODE_AP
            self.mode_btn.config(bg="#303050", fg=TEXT, text="🧱 Modo Parede")
            self.canvas.config(cursor="")
            self._cancel_wall_preview()

    def _cancel_wall_preview(self):
        if self._wall_preview is not None:
            self.canvas.delete(self._wall_preview)
            self._wall_preview = None
        self._wall_start = None

    # -----------------------------------------------------------------------
    # Zoom
    # -----------------------------------------------------------------------

    def _zoom(self, factor):
        self.zoom_level = max(ZOOM_MIN, min(ZOOM_MAX, self.zoom_level * factor))
        self.zoom_label.config(text=f"{int(self.zoom_level*100)}%")
        self.redraw()

    def _zoom_reset(self):
        self.zoom_level = 1.0
        self.zoom_label.config(text="100%")
        self.redraw()

    def _on_ctrl_scroll(self, event):
        factor = ZOOM_STEP if event.delta > 0 else 1 / ZOOM_STEP
        self._zoom(factor)

    # -----------------------------------------------------------------------
    # Coordenadas: canvas ↔ imagem
    # -----------------------------------------------------------------------

    def _canvas_pos(self, event):
        cx = int(self.canvas.canvasx(event.x))
        cy = int(self.canvas.canvasy(event.y))
        return cx, cy

    def _c2i(self, cx, cy):
        """Canvas → imagem (aplicando zoom inverso)."""
        return int(cx / self.zoom_level), int(cy / self.zoom_level)

    def _i2c(self, ix, iy):
        """Imagem → canvas."""
        return ix * self.zoom_level, iy * self.zoom_level

    # -----------------------------------------------------------------------
    # Detecção de objetos
    # -----------------------------------------------------------------------

    def _find_ap(self, ix, iy, thr=13):
        for i, ap in enumerate(self.model.aps):
            if abs(ap.x - ix) < thr and abs(ap.y - iy) < thr:
                return i
        return None

    def _find_wall(self, ix, iy, thr=8):
        for i, w in enumerate(self.model.walls):
            if w.dist_to_point(ix, iy) < thr:
                return i
        return None

    # -----------------------------------------------------------------------
    # Undo / Redo  (salva APs + paredes juntos)
    # -----------------------------------------------------------------------

    def _state_snapshot(self):
        return {
            "aps":   [a.to_dict() for a in self.model.aps],
            "walls": [w.to_dict() for w in self.model.walls],
        }

    def _push_undo(self):
        self._undo_stack.append(self._state_snapshot())
        if len(self._undo_stack) > 60:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _restore(self, state):
        self.model.aps   = [AccessPoint.from_dict(d) for d in state["aps"]]
        self.model.walls = [Wall.from_dict(d) for d in state["walls"]]
        self.selected_ap_index = None
        self.redraw()

    def undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(self._state_snapshot())
        self._restore(self._undo_stack.pop())

    def redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(self._state_snapshot())
        self._restore(self._redo_stack.pop())

    def clear_aps(self):
        if not self.model.aps:
            return
        self._push_undo()
        self.model.aps.clear()
        self.selected_ap_index = None
        self.redraw()

    def clear_walls(self):
        if not self.model.walls:
            return
        self._push_undo()
        self.model.walls.clear()
        self.redraw()

    # -----------------------------------------------------------------------
    # Carregar imagem
    # -----------------------------------------------------------------------

    def load_image(self):
        path = filedialog.askopenfilename(
            filetypes=[("Imagens", "*.png *.jpg *.jpeg *.bmp *.tiff"), ("Todos", "*.*")]
        )
        if not path:
            return
        self.model.image = Image.open(path).convert("RGBA")
        self.model.aps.clear()
        self.model.walls.clear()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.selected_ap_index = None
        AccessPoint._counter = 0
        self.zoom_level = 1.0
        self.zoom_label.config(text="100%")
        self._update_scroll_region()
        self.redraw()

    def _update_scroll_region(self):
        if self.model.image:
            w = int(self.model.image.width  * self.zoom_level)
            h = int(self.model.image.height * self.zoom_level)
            self.canvas.config(scrollregion=(0, 0, w, h))

    # -----------------------------------------------------------------------
    # Helpers de escala
    # -----------------------------------------------------------------------

    def _sync_model_fields(self):
        self.model.area       = self.area_entry.get()
        self.model.real_width = self.width_entry.get()
        self.model.band       = self.band.get()

    def _get_scale(self):
        self._sync_model_fields()
        return self.model.get_scale()

    # -----------------------------------------------------------------------
    # Eventos do canvas
    # -----------------------------------------------------------------------

    def _on_click(self, event):
        if not self.model.image:
            return
        cx, cy = self._canvas_pos(event)
        ix, iy = self._c2i(cx, cy)

        if self._mode == self.MODE_WALL:
            self._wall_start = (ix, iy)
            return

        # Modo AP
        self._push_undo()
        idx = self._find_ap(ix, iy)
        if idx is not None:
            self.selected_ap_index = idx
        else:
            ap = AccessPoint(ix, iy, self.band.get())
            self.model.aps.append(ap)
            self.selected_ap_index = len(self.model.aps) - 1
        self.redraw()

    def _on_drag(self, event):
        cx, cy = self._canvas_pos(event)
        ix, iy = self._c2i(cx, cy)

        if self._mode == self.MODE_WALL and self._wall_start:
            # Preview da parede sendo desenhada
            sx, sy = self._i2c(*self._wall_start)
            mat    = self.wall_material.get()
            color  = WALL_TYPES[mat]["color"]
            thick  = max(1, int(WALL_TYPES[mat]["thick"] * self.zoom_level))

            if self._wall_preview:
                self.canvas.delete(self._wall_preview)
            self._wall_preview = self.canvas.create_line(
                sx, sy, cx, cy,
                fill=color, width=thick, dash=(6, 3), capstyle="round"
            )
            return

        # Modo AP
        if self.selected_ap_index is not None:
            ap = self.model.aps[self.selected_ap_index]
            ap.x, ap.y = ix, iy
            self.redraw()

    def _on_release(self, event):
        if self._mode == self.MODE_WALL and self._wall_start:
            cx, cy = self._canvas_pos(event)
            ix, iy = self._c2i(cx, cy)
            sx, sy = self._wall_start

            # Descarta paredes com menos de 5 px
            if math.hypot(ix - sx, iy - sy) > 5:
                self._push_undo()
                self.model.walls.append(
                    Wall(sx, sy, ix, iy, self.wall_material.get())
                )
            self._cancel_wall_preview()
            self.redraw()

    def _on_right_click(self, event):
        cx, cy = self._canvas_pos(event)
        ix, iy = self._c2i(cx, cy)

        if self._mode == self.MODE_WALL:
            idx = self._find_wall(ix, iy, thr=int(10 / self.zoom_level))
            if idx is not None:
                self._push_undo()
                self.model.walls.pop(idx)
                self.redraw()
        else:
            idx = self._find_ap(ix, iy)
            if idx is not None:
                self._push_undo()
                self.model.aps.pop(idx)
                self.selected_ap_index = None
                self.redraw()

    def _on_hover(self, event):
        cx, cy = self._canvas_pos(event)
        ix, iy = self._c2i(cx, cy)

        # Tooltip em AP
        ap_idx = self._find_ap(ix, iy, thr=16)
        if ap_idx is not None:
            ap    = self.model.aps[ap_idx]
            scale = self._get_scale()
            if scale:
                pos = f"Pos: ({ap.x*scale:.1f} m, {ap.y*scale:.1f} m)"
            else:
                pos = "Defina a largura real"
            self._show_tooltip(f"{ap.label}  [{ap.band}]\n{pos}", event)
            return

        # Tooltip em parede
        w_idx = self._find_wall(ix, iy, thr=int(10 / self.zoom_level))
        if w_idx is not None:
            w = self.model.walls[w_idx]
            loss = WALL_TYPES[w.material]["loss_db"]
            self._show_tooltip(f"Parede: {w.material}\nAtenuação: −{loss} dB", event)
            return

        self._tooltip.withdraw()

    def _show_tooltip(self, text, event):
        self._tooltip_lbl.config(text=text)
        self._tooltip.geometry(f"+{event.x_root + 14}+{event.y_root + 14}")
        self._tooltip.deiconify()

    # -----------------------------------------------------------------------
    # Auto-posicionamento
    # -----------------------------------------------------------------------

    def auto_place(self):
        if not self.model.image:
            return
        self._push_undo()
        self._sync_model_fields()
        self.model.aps.clear()
        AccessPoint._counter = 0

        count = self.model.calculate_ap_count()
        cols  = math.ceil(math.sqrt(count))
        rows  = math.ceil(count / cols)
        w, h  = self.model.image.width, self.model.image.height

        for r in range(rows):
            for c in range(cols):
                if len(self.model.aps) >= count:
                    break
                x = int((c + 1) * w / (cols + 1))
                y = int((r + 1) * h / (rows + 1))
                self.model.aps.append(AccessPoint(x, y, self.band.get()))

        self.redraw()

    # -----------------------------------------------------------------------
    # Renderização
    # -----------------------------------------------------------------------

    def redraw(self):
        if not self.model.image:
            return

        self.canvas.delete("all")
        self._update_scroll_region()
        scale = self._get_scale()

        # ── Heatmap ────────────────────────────────────────────────────────
        if scale is None:
            composite = self.model.image.copy()
        else:
            heatmap   = self.engine.build_heatmap(
                self.model.image.width, self.model.image.height,
                self.model.aps, self.model.walls, scale,
                self.show_dead_zones.get()
            )
            composite = Image.alpha_composite(self.model.image.copy(), heatmap)

        # ── Paredes no PIL (ficam na imagem base) ──────────────────────────
        draw_pil = ImageDraw.Draw(composite, "RGBA")
        for w in self.model.walls:
            cfg_w = WALL_TYPES[w.material]
            # converte cor hex → RGB
            hx = cfg_w["color"].lstrip("#")
            r, g, b = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
            draw_pil.line([w.x1, w.y1, w.x2, w.y2],
                          fill=(r, g, b, 230), width=cfg_w["thick"])

        # ── Escala (zoom) e exibição ───────────────────────────────────────
        if self.zoom_level != 1.0:
            nw = max(1, int(composite.width  * self.zoom_level))
            nh = max(1, int(composite.height * self.zoom_level))
            display = composite.resize((nw, nh), Image.LANCZOS)
        else:
            display = composite

        self._tk_image = ImageTk.PhotoImage(display)
        self.canvas.create_image(0, 0, anchor="nw", image=self._tk_image)

        # ── Marcadores de AP (no canvas, sobre a imagem) ───────────────────
        for i, ap in enumerate(self.model.aps):
            cx, cy   = self._i2c(ap.x, ap.y)
            selected = (i == self.selected_ap_index)
            ring = SUCCESS if selected else DANGER

            r_halo = max(8, int(9 * self.zoom_level))
            r_dot  = max(4, int(5 * self.zoom_level))

            self.canvas.create_oval(cx-r_halo, cy-r_halo, cx+r_halo, cy+r_halo,
                                    fill="", outline=ring, width=1, dash=(3, 3))
            self.canvas.create_oval(cx-r_dot, cy-r_dot, cx+r_dot, cy+r_dot,
                                    fill=ring, outline="white", width=1)

            fs = max(7, int(8 * self.zoom_level))
            font = ("Segoe UI", fs, "bold")
            ox, oy = r_dot + 2, -(r_dot + 4)
            self.canvas.create_text(cx+ox+1, cy+oy+1, text=ap.label,
                                    fill=DARK_BG, font=font, anchor="w")
            self.canvas.create_text(cx+ox,   cy+oy,   text=ap.label,
                                    fill=ring, font=font, anchor="w")

        self._update_labels()

    def _update_labels(self):
        n = len(self.model.aps)
        self.ap_count_lbl.config(text=f"APs: {n}  |  Paredes: {len(self.model.walls)}")
        try:
            rec  = self.model.calculate_ap_count()
            diff = n - rec
            if diff < 0:
                self.rec_label.config(text=f"recomendado: {rec} | faltam {-diff}", fg=DANGER)
            elif diff > 0:
                self.rec_label.config(text=f"recomendado: {rec} | {diff} a mais", fg=WARN)
            else:
                self.rec_label.config(text=f"recomendado: {rec} ✓", fg=SUCCESS)
        except Exception:
            self.rec_label.config(text="")

    # -----------------------------------------------------------------------
    # Salvar / Carregar / Exportar
    # -----------------------------------------------------------------------

    def save_project(self):
        if not self.model.image:
            messagebox.showwarning("Aviso", "Nenhuma planta carregada.")
            return
        self._sync_model_fields()
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=[("JSON", "*.json")])
        if path:
            with open(path, "w") as f:
                json.dump(self.model.to_dict(), f, indent=4)
            messagebox.showinfo("Salvo", "Projeto salvo com sucesso!")

    def load_project(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not path:
            return
        with open(path, "r") as f:
            data = json.load(f)
        self.model = ProjectModel.from_dict(data)
        self.band.set(self.model.band)
        self.area_entry.delete(0, tk.END)
        self.area_entry.insert(0, self.model.area)
        self.width_entry.delete(0, tk.END)
        self.width_entry.insert(0, self.model.real_width)
        self.selected_ap_index = None
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.zoom_level = 1.0
        self.zoom_label.config(text="100%")
        self._update_scroll_region()
        self.redraw()

    def save_image(self):
        if not self.model.image:
            return
        scale = self._get_scale()
        if scale is None:
            messagebox.showwarning("Aviso", "Informe a largura real para exportar.")
            return

        heatmap   = self.engine.build_heatmap(
            self.model.image.width, self.model.image.height,
            self.model.aps, self.model.walls, scale,
            self.show_dead_zones.get()
        )
        combined  = Image.alpha_composite(self.model.image.copy(), heatmap)
        draw      = ImageDraw.Draw(combined, "RGBA")

        # Paredes
        for w in self.model.walls:
            cfg_w = WALL_TYPES[w.material]
            hx = cfg_w["color"].lstrip("#")
            r, g, b = int(hx[0:2],16), int(hx[2:4],16), int(hx[4:6],16)
            draw.line([w.x1, w.y1, w.x2, w.y2], fill=(r,g,b,230), width=cfg_w["thick"])

        # APs
        font_ap = _load_font(13)
        for ap in self.model.aps:
            draw.ellipse((ap.x-5, ap.y-5, ap.x+5, ap.y+5),
                         fill="#ff4a4a", outline="white", width=1)
            draw.text((ap.x + 8, ap.y - 12), ap.label, fill="#ff4a4a", font=font_ap)

        self._draw_image_legend(draw, combined.width, combined.height)

        path = filedialog.asksaveasfilename(defaultextension=".png",
                                            filetypes=[("PNG", "*.png")])
        if path:
            combined.save(path)
            messagebox.showinfo("Exportado", "Imagem exportada com sucesso!")

    def _draw_image_legend(self, draw, img_w, img_h):
        LINE   = 20   # altura de cada linha de item
        PAD    = 10   # margem externa
        IPAD   = 10   # padding interno
        BOX_W  = 215

        items_sig = [
            ((0, 210, 80),  "Ótimo  > 66%"),
            ((255, 200, 0), "Regular 33–66%"),
            ((255, 60, 0),  "Fraco  < 33%"),
            ((60, 60, 80),  "Sem cobertura"),
        ]

        # Calcula altura total do conteúdo
        total_h = (
            IPAD
            + 14 + 4          # título "Sinal" + gap
            + len(items_sig) * LINE
            + 12               # separador
            + 14 + 4          # título "Paredes" + gap
            + len(WALL_TYPES) * LINE
            + IPAD
        )

        bx = img_w - BOX_W - PAD
        by = img_h - total_h - PAD

        # Caixa de fundo
        draw.rectangle(
            [bx - IPAD, by - IPAD, img_w - PAD + IPAD, img_h - PAD + IPAD],
            fill=(20, 20, 32, 215), outline=(90, 90, 120, 255), width=1
        )

        font_title = _load_font(12)
        font_item  = _load_font(11)

        y = by

        # ── Seção Sinal ────────────────────────────────────────────────────
        draw.text((bx, y), "Sinal Wi-Fi", fill=(160, 170, 220), font=font_title)
        y += 18

        for c, lbl in items_sig:
            draw.rectangle([bx, y + 2, bx + 14, y + 14], fill=c + (220,))
            draw.text((bx + 20, y), lbl, fill=(200, 200, 220), font=font_item)
            y += LINE

        y += 10

        # ── Seção Paredes ──────────────────────────────────────────────────
        draw.text((bx, y), "Paredes", fill=(160, 170, 220), font=font_title)
        y += 18

        for mat, cfg_w in WALL_TYPES.items():
            hx = cfg_w["color"].lstrip("#")
            r, g, b = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
            draw.rectangle([bx, y + 2, bx + 14, y + 14], fill=(r, g, b, 220))
            draw.text((bx + 20, y), f"{mat}  -{cfg_w['loss_db']} dB", fill=(200, 200, 220), font=font_item)
            y += LINE


# ===========================================================================
if __name__ == "__main__":
    root = tk.Tk()
    app  = WifiPlannerApp(root)
    root.mainloop()
