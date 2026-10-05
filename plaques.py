"""
Shared code for the London Blue Plaques bot (@blueplaques.bsky.social).

Three scripts use it:
  blueplaques_harvest.py  walks Wikimedia Commons for London's plaques
  blueplaques_pick.py     has the model choose and transcribe a close-up
  blueplaques_post.py     posts one plaque

Everything here that turns Commons' free text into a post is pure and tested
in test_blueplaques.py. The network and the model live in the scripts.
"""

import html
import json
import os
import re
import subprocess
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / 'data'
PLAQUES_FILE = DATA / 'plaques.json'
PICKS_FILE = DATA / 'picks.json'
STATE_FILE = DATA / 'post_state.json'

USER_AGENT = 'blueplaques-bot/0.1 (https://bsky.app/profile/blueplaques.bsky.social; stanfordc+claude@mac.com)'
COMMONS_API = 'https://commons.wikimedia.org/w/api.php'
ROOT_CATEGORY = 'Category:Blue plaques in London'

# A category that groups plaques rather than being one. Measured on the
# London tree on 5 October 2026: 73 of 1,131 categories are groupings and
# every one matches this; the other 1,058 are one plaque each.
GROUPING = re.compile(
    r'plaques\b|plaques in|by (borough|county|city)|'
    r'in the (London Borough|City|Royal Borough)', re.I)

# Borough grouping categories, so the third hashtag names the borough. The
# per-plaque category's own suffix is unreliable for this: it is often a
# district ("Hampstead", "Holborn") and once misspelt ("Maylebone").
BOROUGH_CAT = re.compile(
    r'^Category:Blue plaques in (?:the )?'
    r'(?:London Borough of |Royal Borough of |City of )?(.+)$')
NOT_BOROUGHS = {'London', 'London by borough', 'Bloomsbury', 'Hampstead',
                'Highgate', 'Kentish Town', 'Marylebone', 'Mayfair',
                'Muswell Hill', 'Richmond, London', 'Soho'}


# ------------------------------------------------------------- the network


def curl_json(url, timeout=60):
    """curl, not urllib: this Python's SSL stack fails certificate checks
    here (reference_py313_ssl_urllib). Returns None on any failure."""
    r = subprocess.run(['curl', '-s', '-A', USER_AGENT, '--max-time', str(timeout), url],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def commons(**params):
    params.update(action='query', format='json')
    return curl_json(COMMONS_API + '?' + urllib.parse.urlencode(params))


def load_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text())


def save_json(path, obj):
    """Sibling temp file and an atomic rename, as every poster here does."""
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False))
    os.replace(tmp, path)


# ---------------------------------------------------------------- licences


def strip_html(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s or ''))).strip()


def licence_ok(licence, artist):
    """Can a photo be posted, and credited as its licence requires?

    Free licences only: CC0, public domain, CC BY and CC BY-SA. NC and ND are
    refused (a bot account is not a clear non-commercial use, and ND forbids
    the resize every post makes). An attribution licence with no usable
    author name is refused too: a CC BY photo we cannot credit is one we may
    not post.
    """
    lic = (licence or '').strip()
    low = lic.lower()
    if not lic or '-nc' in low or ' nc' in low or '-nd' in low or ' nd' in low:
        return False
    if low in ('cc0', 'public domain', 'pd') or low.startswith('cc0') or 'public domain' in low:
        return True
    if low.startswith('cc by'):
        name = (artist or '').strip()
        return 0 < len(name) <= 60
    return False


def credit_name(artist):
    """The photographer as the credit line shows them. Commons' Artist field
    is HTML and sometimes a sentence ("Own work by X"). A name over 60
    characters never gets here: licence_ok() refuses the photo instead."""
    name = strip_html(artist)
    name = re.sub(r'^(own work,? )?(by )', '', name, flags=re.I).strip()
    return name or 'Unknown'


# ------------------------------------------------------ Commons descriptions

POSTCODE = re.compile(r'\b([A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2})\b')
MONTHS = ('January|February|March|April|May|June|July|August|September|'
          'October|November|December')
# "Blue plaque erected in 1959 by London County Council at 3 Lyall Street, ..."
# "Blue plaque erected on 21st May 2014 by The Marchmont Association at ..."
# "Blue plaque erected 2007 by English Heritage at 14 Soho Square, ..."
# "Blue plaque erected by City of London at Bow Bells House, ..."
ERECTED = re.compile(
    r'erected\s+(?:(?:on|in)\s+)?'
    r'(?P<when>(?:\d{1,2}(?:st|nd|rd|th)?\s+(?:' + MONTHS + r')\s+)?\d{4})?\s*'
    r'by\s+(?P<who>.+?)\s+at\s+(?P<where>[^.]+)', re.I)
ORIGINALLY = re.compile(
    r'Originally erected by\s+(?:the\s+)?(?P<who>.+?)\s+in\s+(?P<year>\d{4})\s+at\s+'
    r'(?P<where>.+?)(?:\s+but\b|\s+and\b|[.;]|$)', re.I)

BOROUGH_SUFFIXES = re.compile(
    r'^(City of Westminster|City of London|London Borough of .+|Royal Borough of .+|'
    r'.+ London Borough)$', re.I)


def clean_address(where):
    """'3 Lyall Street, Belgravia, London SW1X 8DW, City of Westminster'
    becomes '3 Lyall Street, Belgravia, SW1X 8DW'. 'London' goes because the
    account is London's, the borough goes because it is the third hashtag,
    and anything after the postcode goes because it is always one of those."""
    parts = [p.strip() for p in where.split(',') if p.strip()]
    out = []
    for p in parts:
        if BOROUGH_SUFFIXES.match(p):
            continue
        p = re.sub(r'^London\s+(?=[A-Z]{1,2}\d)', '', p)   # "London SW1X 8DW"
        if p.lower() == 'london':
            continue
        out.append(p)
        if POSTCODE.search(p):
            break
    return ', '.join(out)


def ordinal_date(when):
    """'21st May 2014' -> '21 May 2014'; a bare year stays a year."""
    return re.sub(r'(\d{1,2})(st|nd|rd|th)\b', r'\1', (when or '').strip())


def tidy_scheme(who):
    who = who.strip().rstrip(',')
    who = re.sub(r'^the\s+', '', who, flags=re.I)
    return who


def parse_description(desc):
    """Pull address, scheme, date and any earlier plaque out of one Commons
    description. Returns {} when the description is not in the structured
    'erected ... by X at Y' form, which 44 of 50 sampled plaques have."""
    text = strip_html(desc)
    m = ERECTED.search(text)
    if not m:
        return {}
    out = {
        'address': clean_address(m.group('where')),
        'scheme': tidy_scheme(m.group('who')),
        'when': ordinal_date(m.group('when')),
    }
    o = ORIGINALLY.search(text)
    if o:
        out['earlier'] = {
            'scheme': tidy_scheme(o.group('who')),
            'year': o.group('year'),
            'address': clean_address(o.group('where')),
        }
    return out


def best_details(files, picked_title):
    """The picked photo's own description first, then the others'. Each field
    is taken from the first description that has it."""
    order = sorted(files, key=lambda f: f['title'] != picked_title)
    found = {}
    for f in order:
        d = parse_description(f.get('description', ''))
        for k, v in d.items():
            if v and k not in found:
                found[k] = v
    if 'address' not in found:
        # Some photographers write only "14 Soho Square" or a postcode with
        # no 'erected' sentence. Take a description that carries a postcode.
        for f in order:
            t = strip_html(f.get('description', ''))
            if POSTCODE.search(t) and len(t) < 160 and ' erected ' not in f' {t} ':
                tail = re.split(r'\s+-\s+', t)[-1]
                found['address'] = clean_address(tail)
                break
    return found


# ------------------------------------------------------------ transcription

# Date ranges are printed with hyphens, tildes or spaced dashes. All become
# an en dash; a hyphen between letters ("Horn-player") stays a hyphen.
DATE_RANGE = re.compile(r'(\d{4}|c\.\s?\d{4})\s*[-~–—]\s*(\d{1,4})')


def normalise_dashes(s):
    s = DATE_RANGE.sub(lambda m: f'{m.group(1)}–{m.group(2)}', s)
    s = re.sub(r'(?<=[A-Za-z])~(?=[A-Za-z])', '-', s)   # "Horn~player"
    return s


def curl_quotes(s):
    """House style: curly quotes and apostrophes."""
    s = re.sub(r"(?<=\w)'(?=\w)", '’', s)          # don't, Artist's
    s = re.sub(r"(^|[\s(“])'", r'\1‘', s)
    s = s.replace("'", '’')
    s = re.sub(r'(^|[\s(‘])"', r'\1“', s)
    s = s.replace('"', '”')
    return s


def words(s):
    """Word sequence for comparing a transcription with its prose reading:
    case, punctuation and dash style ignored."""
    s = normalise_dashes(s).lower()
    s = s.replace('&', ' and ')
    return re.findall(r'[a-z0-9]+', s)


def prose_matches(lines, prose):
    """The guard on the model's prose reading: it must contain exactly the
    transcribed words, in order, and nothing else. Recasing and punctuation
    are what the prose is for; an added or dropped word is a refusal."""
    return words(' '.join(lines)) == words(prose)


def plaque_phrase(phrase):
    """'a round blue plaque' as the alt's opening. Anything that does not
    look like that noun phrase is replaced with a plain one rather than
    posted, since this field is the one place the model describes freely."""
    p = re.sub(r'\s+', ' ', (phrase or '')).strip().rstrip('.')
    if re.fullmatch(r"(?i)an? [a-z ,-]{0,60}plaque( (with|in|on) [a-z ,-]{1,60})?", p):
        return p[0].upper() + p[1:]
    return 'A plaque'


def build_alt(pick):
    prose = curl_quotes(normalise_dashes(pick['prose'].strip()))
    if prose and prose[-1] not in '.!?”':
        prose += '.'
    return f'{plaque_phrase(pick.get("plaque"))}. A.I.-transcribed: “{prose}”'


# ------------------------------------------------------------------ the post

MAX_CHARS = 290


def tag_slug(name):
    """'Kensington and Chelsea' -> 'KensingtonAndChelsea'."""
    return ''.join(w[:1].upper() + w[1:] for w in re.findall(r"[A-Za-z]+", name or ''))


def scheme_line(details, rim_scheme=None):
    scheme = details.get('scheme') or rim_scheme
    if not scheme:
        return ''
    when = details.get('when', '')
    earlier = details.get('earlier')
    if earlier:
        line = f'{scheme}, {when}.' if when else f'{scheme}.'
        e = earlier
        line += f' The {e["scheme"]}’s plaque of {e["year"]} was at {e["address"]}.'
        return line
    if re.fullmatch(r'\d{4}', when):
        return f'{scheme} plaque, put up in {when}.'
    if when:
        return f'{scheme} plaque, put up on {when}.'
    return f'{scheme} plaque.'


def compose(plaque, pick):
    """The post as a list of (kind, text, target) parts, kind being 'text',
    'link' or 'tag'. Kept as data, not a TextBuilder, so it can be tested
    without atproto and printed in a dry run exactly as it will read."""
    files = {f['title']: f for f in plaque['files']}
    photo = files[pick['file']]
    details = best_details(plaque['files'], pick['file'])
    rim = pick.get('scheme_rim')
    rim_scheme = rim.title() if rim and rim.isupper() else rim

    parts = []
    address = details.get('address')
    if address:
        parts.append(('text', curl_quotes(address) + '\n', None))
    line = scheme_line(details, rim_scheme)
    if line:
        parts.append(('text', curl_quotes(line) + '\n', None))
    lat, lon = photo.get('lat'), photo.get('lon')
    if lat is None:
        for f in plaque['files']:
            if f.get('lat') is not None:
                lat, lon = f['lat'], f['lon']
                break
    if lat is not None:
        parts.append(('link', '📍 Map', f'https://www.google.com/maps?q={lat},{lon}'))
        parts.append(('text', '\n', None))
    parts.append(('text', '\nPhoto: ', None))
    parts.append(('link', curl_quotes(credit_name(photo['artist'])), photo['page']))
    parts.append(('text', f', {photo["licence"]}\n', None))
    tags = ['BluePlaques', 'London']
    if plaque.get('borough'):
        tags.append(tag_slug(plaque['borough']))
    for i, t in enumerate(tags):
        if i:
            parts.append(('text', ' ', None))
        parts.append(('tag', f'#{t}', t))
    return parts


def render(parts):
    return ''.join(text for _, text, _ in parts)


def postable(plaque, pick):
    """A plaque goes out only with a readable pick whose prose passed the
    word check, on a photo we can license, and with something to say where
    it is."""
    if not pick or not pick.get('ok'):
        return False
    files = {f['title']: f for f in plaque.get('files', [])}
    photo = files.get(pick.get('file'))
    if not photo or not licence_ok(photo.get('licence'), photo.get('artist')):
        return False
    parts = compose(plaque, pick)
    text = render(parts)
    has_place = any(k == 'link' and t == '📍 Map' for k, t, _ in parts) or bool(
        best_details(plaque['files'], pick['file']).get('address'))
    return has_place and len(text) <= MAX_CHARS
