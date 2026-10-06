import os
import re
import sys
import time
import threading
import subprocess
import difflib
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import requests
from bs4 import BeautifulSoup

SEARCH_BASE = "https://www.wnacg.com/search/?q={}"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Referer": "https://www.wnacg.com/",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
ILLEGAL = r'[\\/:*?"<>|]'

MAX_PAGES = 30
PAGE_DELAY = 0.6
MAX_ITEM_PAGES = 300
AI_MARKERS = [
    "ai generated", "ai-generated", "ai生成", "ai绘制", "ai繪製",
    "[ai]", "(ai)", "【ai】",
]
HL_BG = "#1976D2"
HL_FG = "white"
OPEN_ICON = "📂"


class App:
    def __init__(self, root):
        self.root = root
        root.title("批量文件搜索重命名")

        try:
            self.dpi = root.winfo_fpixels('1i')
        except Exception:
            self.dpi = 96
        self.scale = max(1.0, self.dpi / 96.0)

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        win_w = min(int(1250 * self.scale), int(sw * 0.95))
        win_h = min(int(940 * self.scale), int(sh * 0.92))
        root.geometry(f"{win_w}x{win_h}+{(sw - win_w) // 2}+{(sh - win_h) // 2}")
        root.minsize(int(900 * self.scale), int(600 * self.scale))

        self.fs     = max(10, int(10 * self.scale))
        self.fs_log = max(9,  int(9  * self.scale))
        self.font_main = ("Microsoft YaHei", self.fs)
        self.font_log  = ("Consolas", self.fs_log)

        self.directory = ""
        self.files = []
        self.searching = False
        self._scrolling_sync = False
        self._last_synced_item = None
        self._jump_after_id = None
        self._last_open_time = 0.0

        self._build()

    # ---------------- UI ----------------
    def _build(self):
        pad = int(10 * self.scale)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview", font=self.font_main,
                        rowheight=int(28 * self.scale))
        style.configure("Treeview.Heading", font=self.font_main)
        style.map("Treeview",
                  background=[("selected", HL_BG)],
                  foreground=[("selected", HL_FG)])

        top = tk.Frame(self.root); top.pack(fill=tk.X, padx=pad, pady=int(8 * self.scale))
        tk.Label(top, text="目录：", font=self.font_main).pack(side=tk.LEFT)
        self.dir_entry = tk.Entry(top, font=self.font_main)
        self.dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        tk.Button(top, text="浏览", width=8, font=self.font_main,
                  command=self.select_dir).pack(side=tk.LEFT)
        tk.Button(top, text="读取", width=8, font=self.font_main,
                  command=self.load_files).pack(side=tk.LEFT, padx=5)

        mid = tk.Frame(self.root); mid.pack(fill=tk.BOTH, expand=True, padx=pad)

        # ---- 左 Treeview ----
        left = tk.LabelFrame(mid,
                             text="① 原始文件名（双击内联编辑，点 📂 打开）",
                             font=self.font_main)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lf_left = tk.Frame(left)
        lf_left.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.left_tree = ttk.Treeview(lf_left, columns=("name", "open"),
                                      show="headings", selectmode="browse")
        self.left_tree.heading("name", text="原始文件名")
        self.left_tree.heading("open", text="打开")
        self.left_tree.column("name", width=int(280 * self.scale), anchor="w")
        self.left_tree.column("open", width=int(56 * self.scale),
                              anchor="center", stretch=False)

        self.left_sb = ttk.Scrollbar(lf_left, orient="vertical",
                                     command=self.left_tree.yview)
        self.left_tree.configure(yscrollcommand=self._on_yscroll_left)
        self.left_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.left_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ---- 右 Treeview ----
        right = tk.LabelFrame(mid,
                              text="② 新文件名（双击内联编辑，点 📂 打开；Shift/Ctrl 多选）",
                              font=self.font_main)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(pad, 0))
        lf_right = tk.Frame(right)
        lf_right.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.right_tree = ttk.Treeview(lf_right, columns=("name", "open"),
                                       show="headings", selectmode="extended")
        self.right_tree.heading("name", text="新文件名")
        self.right_tree.heading("open", text="打开")
        self.right_tree.column("name", width=int(280 * self.scale), anchor="w")
        self.right_tree.column("open", width=int(56 * self.scale),
                               anchor="center", stretch=False)

        self.right_sb = ttk.Scrollbar(lf_right, orient="vertical",
                                      command=self.right_tree.yview)
        self.right_tree.configure(yscrollcommand=self._on_yscroll_right)
        self.right_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.right_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ---- 事件绑定 ----
        self.left_tree.bind("<<TreeviewSelect>>", self._on_left_select)
        self.left_tree.bind("<Button-1>",
                            lambda e: self._on_click(e, self.left_tree), add="+")
        self.left_tree.bind("<Double-1>",
                            lambda e: self._on_double(e, self.left_tree, "L"))
        self.left_tree.bind("<MouseWheel>", self._on_mousewheel)

        self.right_tree.bind("<<TreeviewSelect>>", self._on_right_select)
        self.right_tree.bind("<Button-1>",
                             lambda e: self._on_click(e, self.right_tree), add="+")
        self.right_tree.bind("<Double-1>",
                             lambda e: self._on_double(e, self.right_tree, "R"))
        self.right_tree.bind("<MouseWheel>", self._on_mousewheel)
        self.right_tree.bind("<F2>", lambda e: self._edit_current_tree("R"))

        # ---- 快捷键 Ctrl+C / Ctrl+A ----
        for t in (self.left_tree, self.right_tree):
            t.bind("<Control-c>", lambda e, w=t: self._copy_from_tree(w))
            t.bind("<Control-C>", lambda e, w=t: self._copy_from_tree(w))
            t.bind("<Control-a>", lambda e, w=t: self._select_all_in(w))
            t.bind("<Control-A>", lambda e, w=t: self._select_all_in(w))
            t.bind("<Control-Shift-Key-A>", lambda e, w=t: self._select_all_in(w))

        # ---- 右键菜单 ----
        self.ctx_left_menu = tk.Menu(self.root, tearoff=0, font=self.font_main)
        self.ctx_left_menu.add_command(label="编辑该行",
                                       command=lambda: self._edit_current_tree("L"))
        self.ctx_left_menu.add_command(label="复制文件名 (Ctrl+C)",
                                       command=lambda: self._copy_from_tree(self.left_tree))
        self.ctx_left_menu.add_separator()
        self.ctx_left_menu.add_command(label="清空该行",
                                       command=lambda: self._clear_current_tree("L"))

        self.ctx_right_menu = tk.Menu(self.root, tearoff=0, font=self.font_main)
        self.ctx_right_menu.add_command(label="编辑该行",
                                        command=lambda: self._edit_current_tree("R"))
        self.ctx_right_menu.add_command(label="复制文本 (Ctrl+C)",
                                        command=lambda: self._copy_from_tree(self.right_tree))
        self.ctx_right_menu.add_separator()
        self.ctx_right_menu.add_command(label="清空该行",
                                        command=lambda: self._clear_current_tree("R"))

        self.left_tree.bind("<Button-3>",
                            lambda e: self._show_menu(e, self.left_tree,
                                                      self.ctx_left_menu))
        self.right_tree.bind("<Button-3>",
                             lambda e: self._show_menu(e, self.right_tree,
                                                       self.ctx_right_menu))

        # ---- 底部 ----
        bottom = tk.Frame(self.root); bottom.pack(fill=tk.X, padx=pad,
                                                  pady=int(8 * self.scale))
        self.status = tk.Label(bottom, text="就绪", fg="gray", font=self.font_main)
        self.status.pack(side=tk.LEFT)

        tk.Button(bottom, text="确认重命名选中行", width=18, font=self.font_main,
                  bg="#2196F3", fg="white",
                  command=self.confirm_rename).pack(side=tk.RIGHT, padx=3)
        tk.Button(bottom, text="开始搜索", width=12, font=self.font_main,
                  bg="#4CAF50", fg="white",
                  command=self.start_search).pack(side=tk.RIGHT,
                                                  padx=int(10 * self.scale))
        tk.Button(bottom, text="编辑选中行", width=12, font=self.font_main,
                  command=lambda: self._edit_current_tree("R")).pack(side=tk.RIGHT, padx=3)
        tk.Button(bottom, text="清空选择", width=10, font=self.font_main,
                  command=self.clear_selection).pack(side=tk.RIGHT, padx=3)
        tk.Button(bottom, text="全选", width=8, font=self.font_main,
                  command=lambda: self._select_all_in(self.right_tree)).pack(side=tk.RIGHT, padx=3)

        # ---- 日志 ----
        lf = tk.LabelFrame(self.root, text="日志", font=self.font_main)
        lf.pack(fill=tk.X, padx=pad, pady=(0, int(8 * self.scale)))
        self.log_text = tk.Text(lf, height=12, font=self.font_log, state="disabled")
        ls = ttk.Scrollbar(lf, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=ls.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)
        ls.pack(side=tk.RIGHT, fill=tk.Y)

    # ---------------- 复制 / 全选 ----------------
    def _copy_from_tree(self, tree):
        sel = tree.selection()
        if not sel:
            return "break"
        if len(sel) == 1:
            text = tree.set(sel[0], "name")
        else:
            text = "\n".join(tree.set(i, "name") for i in sel)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status.config(text=f"已复制 {len(sel)} 项到剪贴板", fg="blue")
        return "break"

    def _select_all_in(self, tree):
        items = tree.get_children()
        if not items:
            return "break"
        mode = str(tree.cget("selectmode"))
        if mode == "browse":
            # 左栏为单选模式，仅选第一行
            self._last_synced_item = items[0]
            tree.selection_set(items[0])
            tree.focus(items[0])
            tree.see(items[0])
            self.status.config(text="左栏为单选模式，已选中第一行", fg="gray")
        else:
            self._last_synced_item = items[0]
            tree.selection_set(items)
            tree.focus(items[0])
            tree.see(items[0])
            self.status.config(text=f"已选中 {len(items)} 行", fg="blue")
        return "break"

    # ---------------- 选择联动 ----------------
    def _on_left_select(self, event=None):
        # 焦点交给左树，Ctrl+C/Ctrl+A 才能响应
        try:
            self.left_tree.focus_set()
        except Exception:
            pass
        sel = self.left_tree.selection()
        if not sel:
            return
        item = sel[0]
        if item == self._last_synced_item:
            return
        self._last_synced_item = item
        self.right_tree.selection_set(item)
        self.right_tree.focus(item)
        self.right_tree.see(item)
        idx = int(item)
        if 0 <= idx < len(self.files):
            self._schedule_jump(self.files[idx])

    def _on_right_select(self, event=None):
        try:
            self.right_tree.focus_set()
        except Exception:
            pass
        sel = self.right_tree.selection()
        if not sel:
            return
        item = sel[0]
        if item == self._last_synced_item:
            return
        self._last_synced_item = item
        self.left_tree.selection_set(item)
        self.left_tree.focus(item)
        self.left_tree.see(item)
        idx = int(item)
        if 0 <= idx < len(self.files):
            self._schedule_jump(self.files[idx])

    def _schedule_jump(self, filename):
        if self._jump_after_id is not None:
            try:
                self.root.after_cancel(self._jump_after_id)
            except Exception:
                pass
        self._jump_after_id = self.root.after(80, self._jump_log_now, filename)

    def _jump_log_now(self, filename):
        self._jump_after_id = None
        if not filename:
            return
        try:
            total = int(self.log_text.index("end-1c").split(".")[0])
        except Exception:
            return
        if total <= 1:
            return
        pos = None
        try:
            pos = self.log_text.search(f"] {filename}", "1.0",
                                       stopindex=tk.END, nocase=True)
        except Exception:
            pos = None
        if not pos:
            kw = os.path.splitext(filename)[0]
            if kw:
                try:
                    pos = self.log_text.search(kw, "1.0",
                                               stopindex=tk.END, nocase=True)
                except Exception:
                    pos = None
        if not pos:
            return
        try:
            line = int(pos.split(".")[0])
            state = self.log_text.cget("state")
            self.log_text.configure(state="normal")
            self.log_text.tag_remove("jump_hl", "1.0", tk.END)
            self.log_text.tag_add("jump_hl", f"{line}.0", f"{line}.end")
            self.log_text.tag_configure("jump_hl", background=HL_BG,
                                        foreground=HL_FG)
            self.log_text.configure(state=state)
            self.log_text.see(pos)
        except Exception:
            pass

    # ---------------- 内联编辑 ----------------
    def _start_cell_edit(self, tree, item, side):
        bbox = tree.bbox(item, "#1")
        if not bbox:
            return
        x, y, w, h = bbox
        cur = tree.set(item, "name")
        entry = tk.Entry(tree, font=self.font_main, borderwidth=1,
                         relief="solid", bg="#FFFDE7")
        entry.place(x=x, y=y, width=w, height=h)
        entry.delete(0, tk.END)
        entry.insert(0, cur)
        entry.focus_set()
        entry.select_range(0, tk.END)
        entry.icursor(tk.END)

        finished = [False]

        def finish(e=None):
            if finished[0]:
                return
            finished[0] = True
            val = entry.get().strip()
            entry.destroy()
            self._after_cell_edit(tree, item, side, val)

        def cancel(e=None):
            if finished[0]:
                return
            finished[0] = True
            entry.destroy()

        entry.bind("<Return>", finish)
        entry.bind("<Escape>", cancel)
        entry.bind("<FocusOut>", finish)

    def _after_cell_edit(self, tree, item, side, new_val):
        idx = int(item)
        tree.set(item, "name", new_val)
        if side == "L":
            self.log(f"  ✎ 第 {idx+1} 行 原文件名 → {new_val}")
            if self.right_tree.exists(item):
                self.right_tree.set(item, "name", new_val)
            if idx < len(self.files):
                self._schedule_jump(self.files[idx])
        else:
            self.log(f"  ✎ 第 {idx+1} 行 新文件名 → {new_val}")

    def _on_double(self, event, tree, side):
        region = tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = tree.identify_column(event.x)
        if col != "#1":
            return
        item = tree.identify_row(event.y)
        if not item:
            return
        self.root.after(10, lambda: self._start_cell_edit(tree, item, side))

    def _edit_current_tree(self, side):
        tree = self.left_tree if side == "L" else self.right_tree
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中一行")
            return
        self._start_cell_edit(tree, sel[0], side)

    def _clear_current_tree(self, side):
        tree = self.left_tree if side == "L" else self.right_tree
        sel = tree.selection()
        if not sel:
            return
        item = sel[0]
        tree.set(item, "name", "")
        self.log(f"  ⌫ 第 {int(item)+1} 行已清空")

    def _show_menu(self, event, tree, menu):
        item = tree.identify_row(event.y)
        if not item:
            return
        if item not in tree.selection():
            self._last_synced_item = item
            tree.selection_set(item)
        tree.focus_set()
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ---------------- 打开文件 ----------------
    def _on_click(self, event, tree):
        region = tree.identify_region(event.x, event.y)
        if region != "cell":
            return None
        col = tree.identify_column(event.x)
        if col != "#2":
            return None
        item = tree.identify_row(event.y)
        if not item:
            return None
        now = time.time()
        if now - self._last_open_time < 0.5:
            return "break"
        self._last_open_time = now
        self._open_file_at(int(item))
        return "break"

    def _open_file_at(self, idx):
        if not self.directory:
            messagebox.showwarning("提示", "请先读取目录")
            return
        if idx < 0 or idx >= len(self.files):
            return
        path = os.path.join(self.directory, self.files[idx])
        if not os.path.exists(path):
            messagebox.showwarning("提示", f"文件不存在：\n{path}")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
            self.status.config(text=f"已打开：{self.files[idx]}", fg="blue")
        except Exception as e:
            messagebox.showerror("错误", f"无法打开：{e}")

    # ---------------- 滚动同步 ----------------
    def _on_yscroll_left(self, first, last):
        self.left_sb.set(first, last)
        if self._scrolling_sync:
            return
        self._scrolling_sync = True
        try:
            self.right_tree.yview_moveto(first)
        finally:
            self._scrolling_sync = False

    def _on_yscroll_right(self, first, last):
        self.right_sb.set(first, last)
        if self._scrolling_sync:
            return
        self._scrolling_sync = True
        try:
            self.left_tree.yview_moveto(first)
        finally:
            self._scrolling_sync = False

    def _on_mousewheel(self, event):
        step = -1 if event.delta > 0 else 1
        self._scrolling_sync = True
        try:
            self.left_tree.yview_scroll(step, "units")
            self.right_tree.yview_scroll(step, "units")
        finally:
            self._scrolling_sync = False
        return "break"

    # ---------------- 日志 ----------------
    def log(self, msg):
        def _do():
            try:
                at_bottom = self.log_text.yview()[1] >= 0.999
            except Exception:
                at_bottom = True
            self.log_text.configure(state="normal")
            self.log_text.insert(tk.END, str(msg) + "\n")
            self.log_text.configure(state="disabled")
            if at_bottom:
                self.log_text.see(tk.END)
        if threading.current_thread() is threading.main_thread():
            _do()
        else:
            self.root.after(0, _do)

    # ---------------- 目录 ----------------
    def select_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.dir_entry.delete(0, tk.END)
            self.dir_entry.insert(0, d)

    def load_files(self):
        d = self.dir_entry.get().strip()
        if not d or not os.path.isdir(d):
            messagebox.showwarning("提示", "请选择有效目录")
            return
        self.directory = d
        try:
            self.files = sorted(f for f in os.listdir(d)
                                if os.path.isfile(os.path.join(d, f)))
        except Exception as e:
            messagebox.showerror("错误", str(e))
            return

        self._last_synced_item = None
        self.left_tree.delete(*self.left_tree.get_children())
        self.right_tree.delete(*self.right_tree.get_children())
        for i, f in enumerate(self.files):
            stem = os.path.splitext(f)[0]
            iid = str(i)
            self.left_tree.insert("", tk.END, iid=iid,
                                  values=(stem, OPEN_ICON))
            self.right_tree.insert("", tk.END, iid=iid,
                                   values=("", OPEN_ICON))
        self.log(f"读取目录：{d}，共 {len(self.files)} 个文件")
        self.status.config(text=f"已读取 {len(self.files)} 个文件", fg="green")

    # ---------------- 清空选择 ----------------
    def clear_selection(self):
        self._last_synced_item = None
        self.right_tree.selection_remove(*self.right_tree.selection())
        self.status.config(text="已清空选择", fg="gray")

    # ---------------- 搜索 ----------------
    def start_search(self):
        if self.searching:
            return
        if not self.files:
            messagebox.showwarning("提示", "请先读取文件")
            return
        self.searching = True
        self.status.config(text="搜索中...", fg="orange")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        s = requests.Session()
        s.headers.update(HEADERS)
        total = len(self.files)

        for i, name in enumerate(self.files):
            base_kw = os.path.splitext(name)[0]
            self.log(f"[{i+1}/{total}] {name}")

            result = self._search_one(s, base_kw)
            self.log(f"    首次：{result if result else '（无匹配）'}")

            if not result and len(base_kw) > 2:
                trimmed = base_kw[2:]
                if trimmed != base_kw:
                    self.log(f"    ↻ 立即重试（去前2字符）：{trimmed}")
                    result = self._search_one(s, trimmed)
                    self.log(f"    重试：{result if result else '（仍无匹配）'}")

            self.log(f"    → 最终：{result if result else '（无匹配，跳过）'}")

            self.root.after(0, self._set_right_if_empty, i, result)
            self.root.after(0, self.status.config,
                            {"text": f"搜索 {i+1}/{total}", "fg": "orange"})
            time.sleep(1)

        self.root.after(0, self._search_done)

    def _search_done(self):
        self.searching = False
        self.status.config(text="搜索完成，可双击任一侧编辑，选中行后确认重命名", fg="green")
        self.log("=== 搜索完成 ===")

    def _set_right_if_empty(self, i, text):
        item = str(i)
        if not self.right_tree.exists(item):
            return
        cur = (self.right_tree.set(item, "name") or "").strip()
        if not cur:
            self.right_tree.set(item, "name", text)

    def _search_one(self, session, keyword_raw):
        kw = re.sub(r"[\s\-_]+", " ", keyword_raw).strip()
        if not kw:
            return ""

        encoded = requests.utils.quote(kw)
        seen_titles = set()
        all_items = []

        for page_num in range(1, MAX_PAGES + 1):
            if page_num == 1:
                url = SEARCH_BASE.format(encoded)
            else:
                url = f"{SEARCH_BASE.format(encoded)}&p={page_num}"

            try:
                r = session.get(url, timeout=20)
                self.log(f"      【第{page_num}页】HTTP {r.status_code}，"
                         f"{len(r.text)} 字节")
                if r.status_code != 200:
                    self.log(f"      状态码异常，停止翻页")
                    break
                r.encoding = r.apparent_encoding or "utf-8"
                html = r.text
            except Exception as e:
                self.log(f"      异常：{e}")
                break

            soup = BeautifulSoup(html, "html.parser")
            links = soup.select('a[href*="/photos-index-aid-"]')
            if not links:
                self.log(f"      第{page_num}页无任何条目，停止翻页")
                break

            page_items = []
            page_seen = set()
            filtered_ai = 0
            filtered_big = 0

            for a in links:
                t = (a.get("title") or a.get_text(strip=True) or "").strip()
                if not t or t in page_seen:
                    continue
                if self._is_ai_generated(t):
                    filtered_ai += 1
                    continue
                pages = self._extract_pages(a)
                if pages > MAX_ITEM_PAGES:
                    filtered_big += 1
                    continue
                page_seen.add(t)
                page_items.append((t, pages))

            if not page_items:
                self.log(f"      第{page_num}页无有效条目，停止翻页")
                break

            new_items = [(t, p) for t, p in page_items if t not in seen_titles]
            if not new_items:
                self.log(f"      第{page_num}页没有新文件名，停止翻页")
                break

            seen_titles.update(page_seen)
            all_items.extend(new_items)

            extra = ""
            if filtered_ai or filtered_big:
                extra = f"｜过滤 AI {filtered_ai} 条、>{MAX_ITEM_PAGES}页 {filtered_big} 条"
            self.log(f"      第{page_num}页：{len(page_items)} 条"
                     f"（其中 {len(new_items)} 条新，累计 {len(all_items)} 条）{extra}")

            time.sleep(PAGE_DELAY)

        if not all_items:
            self.log("      ✗ 所有页都无候选")
            return ""

        self.log(f"      — 翻页结束，共收集 {len(all_items)} 条候选 —")
        return self._pick_best(all_items, kw)

    def _is_ai_generated(self, t):
        low = t.lower()
        return any(m in low for m in AI_MARKERS)

    def _pick_best(self, items, kw):
        low = kw.lower()
        def sim(t):
            return difflib.SequenceMatcher(None, low, t.lower()).ratio()

        exact    = [(t, p) for t, p in items if t.lower() == low]
        contains = [(t, p) for t, p in items if low in t.lower()]
        similar  = [(t, p) for t, p in items if sim(t) >= 0.35]

        for grp, lbl in ((exact, "精确"), (contains, "包含"), (similar, "相似")):
            if grp:
                top = sorted(grp, key=lambda x: -x[1])[:3]
                self.log(f"      · {lbl} {len(grp)} 条，Top3：")
                for t, p in top:
                    self.log(f"          [{p:>4}张] {t[:70]}")

        for group, label in ((exact, "精确匹配"),
                             (contains, "包含匹配"),
                             (similar, "相似匹配")):
            if group:
                group.sort(key=lambda x: -x[1])
                best = group[0]
                self.log(f"      ✓ {label}，共 {len(group)} 条，取页数最多："
                         f"[{best[1]}张] {best[0][:60]}")
                return best[0]

        self.log("      ✗ 无相似度 ≥ 0.35 的匹配")
        return ""

    def _extract_pages(self, a_tag):
        parent = a_tag
        for _ in range(6):
            parent = parent.parent
            if parent is None:
                break
            info = parent.find(class_="info_col")
            if info:
                text = info.get_text()
                m = re.search(r'(\d+)\s*張圖片', text)
                if m:
                    return int(m.group(1))
                m = re.search(r'(\d+)\s*张图片', text)
                if m:
                    return int(m.group(1))
                break
        return 0

    # ---------------- 确认重命名 ----------------
    def confirm_rename(self):
        sel = self.right_tree.selection()
        self.log(f"=== 确认重命名，选中 {len(sel)} 行 ===")

        if not sel:
            messagebox.showinfo(
                "提示",
                "右侧列表里没有选中任何行。\n\n"
                "请先在右侧列表中选中要重命名的行：\n"
                "· 单击 = 选中一行\n"
                "· Shift + 单击 = 范围选中\n"
                "· Ctrl + 单击 = 加选\n"
                "· Ctrl + A = 全选\n"
                "· 双击 = 直接编辑该行")
            return

        tasks = []
        for item in sel:
            i = int(item)
            if i >= len(self.files):
                continue
            old_disk = self.files[i]
            old_stem, ext = os.path.splitext(old_disk)
            left_text  = (self.left_tree.set(item, "name") or "").strip()
            right_text = (self.right_tree.set(item, "name") or "").strip()

            if right_text:
                new_raw = right_text
            elif left_text and left_text != old_stem:
                new_raw = left_text
            else:
                self.log(f"  [跳过] 第 {i+1} 行无变化：{old_disk}")
                continue

            tasks.append((i, old_disk, ext, new_raw))

        if not tasks:
            messagebox.showinfo(
                "提示",
                f"选中的 {len(sel)} 行，没有可用于重命名的新名称。\n\n"
                "请先点“开始搜索”填充右侧，或双击任意一侧手动填写。")
            return

        lines = []
        for i, old_name, ext, new_raw in tasks[:12]:
            lines.append(f"{old_name}\n    ➜  {new_raw}")
        if len(tasks) > 12:
            lines.append(f"... 还有 {len(tasks)-12} 个")
        preview = "\n".join(lines)

        if not messagebox.askyesno(
                "确认重命名",
                f"即将重命名 {len(tasks)} 个文件：\n\n{preview}\n\n确定执行？"):
            return

        ok_n, fail = 0, []
        updates = []

        for i, old_disk, ext, new_raw in tasks:
            old_path = os.path.join(self.directory, old_disk)
            new_base = re.sub(ILLEGAL, "_", new_raw).strip().rstrip(".") or "unnamed"
            if not new_base.lower().endswith(ext.lower()):
                new_name = new_base + ext
            else:
                new_name = new_base
            new_path = os.path.join(self.directory, new_name)

            if os.path.normcase(new_path) == os.path.normcase(old_path):
                self.log(f"  - 名称相同跳过：{old_disk}")
                continue

            if os.path.exists(new_path):
                stem2, e2 = os.path.splitext(new_name)
                k = 1
                while os.path.exists(os.path.join(self.directory, f"{stem2}_{k}{e2}")):
                    k += 1
                new_name = f"{stem2}_{k}{e2}"
                new_path = os.path.join(self.directory, new_name)

            try:
                os.rename(old_path, new_path)
                ok_n += 1
                updates.append((i, new_name))
                self.log(f"  ✓ {old_disk}  →  {new_name}")
            except Exception as e:
                fail.append(f"{old_disk}: {e}")
                self.log(f"  ✗ {old_disk}: {e}")

        for i, new_name in updates:
            self.files[i] = new_name
            new_stem = os.path.splitext(new_name)[0]
            item = str(i)
            self.left_tree.set(item, "name", new_stem)
            self.right_tree.set(item, "name", "")

        self._last_synced_item = None
        self.right_tree.selection_remove(*self.right_tree.selection())

        msg = f"成功重命名 {ok_n} 个文件"
        if fail:
            msg += f"，失败 {len(fail)} 个"
            messagebox.showwarning("部分失败", "\n".join(fail[:8]))
        self.status.config(text=msg, fg="green")
        self.log(f"=== {msg} ===")


if __name__ == "__main__":
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    try:
        dpi = root.winfo_fpixels('1i')
        root.tk.call('tk', 'scaling', dpi / 72.0)
    except Exception:
        pass

    App(root)
    root.mainloop()
