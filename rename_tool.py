import os
import re
import time
import threading
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
        self._syncing = False
        self._build()

    # ---------------- UI ----------------
    def _build(self):
        pad = int(10 * self.scale)

        top = tk.Frame(self.root); top.pack(fill=tk.X, padx=pad, pady=int(8 * self.scale))
        tk.Label(top, text="目录：", font=self.font_main).pack(side=tk.LEFT)
        self.dir_entry = tk.Entry(top, font=self.font_main)
        self.dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        tk.Button(top, text="浏览", width=8, font=self.font_main,
                  command=self.select_dir).pack(side=tk.LEFT)
        tk.Button(top, text="读取", width=8, font=self.font_main,
                  command=self.load_files).pack(side=tk.LEFT, padx=5)

        mid = tk.Frame(self.root); mid.pack(fill=tk.BOTH, expand=True, padx=pad)

        # 左列表
        left = tk.LabelFrame(mid, text="① 原始文件名（只读）", font=self.font_main)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lf_left = tk.Frame(left)
        lf_left.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.left_list = tk.Listbox(lf_left, font=self.font_main,
                                    selectmode=tk.NONE, activestyle="none")
        self.left_sb = ttk.Scrollbar(lf_left, orient="vertical",
                                     command=self.left_list.yview)
        self.left_list.configure(yscrollcommand=self._on_yscroll_left)
        self.left_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.left_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 右列表
        right = tk.LabelFrame(
            mid, font=self.font_main,
            text="② 新文件名 —— 双击 / F2 / 右键可编辑；单击选中，Shift/Ctrl 多选；拖动划选")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(pad, 0))
        lf_right = tk.Frame(right)
        lf_right.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.right_list = tk.Listbox(
            lf_right, font=self.font_main, fg="#1565C0",
            selectmode=tk.EXTENDED, selectbackground="#1976D2",
            selectforeground="white")
        self.right_sb = ttk.Scrollbar(lf_right, orient="vertical",
                                      command=self.right_list.yview)
        self.right_list.configure(yscrollcommand=self._on_yscroll_right)
        self.right_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.right_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 编辑绑定
        self.right_list.bind("<Double-Button-1>", self._edit_right)
        self.right_list.bind("<F2>", self._edit_current)
        self.right_list.bind("<Button-3>", self._show_context_menu)
        # F2 生效需要 listbox 有焦点
        self.right_list.bind("<Button-1>", lambda e: self.right_list.focus_set(), add="+")

        # 右键菜单
        self.ctx_menu = tk.Menu(self.root, tearoff=0, font=self.font_main)
        self.ctx_menu.add_command(label="编辑该行 (F2)", command=self._edit_current)
        self.ctx_menu.add_command(label="复制文本", command=self._copy_current)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="清空该行", command=self._clear_current)

        # 滚轮同步
        self.left_list.bind("<MouseWheel>", self._on_mousewheel)
        self.right_list.bind("<MouseWheel>", self._on_mousewheel)

        # 底部按钮
        bottom = tk.Frame(self.root); bottom.pack(fill=tk.X, padx=pad, pady=int(8 * self.scale))
        self.status = tk.Label(bottom, text="就绪", fg="gray", font=self.font_main)
        self.status.pack(side=tk.LEFT)

        tk.Button(bottom, text="确认重命名选中行", width=18, font=self.font_main,
                  bg="#2196F3", fg="white",
                  command=self.confirm_rename).pack(side=tk.RIGHT, padx=3)
        tk.Button(bottom, text="开始搜索", width=12, font=self.font_main,
                  bg="#4CAF50", fg="white",
                  command=self.start_search).pack(side=tk.RIGHT, padx=int(10 * self.scale))
        tk.Button(bottom, text="编辑选中行", width=12, font=self.font_main,
                  command=self._edit_current).pack(side=tk.RIGHT, padx=3)
        tk.Button(bottom, text="清空选择", width=10, font=self.font_main,
                  command=self.clear_selection).pack(side=tk.RIGHT, padx=3)
        tk.Button(bottom, text="全选", width=8, font=self.font_main,
                  command=self.select_all).pack(side=tk.RIGHT, padx=3)

        # 日志
        lf = tk.LabelFrame(self.root, text="日志", font=self.font_main)
        lf.pack(fill=tk.X, padx=pad, pady=(0, int(8 * self.scale)))
        self.log_text = tk.Text(lf, height=12, font=self.font_log, state="disabled")
        ls = ttk.Scrollbar(lf, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=ls.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)
        ls.pack(side=tk.RIGHT, fill=tk.Y)

    # ---------------- 滚动同步 ----------------
    def _on_yscroll_left(self, first, last):
        self.left_sb.set(first, last)
        if not self._syncing:
            self._syncing = True
            try:
                self.right_list.yview_moveto(first)
            finally:
                self._syncing = False

    def _on_yscroll_right(self, first, last):
        self.right_sb.set(first, last)
        if not self._syncing:
            self._syncing = True
            try:
                self.left_list.yview_moveto(first)
            finally:
                self._syncing = False

    def _on_mousewheel(self, event):
        step = -1 if event.delta > 0 else 1
        self.left_list.yview_scroll(step, "units")
        self.right_list.yview_scroll(step, "units")
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

        self.left_list.delete(0, tk.END)
        self.right_list.delete(0, tk.END)
        for f in self.files:
            self.left_list.insert(tk.END, f)
            self.right_list.insert(tk.END, "")
        self.log(f"读取目录：{d}，共 {len(self.files)} 个文件")
        self.status.config(text=f"已读取 {len(self.files)} 个文件", fg="green")

    # ---------------- 全选 / 清空 ----------------
    def select_all(self):
        n = self.right_list.size()
        if n == 0:
            return
        self.right_list.selection_set(0, tk.END)
        self.right_list.see(0)
        self.status.config(text=f"已选中 {n} 行", fg="blue")

    def clear_selection(self):
        self.right_list.selection_clear(0, tk.END)
        self.status.config(text="已清空选择", fg="gray")

    # ---------------- 手动编辑（核心） ----------------
    def _edit_right(self, event):
        """双击右栏某行 → 编辑"""
        idx = self.right_list.nearest(event.y)
        if idx < 0:
            return
        self._open_edit_dialog(idx)

    def _edit_current(self, event=None):
        """F2 / 按钮 / 右键菜单 → 编辑当前选中行"""
        sel = self.right_list.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先在右侧列表中选中一行")
            return
        # 只编辑第一个选中项
        self._open_edit_dialog(sel[0])

    def _open_edit_dialog(self, idx):
        """弹出编辑对话框，修改后写回右栏 Listbox"""
        cur = self.right_list.get(idx)
        dlg = tk.Toplevel(self.root)
        dlg.title(f"编辑新文件名 —— 第 {idx+1} 行")
        dlg.geometry(f"{int(820*self.scale)}x{int(150*self.scale)}")
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text=f"对应文件：{self.left_list.get(idx)}",
                 font=self.font_main, fg="#555").pack(anchor="w", padx=10, pady=(10, 0))

        e = tk.Entry(dlg, font=self.font_main)
        e.pack(fill=tk.X, padx=10, pady=10)
        e.insert(0, cur)
        e.focus_set()
        e.select_range(0, tk.END)
        e.icursor(tk.END)

        def ok(_=None):
            new_text = e.get().strip()
            self.right_list.delete(idx)
            self.right_list.insert(idx, new_text)
            # 保留该行的选中状态
            self.right_list.selection_clear(0, tk.END)
            self.right_list.selection_set(idx)
            self.right_list.see(idx)
            self.status.config(text=f"第 {idx+1} 行已更新", fg="blue")
            self.log(f"  ✎ 第 {idx+1} 行手动修改为：{new_text}")
            dlg.destroy()

        def cancel(_=None):
            dlg.destroy()

        btn_frame = tk.Frame(dlg)
        btn_frame.pack(pady=5)
        tk.Button(btn_frame, text="确定", width=10, font=self.font_main,
                  command=ok).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="取消", width=10, font=self.font_main,
                  command=cancel).pack(side=tk.LEFT, padx=5)

        dlg.bind("<Return>", ok)
        dlg.bind("<Escape>", cancel)

    def _copy_current(self):
        sel = self.right_list.curselection()
        if not sel:
            return
        text = self.right_list.get(sel[0])
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status.config(text="已复制到剪贴板", fg="blue")

    def _clear_current(self):
        sel = self.right_list.curselection()
        if not sel:
            return
        idx = sel[0]
        self.right_list.delete(idx)
        self.right_list.insert(idx, "")
        self.log(f"  ⌫ 第 {idx+1} 行已清空")

    def _show_context_menu(self, event):
        idx = self.right_list.nearest(event.y)
        if idx >= 0:
            # 如果该行没选中，就选中它
            if idx not in self.right_list.curselection():
                self.right_list.selection_clear(0, tk.END)
                self.right_list.selection_set(idx)
        try:
            self.ctx_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.ctx_menu.grab_release()

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

            # 只在右栏当前为空时填入，避免覆盖用户手动编辑的内容
            self.root.after(0, self._set_right_if_empty, i, result)
            self.root.after(0, self.status.config,
                            {"text": f"搜索 {i+1}/{total}", "fg": "orange"})
            time.sleep(1)

        self.root.after(0, self._search_done)

    def _search_done(self):
        self.searching = False
        self.status.config(text="搜索完成，可双击右侧编辑，再选中要重命名的行", fg="green")
        self.log("=== 搜索完成 ===")

    def _set_right_if_empty(self, i, text):
        """仅当该行右栏为空时写入搜索结果，保护用户手动编辑的内容"""
        if i >= self.right_list.size():
            return
        cur = (self.right_list.get(i) or "").strip()
        if not cur:
            self.right_list.delete(i)
            self.right_list.insert(i, text)

    # ---------- 逐页搜索 ----------
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
        sel = self.right_list.curselection()
        self.log(f"=== 确认重命名，选中 {len(sel)} 行 ===")

        if not sel:
            messagebox.showinfo(
                "提示",
                "右侧列表里没有选中任何行。\n\n"
                "请先在右侧列表中选中要重命名的行：\n"
                "· 单击 = 选中一行\n"
                "· Shift + 单击 = 范围选中\n"
                "· Ctrl + 单击 = 加选\n"
                "· 双击 / F2 = 手动编辑该行")
            return

        tasks = []
        for i in sel:
            old_name = self.left_list.get(i)
            # ★ 直接取右栏当前显示文字 —— 无论来自搜索还是手动编辑，都是最终值
            new_raw = (self.right_list.get(i) or "").strip()
            if not new_raw:
                self.log(f"  [跳过] 第 {i+1} 行右栏为空：{old_name}")
                continue
            tasks.append((i, old_name, new_raw))

        if not tasks:
            messagebox.showinfo(
                "提示",
                f"选中的 {len(sel)} 行，右侧文字都是空的。\n\n"
                "请先点“开始搜索”填充右侧，或双击右侧行手动填写。")
            return

        lines = []
        for i, old, new in tasks[:12]:
            lines.append(f"{old}\n    ➜  {new}")
        if len(tasks) > 12:
            lines.append(f"... 还有 {len(tasks)-12} 个")
        preview = "\n".join(lines)

        if not messagebox.askyesno(
                "确认重命名",
                f"即将重命名 {len(tasks)} 个文件：\n\n{preview}\n\n确定执行？"):
            return

        ok_n, fail = 0, []
        updates = []

        for i, old_name, new_raw in tasks:
            old_path = os.path.join(self.directory, old_name)
            ext = os.path.splitext(old_name)[1]
            new_base = re.sub(ILLEGAL, "_", new_raw).strip().rstrip(".") or "unnamed"
            new_name = new_base if new_base.lower().endswith(ext.lower()) else new_base + ext
            new_path = os.path.join(self.directory, new_name)

            if os.path.normcase(new_path) == os.path.normcase(old_path):
                self.log(f"  - 名称相同跳过：{old_name}")
                continue

            if os.path.exists(new_path):
                stem, e = os.path.splitext(new_name)
                k = 1
                while os.path.exists(os.path.join(self.directory, f"{stem}_{k}{e}")):
                    k += 1
                new_name = f"{stem}_{k}{e}"
                new_path = os.path.join(self.directory, new_name)

            try:
                os.rename(old_path, new_path)
                ok_n += 1
                updates.append((i, new_name))
                self.log(f"  ✓ {old_name}  →  {new_name}")
            except Exception as e:
                fail.append(f"{old_name}: {e}")
                self.log(f"  ✗ {old_name}: {e}")

        for i, new_name in updates:
            self.left_list.delete(i)
            self.left_list.insert(i, new_name)
            self.right_list.delete(i)
            self.right_list.insert(i, "")

        self.right_list.selection_clear(0, tk.END)
        for i, new_name in updates:
            self.files[i] = new_name

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
