#!/usr/bin/env python3
"""
For each plaque, show the model every freely licensed photo of it and have it
choose the one whose inscription is most readable, transcribe the inscription
and give it as one line of prose. Writes data/picks.json.

Tested on 50 random London plaques on 5 October 2026: a readable close-up
chosen for all 50 and no wrong word in any transcription, checked by eye
against every photo. Two faults in that run shaped the prompt: a second
tablet under Rossetti's plaque was left out, and a colour-processed photo of
Little Tich's was chosen over a true-colour one.

The prose reading is checked in code (plaques.prose_matches): it must be the
transcribed words, in order, and nothing else. A pick that fails is recorded
and not posted.

A call that could not be made (an expired token, a quota, a timeout) is NOT
cached, so the next run tries again. Three in a row stop the run: that is a
credential or a quota, not a plaque.

Usage:
    python3 blueplaques_pick.py                  # every plaque without a pick
    python3 blueplaques_pick.py --limit 20
    python3 blueplaques_pick.py --only "Category:Mary Seacole lived here (blue plaque), Westminster"
    python3 blueplaques_pick.py --redo --only "..."
"""

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path

from plaques import (PICKS_FILE, PLAQUES_FILE, USER_AGENT, licence_ok, load_json,
                     prose_matches, save_json)

MODEL = 'claude-sonnet-5'
CONFINED = ['--restricted', '--tools', 'Read']   # reference_claude_p_is_an_agent
MAX_PHOTOS = 8
WIDTH = 1200
TIMEOUT = 400
WORKERS = 3
TOKEN = ('seoulbot', 'claude-oauth-token')        # the scheduled bots' long-lived token

PROMPT = """The files photo1.jpg to photo{n}.jpg in this directory are photographs of ONE commemorative plaque in London, taken by different people. Read each file.

Pick the single photo in which the plaque's full inscription is most clearly readable. Reject photos where the plaque is small in the frame, cut off, blurred, badly angled or obscured, and photos of an unveiling ceremony. Prefer photos in natural colour over ones that have been tinted, posterised or heavily colour-processed, when both are readable.

Then transcribe the inscription exactly as printed in the photo you picked. Do not add, correct, expand or guess any word. If any word cannot be read with certainty, set "readable" to false.

Reply with ONLY this JSON, no other text:
{{"pick": <photo number, or null if none is readable>,
  "readable": true|false,
  "scheme_rim": "<the scheme or body named around the rim or at the top, e.g. GREATER LONDON COUNCIL, exactly as printed, or empty>",
  "lines": ["<each remaining printed line of the main inscription, in order, exactly as printed>"],
  "extra": ["<each line of any additional tablet or panel fixed to the plaque or directly beneath it as part of it, or none>"],
  "prose": "<the whole inscription, rim first, then the lines, then any extra tablet, as natural English prose: the same words in the same order, nothing added or left out, in ordinary sentence case with proper nouns capitalised, and with commas and full stops where a reader would expect them>",
  "plaque": "<a short noun phrase for the plaque itself, starting 'a' or 'an' and ending 'plaque', e.g. 'a round blue plaque' or 'a rectangular blue plaque in a green ceramic frame'>",
  "why": "<one short sentence on why this photo>"}}"""

_lock = threading.Lock()


def claude_env():
    env = os.environ.copy()
    r = subprocess.run(['security', 'find-generic-password', '-a', TOKEN[0], '-s', TOKEN[1], '-w'],
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        env['CLAUDE_CODE_OAUTH_TOKEN'] = r.stdout.strip()
    return env


def candidates(plaque):
    """Licensed photos only, so the model can never choose one we may not
    post. Names that carry an inscription come first, since in the sample
    those were nearly always close-ups, and only MAX_PHOTOS are shown."""
    ok = [f for f in plaque['files'] if licence_ok(f['licence'], f['artist'])]
    inscr = re.compile(r'lived|born|died|worked|stayed|site of|here', re.I)
    ok.sort(key=lambda f: (not inscr.search(f['title']), 'geograph' in f['title'].lower(), f['title']))
    return ok[:MAX_PHOTOS]


def signature(files):
    return '|'.join(f['title'] for f in files)


def thumb(title, width=WIDTH):
    name = title.removeprefix('File:').replace(' ', '_')
    return ('https://commons.wikimedia.org/wiki/Special:FilePath/'
            f'{urllib.parse.quote(name)}?width={width}')


def ask(plaque, files, env):
    """One model call. Returns (record, None) or (None, reason)."""
    with tempfile.TemporaryDirectory() as td:
        for i, f in enumerate(files, 1):
            out = Path(td, f'photo{i}.jpg')
            r = subprocess.run(['curl', '-sL', '-A', USER_AGENT, '--max-time', '90',
                                '-o', str(out), thumb(f['title'])], capture_output=True)
            if r.returncode != 0 or not out.exists() or out.stat().st_size < 2000:
                return None, f'could not fetch {f["title"]}'
        try:
            r = subprocess.run(['claude', '-p', *CONFINED, '--model', MODEL, PROMPT.format(n=len(files))],
                               capture_output=True, text=True, cwd=td, env=env,
                               stdin=subprocess.DEVNULL, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            return None, 'model timed out'
    if r.returncode != 0:
        return None, f'claude exited {r.returncode}: {(r.stdout + r.stderr).strip()[:200]}'
    m = re.search(r'\{.*\}', r.stdout, re.S)
    try:
        a = json.loads(m.group(0)) if m else None
    except ValueError:
        a = None
    if not isinstance(a, dict):
        return None, f'no JSON in reply: {r.stdout.strip()[:200]}'

    rec = {'sig': signature(files), 'model': MODEL,
           'at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
           'scheme_rim': (a.get('scheme_rim') or '').strip(),
           'lines': [l for l in (a.get('lines') or []) if str(l).strip()],
           'extra': [l for l in (a.get('extra') or []) if str(l).strip()],
           'prose': (a.get('prose') or '').strip(),
           'plaque': a.get('plaque') or '', 'why': a.get('why') or ''}
    n = a.get('pick')
    if not a.get('readable') or not isinstance(n, int) or not 1 <= n <= len(files):
        rec.update(ok=False, file=None, reason='model found no readable photo')
        return rec, None
    rec['file'] = files[n - 1]['title']
    if not rec['prose'] or not prose_matches(rec['lines'], rec['prose'],
                                             [rec['scheme_rim']] + rec['extra']):
        rec.update(ok=False, reason='prose does not match the transcription word for word')
        return rec, None
    rec.update(ok=True, reason='')
    return rec, None


VERIFY_PROMPT = """The file photo.jpg in this directory is a photograph of a commemorative plaque. Read it.

Is the plaque's full inscription clearly readable in THIS photograph alone, with the plaque large enough in the frame to read every word? Answer from this photograph only: do not use anything you know about the plaque or its subject.

If it is readable, transcribe every word exactly as printed, including any scheme name around the rim and any tablet fixed beneath it. If any word cannot be read in this photograph, set "readable" to false.

Reply with ONLY this JSON, no other text:
{"readable": true|false, "text": "<every word of the inscription, as printed, or empty>"}"""


def verify(rec, env):
    """An independent second read: the chosen photo ALONE, transcribed afresh.
    The pick is posted only if this agrees word for word with the first
    transcription (order aside). Added 6 October 2026 after the first pre-pick
    chose, for Kenneth Williams, a photo of the whole building with the plaque
    a speck on its front: the first call saw every photo, so it could
    transcribe the plaque from another and still pick the wrong one, and the
    prose check, which compares the model only with itself, cannot see that.
    Returns (True|False, reason), or (None, reason) when the call could not be
    made, which is not a verdict."""
    from plaques import words
    with tempfile.TemporaryDirectory() as td:
        out = Path(td, 'photo.jpg')
        r = subprocess.run(['curl', '-sL', '-A', USER_AGENT, '--max-time', '90', '-o', str(out),
                            thumb(rec['file'])], capture_output=True)
        if r.returncode != 0 or not out.exists() or out.stat().st_size < 2000:
            return None, f'could not fetch {rec["file"]}'
        try:
            r = subprocess.run(['claude', '-p', *CONFINED, '--model', MODEL, VERIFY_PROMPT],
                               capture_output=True, text=True, cwd=td, env=env,
                               stdin=subprocess.DEVNULL, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            return None, 'model timed out'
    if r.returncode != 0:
        return None, f'claude exited {r.returncode}: {(r.stdout + r.stderr).strip()[:200]}'
    m = re.search(r'\{.*\}', r.stdout, re.S)
    try:
        a = json.loads(m.group(0)) if m else None
    except ValueError:
        a = None
    if not isinstance(a, dict):
        return None, f'no JSON in reply: {r.stdout.strip()[:200]}'
    rec['verify_text'] = a.get('text') or ''
    if not a.get('readable'):
        return False, 'second read: inscription not readable in the chosen photo'
    first = ([rec.get('scheme_rim', '')] + rec.get('lines', []) + rec.get('extra', []))
    first = [l for l in first if str(l).strip().lower() not in ('', 'none', 'n/a', 'null')]
    if sorted(words(' '.join(first))) != sorted(words(a.get('text') or '')):
        return False, 'second read disagrees with the first transcription'
    return True, ''


def main():
    ap = argparse.ArgumentParser(description='Choose and transcribe a photo for each plaque.')
    ap.add_argument('--limit', type=int, default=0, help='stop after this many plaques')
    ap.add_argument('--only', action='append', default=[], help='one plaque category (repeatable)')
    ap.add_argument('--redo', action='store_true', help='ignore any cached pick')
    ap.add_argument('--verify', action='store_true',
                    help='second read of every usable pick not yet verified')
    ap.add_argument('--recheck', action='store_true',
                    help='re-judge every cached pick against the current word check; no model calls')
    args = ap.parse_args()

    if args.recheck:
        picks = load_json(PICKS_FILE, {})
        changed = 0
        for rec in picks.values():
            if not rec.get('file') or rec.get('reason') == 'model found no readable photo':
                continue
            ok = bool(rec.get('prose')) and prose_matches(
                rec.get('lines', []), rec['prose'], [rec.get('scheme_rim', '')] + rec.get('extra', []))
            if ok != rec.get('ok'):
                changed += 1
            rec['ok'] = ok
            rec['reason'] = '' if ok else 'prose does not match the transcription word for word'
        save_json(PICKS_FILE, picks)
        print(f'{changed} picks changed; {sum(1 for r in picks.values() if r.get("ok"))} of {len(picks)} usable.')
        return

    plaques = load_json(PLAQUES_FILE, {})
    if not plaques:
        sys.exit(f'No roster at {PLAQUES_FILE}: run blueplaques_harvest.py first.')
    picks = load_json(PICKS_FILE, {})

    if args.verify:
        env = claude_env()
        todo = [t for t, r in sorted(picks.items()) if r.get('ok') and 'verified' not in r]
        if args.limit:
            todo = todo[:args.limit]
        print(f'{len(todo)} picks to verify.')
        state = {'run': 0}
        stop = threading.Event()

        def check(title):
            if stop.is_set():
                return
            ok, why = verify(picks[title], env)
            with _lock:
                if ok is None:
                    state['run'] += 1
                    print(f'  NOT CHECKED {title[9:]}: {why}')
                    if state['run'] >= 3:
                        stop.set()
                    return
                state['run'] = 0
                picks[title]['verified'] = ok
                picks[title]['verify_reason'] = why
                save_json(PICKS_FILE, picks)
                print(f'  {"ok " if ok else "NO "} {title[9:]}' + ('' if ok else f' ({why})'))

        with cf.ThreadPoolExecutor(WORKERS) as ex:
            list(ex.map(check, todo))
        v = [r for r in picks.values() if 'verified' in r]
        print(f'{sum(1 for r in v if r["verified"])} of {len(v)} verified picks agree.')
        if stop.is_set():
            sys.exit('Stopped after three failures in a row: check the token and the quota.')
        return

    todo = []
    for title in (args.only or sorted(plaques)):
        p = plaques.get(title)
        if not p:
            sys.exit(f'Not in the roster: {title}')
        files = candidates(p)
        if not files:
            continue
        cached = picks.get(title)
        if cached and cached.get('sig') == signature(files) and not args.redo:
            continue
        todo.append((p, files))
    if args.limit:
        todo = todo[:args.limit]
    print(f'{len(todo)} plaques to pick ({len(picks)} cached).')

    env = claude_env()
    failures = {'run': 0, 'total': 0}
    stop = threading.Event()

    def work(item):
        p, files = item
        if stop.is_set():
            return
        rec, err = ask(p, files, env)
        with _lock:
            if err:
                failures['run'] += 1
                failures['total'] += 1
                print(f'  NOT CHECKED {p["title"][9:]}: {err}')
                if failures['run'] >= 3:
                    stop.set()
                return
            failures['run'] = 0
            picks[p['title']] = rec
            save_json(PICKS_FILE, picks)
            print(f'  {"ok " if rec["ok"] else "NO "} {p["title"][9:]}'
                  + ('' if rec['ok'] else f' ({rec["reason"]})'))

    with cf.ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(work, todo))
    good = sum(1 for r in picks.values() if r.get('ok'))
    print(f'{good} of {len(picks)} picks usable; {failures["total"]} not checked this run.')
    if stop.is_set():
        sys.exit('Stopped after three failures in a row: check the token and the quota.')


if __name__ == '__main__':
    main()
