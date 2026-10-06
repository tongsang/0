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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
# Windows 文件名非法字符
ILLEGAL = r'[\\/:*?"<>|]'


class FileRenameTool:
    def __init__(self, root):
        self.root = root
        self.root.title("批量文件搜索重命名工具")
        self.root.geometry("1200x720")

        self.directory = ""
        self.files = []
        self.new_names = []
        self.check_vars = []
        self.searching = False
        self._press_timer = None

        self._build_ui()

    # ================== UI ==================
    def _build_ui(self):
        top = tk.Frame(self.root)
        top.pack(fill=tk.X, padx=10, pady=8)
        tk.Label(top, text="目标目录：").pack(side=tk.LEFT)
        self.dir_entry = tk.Entry(top)
        self.dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        tk.Button(top, text="浏览", command=self.select_directory, width=8).pack(side=tk.LEFT)
        tk.Button(top, text="读取文件", command=self.load_files, width=10).pack(side=tk.LEFT, padx=5)

        mid = tk.Frame(self.root)
        mid.pack(fill=tk.BOTH, expand=True, padx=10)

        left = tk.LabelFrame(mid, text="① 原始文件名")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.left_list = tk.Listbox(left, font=("Microsoft YaHei", 10))
        self.left_list.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        right = tk.LabelFrame(mid, text="② 搜索到的新文件名")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(10, 0))
        self.right_list = tk.Listbox(right, font=("Microsoft YaHei", 10), fg="#1565C0")
        self.right_list.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        check_frame = tk.LabelFrame(self.root, text="③ 勾选需要重命名的项（长按此处任意位置 0.8 秒可全选）")
        check_frame.pack(fill=tk.X, padx=10, pady=5)
        self.check_canvas = tk.Canvas(check_frame, height=140)
        self.check_scroll = ttk.Scrollbar(check_frame, orient="vertical",
                                          command=self.check_canvas.yview)
        self.check_inner = tk.Frame(self.check_canvas)
        self.check_inner.bind(
            "<Configure>",
            lambda e: self.check_canvas.configure(scrollregion=self.check_canvas.bbox("all")))
        self.check_canvas.create_window((0, 0), window=self.check_inner, anchor="nw")
        self.check_canvas.configure(yscrollcommand=self.check_scroll.set)
        self.check_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.check_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        bottom = tk.Frame(self.root)
        bottom.pack(fill=tk.X, padx=10, pady=8)
        self.status = tk.Label(bottom, text="就绪", fg="gray")
        self.status.pack(side=tk.LEFT)

        tk.Button(bottom, text="确认重命名", command=self.confirm_rename,
                  bg="#2196F3", fg="white", width=12).pack(side=tk.RIGHT, padx=3)
        tk.Button(bottom, text="开始搜索", command=self.start_search,
                  bg="#4CAF50", fg="white", width=12).pack(side=tk.RIGHT, padx=10)
        tk.Button(bottom, text="全不选", command=self.deselect_all,
                  width=8).pack(side=tk.RIGHT, padx=3)
        tk.Button(bottom, text="全选", command=self.select_all,
                  width=8).pack(side=tk.RIGHT, padx=3)

        # 长按检测（绑定到整个 checkbox 容器）
        self.check_canvas.bind("<ButtonPress-1>", self._on_press)
        self.check_canvas.bind("<ButtonRelease-1>", self._on_release)
        self.check_inner.bind("<ButtonPress-1>", self._on_press)
        self.check_inner.bind("<ButtonRelease-1>", self._on_release)

    # ============== 功能 1：读取目录 ==============
    def select_directory(self):
        d = filedialog.askdirectory()
        if d:
            self.dir_entry.delete(0, tk.END)
            self.dir_entry.insert(0, d)

    def load_files(self):
        d = self.dir_entry.get().strip()
        if not d or not os.path.isdir(d):
            messagebox.showwarning("提示", "请先选择有效的目录")
            return
        self.directory = d
        try:
            self.files = sorted(
                f for f in os.listdir(d)
                if os.path.isfile(os.path.join(d, f))
            )
        except Exception as e:
            messagebox.showerror("错误", f"读取目录失败：{e}")
            return
        self.new_names = [""] * len(self.files)
        self._refresh_ui()
        self.status.config(text=f"已读取 {len(self.files)} 个文件", fg="green")

    # ============== 功能 2：刷新界面 ==============
    def _refresh_ui(self):
        self.left_list.delete(0, tk.END)
        self.right_list.delete(0, tk.END)
        for name in self.files:
            self.left_list.insert(tk.END, name)
        for name in self.new_names:
            self.right_list.insert(tk.END, name if name else "（未搜索）")

        for w in self.check_inner.winfo_children():
            w.destroy()
        self.check_vars.clear()
        for i, name in enumerate(self.files):
            var = tk.BooleanVar(value=False)
            self.check_vars.append(var)
            tk.Checkbutton(self.check_inner, text=f"[{i+1:03d}] {name}",
                           variable=var, anchor="w",
                           font=("Microsoft YaHei", 9)).pack(fill=tk.X, padx=5)

    def _update_row(self, i):
        if i < self.right_list.size():
            self.right_list.delete(i)
            self.right_list.insert(i, self.new_names[i] or "（未搜索）")

    # ============== 长按全选 ==============
    def _on_press(self, event):
        if self._press_timer:
            self.root.after_cancel(self._press_timer)
        self._press_timer = self.root.after(800, self._long_press_fired)

    def _on_release(self, event):
        if self._press_timer:
            self.root.after_cancel(self._press_timer)
            self._press_timer = None

    def _long_press_fired(self):
        self._press_timer = None
        self.select_all()
        self.status.config(text="长按已触发：全选", fg="blue")

    def select_all(self):
        for v in self.check_vars:
            v.set(True)

    def deselect_all(self):
        for v in self.check_vars:
            v.set(False)

    # ============== 功能 3：搜索 ==============
    def start_search(self):
        if self.searching:
            return
        if not self.files:
            messagebox.showwarning("提示", "请先读取文件")
            return
        self.searching = True
        self.status.config(text="正在搜索...", fg="orange")
        threading.Thread(target=self._search_worker, daemon=True).start()

    def _search_worker(self):
        session = requests.Session()
        session.headers.update(HEADERS)

        for i, filename in enumerate(self.files):
            keyword = os.path.splitext(filename)[0]
            # 去掉常见后缀噪声，提高搜索命中率（可自行调整）
            clean_kw = re.sub(r"[\s\-_]+", " ", keyword).strip()
            url = SEARCH_URL.format(requests.utils.quote(clean_kw))
            try:
                r = session.get(url, timeout=20)
                r.raise_for_status()
                r.encoding = r.apparent_encoding or "utf-8"
                result = self._parse_result(r.text, clean_kw)
            except Exception as e:
                result = f"[搜索失败] {e.__class__.__name__}"
            self.new_names[i] = result
            self.root.after(0, self._update_row, i)
            self.root.after(0, self.status.config,
                            {"text": f"搜索 {i+1}/{len(self.files)}：{filename}",
                             "fg": "orange"})
            time.sleep(1)   # 间隔 1 秒

        self.root.after(0, self._search_done)

    def _search_done(self):
        self.searching = False
        self.status.config(text="搜索完成", fg="green")

    def _parse_result(self, html, keyword):
        """从搜索结果页面提取目标文件名。
        目标结构: <a href="/photos-index-aid-332613.html" title="XXX">XXX</a>
        """
        soup = BeautifulSoup(html, "html.parser")
        links = soup.select('a[href*="/photos-index-aid-"]')
        if not links:
            return "（未找到匹配结果）"

        # 提取 title 属性（优先），否则用标签文本
        candidates, seen = [], set()
        for a in links:
            t = (a.get("title") or a.get_text(strip=True) or "").strip()
            if t and t not in seen:
                seen.add(t)
                candidates.append(t)

        if not candidates:
            return "（未找到匹配结果）"

        kw_low = keyword.lower()
        # 1. 完全匹配
        for c in candidates:
            if c.lower() == kw_low:
                return c
        # 2. 包含匹配
        for c in candidates:
            if kw_low in c.lower():
                return c
        # 3. 相似度最高
        best = difflib.get_close_matches(keyword, candidates, n=1, cutoff=0.2)
        return best[0] if best else candidates[0]

    # ============== 功能 4：确认重命名 ==============
    def _sanitize(self, name):
        name = re.sub(ILLEGAL, "_", name).strip().rstrip(".")
        return name

    def confirm_rename(self):
        selected = [i for i, v in enumerate(self.check_vars) if v.get()]
        if not selected:
            messagebox.showinfo("提示", "没有勾选任何文件")
            return

        valid = []
        for i in selected:
            new = (self.new_names[i] or "").strip()
            if new and not new.startswith(("[", "（未找到")):
                valid.append(i)

        if not valid:
            messagebox.showinfo("提示", "勾选的文件中，没有可用的新文件名")
            return

        preview_lines = []
        for i in valid[:12]:
            preview_lines.append(f"{self.files[i]}\n    ➜  {self.new_names[i]}")
        if len(valid) > 12:
            preview_lines.append(f"... 还有 {len(valid)-12} 个")
        preview = "\n".join(preview_lines)

        if not messagebox.askyesno(
                "确认重命名",
                f"即将重命名 {len(valid)} 个文件：\n\n{preview}\n\n确定执行？"):
            return

        ok, fail = 0, []
        for i in valid:
            old_name = self.files[i]
            old_path = os.path.join(self.directory, old_name)
            ext = os.path.splitext(old_name)[1]
            new_base = self._sanitize(self.new_names[i])
            new_name = new_base if new_base.lower().endswith(ext.lower()) else new_base + ext
            new_path = os.path.join(self.directory, new_name)

            # 避免重名
            if os.path.exists(new_path) and new_path != old_path:
                stem, e = os.path.splitext(new_name)
                k = 1
                while os.path.exists(os.path.join(self.directory, f"{stem}_{k}{e}")):
                    k += 1
                new_name = f"{stem}_{k}{e}"
                new_path = os.path.join(self.directory, new_name)

            try:
                os.rename(old_path, new_path)
                self.files[i] = new_name
                ok += 1
            except Exception as e:
                fail.append(f"{old_name}: {e}")

        self._refresh_ui()
        msg = f"成功重命名 {ok} 个文件"
        if fail:
            msg += f"，失败 {len(fail)} 个"
            messagebox.showwarning("部分失败", "\n".join(fail[:8]))
        self.status.config(text=msg, fg="green")


if __name__ == "__main__":
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    FileRenameTool(root)
    root.mainloop()