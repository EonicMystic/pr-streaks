#!/usr/bin/env python3
"""Build top-500 'longest streak of competitions with a PR' lists from the WCA results export.

Usage: python3 build_streaks.py <WCA_export_tsv.zip | folder_with_tsvs> <output_dir> [history_dir]
"""
import csv, datetime, glob, json, os, sys, tempfile, zipfile
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict

src, out_dir = sys.argv[1], sys.argv[2]
# How many rows each list keeps (everyone tied at the cutoff rank is kept too)
LIMITS = {'top_world_all.csv': 250, 'top_world_active.csv': 100,
          'top_usa_all.csv': 150, 'top_usa_active.csv': 50}
SNAP_MIN = 30      # each daily snapshot remembers every ACTIVE streak of at least this length
WINDOW_DAYS = 7    # the Gained and Lost pages look back this many days
COUNTRY_MIN = 40   # the by-country pages list every non-US streak of at least this length
os.makedirs(out_dir, exist_ok=True)

tmp = None
if src.endswith('.zip'):
    tmp = tempfile.mkdtemp()
    with zipfile.ZipFile(src) as z:
        for name in ('competitions', 'persons', 'results'):
            z.extract(f'WCA_export_{name}.tsv', tmp)
        if 'metadata.json' in z.namelist():
            z.extract('metadata.json', tmp)
    D = tmp
else:
    D = src

def rows(name):
    with open(f'{D}/WCA_export_{name}.tsv', encoding='utf-8', errors='replace') as f:
        header = f.readline().rstrip('\n').split('\t')
        idx = {h: i for i, h in enumerate(header)}
        n = len(header)
        for line in f:
            p = line.rstrip('\n').split('\t')
            if len(p) == n:
                yield idx, p

try:
    _stamp = json.load(open(f'{D}/metadata.json')).get('export_date', '')
except Exception:
    _stamp = ''
_export_day = datetime.date.fromisoformat(_stamp[:10]) if _stamp[:10] else datetime.date.today()
_cut = _export_day - datetime.timedelta(days=365)
CUTOFF = (_cut.year, _cut.month, _cut.day)      # "last 12 months" means comps after this date
GAIN_DAYS = [7, 30, 90, 180, 365]                # windows for the 'streak gained' tab
GAIN_MIN_RUN = 3                                 # only running streaks of at least this many comps are listed
_gcuts = [_export_day - datetime.timedelta(days=d) for d in GAIN_DAYS]
_gcuts = [(c.year, c.month, c.day) for c in _gcuts]

comp_date, comp_name = {}, {}
for idx, p in rows('competitions'):
    comp_date[p[idx['id']]] = (int(p[idx['year']]), int(p[idx['month']]), int(p[idx['day']]))
    comp_name[p[idx['id']]] = p[idx['name']]

BIG = 10**9
pe = defaultdict(dict)   # (person, event) -> comp -> [best, average]
att = defaultdict(set)
for idx, p in rows('results'):
    comp = p[idx['competition_id']]
    if comp not in comp_date:
        continue
    pid = p[idx['person_id']]
    att[pid].add(comp)
    b, a = int(p[idx['best']]), int(p[idx['average']])
    d = pe[(pid, p[idx['event_id']])].setdefault(comp, [BIG, BIG])
    if 0 < b < d[0]: d[0] = b
    if 0 < a < d[1]: d[1] = a

key = lambda c: (comp_date[c], c)
pr = defaultdict(set)
for (pid, ev), comps in pe.items():
    br = ar = BIG
    for c in sorted(comps, key=key):
        bm, am = comps[c]
        ok = False
        if bm < BIG and bm <= br: ok = True; br = bm   # ties count as a PR
        if am < BIG and am <= ar: ok = True; ar = am
        if ok: pr[pid].add(c)

people = {}
for idx, p in rows('persons'):
    w = p[idx['wca_id']]
    if p[idx['sub_id']] == '1' or w not in people:
        people[w] = (p[idx['name']], p[idx['country_id']])

results = []
for pid, comps in att.items():
    ordered = sorted(comps, key=key)
    prs = pr.get(pid, set())
    cur = 0; cs = None; best = 0; bs = be = None; bi = -1
    for i, c in enumerate(ordered):
        if c in prs:
            if cur == 0: cs = c
            cur += 1
            if cur > best: best, bs, be, bi = cur, cs, c, i
        else:
            cur = 0
    if best:
        name, country = people.get(pid, (pid, ''))
        still = (bi == len(ordered) - 1)            # the longest streak is still running
        recent = sum(1 for c in ordered if comp_date[c] > CUTOFF)
        broke = comp_name[ordered[bi + 1]] if not still else ''
        results.append(dict(wca_id=pid, name=name, country=country, streak=best,
                            start=comp_name[bs], end=comp_name[be], active=cur > 0, still=still,
                            run=cur, run_start=comp_name[cs] if cur > 0 else '',
                            total_comps=len(ordered), last_comp=comp_name[ordered[-1]], recent=recent,
                            broke_at=broke, current=cur,
                            gains=[sum(1 for c in ordered[len(ordered) - cur:] if comp_date[c] > g) for g in _gcuts] if cur >= GAIN_MIN_RUN else None,
                            lc=ordered[bi - best + 1:bi + 1] if best >= 3 else None,
                            cc=ordered[len(ordered) - cur:] if (best >= 3 and cur > 0 and not still) else None,
                            bk=ordered[bi + 1] if (best >= 3 and not still) else None))

# ---------- per-competition detail for streaks of 3+ (tap a dot on a person page) ----------
EVENTS = ['333', '222', '444', '555', '666', '777', '333bf', '333fm', '333oh', 'clock', 'minx', 'pyram',
          'skewb', 'sq1', '444bf', '555bf', '333mbf', 'magic', 'mmagic', '333mbo', '333ft']
EVBIT = {e: i for i, e in enumerate(EVENTS)}
need = {}
for r in results:
    if r['lc']:
        need[r['wca_id']] = set(r['lc']) | set(r['cc'] or ())
evs = defaultdict(dict)                 # person -> comp -> bitmask of events with a PR there
for (pid, ev), comps in pe.items():
    nd = need.get(pid)
    bit = EVBIT.get(ev)
    if nd is None or bit is None:
        continue
    br = ar = BIG
    for c in sorted(comps, key=key):
        bm, am = comps[c]
        ok = False
        if bm < BIG and bm <= br: ok = True; br = bm
        if am < BIG and am <= ar: ok = True; ar = am
        if ok and c in nd:
            evs[pid][c] = evs[pid].get(c, 0) | (1 << bit)
used = set()
for r in results:
    if r['lc']:
        used.update(r['lc']); used.update(r['cc'] or ())
        if r['bk']: used.add(r['bk'])
cidx = {c: i for i, c in enumerate(sorted(used, key=key))}
SHARDS = 256
def shard_of(w):
    h = 0
    for ch in w:
        h = (h * 31 + ord(ch)) % 1000003
    return h % SHARDS
def b36(n):
    d = '0123456789abcdefghijklmnopqrstuvwxyz'
    if n == 0: return '0'
    o = ''
    while n: n, m = divmod(n, 36); o = d[m] + o
    return o
sc_dir = os.path.join(out_dir, 'sc')
os.makedirs(sc_dir, exist_ok=True)
for f_ in os.listdir(sc_dir):
    os.remove(os.path.join(sc_dir, f_))
shards = defaultdict(list)
for r in results:
    if not r['lc']:
        continue
    m = evs.get(r['wca_id'], {})
    enc = lambda lst: ','.join(b36(cidx[c]) + '.' + b36(m.get(c, 0)) for c in lst)
    line = r['wca_id'] + '|' + enc(r['lc']) + '|' + (enc(r['cc']) if r['cc'] else '') + '|' + (b36(cidx[r['bk']]) if r['bk'] else '')
    shards[shard_of(r['wca_id'])].append(line)
for k, lines in shards.items():
    with open(os.path.join(sc_dir, f'{k}.txt'), 'w', encoding='utf-8') as f_:
        f_.write('\n'.join(lines))
with open(os.path.join(sc_dir, 'comps.json'), 'w', encoding='utf-8') as f_:
    json.dump([[comp_date[c][0] * 10000 + comp_date[c][1] * 100 + comp_date[c][2], comp_name[c]] for c in sorted(cidx, key=cidx.get)], f_, ensure_ascii=False, separators=(',', ':'))
print('streak detail shards:', len(shards), 'competitions used:', len(cidx))
for r in results:           # free the lists
    r['lc'] = r['cc'] = r['bk'] = None
del evs, need

def ranked(subset, fld='streak'):
    subset = sorted(subset, key=lambda r: (-r[fld], r['name']))
    out = []; rank = 0
    for i, r in enumerate(subset):
        if i == 0 or r[fld] != subset[i-1][fld]:
            rank = i + 1                    # ties share the best rank
        out.append((rank, r))
    return out

def write_list(fname, subset, show_active=True, show_pace=False, use_run=False):
    limit = LIMITS[fname]
    fld = 'run' if use_run else 'streak'
    ties = Counter(r[fld] for r in subset)
    header = ['Rank', 'Streak', 'Name', 'WCA ID', 'Country']
    if show_active: header.append('Still running')
    header += ['Streak start', 'Streak end']
    if show_pace: header += ['Comps in last 12 months', 'Comps per month']
    with open(os.path.join(out_dir, fname), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(header)
        for rank, r in ranked(subset, fld):
            if rank > limit: break
            row = [f"T-{rank}" if ties[r[fld]] > 1 else rank, r[fld], r['name'], r['wca_id'], r['country']]
            if show_active: row.append('Yes' if r['still'] else 'No')
            if use_run: row += [r['run_start'], '——']
            else: row += [r['start'], '——' if r['still'] else r['end']]
            if show_pace: row += [r['recent'], f"{r['recent'] / 12:.1f}"]
            w.writerow(row)

usa = lambda r: r['country'] == 'USA'
write_list('top_world_all.csv', results)
write_list('top_world_active.csv', [r for r in results if r['active']], show_active=False, show_pace=True, use_run=True)
write_list('top_usa_all.csv', [r for r in results if usa(r)])
write_list('top_usa_active.csv', [r for r in results if r['active'] and usa(r)], show_active=False, show_pace=True, use_run=True)

# ---------- what changed over the last 7 days ----------
try:
    stamp = json.load(open(f'{D}/metadata.json')).get('export_date', '')
except Exception:
    stamp = ''
today = (stamp[:10] or datetime.date.today().isoformat())
today_d = datetime.date.fromisoformat(today)

hist_dir = sys.argv[3] if len(sys.argv) > 3 else os.path.join(out_dir, 'history')
os.makedirs(hist_dir, exist_ok=True)
now_active = {r['wca_id']: r for r in results if r['active']}

# one small snapshot per export date: every active streak of SNAP_MIN or more
old_state = os.path.join(out_dir, 'state.csv')          # from the earlier version of this script
if os.path.exists(old_state):
    os.remove(old_state)

def read_snapshot(path):
    snap = {}
    with open(path, newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            snap[r['wca_id']] = dict(name=r['name'], country=r['country'], streak=int(r['streak']))
    return snap

snaps = {}
for p in glob.glob(os.path.join(hist_dir, 'state_*.csv')):
    d = os.path.basename(p)[6:-4]
    try:
        snaps[datetime.date.fromisoformat(d)] = p
    except ValueError:
        pass
earlier = sorted(d for d in snaps if d < today_d)
base_date = None
if earlier:
    target = today_d - datetime.timedelta(days=WINDOW_DAYS)
    ok = [d for d in earlier if d <= target]
    base_date = ok[-1] if ok else earlier[0]      # newest snapshot at least 7 days old, else the oldest we have
prev = read_snapshot(snaps[base_date]) if base_date else {}
since = base_date.isoformat() if base_date else ''

# write today's snapshot, then drop snapshots older than 10 days
with open(os.path.join(hist_dir, f'state_{today}.csv'), 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['wca_id', 'name', 'country', 'streak'])
    for k, r in sorted(now_active.items(), key=lambda kv: -kv[1]['run']):
        if r['run'] >= SNAP_MIN:
            w.writerow([k, r['name'], r['country'], r['run']])
for d, p in snaps.items():
    if d < today_d - datetime.timedelta(days=10):
        os.remove(p)

# sorted streak values for each board, to look up "what rank is a streak of N?"
boards = {
    'world_all':    sorted(r['streak'] for r in results),
    'world_active': sorted(r['run'] for r in results if r['active']),
    'usa_all':      sorted(r['streak'] for r in results if usa(r)),
    'usa_active':   sorted(r['run'] for r in results if r['active'] and usa(r)),
}
def rank_of(board, value):
    arr = boards[board]
    rank = len(arr) - bisect_right(arr, value) + 1             # ties share the best rank
    tied = bisect_right(arr, value) - bisect_left(arr, value) > 1
    return f'T-{rank}' if tied else rank
NA = '——'
by_id = {r['wca_id']: r for r in results}

# ---------- non-US countries: everyone with a streak of COUNTRY_MIN or more ----------
by_country = defaultdict(list)
for r in results:
    if r['country'] != 'USA' and r['streak'] >= COUNTRY_MIN:
        by_country[r['country']].append(r)
with open(os.path.join(out_dir, 'countries.csv'), 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Country', 'Streak', 'Name', 'WCA ID', 'Still running', 'World all-time rank', 'World active rank',
                'Comps in last 12 months', 'Streak start', 'Streak end'])
    for country in sorted(by_country):
        for r in sorted(by_country[country], key=lambda r: (-r['streak'], r['name'])):
            w.writerow([country, r['streak'], r['name'], r['wca_id'], 'Yes' if r['still'] else 'No',
                        rank_of('world_all', r['streak']),
                        rank_of('world_active', r['run']) if r['still'] else NA,
                        r['recent'], r['start'], '——' if r['still'] else r['end']])
with open(os.path.join(out_dir, 'country_summary.csv'), 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Country', f'People with a streak of {COUNTRY_MIN}+', f'Streaks of {COUNTRY_MIN}+ still running',
                'Best streak', 'Best streak still running'])
    rows_ = []
    for country, rs in by_country.items():
        act = [r['streak'] for r in rs if r['still']]
        rows_.append([country, len(rs), len(act), max(r['streak'] for r in rs), max(act) if act else NA])
    rows_.sort(key=lambda x: (-x[1], -x[3], x[0]))
    w.writerows(rows_)

# ---------- every competitor with at least one PR comp, for the website ----------
with open(os.path.join(out_dir, 'people.csv'), 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Name', 'WCA ID', 'Country', 'Streak', 'Active', 'Current'])
    for r in results:
        w.writerow([r['name'], r['wca_id'], r['country'], r['streak'], 1 if r['still'] else 0, r['run']])

# ---------- how much running streaks have grown over the last 7 / 30 / 90 / 180 / 365 days ----------
with open(os.path.join(out_dir, 'gains.csv'), 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Name', 'WCA ID', 'Country', 'Current'] + [f'G{d}' for d in GAIN_DAYS])
    for r in sorted(results, key=lambda r: (-r['run'], r['name'])):
        if r['gains'] and r['gains'][-1] > 0:
            w.writerow([r['name'], r['wca_id'], r['country'], r['run']] + r['gains'])

# ---------- extra detail for longer streaks (loaded by the site only when someone opens a person) ----------
with open(os.path.join(out_dir, 'details.csv'), 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['WCA ID', 'Streak start', 'Streak end', 'Broke at', 'Comps in last 12 months', 'Current run start'])
    for r in results:
        if r['streak'] >= 3:
            w.writerow([r['wca_id'], r['start'], r['end'], r['broke_at'], r['recent'], r['run_start']])

gained, lost = [], []
if prev:
    for k, r in now_active.items():
        if r['run'] < SNAP_MIN:
            continue
        p = prev.get(k)
        if p is None:
            before, gain = 'Under 30', NA          # was below the snapshot cutoff a week ago
        else:
            gain = r['run'] - p['streak']
            if gain <= 0:
                continue
            before = p['streak']
        is_us = r['country'] == 'USA'
        gained.append([r['name'], k, r['country'], before, r['run'], gain,
                       rank_of('world_active', r['run']), rank_of('world_all', r['streak']),
                       rank_of('usa_active', r['run']) if is_us else NA,
                       rank_of('usa_all', r['streak']) if is_us else NA, since])
    for k, p in prev.items():
        r = by_id.get(k)
        if r is not None and r['active'] and r['run'] >= p['streak']:
            continue                                      # still running at least as long as before
        final = max(p['streak'], r['streak']) if r else p['streak']      # their final streak number
        is_us = p['country'] == 'USA'
        lost.append([p['name'], k, p['country'], final,
                     rank_of('world_all', final), rank_of('usa_all', final) if is_us else NA,
                     r['start'] if r else '', r['end'] if r else '',
                     r['broke_at'] if r else '', since])
gained.sort(key=lambda x: (-x[4], x[0]))
lost.sort(key=lambda x: (-x[3], x[0]))

def write_rows(fname, header, rows, empty_msg):
    with open(os.path.join(out_dir, fname), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(header)
        if not prev:
            w.writerow(['Not enough history yet - this page fills in once a newer export arrives'] + [''] * (len(header) - 1))
        elif not rows:
            w.writerow([empty_msg] + [''] * (len(header) - 1))
        w.writerows(rows)

write_rows('gained.csv',
           ['Name', 'WCA ID', 'Country', 'Streak a week ago', 'New streak', 'Gained',
            'World active rank', 'World all-time rank', 'USA active rank', 'USA all-time rank', 'Compared with'],
           gained, 'Nobody gained a PR streak in this period')
write_rows('lost.csv',
           ['Name', 'WCA ID', 'Country', 'Final streak', 'World all-time rank', 'USA all-time rank',
            'Streak start', 'Streak end', 'Broke at (first comp without a PR)', 'Compared with'],
           lost, 'Nobody lost a streak in this period')


# ---------- home-screen icons and manifest (so the site can be added to a phone's Home Screen) ----------
def _png(size, path):
    import zlib, struct
    S = 3                                    # supersampling for smooth edges
    bg, green, ring = (14, 17, 22), (86, 211, 100), (18, 53, 31)
    n = 5
    r = size * 0.075
    gap = size * 0.165
    cx0 = size / 2 - gap * (n - 1) / 2
    cy = size / 2
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            cov_g = cov_r = 0
            for sy in range(S):
                for sx in range(S):
                    px, py = x + (sx + .5) / S, y + (sy + .5) / S
                    for k in range(n):
                        d = ((px - (cx0 + gap * k)) ** 2 + (py - cy) ** 2) ** .5
                        if d <= r:
                            cov_g += 1; break
                        if k == n - 1 and d <= r * 1.75:
                            cov_r += 1
            t = S * S
            g, rg = cov_g / t, cov_r / t
            col = [bg[i] * (1 - g - rg) + green[i] * g + ring[i] * rg for i in range(3)]
            row += bytes(int(round(c)) for c in col)
        rows.append(bytes(row))
    raw = b''.join(rows)
    def chunk(tag, data):
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0)) \
        + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')
    with open(path, 'wb') as f:
        f.write(png)

for _size, _name in ((180, 'apple-touch-icon.png'), (192, 'icon-192.png'), (512, 'icon-512.png')):
    _png(_size, os.path.join(out_dir, _name))
with open(os.path.join(out_dir, 'manifest.webmanifest'), 'w', encoding='utf-8') as f:
    json.dump({'name': 'PR Streak Rankings', 'short_name': 'PR Streaks', 'start_url': '../', 'scope': '../',
               'display': 'standalone', 'background_color': '#0e1116', 'theme_color': '#0e1116',
               'icons': [{'src': 'icon-192.png', 'sizes': '192x192', 'type': 'image/png'},
                         {'src': 'icon-512.png', 'sizes': '512x512', 'type': 'image/png'}]}, f, indent=1)

with open(os.path.join(out_dir, 'updated.csv'), 'w') as f:
    f.write(f'Data from WCA export\n{stamp}\n')
print('done', len(results), 'people;', len(gained), 'gained,', len(lost), 'lost; compared with', since or 'nothing yet', '; export', stamp)
