# -*- coding: utf-8 -*-
"""分层抽样：corpus 400 篇 + edge_cases 100 篇 + 内部 held-out 测试池 300 篇。
确定性种子，可复现。输出 planid 清单供打包脚本使用。
"""
import io, csv, re, json, random, collections

SEED = 20260914
N_CORPUS, N_EDGE, N_TEST = 400, 100, 300

def load(p):
    with io.open(p, encoding='utf-8') as f:
        return list(csv.DictReader(f, delimiter='\t'))

meta = {r['planid']: r for r in load(r'D:\_gszy_build\plans_meta.tsv')}
disk = load(r'D:\_gszy_build\disk_files.tsv')

# ---------- 文件归属 ----------
IMG = {'png','jpeg','jpg','gif','bmp','tiff','tif','webp','emf','wmf'}
agg = collections.defaultdict(lambda: {'html': 0, 'pdf': 0, 'docx': 0,
                                       'n_img': 0, 'img_bytes': 0, 'files': []})
for r in disk:
    parts = re.split(r'[\\/]', r['relpath'])
    if len(parts) < 2 or not parts[0].isdigit():
        continue
    stem = parts[1].rsplit('.', 1)[0] if '.' in parts[1] else parts[1]
    if stem.endswith('-Origin'):
        stem = stem[:-7]
    if not stem.isdigit():
        continue
    k = (parts[0], stem)
    e, s = r['ext'], int(r['size'])
    a = agg[k]
    if e in ('html', 'htm'): a['html'] = max(a['html'], s)
    elif e == 'pdf':         a['pdf'] = max(a['pdf'], s)
    elif e in ('docx','doc'):a['docx'] = max(a['docx'], s)
    elif e in IMG:
        a['n_img'] += 1; a['img_bytes'] += s
    a['files'].append((r['relpath'], e, s))

# ---------- 可用性 ----------
LVL = {3: '国家', 6: '省', 9: '市', 12: '区县'}
FYP = {'第十四个五年规划':'十四五', '第十三个五年规划':'十三五', '第十二个五年规划':'十二五',
       '第十一个五年规划':'十一五', '政府工作报告':'政府工作报告', '第十个五年计划':'十五计划'}

rows = []
for pid, m in meta.items():
    k = (m['areaid'], pid)
    a = agg.get(k)
    tl = int(m['text_len'] or 0)
    html = a['html'] if a else 0
    rows.append({
        'planid': pid, 'title': m['title'], 'areaid': m['areaid'],
        'area_name': m['area_name'], 'area_level': m['area_level'],
        'parent_area_name': m['parent_area_name'],
        'level': LVL.get(len(m['area_level']), '?'),
        'fypid': m['fypid'], 'fyp_name': m['fyp_name'],
        'era': FYP.get(m['fyp_name'], '其他'),
        'plantype': m['plantype'], 'fileext': m['fileext'],
        'text_len': tl,
        'html_size': html, 'html_real': html >= 20480, 'html_stub': 0 < html < 20480,
        'pdf_size': a['pdf'] if a else 0,
        'docx_size': a['docx'] if a else 0,
        'n_img': a['n_img'] if a else 0,
        'img_bytes': a['img_bytes'] if a else 0,
        'has_files': a is not None,
    })

usable = [r for r in rows if r['has_files'] and (r['text_len'] > 0 or r['html_real'])]
_uid = {r['planid'] for r in usable}
unusable = [r for r in rows if r['planid'] not in _uid]
print('total', len(rows), '| usable', len(usable), '| unusable', len(unusable))

# ---------- 配额分配：最大余数法 + 下限修正 ----------
def allocate(sizes, target, floor):
    """sizes: {key: n}；按比例分配 target，每格不低于 floor（若该格成员足够）"""
    tot = sum(sizes.values())
    alloc = {k: min(n, max(min(floor, n), int(target * n / tot))) for k, n in sizes.items()}
    for _ in range(100000):
        s = sum(alloc.values())
        if s == target: break
        if s < target:
            # 补最欠代表的格（缺口比最大）
            cand = [k for k in alloc if alloc[k] < sizes[k]]
            if not cand: break
            k = max(cand, key=lambda k: (target * sizes[k] / tot) - alloc[k])
            alloc[k] += 1
        else:
            # 砍最过代表的格（超出比最大，且不低于 floor）
            cand = [k for k in alloc if alloc[k] > min(floor, sizes[k])]
            if not cand: break
            k = max(cand, key=lambda k: alloc[k] - (target * sizes[k] / tot))
            alloc[k] -= 1
    return alloc

rng = random.Random(SEED)
CELLS = collections.defaultdict(list)
for r in usable:
    CELLS[(r['level'], r['era'])].append(r)
cell_alloc = allocate({k: len(v) for k, v in CELLS.items()}, N_CORPUS, floor=3)

# 格内按 plantype 再分配
alloc = {}
for k, v in CELLS.items():
    bypt = collections.defaultdict(list)
    for r in v: bypt[r['plantype']].append(r)
    sub = allocate({p: len(x) for p, x in bypt.items()}, cell_alloc[k], floor=1)
    for p, n in sub.items():
        alloc[(k[0], k[1], p)] = (n, bypt[p])
groups = {k: v[1] for k, v in alloc.items()}
alloc_n = {k: v[0] for k, v in alloc.items()}

# 抽样（省域扩散上限）
PROV_CAP = 22
picked, prov_used = [], collections.Counter()
for k in sorted(groups, key=lambda k: -len(groups[k])):
    pool = sorted(groups[k], key=lambda r: r['planid'])
    rng.shuffle(pool)
    n = 0
    for r in pool:
        if n >= alloc_n[k]: break
        pv = r['parent_area_name'] or r['area_name']
        if r['level'] != '国家' and prov_used[pv] >= PROV_CAP:
            continue
        picked.append(r); prov_used[pv] += 1; n += 1
# 第二轮：放宽省域上限，补齐剩余配额
picked_ids = {r['planid'] for r in picked}
if len(picked) < N_CORPUS:
    for k in sorted(groups, key=lambda k: -len(groups[k])):
        pool = sorted(groups[k], key=lambda r: r['planid'])
        rng.shuffle(pool)
        n = sum(1 for r in picked if (r['level'], r['era'], r['plantype']) == k)
        for r in pool:
            if len(picked) >= N_CORPUS: break
            if n >= alloc_n[k]: break
            if r['planid'] in picked_ids: continue
            picked.append(r); n += 1
corpus = picked
picked_ids = {r['planid'] for r in corpus}
print('corpus allocated:', sum(alloc_n.values()), '-> actually picked:', len(corpus))

# ---------- 边界样例 100 篇 ----------
rest = [r for r in usable if r['planid'] not in picked_ids]
rng.shuffle(rest)
EDGE_RULES = [
    ('E1-超长文档',        lambda r: r['text_len'] >= 100000, 15),
    ('E2-HTML空壳取自PDF', lambda r: r['html_stub'] and r['pdf_size'] > 0, 20),
    ('E3-图片密集',        lambda r: r['n_img'] >= 10, 15),
    ('E4-极短文档',        lambda r: 0 < r['text_len'] < 1500, 15),
    ('E5-早期规划期',      lambda r: r['era'] in ('十五计划','其他'), 15),
    ('E6-超长HTML',        lambda r: r['html_size'] >= 500000, 10),
    ('E7-仅有docx原文',    lambda r: r['html_size'] == 0 and r['docx_size'] > 0, 10),
]
edge, used = [], set()
for name, fn, cap in EDGE_RULES:
    c = 0
    for r in rest:
        if c >= cap: break
        if r['planid'] in used: continue
        if fn(r):
            edge.append(dict(r, edge_tag=name)); used.add(r['planid']); c += 1
# 补足到 100
for r in rest:
    if len(edge) >= N_EDGE: break
    if r['planid'] in used: continue
    edge.append(dict(r, edge_tag='E8-补充')); used.add(r['planid'])
edge = edge[:N_EDGE]

# ---------- held-out 测试池 300 篇（内部，不打包） ----------
used_all = picked_ids | {r['planid'] for r in edge}
pool = [r for r in usable if r['planid'] not in used_all]
rng.shuffle(pool)
test_pool = pool[:N_TEST]

# 显式缺陷样例（无正文）
defects = [r for r in rows if not (r['has_files'] and (r['text_len'] > 0 or r['html_real']))]
rng.shuffle(defects)
defect_pick = defects[:6]

out = {
    'seed': SEED,
    'counts': {'total_lib': len(rows), 'usable': len(usable), 'unusable': len(unusable),
               'corpus': len(corpus), 'edge': len(edge), 'test_pool': len(test_pool)},
    'corpus': corpus, 'edge': edge, 'defects': defect_pick,
    'test_pool_ids': [r['planid'] for r in test_pool],
}
with io.open(r'D:\_gszy_build\sample.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=1)

# 配额表摘要
def tab(rs):
    return collections.Counter((r['level'], r['era']) for r in rs)
print('\n--- corpus 层级x时期 ---')
t = tab(corpus)
lv = ['国家','省','市','区县']; er = ['十四五','十三五','十二五','十一五','政府工作报告','十五计划','其他']
print('        ' + ''.join(f'{e:>10}' for e in er) + '   合计')
for l in lv:
    print(f'{l:<8}' + ''.join(f'{t.get((l,e),0):>10}' for e in er) + f'{sum(t.get((l,e),0) for e in er):>7}')
print('\ncorpus plantype:', dict(collections.Counter(r['plantype'] for r in corpus)))
print('corpus fileext :', dict(collections.Counter(r['fileext'] for r in corpus)))
print('corpus html_real/stub/none:', sum(r['html_real'] for r in corpus),
      sum(r['html_stub'] for r in corpus), sum(1 for r in corpus if r['html_size']==0))
print('corpus with images:', sum(1 for r in corpus if r['n_img']>0),
      '| img MB:', round(sum(r['img_bytes'] for r in corpus)/1048576,1))
print('corpus text chars:', sum(r['text_len'] for r in corpus))
print('\nedge tags:', dict(collections.Counter(r['edge_tag'] for r in edge)))
print('defect ids:', [r['planid'] for r in defect_pick])
