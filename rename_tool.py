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
        root.geometry("1250x760")
        self.directory = ""
        self.files = []
        self.searching = False
        self._build()

    # ---------------- UI ----------------
    def _build(self):
        # 顶部：目录
        top = tk.Frame(self.root); top.pack(fill=tk.X, padx=10, pady=8)
        tk.Label(top, text="目录：").pack(side=tk.LEFT)
        self.dir_entry = tk.Entry(top)
        self.dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        tk.Button(top, text="浏览", width=8, command=self.select_dir).pack(side=tk.LEFT)
        tk.Button(top, text="读取", width=8, command=self.load_files).pack(side=tk.LEFT, padx=5)

        # 中部：左右两个列表
        mid = tk.Frame(self.root); mid.pack(fill=tk.BOTH, expand=True, padx=10)

        # 左：原始文件名（只读参考）
        left = tk.LabelFrame(mid, text="① 原始文件名（只读）")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.left_list = tk.Listbox(left, font=("Microsoft YaHei", 10),
                                    selectmode=tk.NONE, activestyle="none")
        self.left_list.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 右：新文件名（多选/框选主战场）
        right = tk.LabelFrame(
            mid,
            text="② 新文件名 —— 单击选中 / Shift+单击范围选 / Ctrl+单击加选 / 鼠标拖动划选")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(10, 0))
        self.right_list = tk.Listbox(
            right, font=("Microsoft YaHei", 10), fg="#1565C0",
            selectmode=tk.EXTENDED, selectbackground="#1976D2",
            selectforeground="white")
        self.right_list.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.right_list.bind("<Double-Button-1>", self._edit_right)

        # 底部操作栏
        bottom = tk.Frame(self.root); bottom.pack(fill=tk.X, padx=10, pady=8)
        self.status = tk.Label(bottom, text="就绪", fg="gray")
        self.status.pack(side=tk.LEFT)

        tk.Button(bottom, text="确认重命名选中行", width=18, bg="#2196F3", fg="white",
                  command=self.confirm_rename).pack(side=tk.RIGHT, padx=3)
        tk.Button(bottom, text="开始搜索", width=12, bg="#4CAF50", fg="white",
                  command=self.start_search).pack(side=tk.RIGHT, padx=10)
        tk.Button(bottom, text="清空选择", width=10,
                  command=self.clear_selection).pack(side=tk.RIGHT, padx=3)
        tk.Button(bottom, text="全选", width=8,
                  command=self.select_all).pack(side=tk.RIGHT, padx=3)

        # 日志
        lf = tk.LabelFrame(self.root, text="日志")
        lf.pack(fill=tk.X, padx=10, pady=(0, 8))
        self.log_text = tk.Text(lf, height=7, font=("Consolas", 9), state="disabled")
        ls = ttk.Scrollbar(lf, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=ls.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)
        ls.pack(side=tk.RIGHT, fill=tk.Y)

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
        dlg.geometry("720x110")
        dlg.transient(self.root)
        dlg.grab_set()
        e = tk.Entry(dlg, font=("Microsoft YaHei", 10))
        e.pack(fill=tk.X, padx=10, pady=10)
        e.insert(0, self.right_list.get(idx))
        e.focus_set()
        e.select_range(0, tk.END)
        def ok(_=None):
            self.right_list.delete(idx)
            self.right_list.insert(idx, e.get())
            self.right_list.selection_set(idx)
            dlg.destroy()
        tk.Button(dlg, text="确定", command=ok).pack(pady=5)
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
            kw = os.path.splitext(name)[0]
            kw = re.sub(r"[\s\-_]+", " ", kw).strip()
            url = SEARCH_URL.format(requests.utils.quote(kw))
            self.log(f"[{i+1}/{total}] {name}")
            try:
                r = s.get(url, timeout=20)
                self.log(f"    HTTP {r.status_code}，{len(r.text)} 字节")
                if r.status_code != 200:
                    result = ""
                else:
                    r.encoding = r.apparent_encoding or "utf-8"
                    result = self._parse(r.text, kw)
                    self.log(f"    → {result if result else '（无匹配）'}")
            except Exception as e:
                result = ""
                self.log(f"    异常：{e}")

            self.root.after(0, self._set_right, i, result)
            self.root.after(0, self.status.config,
                            {"text": f"搜索 {i+1}/{total}", "fg": "orange"})
            time.sleep(1)
        self.root.after(0, self._search_done)

    def _search_done(self):
        self.searching = False
        self.status.config(text="搜索完成，请在右侧列表中选中要重命名的行", fg="green")
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
            return ""
        cands = []
        for a in links:
            t = (a.get("title") or a.get_text(strip=True) or "").strip()
            if t and t not in cands:
                cands.append(t)
        if not cands:
            return ""
        low = kw.lower()
        for c in cands:
            if c.lower() == low:
                return c
        for c in cands:
            if low in c.lower():
                return c
        m = difflib.get_close_matches(kw, cands, n=1, cutoff=0.2)
        return m[0] if m else cands[0]

    # ---------------- 确认重命名（只处理选中行，原地更新 UI） ----------------
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

        # 直接取右栏当前文字
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

        # 预览
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
        updates = []   # (行索引, 新文件名) —— 用于原地刷新 UI

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

        # ===== 原地更新被重命名的行，其他行保持原样 =====
        for i, new_name in updates:
            # 更新左栏文字为新文件名
            self.left_list.delete(i)
            self.left_list.insert(i, new_name)
            # 右栏对应位置清空（表示已完成）
            self.right_list.delete(i)
            self.right_list.insert(i, "")
        # 清除选择
        self.right_list.selection_clear(0, tk.END)
        # 同步内存里的文件名列表，避免后续操作错位
        for i, new_name in updates:
            self.files[i] = new_name

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
