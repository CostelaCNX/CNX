#!/usr/bin/python3
"""
CNX Pack Installer - macOS
A polished macOS port of CNX_Installer_v1.1 by CostelaBR.

Runs on the system Python (/usr/bin/python3) which ships with tkinter.

Note on rendering: this macOS Tk build ignores explicit `bg` colors on classic
widgets (Frame/Label/Button) under dark mode, so the entire UI is drawn on a
single tk.Canvas — canvas primitives (rectangles, text) always honor their
colors. The only real widget is the tk.Text log, which does honor its bg.
"""

import tkinter as tk
from tkinter import messagebox
import subprocess
import threading
import urllib.request
import json
import zipfile
import io
import os
import re
import plistlib

REPOS = ["CostelaCNX/CNX", "CNX17/CNX_spare"]
VERSION = "v1.1  ·  macOS Edition"

# ── Design tokens ────────────────────────────────────────────────────────────
C = {
    "bg":          "#F4F5FB",
    "card":        "#FFFFFF",
    "header":      "#4834D4",
    "header_lo":   "#5B4FE0",
    "accent":      "#6C5CE7",
    "accent_dim":  "#A29BFE",
    "accent_press":"#5547D0",
    "green":       "#00B894",
    "green_hover": "#00CEA6",
    "green_press": "#019875",
    "text":        "#2D3436",
    "muted":       "#8C95A6",
    "border":      "#D9DEEC",
    "field":       "#EEF0F8",
    "field_hover": "#E2E6F4",
    "term_bg":     "#1A1B26",
    "term_fg":     "#9ECE6A",
    "term_dim":    "#565F89",
    "term_cyan":   "#7DCFFF",
    "term_white":  "#C0CAF5",
    "term_amber":  "#E0AF68",
    "term_red":    "#F7768E",
}

FONT_UI   = "Avenir Next"
FONT_MONO = "Menlo"


# ── disk helpers ───────────────────────────────────────────────────────────────

def list_removable_drives():
    """All removable/ejectable whole disks (handles built-in SD readers too)."""
    try:
        r = subprocess.run(["diskutil", "list", "-plist"], capture_output=True)
        data = plistlib.loads(r.stdout)
        out = []
        for disk in data.get("WholeDisks", []):
            info = _disk_info(disk)
            if info and info["removable"]:
                label = "{}  ·  {}  ·  [{}]".format(info["name"], info["size"], disk)
                out.append((label, disk))
        return out
    except Exception:
        return []


def _disk_info(disk):
    try:
        r = subprocess.run(["diskutil", "info", "-plist", disk], capture_output=True)
        d = plistlib.loads(r.stdout)
        name = d.get("VolumeName") or d.get("MediaName") or disk
        size = d.get("TotalSize", 0)
        removable = d.get("RemovableMedia") or d.get("RemovableMediaOrExternalDevice")
        return {"name": name, "size": "{:.0f} GB".format(size / 1e9),
                "removable": bool(removable)}
    except Exception:
        return None


def _dev_from_label(label):
    m = re.search(r'\[(disk\d+)\]', label)
    return "/dev/{}".format(m.group(1)) if m else None


# ── GitHub / download ──────────────────────────────────────────────────────────

def _zip_url(repo):
    url = "https://api.github.com/repos/{}/releases/latest".format(repo)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    tag = data.get("tag_name", "?")
    for asset in data.get("assets", []):
        if asset["name"].endswith(".zip"):
            return asset["browser_download_url"], tag
    raise ValueError("Nenhum ZIP no release de {}".format(repo))


def _download(url, progress_cb):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        buf = bytearray()
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            buf.extend(chunk)
            if total:
                progress_cb(len(buf), total)
    return bytes(buf)


# ── ZIP / BMP ───────────────────────────────────────────────────────────────────

def _extract_zip(data, dest, log):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        for name in names:
            try:
                zf.extract(name, dest)
            except Exception as e:
                log("   ! {} — {}".format(name, e), "warn")
        log("   {} arquivos extraídos".format(len(names)), "dim")


def _extract_hidden_zip(dest, log):
    bmp = os.path.join(dest, "bootloader", "bootlogo_atmo_sys.bmp")
    if not os.path.exists(bmp):
        log("   (sem pacote oculto — ok)", "dim")
        return
    tmp = os.path.join(dest, "cnx_temp.zip")
    try:
        with open(bmp, "rb") as f:
            data = f.read()
        idx = data.find(b"PK\x03\x04")
        if idx == -1:
            log("   (nenhum ZIP embutido no BMP)", "dim")
            return
        with open(tmp, "wb") as f:
            f.write(data[idx:])
        with zipfile.ZipFile(tmp) as zf:
            names = zf.namelist()
            for name in names:
                try:
                    zf.extract(name, dest)
                except Exception as e:
                    log("   ! {} — {}".format(name, e), "warn")
        log("   {} arquivos ocultos extraídos".format(len(names)), "dim")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ── Main app (Canvas-based UI) ───────────────────────────────────────────────────

class App(tk.Tk):
    W, H = 720, 648
    PAD = 24

    def __init__(self):
        super().__init__()
        self.title("CNX Pack Installer")
        # Force a light palette (helps window chrome / native dialogs).
        try:
            self.tk_setPalette(background=C["bg"], foreground=C["text"])
        except tk.TclError:
            pass
        self.resizable(False, False)
        self.geometry("{}x{}".format(self.W, self.H))

        self.canvas = tk.Canvas(self, width=self.W, height=self.H,
                                bg=C["bg"], highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self._buttons = {}     # tag -> dict(rect,label,colors,enabled,cmd)
        self._drives = []
        self._sel_label = ""
        self._dropdown = None

        self._build()
        self._refresh()
        self._center()

        # close dropdown when clicking elsewhere
        self.canvas.bind("<Button-1>", self._maybe_close_dropdown, add="+")

    # ── geometry ────────────────────────────────────────────────────────────────

    def _center(self):
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, (sw - self.W) // 2)
        y = max(0, (sh - self.H) // 3)
        self.geometry("+{}+{}".format(x, y))

    # ── primitives ────────────────────────────────────────────────────────────────

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        """Draw a rounded rectangle as a smoothed polygon."""
        pts = [
            x1 + r, y1,  x2 - r, y1,  x2, y1,  x2, y1 + r,
            x2, y2 - r,  x2, y2,  x2 - r, y2,  x1 + r, y2,
            x1, y2,  x1, y2 - r,  x1, y1 + r,  x1, y1,
        ]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def _button(self, tag, x1, y1, x2, y2, text, font_size,
                fill, hover, press, fg, cmd, radius=10):
        rect = self._round_rect(x1, y1, x2, y2, radius,
                                fill=fill, outline=fill, tags=(tag,))
        lbl = self.canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=text,
                                      fill=fg, font=(FONT_UI, font_size, "bold"),
                                      tags=(tag,))
        self._buttons[tag] = {
            "rect": rect, "label": lbl, "fill": fill, "hover": hover,
            "press": press, "fg": fg, "enabled": True, "cmd": cmd,
        }
        self.canvas.tag_bind(tag, "<Enter>", lambda e, t=tag: self._btn_state(t, "hover"))
        self.canvas.tag_bind(tag, "<Leave>", lambda e, t=tag: self._btn_state(t, "fill"))
        self.canvas.tag_bind(tag, "<ButtonPress-1>", lambda e, t=tag: self._btn_state(t, "press"))
        self.canvas.tag_bind(tag, "<ButtonRelease-1>", lambda e, t=tag: self._btn_click(t))
        return tag

    def _btn_state(self, tag, which):
        b = self._buttons[tag]
        if not b["enabled"]:
            return
        col = b[which]
        self.canvas.itemconfig(b["rect"], fill=col, outline=col)
        self.canvas.configure(cursor="pointinghand")

    def _btn_click(self, tag):
        b = self._buttons[tag]
        if not b["enabled"]:
            return
        self.canvas.itemconfig(b["rect"], fill=b["hover"], outline=b["hover"])
        b["cmd"]()

    def _btn_enable(self, tag, enabled):
        b = self._buttons[tag]
        b["enabled"] = enabled
        col = b["fill"] if enabled else C["border"]
        self.canvas.itemconfig(b["rect"], fill=col, outline=col)
        self.canvas.itemconfig(b["label"], fill=b["fg"] if enabled else C["muted"])

    # ── build the static layout ─────────────────────────────────────────────────

    def _build(self):
        cv = self.canvas
        W, P = self.W, self.PAD

        # Header band
        cv.create_rectangle(0, 0, W, 96, fill=C["header"], outline=C["header"])
        cv.create_rectangle(0, 92, W, 96, fill=C["header_lo"], outline=C["header_lo"])
        cv.create_text(P, 34, anchor="w", text="⬢  CNX Pack Installer",
                       fill="white", font=(FONT_UI, 22, "bold"))
        cv.create_text(P + 2, 66, anchor="w", text=VERSION,
                       fill=C["accent_dim"], font=(FONT_UI, 12))

        # Section 1
        self._section(P, 124, "1", "Selecione o SD Card")

        # Drive selector (custom dropdown field) + refresh
        self.sel_x1, self.sel_y1, self.sel_x2, self.sel_y2 = P, 150, 556, 192
        self._round_rect(self.sel_x1, self.sel_y1, self.sel_x2, self.sel_y2, 10,
                         fill=C["field"], outline=C["border"], tags=("selector",))
        self.sel_text = cv.create_text(self.sel_x1 + 16, (self.sel_y1 + self.sel_y2) / 2,
                                       anchor="w", text="Procurando unidades…",
                                       fill=C["muted"], font=(FONT_UI, 12),
                                       tags=("selector",))
        cv.create_text(self.sel_x2 - 18, (self.sel_y1 + self.sel_y2) / 2,
                       text="▾", fill=C["accent"], font=(FONT_UI, 13, "bold"),
                       tags=("selector",))
        cv.tag_bind("selector", "<Enter>",
                    lambda e: cv.configure(cursor="pointinghand"))
        cv.tag_bind("selector", "<Leave>",
                    lambda e: cv.configure(cursor=""))
        cv.tag_bind("selector", "<Button-1>", self._toggle_dropdown)

        self._button("refresh", 568, 150, W - P, 192, "⟳  Atualizar", 12,
                     C["accent"], C["accent_dim"], C["accent_press"], "white",
                     self._refresh)

        # Section 2
        self._section(P, 218, "2", "Status do Processo")

        # Log terminal (real tk.Text placed on canvas)
        log_y1, log_y2 = 246, 512
        self._round_rect(P, log_y1, W - P, log_y2, 10,
                         fill=C["term_bg"], outline=C["border"])
        self.log = tk.Text(cv, font=(FONT_MONO, 11), bg=C["term_bg"],
                           fg=C["term_fg"], insertbackground=C["term_fg"],
                           relief="flat", bd=0, padx=14, pady=12, wrap="word",
                           state="disabled", highlightthickness=0,
                           spacing1=1, spacing3=2)
        cv.create_window(P + 2, log_y1 + 2, anchor="nw", window=self.log,
                         width=(W - P) - (P) - 4, height=(log_y2 - log_y1) - 4)
        for tag, col, bold in [
            ("info", C["term_fg"], False), ("step", C["term_cyan"], True),
            ("ok", "#9ECE6A", True), ("warn", C["term_amber"], False),
            ("err", C["term_red"], True), ("dim", C["term_dim"], False),
            ("white", C["term_white"], False),
        ]:
            self.log.tag_configure(
                tag, foreground=col,
                font=(FONT_MONO, 11, "bold") if bold else (FONT_MONO, 11))

        # Progress bar
        self.pb_x1, self.pb_x2 = P, W - P
        self.pb_y1, self.pb_y2 = 528, 540
        self._round_rect(self.pb_x1, self.pb_y1, self.pb_x2, self.pb_y2, 6,
                         fill=C["field"], outline=C["field"])
        self.pb_fill = self._round_rect(self.pb_x1, self.pb_y1, self.pb_x1 + 1,
                                        self.pb_y2, 6,
                                        fill=C["green"], outline=C["green"])

        # Start button
        self._button("start", P, 560, W - P, 612, "INICIAR PROCESSO", 15,
                     C["green"], C["green_hover"], C["green_press"], "white", self._start)

    def _section(self, x, y, num, title):
        cv = self.canvas
        self._round_rect(x, y - 11, x + 26, y + 11, 6,
                         fill=C["accent"], outline=C["accent"])
        cv.create_text(x + 13, y, text=num, fill="white", font=(FONT_UI, 12, "bold"))
        cv.create_text(x + 38, y, anchor="w", text=title, fill=C["text"],
                       font=(FONT_UI, 14, "bold"))

    # ── progress ──────────────────────────────────────────────────────────────────

    def _set_progress(self, v):
        def apply():
            w = self.pb_x1 + max(1, (self.pb_x2 - self.pb_x1) * v / 100.0)
            self.canvas.coords(self.pb_fill, *self._round_pts(
                self.pb_x1, self.pb_y1, w, self.pb_y2, 6))
        self.after(0, apply)

    def _round_pts(self, x1, y1, x2, y2, r):
        return [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]

    # ── logging ───────────────────────────────────────────────────────────────────

    def _log(self, msg, tag="info"):
        self.after(0, self._log_main, msg, tag)

    def _log_main(self, msg, tag):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    # ── drive dropdown ──────────────────────────────────────────────────────────

    def _toggle_dropdown(self, _=None):
        if self._dropdown is not None:
            self._close_dropdown()
            return
        if not self._drives:
            return
        rows = self._drives
        rowh = 36
        h = rowh * len(rows) + 4
        width = int(self.sel_x2 - self.sel_x1)
        x = self.winfo_rootx() + int(self.sel_x1)
        y = self.winfo_rooty() + int(self.sel_y2) + 4

        dd = tk.Toplevel(self)
        dd.overrideredirect(True)
        dd.geometry("{}x{}+{}+{}".format(width, h, x, y))
        dc = tk.Canvas(dd, width=width, height=h, bg=C["card"],
                       highlightthickness=1, highlightbackground=C["accent"])
        dc.pack(fill="both", expand=True)
        for i, (label, dev) in enumerate(rows):
            ry = 2 + i * rowh
            tag = "row{}".format(i)
            rect = dc.create_rectangle(2, ry, width - 2, ry + rowh,
                                       fill=C["card"], outline=C["card"], tags=(tag,))
            dc.create_text(14, ry + rowh / 2, anchor="w", text=label,
                           fill=C["text"], font=(FONT_UI, 12), tags=(tag,))
            dc.tag_bind(tag, "<Enter>",
                        lambda e, r=rect: dc.itemconfig(r, fill=C["field"], outline=C["field"]))
            dc.tag_bind(tag, "<Leave>",
                        lambda e, r=rect: dc.itemconfig(r, fill=C["card"], outline=C["card"]))
            dc.tag_bind(tag, "<Button-1>", lambda e, lab=label: self._pick(lab))
        dd.bind("<Escape>", lambda e: self._close_dropdown())
        self._dropdown = dd

    def _close_dropdown(self):
        if self._dropdown is not None:
            try:
                self._dropdown.destroy()
            except tk.TclError:
                pass
            self._dropdown = None

    def _maybe_close_dropdown(self, event):
        # close unless the click was on the selector itself
        if self._dropdown is None:
            return
        items = self.canvas.find_withtag("selector")
        hit = self.canvas.find_withtag("current")
        if hit and hit[0] in items:
            return
        self._close_dropdown()

    def _pick(self, label):
        self._sel_label = label
        self.canvas.itemconfig(self.sel_text, text=label, fill=C["text"])
        self._close_dropdown()
        dev = _dev_from_label(label)
        if dev:
            self._log("→ Selecionado: {}".format(dev), "white")

    # ── refresh ─────────────────────────────────────────────────────────────────

    def _refresh(self):
        self._close_dropdown()
        self._drives = list_removable_drives()
        if self._drives:
            self._pick(self._drives[0][0])
        else:
            self._sel_label = ""
            self.canvas.itemconfig(self.sel_text,
                                   text="Nenhuma unidade removível encontrada",
                                   fill=C["muted"])
            self._log("Nenhuma unidade removível encontrada.", "warn")
            self._log("Conecte o SD card e clique em Atualizar.", "dim")

    def _set_ui(self, enabled):
        self._btn_enable("start", enabled)
        self._btn_enable("refresh", enabled)

    # ── install flow ──────────────────────────────────────────────────────────────

    def _start(self):
        label = self._sel_label
        if not label:
            messagebox.showwarning("Atenção", "Selecione uma unidade primeiro.")
            return
        dev = _dev_from_label(label)
        if not dev:
            messagebox.showerror("Erro", "Não foi possível identificar a unidade.")
            return
        if dev in ("/dev/disk0", "/dev/disk1", "/dev/disk2", "/dev/disk3"):
            messagebox.showerror("Erro Crítico",
                                 "Disco interno detectado — operação bloqueada por segurança!")
            return
        if not messagebox.askyesno(
            "Confirmar formatação",
            "A unidade {} será FORMATADA como FAT32.\n\n"
            "TODOS OS DADOS SERÃO APAGADOS PERMANENTEMENTE.\n\n"
            "Deseja continuar?".format(dev), icon="warning"):
            return
        self._set_ui(False)
        self._set_progress(0)
        threading.Thread(target=self._run, args=(dev,), daemon=True).start()

    def _run(self, dev):
        try:
            self._install(dev)
        except Exception as e:
            self._log("", "info")
            self._log("✕  ERRO: {}".format(e), "err")
            self.after(0, lambda: messagebox.showerror(
                "Erro", "Falha no processo:\n\n{}\n\n"
                "Verifique a unidade e tente novamente.".format(e)))
        finally:
            self.after(0, lambda: self._set_ui(True))

    def _install(self, dev):
        # 1 — format
        self._log("", "info")
        self._log("[1/4]  Formatando {} como FAT32…".format(dev), "step")
        r = subprocess.run(["diskutil", "eraseDisk", "FAT32", "SWITCH SD", dev],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "diskutil eraseDisk falhou")
        self._log("   ✓ Formatação concluída", "ok")
        self._set_progress(10)

        mount = self._find_mount(dev)
        if not mount:
            raise RuntimeError("A unidade não montou após a formatação.")
        self._log("   ↳ Montado em: {}".format(mount), "dim")
        self._set_progress(15)

        # 2 — download
        self._log("", "info")
        self._log("[2/4]  Baixando o CNX Pack…", "step")
        zip_data = None
        for repo in REPOS:
            try:
                url, tag = _zip_url(repo)
                self._log("   ↳ {} ({})".format(repo, tag), "dim")
                last = [0]
                def cb(done, total, lp=last):
                    self._set_progress(15 + int((done / total) * 68))
                    pct = int((done / total) * 100)
                    if pct >= lp[0] + 5:
                        lp[0] = pct
                        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                        self._log("   {} {:>3}%  ({:.0f}/{:.0f} MB)".format(
                            bar, pct, done / 1e6, total / 1e6), "info")
                zip_data = _download(url, cb)
                self._log("   ✓ Download concluído — {:.1f} MB".format(
                    len(zip_data) / 1e6), "ok")
                break
            except Exception as e:
                self._log("   ! Falha em {}: {}".format(repo, e), "warn")
                if repo != REPOS[-1]:
                    self._log("   ↳ Tentando repositório de backup…", "dim")
        if not zip_data:
            raise RuntimeError("Falha no download em todos os repositórios.")
        self._set_progress(83)

        # 3 — extract
        self._log("", "info")
        self._log("[3/4]  Extraindo arquivos para o SD card…", "step")
        _extract_zip(zip_data, mount, self._log)
        self._log("   ✓ Extração concluída", "ok")
        self._set_progress(92)

        # 4 — hidden zip
        self._log("", "info")
        self._log("[4/4]  Verificando pacote oculto…", "step")
        _extract_hidden_zip(mount, self._log)
        self._log("   ✓ Verificação concluída", "ok")
        self._set_progress(100)

        self._log("", "info")
        self._log("✓  CONCLUÍDO! SD card pronto para uso.", "ok")
        self._log("   Pode remover o cartão com segurança.", "dim")
        self.after(0, lambda: messagebox.showinfo(
            "Sucesso 🎉",
            "CNX Pack instalado com sucesso em {}!\n\n"
            "Você já pode remover o SD card com segurança.".format(dev)))

    def _find_mount(self, dev):
        for part in ("s2", "s1"):
            try:
                r = subprocess.run(["diskutil", "info", "-plist", dev + part],
                                   capture_output=True)
                mp = plistlib.loads(r.stdout).get("MountPoint")
                if mp and os.path.isdir(mp):
                    return mp
            except Exception:
                pass
        if os.path.isdir("/Volumes/SWITCH SD"):
            return "/Volumes/SWITCH SD"
        return None


if __name__ == "__main__":
    App().mainloop()
