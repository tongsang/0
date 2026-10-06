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
ILLEGAL = r'[\\/:*?"<>|]'
TAG_NOTFOUND = "（未找到匹配结果）"
TAG_FAIL = "[搜索失败]"
TAG_EMPTY = ""


class FileRenameTool:
    def __init__(self, root):
        self.root = root
        self.root.title("批量文件搜索重命名工具")
        self.root.geometry("1200x800")

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

        # 日志区
        log_frame = tk.LabelFrame(self.root, text="日志")
        log_frame.pack(fill=tk.X, padx=10, pady=5)
        self.log_text = tk.Text(log_frame, height=7, font=("Consolas", 9), state="disabled")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

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

        self.check_canvas.bind("<ButtonPress-1>", self._on_press)
        self.check_canvas.bind("<ButtonRelease-1>", self._on_release)
        self.check_inner.bind("<ButtonPress-1>", self._on_press)
        self.check_inner.bind("<ButtonRelease-1>", self._on_release)

    # ============== 日志 ==============
    def log(self, msg):
        def _do():
            self.log_text.configure(state="normal")
            self.log_text.insert(tk.END, msg + "\n")
            self.log_text.see(tk.END)
            self.log_text.configure(state="disabled")
        if threading.current_thread() is threading.main_thread():
            _do()
        else:
            self.root.after(0, _do)

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
        self.log(f"读取目录：{d}")
        self.log(f"共 {len(self.files)} 个文件")
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
            display = self.new_names[i] if self.new_names[i] else "（未搜索）"
            self.right_list.insert(i, display)

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
        self.log("=" * 60)
        self.log("开始搜索...")
        self.status.config(text="正在搜索...", fg="orange")
        threading.Thread(target=self._search_worker, daemon=True).start()

    def _search_worker(self):
        session = requests.Session()
        session.headers.update(HEADERS)

        for i, filename in enumerate(self.files):
            keyword = os.path.splitext(filename)[0]
            clean_kw = re.sub(r"[\s\-_]+", " ", keyword).strip()
            url = SEARCH_URL.format(requests.utils.quote(clean_kw))
            self.log(f"[{i+1}/{len(self.files)}] {filename}")
            self.log(f"  URL: {url}")

            try:
                r = session.get(url, timeout=20)
                self.log(f"  HTTP {r.status_code}, 长度 {len(r.text)}")
                if r.status_code != 200:
                    result = f"{TAG_FAIL} HTTP {r.status_code}"
                else:
                    r.encoding = r.apparent_encoding or "utf-8"
                    result = self._parse_result(r.text, clean_kw)
                    self.log(f"  解析结果: {result[:80]}")
            except Exception as e:
                result = f"{TAG_FAIL} {e.__class__.__name__}: {e}"
                self.log(f"  异常: {e}")

            self.new_names[i] = result
            self.root.after(0, self._update_row, i)
            self.root.after(0, self.status.config,
                            {"text": f"搜索 {i+1}/{len(self.files)}：{filename}",
                             "fg": "orange"})
            time.sleep(1)

        self.root.after(0, self._search_done)

    def _search_done(self):
        self.searching = False
        self.status.config(text="搜索完成", fg="green")
        self.log("搜索完成")

    def _parse_result(self, html, keyword):
        soup = BeautifulSoup(html, "html.parser")

        # 主要选择器：匹配 <a href="/photos-index-aid-xxx.html" title="...">
        links = soup.select('a[href*="/photos-index-aid-"]')
        self.log(f"  · photos-index-aid 链接: {len(links)} 个")

        if not links:
            # 备用：任何带 title 的链接
            links = [a for a in soup.find_all("a", href=True)
                     if a.get("title") and len(a.get("title").strip()) > 2]
            self.log(f"  · 备用(带 title 的 a): {len(links)} 个")

        if not links:
            # 再备用：常见容器
            for sel in [".gallary_item .title a", ".title a", "a.title", ".info_box a"]:
                links = soup.select(sel)
                if links:
                    self.log(f"  · 选择器 {sel}: {len(links)} 个")
                    break

        candidates, seen = [], set()
        for a in links:
            t = (a.get("title") or a.get_text(strip=True) or "").strip()
            if t and t not in seen and len(t) > 2:
                seen.add(t)
                candidates.append(t)

        self.log(f"  · 候选名称: {len(candidates)} 个")
        if candidates:
            for c in candidates[:3]:
                self.log(f"      - {c[:70]}")

        if not candidates:
            return TAG_NOTFOUND

        kw_low = keyword.lower()
        for c in candidates:
            if c.lower() == kw_low:
                return c
        for c in candidates:
            if kw_low in c.lower():
                return c
        best = difflib.get_close_matches(keyword, candidates, n=1, cutoff=0.2)
        return best[0] if best else candidates[0]

    # ============== 功能 4：确认重命名 ==============
    def _sanitize(self, name):
        return re.sub(ILLEGAL, "_", name).strip().rstrip(".")

    def confirm_rename(self):
        selected = [i for i, v in enumerate(self.check_vars) if v.get()]
        if not selected:
            messagebox.showinfo("提示", "没有勾选任何文件")
            return

        # 分类统计
        valid, empty, failed, notfound = [], [], [], []
        for i in selected:
            new = (self.new_names[i] or "").strip()
            if not new:
                empty.append(i)
            elif new.startswith(TAG_FAIL):
                failed.append(i)
            elif new.startswith("（未找到"):
                notfound.append(i)
            else:
                valid.append(i)

        if not valid:
            msg = f"勾选了 {len(selected)} 个文件，但没有可用的新文件名。\n\n"
            if empty:
                msg += f"· {len(empty)} 个尚未搜索（请先点击“开始搜索”）\n"
            if failed:
                msg += f"· {len(failed)} 个搜索失败（网络/反爬）\n"
            if notfound:
                msg += f"· {len(notfound)} 个未找到匹配结果\n"
            msg += "\n请查看下方日志确认具体原因。"
            messagebox.showinfo("提示", msg)
            return

        preview_lines = []
        for i in valid[:12]:
            preview_lines.append(f"{self.files[i]}\n    ➜  {self.new_names[i]}")
        if len(valid) > 12:
            preview_lines.append(f"... 还有 {len(valid)-12} 个")
        preview = "\n".join(preview_lines)

        extra = ""
        if empty or failed or notfound:
            extra = (f"\n\n（另有 {len(empty)} 个未搜索、"
                     f"{len(failed)} 个搜索失败、{len(notfound)} 个未找到，将跳过）")

        if not messagebox.askyesno(
                "确认重命名",
                f"即将重命名 {len(valid)} 个文件：\n\n{preview}{extra}\n\n确定执行？"):
            return

        ok, fail = 0, []
        for i in valid:
            old_name = self.files[i]
            old_path = os.path.join(self.directory, old_name)
            ext = os.path.splitext(old_name)[1]
            new_base = self._sanitize(self.new_names[i])
            new_name = new_base if new_base.lower().endswith(ext.lower()) else new_base + ext
            new_path = os.path.join(self.directory, new_name)

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
                self.log(f"重命名: {old_name}  →  {new_name}")
            except Exception as e:
                fail.append(f"{old_name}: {e}")
                self.log(f"失败: {old_name} - {e}")

        self._refresh_ui()
        msg = f"成功重命名 {ok} 个文件"
        if fail:
            msg += f"，失败 {len(fail)} 个"
            messagebox.showwarning("部分失败", "\n".join(fail[:8]))
        self.status.config(text=msg, fg="green")

    # ============== 复选框状态判断辅助（可选） ==============
    def is_valid_new_name(self, name):
        if not name:
            return False
        name = name.strip()
        if not name:
            return False
        if name.startswith(TAG_FAIL) or name.startswith("（未找到"):
            return False
        return True


if __name__ == "__main__":
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    FileRenameTool(root)
    root.mainloop()
