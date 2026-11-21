#!/usr/bin/env python3
"""Find each competitor's next upcoming competition (accepted registrations only).

Usage: python3 fetch_registrations.py <output_dir>
Writes <output_dir>/next_comps.csv. Never fails the build: if nothing usable is found
the file is removed and the site simply hides the "Next comp" lines.
"""
import sys, os, re, json, csv, time, datetime, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get('WCA_BASE', 'https://www.worldcubeassociation.org').rstrip('/')
UA = 'pr-streaks-site (+https://github.com/EonicMystic/pr-streaks)'
ID_RE = re.compile(r'/persons/(\d{4}[A-Z]{4}\d{2})')
DEADLINE = time.time() + 15 * 60


def get(path, tries=3):
    for k in range(tries):
        if time.time() > DEADLINE:
            return 0, ''
        try:
            req = urllib.request.Request(BASE + path, headers={'User-Agent': UA, 'Accept': 'application/json, text/html'})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and k < tries - 1:
                time.sleep(2 * (k + 1)); continue
            return e.code, ''
        except Exception:
            if k < tries - 1:
                time.sleep(2 * (k + 1)); continue
            return 0, ''
    return 0, ''


def jload(text):
    try:
        return json.loads(text)
    except Exception:
        return None


def list_comps(today):
    comps = {}
    for page in range(1, 120):
        st, body = get(f'/api/v0/competitions?start={today}&page={page}')
        data = jload(body) if st == 200 else None
        if not isinstance(data, list) or not data:
            break
        for c in data:
            if c.get('id') and (c.get('end_date') or c.get('start_date') or '') >= today:
                comps[c['id']] = c
    return comps


def registrants(cid):
    # 1) public WCIF
    st, body = get(f'/api/v0/competitions/{cid}/wcif/public')
    d = jload(body) if st == 200 else None
    if isinstance(d, dict) and isinstance(d.get('persons'), list):
        out = set()
        for p in d['persons']:
            w = p.get('wcaId')
            reg = p.get('registration')
            status = reg.get('status') if isinstance(reg, dict) else None
            if w and status in (None, 'accepted'):
                out.add(w)
        return out, 'wcif'
    # 2) registrations JSON
    st, body = get(f'/api/v0/competitions/{cid}/registrations')
    d = jload(body) if st == 200 else None
    if isinstance(d, list):
        out = set()
        for it in d:
            if not isinstance(it, dict):
                continue
            w = it.get('wca_id') or (it.get('user') or {}).get('wca_id')
            status = it.get('status') or (it.get('competing') or {}).get('registration_status')
            if w and status in (None, 'accepted'):
                out.add(w)
        if out:
            return out, 'json'
    # 3) public registrations page
    st, body = get(f'/competitions/{cid}/registrations')
    if st == 200:
        out = set(ID_RE.findall(body))
        if out:
            return out, 'html'
    return None, 'none'


def main():
    out_dir = sys.argv[1]
    path = os.path.join(out_dir, 'next_comps.csv')
    today = datetime.date.today().isoformat()
    comps = list_comps(today)
    print(f'upcoming competitions found: {len(comps)}')

    def work(cid):
        time.sleep(0.1)
        return cid, registrants(cid)

    results = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for cid, (ids, src) in ex.map(work, list(comps)):
            results[cid] = (ids, src)

    sources = {}
    for ids, src in results.values():
        sources[src] = sources.get(src, 0) + 1
    print('registration sources:', sources)

    nxt = {}
    for cid in sorted(comps, key=lambda c: (comps[c].get('start_date') or '', c)):
        ids = results[cid][0]
        if not ids:
            continue
        c = comps[cid]
        for w in ids:
            if w not in nxt:
                nxt[w] = [w, c.get('name', cid), cid, c.get('start_date', ''), c.get('end_date', ''),
                          c.get('city', ''), c.get('country_iso2', '')]
    used = sum(1 for v in results.values() if v[0])
    print(f'competitions with registrations: {used}; people with a next comp: {len(nxt)}')
    if not nxt:
        if os.path.exists(path):
            os.remove(path)
        print('nothing usable found; the site will hide next-comp lines')
        return
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['WCA ID', 'Competition', 'Comp ID', 'Start date', 'End date', 'City', 'Country code'])
        w.writerows(sorted(nxt.values()))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('registration fetch skipped:', e)
