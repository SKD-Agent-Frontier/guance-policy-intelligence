# -*- coding: utf-8 -*-
"""扫描 upload 目录，建立 areaid/planid -> 磁盘文件清单。
输出: areaid, stem, ext, relpath, size
其中 stem 为文件名主干（期望等于 planid）。
"""
import os, io, csv, sys

ROOT = r'D:\plandb\resources\upload'
OUT = r'D:\_gszy_build\disk_files.tsv'
IMG_EXT = {'png','jpeg','jpg','gif','bmp','tiff','tif','webp','emf','wmf','svg'}
DOC_EXT = {'html','htm','pdf','docx','doc'}

n = 0
with io.open(OUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(['areaid','stem','ext','relpath','size','kind'])
    for area in os.listdir(ROOT):
        adir = os.path.join(ROOT, area)
        if not os.path.isdir(adir):
            continue
        for dp, dns, fns in os.walk(adir):
            for fn in fns:
                p = os.path.join(dp, fn)
                ext = fn.rsplit('.', 1)[-1].lower() if '.' in fn else ''
                stem = fn.rsplit('.', 1)[0] if '.' in fn else fn
                # -Origin.docx -> stem 取 planid
                if stem.endswith('-Origin'):
                    stem = stem[:-7]
                kind = 'doc' if ext in DOC_EXT else ('img' if ext in IMG_EXT else 'other')
                try:
                    sz = os.path.getsize(p)
                except OSError:
                    continue
                rel = os.path.relpath(p, ROOT)
                w.writerow([area, stem, ext, rel, sz, kind])
                n += 1
print('scanned files:', n)
