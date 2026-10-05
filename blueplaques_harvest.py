#!/usr/bin/env python3
"""
Walk Wikimedia Commons' "Blue plaques in London" tree and record every plaque
that has a category of its own, with each photo's licence, author,
description and coordinates. Writes data/plaques.json.

A plaque is a category of one plaque ("Piet Mondrian lived here (blue
plaque), Camden"). Categories that group plaques (by borough, by scheme, by
district) are walked through, and the borough ones name the third hashtag.

Read-only against Commons. Safe to re-run: it rebuilds the file whole, and
refuses to write one smaller than FLOOR, so a failed or throttled walk can
never replace a good roster with a short one.

Usage:
    python3 blueplaques_harvest.py
"""

import argparse
import sys
import time

from plaques import (BOROUGH_CAT, GROUPING, NOT_BOROUGHS, PLAQUES_FILE, ROOT_CATEGORY,
                     commons, load_json, save_json, strip_html)

FLOOR = 900          # 1,058 per-plaque categories on 5 October 2026
MAX_DEPTH = 8
PAUSE = 0.2


class WalkFailed(RuntimeError):
    pass


def members(cat, kind):
    out, cont = [], {}
    while True:
        d = commons(list='categorymembers', cmtitle=cat, cmtype=kind, cmlimit=500, **cont)
        if not d or 'query' not in d:
            raise WalkFailed(f'listing failed for {cat}')
        out += [m['title'] for m in d['query']['categorymembers']]
        if 'continue' not in d:
            return out
        cont = {'cmcontinue': d['continue']['cmcontinue']}
        time.sleep(PAUSE)


def files_of(cat):
    out, cont = [], {}
    while True:
        d = commons(generator='categorymembers', gcmtitle=cat, gcmtype='file', gcmlimit=50,
                    prop='imageinfo|coordinates', iiprop='extmetadata|url|size', **cont)
        if d is None:
            raise WalkFailed(f'file listing failed for {cat}')
        for p in (d.get('query') or {}).get('pages', {}).values():
            if 'imageinfo' not in p:
                continue
            ii = p['imageinfo'][0]
            m = ii.get('extmetadata', {})
            co = (p.get('coordinates') or [{}])[0]
            out.append({
                'title': p['title'],
                'page': ii.get('descriptionurl'),
                'artist': strip_html(m.get('Artist', {}).get('value', '')),
                'licence': strip_html(m.get('LicenseShortName', {}).get('value', '')),
                'description': strip_html(m.get('ImageDescription', {}).get('value', '')),
                'lat': co.get('lat'), 'lon': co.get('lon'),
                'width': ii.get('width'), 'height': ii.get('height'),
            })
        if 'continue' not in d:
            return sorted(out, key=lambda f: f['title'])
        cont = {k: v for k, v in d['continue'].items() if k != 'continue'}
        time.sleep(PAUSE)


def borough_of(cat):
    if 'City of London' in cat:          # "London" alone is not a borough
        return 'City of London'
    m = BOROUGH_CAT.match(cat)
    if m and m.group(1) not in NOT_BOROUGHS and not GROUPING.search(m.group(1)):
        return m.group(1)
    return None


def walk():
    plaques, seen = {}, set()

    def visit(cat, depth, borough):
        if cat in seen or depth > MAX_DEPTH:
            return
        seen.add(cat)
        borough = borough_of(cat) or borough
        for sub in members(cat, 'subcat'):
            if GROUPING.search(sub):
                visit(sub, depth + 1, borough)
            elif sub not in plaques:
                plaques[sub] = {'title': sub, 'borough': borough, 'files': files_of(sub)}
                time.sleep(PAUSE)
            elif borough and not plaques[sub].get('borough'):
                plaques[sub]['borough'] = borough
        time.sleep(PAUSE)

    visit(ROOT_CATEGORY, 0, None)
    return plaques


def main():
    argparse.ArgumentParser(description=__doc__.split('\n\n')[0]).parse_args()
    try:
        plaques = walk()
    except WalkFailed as exc:
        sys.exit(f'NOT WRITTEN: {exc}')
    n = len(plaques)
    with_files = sum(1 for p in plaques.values() if p['files'])
    if n < FLOOR:
        sys.exit(f'NOT WRITTEN: only {n} plaques found, under the floor of {FLOOR}')
    old = load_json(PLAQUES_FILE, {})
    save_json(PLAQUES_FILE, plaques)
    print(f'{n} plaques ({with_files} with photos); was {len(old)}. '
          f'{sum(1 for p in plaques.values() if p.get("borough"))} with a borough.')


if __name__ == '__main__':
    main()
