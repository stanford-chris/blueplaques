#!/usr/bin/env python3
"""
Post the four-post "about this account" thread on @blueplaques.bsky.social and
pin its first post. A one-off, modelled on ~/Scripts/pin_bot_posts.py.

Refuses an account that already has something pinned: there is no --replace,
because replacing means deleting the old thread first and that is a decision,
not a default.

Defaults to a DRY RUN and needs --post, the reverse of the scheduled poster:
this is a hand-run, irreversible public write.

Usage:
    python3 blueplaques_pin.py           # print the thread, post nothing
    python3 blueplaques_pin.py --post    # post it and pin the first post
"""

import argparse
import sys

from blueplaques_post import login

MENTION = '@stanfordc.bsky.social'
THREAD = [
    'This account posts a London blue plaque twice a day, from every scheme: English Heritage '
    'and the old London County Council and Greater London Council, the City of London, the '
    'boroughs and local societies. 🧵',
    'Every photograph comes from Wikimedia Commons and is freely licensed: public domain, CC0, '
    'CC BY or CC BY-SA. Each post names the photographer and links to the photo’s own page, '
    'where the licence is set out in full.',
    'The alt text is the plaque’s inscription, read from the photograph by A.I. A plaque is '
    'posted only when the model’s two readings of it agree word for word. The address, scheme '
    'and year come from the photographer’s description on Commons.',
    'Not affiliated with English Heritage or any plaque scheme. Spotted a mistake? Reply to the '
    'post. Run by ' + MENTION,
]


def build_text(client, text):
    from atproto import client_utils, models
    tb = client_utils.TextBuilder()
    if MENTION not in text:
        return tb.text(text)
    did = client.com.atproto.identity.resolve_handle(
        models.ComAtprotoIdentityResolveHandle.Params(handle=MENTION.lstrip('@'))).did
    before, after = text.split(MENTION, 1)
    return tb.text(before).mention(MENTION, did).text(after)


def profile_record(client):
    from atproto import models
    return client.com.atproto.repo.get_record(models.ComAtprotoRepoGetRecord.Params(
        repo=client.me.did, collection='app.bsky.actor.profile', rkey='self'))


def main():
    ap = argparse.ArgumentParser(description='Post and pin the about-this-account thread.')
    ap.add_argument('--post', action='store_true', help='actually post (default: dry run)')
    args = ap.parse_args()

    for i, t in enumerate(THREAD, 1):         # every length checked before any write
        if len(t) > 300:
            sys.exit(f'Post {i} is {len(t)} characters, over 300.')
        print(f'--- {i} ({len(t)})\n{t}')
    if not args.post:
        print('\nDry run: nothing posted.')
        return

    from atproto import models
    client = login()
    got = profile_record(client)
    if getattr(got.value, 'pinned_post', None):
        sys.exit(f'Already has a pinned post ({got.value.pinned_post.uri}): not replacing it.')

    root = parent = None
    for t in THREAD:
        reply = None
        if root:
            reply = models.AppBskyFeedPost.ReplyRef(
                root=models.ComAtprotoRepoStrongRef.Main(uri=root.uri, cid=root.cid),
                parent=models.ComAtprotoRepoStrongRef.Main(uri=parent.uri, cid=parent.cid))
        parent = client.send_post(text=build_text(client, t), reply_to=reply, langs=['en'])
        root = root or parent
        print('posted', parent.uri)

    got = profile_record(client)
    record = got.value
    record.pinned_post = models.ComAtprotoRepoStrongRef.Main(uri=root.uri, cid=root.cid)
    client.com.atproto.repo.put_record(models.ComAtprotoRepoPutRecord.Data(
        repo=client.me.did, collection='app.bsky.actor.profile', rkey='self',
        record=record, swap_record=got.cid))
    confirmed = profile_record(client).value.pinned_post
    print('pinned', confirmed.uri if confirmed else 'NOTHING: pin did not take')


if __name__ == '__main__':
    main()
