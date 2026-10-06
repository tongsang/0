def _search_one(self, session, keyword_raw):
    kw = re.sub(r"[\s\-_]+", " ", keyword_raw).strip()
    if not kw:
        return ""

    encoded = requests.utils.quote(kw)
    seen_titles = set()
    all_items = []      # [(title, pages), ...] 去重后的全部候选

    for page_num in range(1, MAX_PAGES + 1):
        if page_num == 1:
            url = SEARCH_BASE.format(encoded)
        else:
            url = f"{SEARCH_BASE.format(encoded)}&p={page_num}"

        try:
            r = session.get(url, timeout=20)
            self.log(f"      【第{page_num}页】HTTP {r.status_code}，{len(r.text)} 字节")
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

        page_items = []     # [(title, pages), ...] 本页去重后
        page_seen = set()
        for a in links:
            t = (a.get("title") or a.get_text(strip=True) or "").strip()
            if not t or t in page_seen:
                continue
            page_seen.add(t)
            pages = self._extract_pages(a)
            page_items.append((t, pages))

        if not page_items:
            self.log(f"      第{page_num}页无标题，停止翻页")
            break

        new_items = [(t, p) for t, p in page_items if t not in seen_titles]
        if not new_items:
            self.log(f"      第{page_num}页没有新文件名，停止翻页")
            break

        seen_titles.update(page_seen)
        all_items.extend(new_items)
        self.log(f"      第{page_num}页：{len(page_items)} 条"
                 f"（其中 {len(new_items)} 条是新的，累计 {len(all_items)} 条）")

        time.sleep(PAGE_DELAY)

    if not all_items:
        return ""

    # 全部候选里选最优
    return self._pick_best(all_items, kw)
