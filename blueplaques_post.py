#!/usr/bin/env python3
"""
Post one London blue plaque to @blueplaques.bsky.social: the address, the
scheme and year, a map link, the photo with its credit, and the inscription
as alt text.

Plaques go out in a fixed shuffled order and never twice. A plaque with no
cached pick is picked now (one model call); one whose pick failed, or which
cannot be credited or placed, is passed over for the next.

Requires the app password in the Keychain:
    security add-generic-password -a blueplaques.bsky.social -s blueplaques-bluesky -w

Usage:
    python3 blueplaques_post.py             # post one plaque
    python3 blueplaques_post.py --dry-run   # print it, post nothing, write no state
"""

import argparse
import io
import random
import subprocess
import sys
import time

from plaques import (PICKS_FILE, PLAQUES_FILE, STATE_FILE, USER_AGENT, build_alt, compose,
                     load_json, postable, render, save_json)

HANDLE = 'blueplaques.bsky.social'
KEYCHAIN_SERVICE = 'blueplaques-bluesky'
SHUFFLE_SEED = 18670122          # 22 January 1867: Byron's birthday, the year of the first plaque
MAX_IMAGE_BYTES = 950_000
SKIP_LIMIT = 25                  # plaques passed over in one run before giving up


def keychain_password(account, service):
    r = subprocess.run(['security', 'find-generic-password', '-a', account, '-s', service, '-w'],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f'No Keychain password for {account} / {service}')
    return r.stdout.strip()


def login(retries=4):
    from atproto import Client, exceptions
    password = keychain_password(HANDLE, KEYCHAIN_SERVICE)
    last = None
    for attempt in range(retries):
        try:
            c = Client()
            c.login(HANDLE, password)
            return c
        except exceptions.NetworkError as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f'Could not log in after {retries} attempts: {last}')


def fetch_image(title):
    """Special:FilePath renders any width, so step down until the photo fits
    under Bluesky's blob limit rather than downloading the original."""
    import urllib.parse
    name = urllib.parse.quote(title.removeprefix('File:').replace(' ', '_'))
    last = None
    for width in (1600, 1200, 1000, 800):
        url = f'https://commons.wikimedia.org/wiki/Special:FilePath/{name}?width={width}'
        for attempt in range(3):
            r = subprocess.run(['curl', '-sL', '-A', USER_AGENT, '--max-time', '60', url],
                               capture_output=True)
            if r.returncode == 0 and len(r.stdout) > 2000:
                if len(r.stdout) <= MAX_IMAGE_BYTES:
                    return r.stdout
                last = f'{len(r.stdout)} bytes at width {width}'
                break
            last = f'curl exit {r.returncode}'
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f'Could not fetch {title}: {last}')


def text_builder(parts):
    from atproto import client_utils
    tb = client_utils.TextBuilder()
    for kind, text, target in parts:
        if kind == 'link':
            tb.link(text, target)
        elif kind == 'tag':
            tb.tag(text, target)
        else:
            tb.text(text)
    return tb


def order_of(plaques, state):
    """Fixed shuffled order, extended with any plaque new to the roster, so
    the sequence survives a re-harvest. Same rule as everylibrary."""
    order = [t for t in state.get('order', []) if t in plaques]
    known = set(order)
    fresh = sorted(t for t in plaques if t not in known)
    if fresh:
        random.Random(SHUFFLE_SEED + len(order)).shuffle(fresh)
        order += fresh
    state['order'] = order
    return order


def main():
    ap = argparse.ArgumentParser(description='Post one London blue plaque.')
    ap.add_argument('--dry-run', action='store_true', help='print the post, post nothing')
    args = ap.parse_args()

    plaques = load_json(PLAQUES_FILE, {})
    if not plaques:
        sys.exit(f'No roster at {PLAQUES_FILE}: run blueplaques_harvest.py first.')
    picks = load_json(PICKS_FILE, {})
    state = load_json(STATE_FILE, {'posted': [], 'passed': {}, 'order': []})
    posted = set(state['posted'])
    passed = state.setdefault('passed', {})

    import blueplaques_pick as picker
    env = None
    skipped = 0
    for title in order_of(plaques, state):
        if title in posted or title in passed:
            continue
        plaque = plaques[title]
        files = picker.candidates(plaque)
        if not files:
            passed[title] = 'no freely licensed photo'
            skipped += 1
            continue
        pick = picks.get(title)
        if not pick or pick.get('sig') != picker.signature(files):
            env = env or picker.claude_env()
            pick, err = picker.ask(plaque, files, env)
            if err:
                # A call that could not be made says nothing about the plaque:
                # stop rather than pass it over, and let the next run retry.
                sys.exit(f'NOT POSTED: could not pick {title}: {err}')
            picks[title] = pick
            save_json(PICKS_FILE, picks)
        if pick.get('ok') and 'verified' not in pick:
            env = env or picker.claude_env()
            ok, why = picker.verify(pick, env)
            if ok is None:
                sys.exit(f'NOT POSTED: could not verify {title}: {why}')
            pick['verified'], pick['verify_reason'] = ok, why
            save_json(PICKS_FILE, picks)
        if not postable(plaque, pick):
            passed[title] = pick.get('reason') or pick.get('verify_reason') or 'not postable (credit, place or length)'
            print(f'passed over: {title[9:]} ({passed[title]})')
            skipped += 1
            if not args.dry_run:
                save_json(STATE_FILE, state)
            if skipped >= SKIP_LIMIT:
                sys.exit(f'NOT POSTED: {SKIP_LIMIT} plaques passed over in a row')
            continue
        break
    else:
        print('Nothing left to post: every plaque has been through.')
        return

    parts = compose(plaque, pick)
    alt = build_alt(pick)
    print('-' * 60)
    print(render(parts))
    print(f'[alt] {alt}')
    print(f'[photo] {pick["file"]}')
    print(f'[plaque] {title}')
    if args.dry_run:
        print('\nDry run: nothing posted, no state written.')
        return

    image = fetch_image(pick['file'])
    from PIL import Image
    from atproto import models
    with Image.open(io.BytesIO(image)) as im:
        ratio = models.AppBskyEmbedDefs.AspectRatio(width=im.width, height=im.height)
    client = login()
    client.send_images(text=text_builder(parts), images=[image], image_alts=[alt],
                       image_aspect_ratios=[ratio], langs=['en'])
    state['posted'].append(title)
    save_json(STATE_FILE, state)
    print(f'Posted ({len(state["posted"])} so far).')


if __name__ == '__main__':
    main()
