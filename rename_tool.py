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

PH_EMPTY     = "（未搜索）"
PH_NOTFOUND  = "（未找到匹配结果）"
PREFIX_FAIL  = "[搜索失败]"

ILLEGAL = r'[\\/:*?"<>|]'


class App:
    def __init__(self, root):
        self.root = root
        root.title("批量文件搜索重命名")
        root.geometry("1250x820")
        self.directory = ""
        self.files = []
        self.searching = False
        self.check_vars = []
        self._press_timer = None
        self._build()

    # ---------------- UI ----------------
    def _build(self):
        top = tk.Frame(self.root); top.pack(fill=tk.X, padx=10, pady=8)
        tk.Label(top, text="目录：").pack(side=tk.LEFT)
        self.dir_entry = tk.Entry(top)
        self.dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        tk.Button(top, text="浏览", width=8, command=self.select_dir).pack(side=tk.LEFT)
        tk.Button(top, text="读取", width=8, command=self.load_files).pack(side=tk.LEFT, padx=5)

        mid = tk.Frame(self.root); mid.pack(fill=tk.BOTH, expand=True, padx=10)
        left = tk.LabelFrame(mid, text="① 原始文件名")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.left_list = tk.Listbox(left, font=("Microsoft YaHei", 10))
        self.left_list.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        right = tk.LabelFrame(mid, text="② 新文件名（双击可手动修改）")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(10, 0))
        self.right_list = tk.Listbox(right, font=("Microsoft YaHei", 10), fg="#1565C0")
        self.right_list.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.right_list.bind("<Double-Button-1>", self._edit_right)

        cf = tk.LabelFrame(self.root, text="③ 勾选需要重命名的项（长按此区域 0.8 秒全选）")
        cf.pack(fill=tk.X, padx=10, pady=5)
        self.check_canvas = tk.Canvas(cf, height=140)
        self.check_scroll = ttk.Scrollbar(cf, orient="vertical", command=self.check_canvas.yview)
        self.check_inner = tk.Frame(self.check_canvas)
        self.check_inner.bind("<Configure>",
            lambda e: self.check_canvas.configure(scrollregion=self.check_canvas.bbox("all")))
        self.check_canvas.create_window((0, 0), window=self.check_inner, anchor="nw")
        self.check_canvas.configure(yscrollcommand=self.check_scroll.set)
        self.check_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.check_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        lf = tk.LabelFrame(self.root, text="日志")
        lf.pack(fill=tk.X, padx=10, pady=5)
        self.log_text = tk.Text(lf, height=7, font=("Consolas", 9), state="disabled")
        ls = ttk.Scrollbar(lf, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=ls.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)
        ls.pack(side=tk.RIGHT, fill=tk.Y)

        bottom = tk.Frame(self.root); bottom.pack(fill=tk.X, padx=10, pady=8)
        self.status = tk.Label(bottom, text="就绪", fg="gray")
        self.status.pack(side=tk.LEFT)
        tk.Button(bottom, text="确认重命名", width=12, bg="#2196F3", fg="white",
                  command=self.confirm_rename).pack(side=tk.RIGHT, padx=3)
        tk.Button(bottom, text="开始搜索", width=12, bg="#4CAF50", fg="white",
                  command=self.start_search).pack(side=tk.RIGHT, padx=10)
        tk.Button(bottom, text="全不选", width=8, command=self.deselect_all).pack(side=tk.RIGHT, padx=3)
        tk.Button(bottom, text="全选", width=8, command=self.select_all).pack(side=tk.RIGHT, padx=3)

        for w in (self.check_canvas, self.check_inner):
            w.bind("<ButtonPress-1>", self._on_press)
            w.bind("<ButtonRelease-1>", self._on_release)

    # ---------------- 日志 ----------------
    def log(self, msg):
        def _do():
            self.log_text.configure(state="normal")
            self.log_text.insert(tk.END, str(msg) + "\n")
            self.log_text.see(tk.END)
            self.log_text.configure(state="disabled")
        if threading.current_thread() is threading.main_thread():
            _do()
        else:
            self.root.after(0, _do)

    # ---------------- 目录 & 载入 ----------------
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
            self.files = sorted(
                f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)))
        except Exception as e:
            messagebox.showerror("错误", str(e))
            return

        self.left_list.delete(0, tk.END)
        self.right_list.delete(0, tk.END)
        for f in self.files:
            self.left_list.insert(tk.END, f)
            self.right_list.insert(tk.END, PH_EMPTY)

        for w in self.check_inner.winfo_children():
            w.destroy()
        self.check_vars.clear()
        for i, f in enumerate(self.files):
            v = tk.BooleanVar(value=False)
            self.check_vars.append(v)
            tk.Checkbutton(self.check_inner, text=f"[{i+1:03d}] {f}",
                           variable=v, anchor="w",
                           font=("Microsoft YaHei", 9)).pack(fill=tk.X, padx=5)

        self.log(f"读取目录：{d}，共 {len(self.files)} 个文件")
        self.status.config(text=f"已读取 {len(self.files)} 个文件", fg="green")

    # ---------------- 右栏手动编辑 ----------------
    def _edit_right(self, event):
        idx = self.right_list.nearest(event.y)
        if idx < 0:
            return
        cur = self.right_list.get(idx)
        dlg = tk.Toplevel(self.root)
        dlg.title("编辑新文件名")
        dlg.geometry("640x100")
        dlg.transient(self.root)
        dlg.grab_set()
        e = tk.Entry(dlg, font=("Microsoft YaHei", 10))
        e.pack(fill=tk.X, padx=10, pady=10)
        e.insert(0, cur)
        e.focus_set()
        def ok(_=None):
            val = e.get()
            self.right_list.delete(idx)
            self.right_list.insert(idx, val)
            dlg.destroy()
        tk.Button(dlg, text="确定", command=ok).pack(pady=5)
        dlg.bind("<Return>", ok)

    # ---------------- 勾选 ----------------
    def _on_press(self, e):
        if self._press_timer:
            self.root.after_cancel(self._press_timer)
        self._press_timer = self.root.after(800, self._long_press_fired)

    def _on_release(self, e):
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
            kw = os.path.splitext(name)[0]
            kw = re.sub(r"[\s\-_]+", " ", kw).strip()
            url = SEARCH_URL.format(requests.utils.quote(kw))
            self.log(f"[{i+1}/{total}] {name}")
            try:
                r = s.get(url, timeout=20)
                self.log(f"    HTTP {r.status_code}，{len(r.text)} 字节")
                if r.status_code != 200:
                    result = f"{PREFIX_FAIL} HTTP {r.status_code}"
                else:
                    r.encoding = r.apparent_encoding or "utf-8"
                    result = self._parse(r.text, kw)
                    self.log(f"    → {result}")
            except Exception as e:
                result = f"{PREFIX_FAIL} {e.__class__.__name__}"
                self.log(f"    异常：{e}")

            self.root.after(0, self._set_right, i, result)
            self.root.after(0, self.status.config,
                            {"text": f"搜索 {i+1}/{total}", "fg": "orange"})
            time.sleep(1)
        self.root.after(0, self._search_done)

    def _search_done(self):
        self.searching = False
        self.status.config(text="搜索完成", fg="green")
        self.log("=== 搜索完成 ===")

    def _set_right(self, i, text):
        if i < self.right_list.size():
            self.right_list.delete(i)
            self.right_list.insert(i, text)

    def _parse(self, html, kw):
        soup = BeautifulSoup(html, "html.parser")
        links = soup.select('a[href*="/photos-index-aid-"]')
        self.log(f"    photos-index-aid 链接 {len(links)} 个")
        if not links:
            return PH_NOTFOUND
        cands = []
        for a in links:
            t = (a.get("title") or a.get_text(strip=True) or "").strip()
            if t and t not in cands:
                cands.append(t)
        if not cands:
            return PH_NOTFOUND
        self.log(f"    候选 {len(cands)} 个，首个：{cands[0][:60]}")
        low = kw.lower()
        for c in cands:
            if c.lower() == low:
                return c
        for c in cands:
            if low in c.lower():
                return c
        m = difflib.get_close_matches(kw, cands, n=1, cutoff=0.2)
        return m[0] if m else cands[0]

    # ---------------- 有效性判定（关键修复） ----------------
    def _is_valid_new(self, i):
        """返回 (是否有效, 原因)"""
        right = (self.right_list.get(i) or "").strip()
        if not right:
            return False, "空"
        if right == PH_EMPTY:
            return False, "未搜索"
        if right == PH_NOTFOUND:
            return False, "未找到"
        if right.startswith(PREFIX_FAIL):
            return False, "搜索失败"
        # ★ 不再用 startswith("[") 判断，因为真实文件名常以 [ 开头
        return True, ""

    def _sanitize(self, name):
        return re.sub(ILLEGAL, "_", name).strip().rstrip(".")

    # ---------------- 确认重命名 ----------------
    def confirm_rename(self):
        selected = [i for i, v in enumerate(self.check_vars) if v.get()]
        self.log(f"=== 确认重命名，勾选 {len(selected)} 项 ===")
        if not selected:
            messagebox.showinfo("提示", "没有勾选任何文件")
            return

        valid, skip_reasons = [], {}
        for i in selected:
            ok, reason = self._is_valid_new(i)
            old = self.left_list.get(i)
            new = self.right_list.get(i)
            if not ok:
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                self.log(f"  [跳过-{reason}] {old}  （右栏：{new}）")
                continue
            if new.strip() == old.strip():
                skip_reasons["名称相同"] = skip_reasons.get("名称相同", 0) + 1
                self.log(f"  [跳过-名称相同] {old}")
                continue
            valid.append(i)
            self.log(f"  [有效] {old}  →  {new}")

        if not valid:
            msg = f"勾选了 {len(selected)} 个文件，但没有可用的新文件名。\n\n"
            for r, n in skip_reasons.items():
                msg += f"· {n} 个：{r}\n"
            msg += "\n请查看下方日志了解具体原因。"
            messagebox.showinfo("提示", msg)
            return

        # 预览
        lines = []
        for i in valid[:12]:
            lines.append(f"{self.left_list.get(i)}\n    ➜  {self.right_list.get(i)}")
        if len(valid) > 12:
            lines.append(f"... 还有 {len(valid)-12} 个")
        preview = "\n".join(lines)
        extra = ""
        if skip_reasons:
            extra = "\n\n（跳过：" + "，".join(f"{n} 个{r}" for r, n in skip_reasons.items()) + "）"

        if not messagebox.askyesno("确认重命名",
                f"即将重命名 {len(valid)} 个文件：\n\n{preview}{extra}\n\n确定执行？"):
            return

        ok_n, fail = 0, []
        for i in valid:
            old_name = self.left_list.get(i)
            new_display = self.right_list.get(i).strip()
            old_path = os.path.join(self.directory, old_name)
            ext = os.path.splitext(old_name)[1]
            new_base = self._sanitize(new_display)
            new_name = new_base if new_base.lower().endswith(ext.lower()) else new_base + ext
            new_path = os.path.join(self.directory, new_name)

            if os.path.exists(new_path) and os.path.normcase(new_path) != os.path.normcase(old_path):
                stem, e = os.path.splitext(new_name)
                k = 1
                while os.path.exists(os.path.join(self.directory, f"{stem}_{k}{e}")):
                    k += 1
                new_name = f"{stem}_{k}{e}"
                new_path = os.path.join(self.directory, new_name)

            try:
                os.rename(old_path, new_path)
                ok_n += 1
                self.log(f"  ✓ {old_name}  →  {new_name}")
            except Exception as e:
                fail.append(f"{old_name}: {e}")
                self.log(f"  ✗ {old_name}: {e}")

        # 重新加载目录以刷新界面
        try:
            self.load_files()
        except Exception:
            pass

        msg = f"成功重命名 {ok_n} 个文件"
        if fail:
            msg += f"，失败 {len(fail)} 个"
            messagebox.showwarning("部分失败", "\n".join(fail[:8]))
        self.status.config(text=msg, fg="green")
        self.log(f"=== {msg} ===")


if __name__ == "__main__":
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App(root)
    root.mainloop()
