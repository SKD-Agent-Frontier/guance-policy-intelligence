# -*- coding: utf-8 -*-
"""构建「管策智研」示例数据集。

输出: D:\管策智研_示例数据集\
  corpus/{planid}/        400 篇分层代表性样本
  edge_cases/{planid}/    100 篇边界压力样例
  metadata/               元数据 CSV / 清单 / 抽样方案 / 复现脚本
"""
import io, os, re, csv, json, shutil, sqlite3, html as HTM, subprocess, sys, collections

SRC   = r'D:\plandb\resources\upload'
DB    = 'file:D:/SY-SITES/plandb/plandb.sqlite?mode=ro'
OUT   = r'D:\管策智研_示例数据集'
SPEC  = r'D:\_gszy_build\sample.json'

PDF_CAP_MB   = 15.0     # 单篇 PDF 上限
DOCX_CAP_MB  = 8.0      # 单篇 Word 原文上限
MEDIA_CAP_MB = 10.0     # 单篇图片总量上限
IMG_EXT = {'png','jpeg','jpg','gif','bmp','tiff','tif','webp','emf','wmf'}
VECTOR  = {'emf','wmf'}

# ============================ HTML -> Markdown ============================
BLOCK = re.compile(r'<p\b[^>]*>.*?</p>|<table\b.*?</table>|\x00TBL\d+\x00', re.S | re.I)

def parse_css(style_text):
    """class -> {'size': pt, 'bold': bool}"""
    out = {}
    for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', style_text):
        sel, body = m.group(1), m.group(2)
        ms = re.search(r'font-size:\s*([\d.]+)\s*pt', body)
        size = float(ms.group(1)) if ms else None
        bold = bool(re.search(r'font-weight:\s*(bold|[6-9]00)', body))
        for cls in re.findall(r'\.([A-Za-z][A-Za-z0-9_\-]*)', sel):
            d = out.setdefault(cls, {'size': None, 'bold': False})
            if size is not None:
                d['size'] = size if d['size'] is None else max(d['size'], size)
            d['bold'] = d['bold'] or bold
    return out

def txt(frag):
    frag = re.sub(r'<br\s*/?>', '\n', frag, flags=re.I)
    frag = re.sub(r'<[^>]+>', '', frag)
    return HTM.unescape(frag).strip()

def table_md(tbl):
    rows = re.findall(r'<tr\b[^>]*>(.*?)</tr>', tbl, re.S | re.I)
    out = []
    for i, row in enumerate(rows):
        cells = re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', row, re.S | re.I)
        cells = [txt(c).replace('|', r'\|').replace('\n', ' ') for c in cells]
        if not cells:
            continue
        out.append('| ' + ' | '.join(cells) + ' |')
        if i == 0:
            out.append('|' + '---|' * len(cells))
    return '\n'.join(out)

def html_to_md(raw, areaid, planid):
    """返回 (markdown, 统计dict)"""
    st = {}
    sm = re.search(r'<style[^>]*>(.*?)</style>', raw, re.S | re.I)
    css = parse_css(sm.group(1)) if sm else {}
    bm = re.search(r'<body[^>]*>(.*?)</body>', raw, re.S | re.I)
    body = bm.group(1) if bm else raw

    tables = []
    def stash(m):
        tables.append(m.group(0)); return '\x00TBL%d\x00' % (len(tables) - 1)
    body = re.sub(r'<table\b.*?</table>', stash, body, flags=re.S | re.I)

    lines, n_h, n_img, n_tbl = [], 0, 0, len(tables)
    prefix = '/upload/%s/%s/' % (areaid, planid)
    for m in BLOCK.finditer(body):
        blk = m.group(0)
        if blk.startswith('\x00TBL'):
            idx = int(blk[4:-1])
            tmd = table_md(tables[idx])
            if tmd:
                lines.append(tmd)
            continue
        if blk[:6].lower().startswith('<table'):
            continue
        cm = re.search(r'<p\b[^>]*class="([^"]*)"', blk, re.I)
        classes = cm.group(1).split() if cm else []
        size, bold = 0.0, False
        for c in classes:
            d = css.get(c)
            if d:
                size = max(size, d['size'] or 0.0)
                bold = bold or d['bold']
        for sc in re.findall(r'<span\b[^>]*class="([^"]*)"', blk, re.I):
            for c in sc.split():
                d = css.get(c)
                if d:
                    size = max(size, d['size'] or 0.0)
                    bold = bold or d['bold']
        # 注意：Word 给每个标题段落都插入 <span id="_Toc…"> 书签锚点，
        # 不能用作目录判据；目录块由 CSS class（TOC2/TOC3…）标识。
        is_toc = 'TOC' in ' '.join(classes)
        # 图片
        for im in re.finditer(r'<img\b[^>]*>', blk, re.I):
            tag = im.group(0)
            src = (re.search(r'src="([^"]*)"', tag) or [None, ''])[1]
            alt = (re.search(r'id="\[([^\]]*)\]"', tag) or [None, ''])[1]
            fn = os.path.basename(src) if src else ''
            base = os.path.splitext(fn)[0]
            newfn = base + '.png' if os.path.splitext(fn)[1][1:].lower() in VECTOR else fn
            if fn:
                n_img += 1
                lines.append('![%s](media/%s)' % (alt or base, newfn))
        # 表格占位
        for tm in re.finditer(r'\x00TBL(\d+)\x00', blk):
            pass
        t = txt(blk)
        t = re.sub(r'\x00TBL\d+\x00', '', t).strip()
        if not t:
            continue
        CN = '一二三四五六七八九十百零〇'
        m_pian  = re.match(r'^第[%s\d]+篇' % CN, t)
        m_zhang = re.match(r'^第[%s\d]+章' % CN, t)
        m_jie   = re.match(r'^第[%s\d]+节' % CN, t)
        if is_toc and not (m_pian or m_zhang or m_jie):
            lines.append('- ' + t)
        elif m_pian:
            lines.append('# ' + t); n_h += 1
        elif m_zhang:
            lines.append('## ' + t); n_h += 1
        elif m_jie:
            lines.append('### ' + t); n_h += 1
        elif size >= 20:
            lines.append('# ' + t); n_h += 1
        elif size >= 14:
            lines.append('## ' + t); n_h += 1
        elif bold and size >= 13:
            lines.append('### ' + t); n_h += 1
        else:
            lines.append(t)
    md = '\n\n'.join(lines)
    md = re.sub(r'\x00TBL\d+\x00', '', md)
    md = re.sub(r'\n{3,}', '\n\n', md)
    st.update(n_headings=n_h, n_img_refs=n_img, n_tables=n_tbl)
    return md.strip(), st

# ============================ 工具 ============================
def safe(s, n=60):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', str(s or '')).strip(' .')
    return s[:n]

def emf_to_png(src, dst):
    ps = ('Add-Type -AssemblyName System.Drawing\n'
          '$i=[System.Drawing.Image]::FromFile("%s")\n'
          '$b=New-Object System.Drawing.Bitmap($i.Width,$i.Height)\n'
          '$g=[System.Drawing.Graphics]::FromImage($b)\n'
          '$g.Clear([System.Drawing.Color]::White)\n'
          '$g.DrawImage($i,0,0,$i.Width,$i.Height)\n'
          '$b.Save("%s",[System.Drawing.Imaging.ImageFormat]::Png)\n'
          '$g.Dispose();$b.Dispose();$i.Dispose()') % (src.replace('\\', '\\\\'), dst.replace('\\', '\\\\'))
    r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                       capture_output=True)
    return r.returncode == 0 and os.path.exists(dst)

# ============================ 主流程 ============================
def main():
    spec = json.load(io.open(SPEC, encoding='utf-8'))
    corpus, edge, defects = spec['corpus'], spec['edge'], spec['defects']
    allrows = {}
    for r in corpus + edge + defects:
        allrows[r['planid']] = r

    if os.path.isdir(OUT):
        # 只清理数据子目录，保留可能已存在的 README
        for d in ('corpus', 'edge_cases', 'metadata'):
            p = os.path.join(OUT, d)
            if os.path.isdir(p): shutil.rmtree(p)
    for d in ('corpus', 'edge_cases', 'metadata'):
        os.makedirs(os.path.join(OUT, d), exist_ok=True)

    con = sqlite3.connect(DB, uri=True); cur = con.cursor()
    pids = sorted(allrows, key=int)
    texts = {}
    for i in range(0, len(pids), 50):
        ch = pids[i:i + 50]
        ph = ','.join('?' * len(ch))
        for r in cur.execute('SELECT planid, text FROM plans WHERE planid IN (%s)' % ph, ch):
            texts[r[0]] = r[1] or ''
    print('fetched texts:', len(texts))

    manifest = []
    stats = collections.Counter()
    def rec(row, bucket):
        pid, area = row['planid'], row['areaid']
        src_dir = os.path.join(SRC, area)
        dst = os.path.join(OUT, bucket, pid)
        os.makedirs(dst, exist_ok=True)
        source_parts = []

        # ---- L1 正文 ----
        html_path = os.path.join(src_dir, pid + '.html')
        md_body, hst = '', {}
        if os.path.exists(html_path) and row['html_real']:
            try:
                raw = io.open(html_path, encoding='utf-8', errors='replace').read()
                md_body, hst = html_to_md(raw, area, pid)
                source_parts.append('html')
            except Exception as e:
                print('  html fail', pid, e)
                md_body = ''
        if not md_body:
            md_body = texts.get(pid, '').strip()
            source_parts.append('dbtxt')

        fm = [
            '---',
            'planid: "%s"' % pid,
            'title: "%s"' % (row['title'] or '').replace('"', "'").strip(),
            'area_name: "%s"' % row['area_name'],
            'area_level: "%s"' % row['area_level'],
            'admin_level: "%s"' % row['level'],
            'parent_area_name: "%s"' % (row['parent_area_name'] or ''),
            'fyp_name: "%s"' % row['fyp_name'],
            'plantype: "%s"' % row['plantype'],
            'plantype_label: "%s"' % {'1': '总体规划纲要', '2': '专项规划', '3': '其他'}.get(row['plantype'], ''),
            'library_fileext: "%s"' % row['fileext'],
            'content_source: "%s"' % ('html' if 'html' in source_parts else 'dbtxt'),
            'text_len: %d' % row['text_len'],
        ]
        if bucket == 'edge_cases':
            fm.append('edge_case: "%s"' % row.get('edge_tag', ''))
        fm.append('---')
        md = '\n'.join(fm) + '\n\n' + md_body + '\n'
        with io.open(os.path.join(dst, pid + '.md'), 'w', encoding='utf-8', newline='\n') as f:
            f.write(md)
        stats['md'] += 1; stats['md_chars'] += len(md_body)

        # ---- L2 HTML ----
        if row['html_real'] and os.path.exists(html_path):
            shutil.copy2(html_path, os.path.join(dst, pid + '.html'))
            stats['html'] += 1

        # ---- L3 原文 ----
        odir = os.path.join(dst, 'origin')
        for fn in os.listdir(src_dir):
            if not fn.lower().startswith(pid.lower()):
                continue
            base = os.path.splitext(fn)[0]
            if base not in (pid, pid + '-Origin'):
                continue
            ext = os.path.splitext(fn)[1][1:].lower()
            if ext not in ('docx', 'doc', 'pdf'):
                continue
            p = os.path.join(src_dir, fn)
            sz = os.path.getsize(p)
            # PDF 收录规则：仅当该篇没有真实 HTML 时收录（否则正文已由 HTML/text 覆盖，PDF 冗余）
            if ext == 'pdf':
                if row['html_real']:
                    manifest.append([pid, 'SKIP', 'origin/' + fn, sz,
                                     '该篇已有格式化 HTML 正文，PDF 按规则不收录'])
                    stats['pdf_redundant'] += 1
                    continue
                if sz > PDF_CAP_MB * 1048576:
                    manifest.append([pid, 'SKIP', 'origin/' + fn, sz, 'PDF 超 %.0fMB 上限未收录' % PDF_CAP_MB])
                    stats['pdf_skipped'] += 1
                    continue
            if ext in ('docx', 'doc') and sz > DOCX_CAP_MB * 1048576:
                manifest.append([pid, 'SKIP', 'origin/' + fn, sz, 'Word 超 %.0fMB 上限未收录' % DOCX_CAP_MB])
                stats['docx_skipped'] += 1
                continue
            os.makedirs(odir, exist_ok=True)
            shutil.copy2(p, os.path.join(odir, fn))
            stats['origin'] += 1
            manifest.append([pid, 'L3-origin', 'origin/' + fn, sz, ''])

        # ---- L4 图片 ----
        mdir = os.path.join(src_dir, pid, 'word', 'media')
        if os.path.isdir(mdir):
            files = []
            for fn in os.listdir(mdir):
                p = os.path.join(mdir, fn)
                if os.path.isfile(p) and fn.rsplit('.', 1)[-1].lower() in IMG_EXT:
                    files.append((fn, os.path.getsize(p)))
            files.sort(key=lambda x: (-x[1], x[0]))
            used, kept, conv = 0, 0, 0
            dstm = os.path.join(dst, 'media')
            for fn, sz in files:
                if used + sz > MEDIA_CAP_MB * 1048576:
                    manifest.append([pid, 'SKIP', 'media/' + fn, sz, '图片超单篇 %.0fMB 上限未收录' % MEDIA_CAP_MB])
                    stats['media_skipped'] += 1
                    continue
                os.makedirs(dstm, exist_ok=True)
                ext = fn.rsplit('.', 1)[-1].lower()
                if ext in VECTOR:
                    base = os.path.splitext(fn)[0]
                    tgt = os.path.join(dstm, base + '.png')
                    if emf_to_png(p, tgt):
                        kept += 1; used += sz; conv += 1
                        stats['vec_converted'] += 1
                        manifest.append([pid, 'L4-media', 'media/' + base + '.png', sz,
                                         '由 %s 转换（原矢量格式）' % fn])
                    else:
                        shutil.copy2(p, os.path.join(dstm, fn)); kept += 1; used += sz
                        manifest.append([pid, 'L4-media', 'media/' + fn, sz, '矢量格式转换失败，原样保留'])
                        stats['vec_failed'] += 1
                else:
                    shutil.copy2(p, os.path.join(dstm, fn))
                    kept += 1; used += sz
                    manifest.append([pid, 'L4-media', 'media/' + fn, sz, ''])
            if kept:
                stats['plans_with_media'] += 1
            stats['media_files'] += kept

        stats['plans'] += 1

    for r in corpus:   rec(r, 'corpus')
    for r in edge:     rec(r, 'edge_cases')
    for r in defects:  rec(r, 'edge_cases')

    with io.open(os.path.join(OUT, 'metadata', 'file_manifest.csv'), 'w',
                 encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f); w.writerow(['planid', 'layer', 'path', 'bytes', 'note'])
        w.writerows(manifest)

    # ---------- 元数据 CSV ----------
    def bucket_of(pid):
        if any(r['planid'] == pid for r in corpus):   return 'corpus'
        if any(r['planid'] == pid for r in defects):  return 'edge_cases/defect'
        return 'edge_cases'

    PT = {'1': '总体规划纲要', '2': '专项规划', '3': '其他'}
    plan_files = collections.defaultdict(lambda: collections.Counter())
    plan_bytes = collections.defaultdict(lambda: collections.Counter())
    for dp, _, fs in os.walk(OUT):
        for f in fs:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, OUT).replace('\\', '/')
            parts = rel.split('/')
            if len(parts) < 2 or parts[0] not in ('corpus', 'edge_cases'):
                continue
            pid = parts[1]
            ext = f.rsplit('.', 1)[-1].lower()
            lay = ('md' if ext == 'md' else 'html' if ext == 'html'
                   else 'media' if parts[2] == 'media' else 'origin')
            try: plan_bytes[pid][lay] += os.path.getsize(p)
            except OSError: pass
            plan_files[pid][lay] += 1

    fields = ['planid','split','edge_case','title','areaid','area_name','admin_level','parent_area_name',
              'fyp_name','plantype','plantype_label','library_fileext','text_len','content_source',
              'n_md','n_html','n_origin','n_media','bytes_md','bytes_html','bytes_origin','bytes_media']
    with io.open(os.path.join(OUT, 'metadata', 'sample_plans.csv'), 'w',
                 encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f); w.writerow(fields)
        for r in sorted(corpus + edge + defects, key=lambda x: (bucket_of(x['planid']), int(x['planid']))):
            pid = r['planid']
            pf, pb = plan_files.get(pid, {}), plan_bytes.get(pid, {})
            w.writerow([pid, bucket_of(pid).split('/')[0], r.get('edge_tag', ''),
                        r['title'], r['areaid'], r['area_name'], r['level'], r['parent_area_name'],
                        r['fyp_name'], r['plantype'], PT.get(r['plantype'], ''), r['fileext'], r['text_len'],
                        'html' if r['html_real'] else 'dbtxt',
                        pf.get('md', 0), pf.get('html', 0), pf.get('origin', 0), pf.get('media', 0),
                        pb.get('md', 0), pb.get('html', 0), pb.get('origin', 0), pb.get('media', 0)])

    # ---------- 抽样方案 ----------
    lv_ct = collections.Counter(r['level'] for r in corpus)
    era_ct = collections.Counter(r['era'] for r in corpus)
    spec_out = {
        'seed': spec['seed'],
        'generated_for': '首届“人文薪火”智能体创新应用大赛 · 管策智研 赛题示例数据集',
        'library_total_plans': 53161,
        'method': '分层随机抽样（最大余数法配额 + 下限修正 + 省域扩散上限）',
        'strata_dims': ['行政层级 admin_level', '规划期 era', '规划类型 plantype'],
        'targets': {'corpus': 400, 'edge_cases': 100, 'internal_test_pool_reserved': 300},
        'achieved': {'corpus': len(corpus), 'edge_cases': len(edge), 'defect_cases': len(defects)},
        'corpus_composition': {
            'by_admin_level': dict(lv_ct), 'by_era': dict(era_ct),
            'by_plantype': dict(collections.Counter(r['plantype'] for r in corpus)),
            'by_library_fileext': dict(collections.Counter(r['fileext'] for r in corpus)),
            'html_real': sum(1 for r in corpus if r['html_real']),
            'html_stub': sum(1 for r in corpus if r['html_stub']),
            'no_html': sum(1 for r in corpus if r['html_size'] == 0),
        },
        'library_reference_share': {
            'by_admin_level': {'国家': 422, '省': 4590, '市': 17230, '区县': 30919},
            'by_library_fileext': {'docx': 24237, 'html': 16650, 'pdf': 12009, 'doc': 265},
        },
        'edge_case_rules': dict(collections.Counter(r.get('edge_tag', '') for r in edge)),
        'inclusion_rules': {
            'L3_origin_pdf': '仅当该篇无真实 HTML 正文时收录；单文件 ≤ %g MB' % PDF_CAP_MB,
            'L3_origin_word': '单文件 ≤ %g MB' % DOCX_CAP_MB,
            'L4_media': '单篇图片总量 ≤ %g MB，按体积降序收录' % MEDIA_CAP_MB,
            'vector_images': 'emf/wmf 矢量图统一转换为 png',
        },
    }
    with io.open(os.path.join(OUT, 'metadata', 'sampling_spec.json'), 'w', encoding='utf-8') as f:
        json.dump(spec_out, f, ensure_ascii=False, indent=1)

    print(json.dumps(dict(stats), ensure_ascii=False, indent=1))
    for d in ('corpus', 'edge_cases'):
        tot = n = 0
        for dp, _, fs in os.walk(os.path.join(OUT, d)):
            for f in fs:
                try: tot += os.path.getsize(os.path.join(dp, f)); n += 1
                except OSError: pass
        print('%s: %d files, %.1f MB' % (d, n, tot / 1048576))

if __name__ == '__main__':
    main()
