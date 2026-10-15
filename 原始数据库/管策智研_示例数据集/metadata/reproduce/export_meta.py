# -*- coding: utf-8 -*-
"""导出 PlanDB 元数据（不含 text 大字段）为 TSV，供抽样使用。"""
import sqlite3, io, csv, sys

DB = 'file:D:/SY-SITES/plandb/plandb.sqlite?mode=ro'
OUT = 'D:/_gszy_build/plans_meta.tsv'

con = sqlite3.connect(DB, uri=True)
cur = con.cursor()
sql = """SELECT planid, areaid, area_name, area_level, parent_areaid, parent_area_name,
                fypid, fyp_name, plantype, fileext, text_len, createtime, title
         FROM plans"""
n = 0
with io.open(OUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(['planid','areaid','area_name','area_level','parent_areaid','parent_area_name',
                'fypid','fyp_name','plantype','fileext','text_len','createtime','title'])
    for r in cur.execute(sql):
        w.writerow(['' if v is None else v for v in r])
        n += 1
print('exported rows:', n)
