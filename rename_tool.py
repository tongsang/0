import os
import re
import time
import threading
import difflib
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import requests
from bs4 import BeautifulSoup

SEARCH_URL = "https://www.wnacg.com/search/?q={}"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Referer": "https://www.wnacg.com/",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
ILLEGAL = r'[\\/:*?"<>|]'


class App:
    def __init__(self, root):
        self.root = root
        root.title("批量文件搜索重命名")

        # ---------- DPI / 分辨率自适应 ----------
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

        left = tk.LabelFrame(mid, text="① 原始文件名（只读）", font=self.font_main)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.left_list = tk.Listbox(left, font=self.font_main,
                                    selectmode=tk.NONE, activestyle="none")
        self.left_list.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        right = tk.LabelFrame(
            mid, font=self.font_main,
            text="② 新文件名 —— 单击选中 / Shift+单击范围选 / Ctrl+单击加选 / 拖动划选")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(pad, 0))
        self.right_list = tk.Listbox(
            right, font=self.font_main, fg="#1565C0",
            selectmode=tk.EXTENDED, selectbackground="#1976D2",
            selectforeground="white")
        self.right_list.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.right_list.bind("<Double-Button-1>", self._edit_right)

        bottom = tk.Frame(self.root); bottom.pack(fill=tk.X, padx=pad, pady=int(8 * self.scale))
        self.status = tk.Label(bottom, text="就绪", fg="gray", font=self.font_main)
        self.status.pack(side=tk.LEFT)

        tk.Button(bottom, text="确认重命名选中行", width=18, font=self.font_main,
                  bg="#2196F3", fg="white",
                  command=self.confirm_rename).pack(side=tk.RIGHT, padx=3)
        tk.Button(bottom, text="开始搜索", width=12, font=self.font_main,
                  bg="#4CAF50", fg="white",
                  command=self.start_search).pack(side=tk.RIGHT, padx=int(10 * self.scale))
        tk.Button(bottom, text="清空选择", width=10, font=self.font_main,
                  command=self.clear_selection).pack(side=tk.RIGHT, padx=3)
        tk.Button(bottom, text="全选", width=8, font=self.font_main,
                  command=self.select_all).pack(side=tk.RIGHT, padx=3)

        lf = tk.LabelFrame(self.root, text="日志", font=self.font_main)
        lf.pack(fill=tk.X, padx=pad, pady=(0, int(8 * self.scale)))
        self.log_text = tk.Text(lf, height=12, font=self.font_log, state="disabled")
        ls = ttk.Scrollbar(lf, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=ls.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)
        ls.pack(side=tk.RIGHT, fill=tk.Y)

    # ---------------- 日志（用户拖动时不强制回到最新） ----------------
    def log(self, msg):
        def _do():
            # 判断插入前是否处于底部（用户没往上拖）
            try:
                at_bottom = self.log_text.yview()[1] >= 0.999
            except Exception:
                at_bottom = True

            self.log_text.configure(state="normal")
            self.log_text.insert(tk.END, str(msg) + "\n")
            self.log_text.configure(state="disabled")

            # 只有原本就在底部才跟随最新；用户手动上滚时不打扰
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

    # ---------------- 双击编辑右栏 ----------------
    def _edit_right(self, event):
        idx = self.right_list.nearest(event.y)
        if idx < 0:
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("编辑新文件名")
        dlg.geometry(f"{int(720*self.scale)}x{int(120*self.scale)}")
        dlg.transient(self.root)
        dlg.grab_set()
        e = tk.Entry(dlg, font=self.font_main)
        e.pack(fill=tk.X, padx=10, pady=10)
        e.insert(0, self.right_list.get(idx))
        e.focus_set()
        e.select_range(0, tk.END)
        def ok(_=None):
            self.right_list.delete(idx)
            self.right_list.insert(idx, e.get())
            self.right_list.selection_set(idx)
            dlg.destroy()
        tk.Button(dlg, text="确定", font=self.font_main, command=ok).pack(pady=5)
        dlg.bind("<Return>", ok)

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

            self.root.after(0, self._set_right, i, result)
            self.root.after(0, self.status.config,
                            {"text": f"搜索 {i+1}/{total}", "fg": "orange"})
            time.sleep(1)

        self.root.after(0, self._search_done)

    def _search_one(self, session, keyword_raw):
        kw = re.sub(r"[\s\-_]+", " ", keyword_raw).strip()
        if not kw:
            return ""
        url = SEARCH_URL.format(requests.utils.quote(kw))
        try:
            r = session.get(url, timeout=20)
            self.log(f"      HTTP {r.status_code}，{len(r.text)} 字节 | {url}")
            if r.status_code != 200:
                return ""
            r.encoding = r.apparent_encoding or "utf-8"
            return self._parse(r.text, kw)
        except Exception as e:
            self.log(f"      异常：{e}")
            return ""

    def _search_done(self):
        self.searching = False
        self.status.config(text="搜索完成，请在右侧列表中选中要重命名的行", fg="green")
        self.log("=== 搜索完成 ===")

    def _set_right(self, i, text):
        if i < self.right_list.size():
            self.right_list.delete(i)
            self.right_list.insert(i, text)

    # ---------- 多结果时取页数最多的 ----------
    def _parse(self, html, kw):
        soup = BeautifulSoup(html, "html.parser")
        links = soup.select('a[href*="/photos-index-aid-"]')
        if not links:
            self.log("      · 无 photos-index-aid 链接")
            return ""

        items = []          # [(name, pages), ...]
        seen = set()
        for a in links:
            t = (a.get("title") or a.get_text(strip=True) or "").strip()
            if not t or t in seen:
                continue
            seen.add(t)
            pages = self._extract_pages(a)
            items.append((t, pages))

        if not items:
            return ""

        self.log(f"      · 候选 {len(items)} 个：")
        for t, p in items[:8]:
            self.log(f"          [{p:>4}张] {t[:70]}")
        if len(items) > 8:
            self.log(f"          ... 还有 {len(items)-8} 个")

        low = kw.lower()
        def sim(t):
            return difflib.SequenceMatcher(None, low, t.lower()).ratio()

        exact    = [(t, p) for t, p in items if t.lower() == low]
        contains = [(t, p) for t, p in items if low in t.lower()]
        similar  = [(t, p) for t, p in items if sim(t) >= 0.35]

        for group, label in ((exact, "精确匹配"),
                             (contains, "包含匹配"),
                             (similar, "相似匹配")):
            if group:
                group.sort(key=lambda x: -x[1])   # 页数降序
                best = group[0]
                self.log(f"      ✓ {label}，取页数最多：[{best[1]}张] {best[0][:60]}")
                return best[0]

        self.log("      ✗ 相似度不足，视为无匹配")
        return ""

    def _extract_pages(self, a_tag):
        """从链接所在容器里的 .info_col 中提取页数，例如 26張圖片 → 26"""
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
                "· Shift + 单击 = 范围选中（框选）\n"
                "· Ctrl + 单击 = 加选\n"
                "· 或点“全选”按钮")
            return

        tasks = []
        for i in sel:
            old_name = self.left_list.get(i)
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

        # 原地更新被重命名的行，其他行保持不变
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
