#!/usr/bin/env python3
"""Find each competitor's next upcoming competition (accepted registrations only).

Usage: python3 fetch_registrations.py <output_dir>
Writes <output_dir>/next_comps.csv. Never fails the build: if nothing usable is found
the file is removed and the site simply hides the "Next comp" lines.
"""
import sys, os, re, json, csv, time, datetime, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get('WCA_BASE', 'https://www.worldcubeassociation.org').rstrip('/')
UA = 'pr-streaks-site (+https://github.com/EonicMystic/pr-streaks)'
ID_RE = re.compile(r'/persons/(\d{4}[A-Z]{4}\d{2})')
DEADLINE = time.time() + 15 * 60


_lock = threading.Lock()
_pause_until = 0.0


def get(path, tries=6):
    global _pause_until
    for k in range(tries):
        if time.time() > DEADLINE:
            return 0, ''
        wait = _pause_until - time.time()
        if wait > 0:
            time.sleep(min(wait, 60))
        try:
            req = urllib.request.Request(BASE + path, headers={'User-Agent': UA, 'Accept': 'application/json, text/html'})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and k < tries - 1:
                try:
                    ra = float(e.headers.get('Retry-After', ''))
                except Exception:
                    ra = 0
                delay = max(ra, 8 * (k + 1)) if e.code == 429 else 3 * (k + 1)
                with _lock:
                    _pause_until = max(_pause_until, time.time() + delay)   # everyone pauses, not just this request
                continue
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
        st, body = get(f'/api/v0/competitions?start={(datetime.date.fromisoformat(today) - datetime.timedelta(days=7)).isoformat()}&page={page}')
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
    return None, 'none'


def diagnose(cid, wid):
    """Print what the WCA returns for one competition and one person (for debugging)."""
    print(f'--- diagnose {cid} / {wid} ---')
    st, body = get(f'/api/v0/competitions/{cid}/wcif/public')
    print('wcif/public http status:', st, 'bytes:', len(body))
    d = jload(body) if st == 200 else None
    if isinstance(d, dict):
        persons = d.get('persons') or []
        counts = {}
        for p in persons:
            reg = p.get('registration')
            k = reg.get('status') if isinstance(reg, dict) else 'no-registration'
            counts[k] = counts.get(k, 0) + 1
        print('wcif persons:', len(persons), 'by status:', counts)
        for p in persons:
            if p.get('wcaId') == wid:
                print('person record:', json.dumps({k: p.get(k) for k in ('name', 'wcaId', 'registration')})[:400])
                break
        else:
            print('person NOT in wcif persons')
    st, body = get(f'/competitions/{cid}/registrations')
    print('registrations page http status:', st, 'bytes:', len(body), 'ids found:', len(set(ID_RE.findall(body))), 'person in page:', wid in body)


def main():
    out_dir = sys.argv[1]
    path = os.path.join(out_dir, 'next_comps.csv')
    # keep competitions through the day after they end, so a multi-day comp never drops out early
    today = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    comps = list_comps(today)
    print(f'upcoming competitions found: {len(comps)}')

    def work(cid):
        time.sleep(0.15)
        return cid, registrants(cid)

    results = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        for cid, (ids, src) in ex.map(work, list(comps)):
            results[cid] = (ids, src)

    # second and third pass for anything that failed (usually rate limiting), after a cooldown
    for attempt in range(2):
        failed = [c for c, v in results.items() if v[0] is None]
        if not failed or time.time() > DEADLINE:
            break
        print(f'retrying {len(failed)} competitions after a short pause')
        time.sleep(20)
        for cid in failed:
            results[cid] = registrants(cid)
            time.sleep(0.5)

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
    try:
        diagnose(os.environ.get('DIAG_COMP', 'AlgsinAnkeny2026'), os.environ.get('DIAG_ID', '2017BYLB01'))
    except Exception as e:
        print('diagnose skipped:', e)
    used = sum(1 for v in results.values() if v[0])
    print(f'competitions with registrations: {used}; people with a next comp: {len(nxt)}')
    if not nxt:
        for fn in ('next_comps.csv', 'comps.csv', 'registrations.csv'):
            if os.path.exists(os.path.join(out_dir, fn)):
                os.remove(os.path.join(out_dir, fn))
        print('nothing usable found; the site will hide next-comp lines')
        return
    with open(os.path.join(out_dir, 'comps.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['Comp ID', 'Competition', 'Start date', 'End date', 'City', 'Country code'])
        for cid in sorted(comps, key=lambda c: (comps[c].get('start_date') or '', c)):
            if results[cid][0]:
                c = comps[cid]
                w.writerow([cid, c.get('name', cid), c.get('start_date', ''), c.get('end_date', ''),
                            c.get('city', ''), c.get('country_iso2', '')])
    with open(os.path.join(out_dir, 'registrations.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['WCA ID', 'Comp ID'])
        for cid in comps:
            for wid in sorted(results[cid][0] or ()):
                w.writerow([wid, cid])
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['WCA ID', 'Competition', 'Comp ID', 'Start date', 'End date', 'City', 'Country code'])
        w.writerows(sorted(nxt.values()))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('registration fetch skipped:', e)
