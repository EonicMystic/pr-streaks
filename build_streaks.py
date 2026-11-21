#!/usr/bin/env python3
"""Build top-500 'longest streak of competitions with a PR' lists from the WCA results export.

Usage: python3 build_streaks.py <WCA_export_tsv.zip | folder_with_tsvs> <output_dir>
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
SEARCH_MIN = 5     # the public search page lists everyone with a streak of at least this length
LOOKUP_MIN = 15    # the name look-up file lists everyone with a streak of at least this length (kept small: Google Sheets refuses IMPORTDATA files over ~1 MB)
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
        active = (bi == len(ordered) - 1)
        recent = sum(1 for c in ordered if comp_date[c] > CUTOFF)
        broke = comp_name[ordered[bi + 1]] if not active else ''
        results.append(dict(wca_id=pid, name=name, country=country, streak=best,
                            start=comp_name[bs], end=comp_name[be], active=active,
                            total_comps=len(ordered), last_comp=comp_name[ordered[-1]], recent=recent,
                            broke_at=broke))

def ranked(subset):
    subset = sorted(subset, key=lambda r: (-r['streak'], r['name']))
    out = []; rank = 0
    for i, r in enumerate(subset):
        if i == 0 or r['streak'] != subset[i-1]['streak']:
            rank = i + 1                    # ties share the best rank
        out.append((rank, r))
    return out

def write_list(fname, subset, show_active=True, show_pace=False):
    limit = LIMITS[fname]
    ties = Counter(r['streak'] for r in subset)
    header = ['Rank', 'Streak', 'Name', 'WCA ID', 'Country']
    if show_active: header.append('Active')
    header += ['Streak start', 'Streak end']
    if show_pace: header += ['Comps in last 12 months', 'Comps per month']
    with open(os.path.join(out_dir, fname), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(header)
        for rank, r in ranked(subset):
            if rank > limit: break
            row = [f"T-{rank}" if ties[r['streak']] > 1 else rank, r['streak'], r['name'], r['wca_id'], r['country']]
            if show_active: row.append('Yes' if r['active'] else 'No')
            row += [r['start'], '——' if r['active'] else r['end']]
            if show_pace: row += [r['recent'], f"{r['recent'] / 12:.1f}"]
            w.writerow(row)

usa = lambda r: r['country'] == 'USA'
write_list('top_world_all.csv', results)
write_list('top_world_active.csv', [r for r in results if r['active']], show_active=False, show_pace=True)
write_list('top_usa_all.csv', [r for r in results if usa(r)])
write_list('top_usa_active.csv', [r for r in results if r['active'] and usa(r)], show_active=False, show_pace=True)

# ---------- what changed over the last 7 days ----------
try:
    stamp = json.load(open(f'{D}/metadata.json')).get('export_date', '')
except Exception:
    stamp = ''
today = (stamp[:10] or datetime.date.today().isoformat())
today_d = datetime.date.fromisoformat(today)

hist_dir = os.path.join(out_dir, 'history')
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
    for k, r in sorted(now_active.items(), key=lambda kv: -kv[1]['streak']):
        if r['streak'] >= SNAP_MIN:
            w.writerow([k, r['name'], r['country'], r['streak']])
for d, p in snaps.items():
    if d < today_d - datetime.timedelta(days=10):
        os.remove(p)

# sorted streak values for each board, to look up "what rank is a streak of N?"
boards = {
    'world_all':    sorted(r['streak'] for r in results),
    'world_active': sorted(r['streak'] for r in results if r['active']),
    'usa_all':      sorted(r['streak'] for r in results if usa(r)),
    'usa_active':   sorted(r['streak'] for r in results if r['active'] and usa(r)),
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
    w.writerow(['Country', 'Streak', 'Name', 'WCA ID', 'Active', 'World all-time rank', 'World active rank',
                'Comps in last 12 months', 'Streak start', 'Streak end'])
    for country in sorted(by_country):
        for r in sorted(by_country[country], key=lambda r: (-r['streak'], r['name'])):
            w.writerow([country, r['streak'], r['name'], r['wca_id'], 'Yes' if r['active'] else 'No',
                        rank_of('world_all', r['streak']),
                        rank_of('world_active', r['streak']) if r['active'] else NA,
                        r['recent'], r['start'], '——' if r['active'] else r['end']])
with open(os.path.join(out_dir, 'country_summary.csv'), 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Country', f'People with a streak of {COUNTRY_MIN}+', f'Active streaks of {COUNTRY_MIN}+',
                'Best streak', 'Best active streak'])
    rows_ = []
    for country, rs in by_country.items():
        act = [r['streak'] for r in rs if r['active']]
        rows_.append([country, len(rs), len(act), max(r['streak'] for r in rs), max(act) if act else NA])
    rows_.sort(key=lambda x: (-x[1], -x[3], x[0]))
    w.writerows(rows_)

# ---------- name look-up file (slim, so Google Sheets can import it) ----------
with open(os.path.join(out_dir, 'lookup.csv'), 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Name', 'WCA ID', 'Country', 'Best streak', 'World all-time rank', 'USA all-time rank',
                'Active', 'World active rank', 'USA active rank'])
    for r in sorted(results, key=lambda r: (-r['streak'], r['name'])):
        if r['streak'] < LOOKUP_MIN:
            break
        is_us = r['country'] == 'USA'
        w.writerow([r['name'], r['wca_id'], r['country'], r['streak'],
                    rank_of('world_all', r['streak']),
                    rank_of('usa_all', r['streak']) if is_us else NA,
                    'Yes' if r['active'] else 'No',
                    rank_of('world_active', r['streak']) if r['active'] else NA,
                    rank_of('usa_active', r['streak']) if (r['active'] and is_us) else NA])

# ---------- bigger file for the search web page (a web page has no 1 MB limit) ----------
with open(os.path.join(out_dir, 'search.csv'), 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Name', 'WCA ID', 'Country', 'Best streak', 'World all-time rank', 'USA all-time rank',
                'Active', 'World active rank', 'USA active rank', 'Comps in last 12 months',
                'Streak start', 'Streak end'])
    for r in sorted(results, key=lambda r: (-r['streak'], r['name'])):
        if r['streak'] < SEARCH_MIN:
            break
        is_us = r['country'] == 'USA'
        w.writerow([r['name'], r['wca_id'], r['country'], r['streak'],
                    rank_of('world_all', r['streak']),
                    rank_of('usa_all', r['streak']) if is_us else NA,
                    'Yes' if r['active'] else 'No',
                    rank_of('world_active', r['streak']) if r['active'] else NA,
                    rank_of('usa_active', r['streak']) if (r['active'] and is_us) else NA,
                    r['recent'], r['start'], '——' if r['active'] else r['end']])

gained, lost = [], []
if prev:
    for k, r in now_active.items():
        if r['streak'] < SNAP_MIN:
            continue
        p = prev.get(k)
        if p is None:
            before, gain = 'Under 30', NA          # was below the snapshot cutoff a week ago
        else:
            gain = r['streak'] - p['streak']
            if gain <= 0:
                continue
            before = p['streak']
        is_us = r['country'] == 'USA'
        gained.append([r['name'], k, r['country'], before, r['streak'], gain,
                       rank_of('world_active', r['streak']), rank_of('world_all', r['streak']),
                       rank_of('usa_active', r['streak']) if is_us else NA,
                       rank_of('usa_all', r['streak']) if is_us else NA, since])
    for k, p in prev.items():
        r = by_id.get(k)
        if r is not None and r['active']:
            continue
        final = r['streak'] if r else p['streak']      # their final streak number
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

with open(os.path.join(out_dir, 'updated.csv'), 'w') as f:
    f.write(f'Data from WCA export\n{stamp}\n')
print('done', len(results), 'people;', len(gained), 'gained,', len(lost), 'lost; compared with', since or 'nothing yet', '; export', stamp)
