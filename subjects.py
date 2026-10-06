"""
Who (or what) a plaque commemorates, as English Wikipedia articles, for the
link-card reply under each post. Asked for on 6 October 2026.

Two routes, and neither matches on a name alone:

1. Commons' own categories. A plaque's category sits inside its subject's
   ("Randolph Caldecott"), which carries a Wikidata item, which carries the
   enwiki sitelink (directly, or through P301 when the item is a category
   item). Measured on 40 postable plaques: 24 reached the right person this
   way, and 8 more reached only the STREET or SQUARE the plaque is on
   ("Edwardes Square", "Sloane Street"). So an article is kept only if it
   shares a significant word with the plaque's own name part.
2. For plaques route 1 misses (Bertrand Russell, H. G. Wells), search
   Wikipedia for the name part and accept a PERSON whose birth and death
   years, from Wikidata, are both printed on the plaque. No years on the
   plaque, no fallback.

The card's thumbnail is used only when it is on Wikimedia Commons (its URL
says /wikipedia/commons/): English Wikipedia's lead image is often a non-free
"fair use" file Wikipedia may show and this account may not copy. His call,
option A, 6 October 2026. Otherwise the card goes without an image.

A plaque with no confident match gets no reply, never a guess.
"""

import re
import urllib.parse

from plaques import GROUPING, commons, curl_json, strip_html

WIKIDATA = 'https://www.wikidata.org/w/api.php'
ENWIKI = 'https://en.wikipedia.org/w/api.php'
SUMMARY = 'https://en.wikipedia.org/api/rest_v1/page/summary/'
MAX_SUBJECTS = 3

STOP = {'the', 'and', 'of', 'here', 'lived', 'live', 'lives', 'born', 'died', 'worked', 'stayed',
        'house', 'site', 'blue', 'plaque', 'plaques', 'london', 'saint', 'church', 'street',
        'road', 'square', 'lord', 'lady', 'sir', 'dame', 'his', 'her', 'was', 'this', 'near',
        'stood', 'from', 'with', 'for', 'who', 'first'}
VERBS = (r'lived|born|died|worked|stayed|lodged|founded|wrote|designed|was|were|ran|trained|met|'
         r'formed|assembled|built|studied|taught|played|composed|painted|stood|opened|recorded|'
         r'situated|located|held|established|invented|made|began|started|died')


def name_part(category_title):
    """'Category:Bertrand Russell lived here (blue plaque), Camden' -> 'Bertrand Russell'."""
    t = category_title.removeprefix('Category:')
    t = re.split(r'\s*\((?:fake\s+)?blue (?:plaque|badge)s?\)', t, flags=re.I)[0]
    t = re.sub(r'\s+blue plaques?\b.*$', '', t, flags=re.I)
    t = re.split(rf'\s+(?:{VERBS})\b', t, flags=re.I)[0]
    return t.strip(' ,')


def significant(s):
    """Content words, with a plural folded to its singular ("Huxleys")."""
    out = set()
    for w in re.findall(r'[a-z]+', strip_html(s).lower()):
        if len(w) > 4 and w.endswith('s') and not w.endswith('ss'):
            w = w[:-1]
        if len(w) >= 3 and w not in STOP:
            out.add(w)
    return out


def plaque_years(pick):
    text = ' '.join([pick.get('scheme_rim', '')] + pick.get('lines', []) + pick.get('extra', []))
    return {int(y) for y in re.findall(r'\b(1[0-9]{3}|20[0-2][0-9])\b', text)}


def _claim_ids(entity, prop):
    return [c['mainsnak'].get('datavalue', {}).get('value', {}).get('id')
            for c in entity.get('claims', {}).get(prop, [])]


def _claim_year(entity, prop):
    for c in entity.get('claims', {}).get(prop, []):
        t = c['mainsnak'].get('datavalue', {}).get('value', {}).get('time', '')
        m = re.match(r'[+-](\d{4})', t)
        if m:
            return int(m.group(1))
    return None


def entities(ids):
    ids = [i for i in ids if i]
    if not ids:
        return {}
    d = curl_json(WIKIDATA + '?' + urllib.parse.urlencode(dict(
        action='wbgetentities', ids='|'.join(ids[:50]), props='sitelinks|claims',
        sitefilter='enwiki', format='json')))
    return (d or {}).get('entities', {})


def is_person(entity):
    return 'Q5' in _claim_ids(entity, 'P31')


def enwiki_title(entity):
    t = entity.get('sitelinks', {}).get('enwiki', {}).get('title')
    return None if not t or t.startswith('Category:') else t


def via_commons(plaque):
    """Route 1: [(enwiki title, is_person)] from the plaque category's parents."""
    d = commons(titles=plaque['title'], prop='categories', cllimit=50, clshow='!hidden')
    if not d:
        return None
    parents = [c['title'] for pg in d.get('query', {}).get('pages', {}).values()
               for c in pg.get('categories', []) if not GROUPING.search(c['title'])]
    if not parents:
        return []
    d = commons(titles='|'.join(parents[:50]), prop='pageprops', ppprop='wikibase_item')
    if not d:
        return None
    qids = [pg.get('pageprops', {}).get('wikibase_item')
            for pg in d.get('query', {}).get('pages', {}).values()]
    found, ents = [], entities(qids)
    for q in qids:
        e = ents.get(q or '', {})
        title = enwiki_title(e)
        if not title:                                  # a category item: follow its main topic
            topic = next(iter(_claim_ids(e, 'P301')), None)
            if topic:
                e = entities([topic]).get(topic, {})
                title = enwiki_title(e)
        if title:
            found.append((title, is_person(e)))
    return found


def via_search(plaque, pick):
    """Route 2: a person found by name whose birth and death years are both on
    the plaque."""
    years = plaque_years(pick)
    name = name_part(plaque['title'])
    if len(years) < 2 or not significant(name):
        return []
    d = curl_json(ENWIKI + '?' + urllib.parse.urlencode(dict(
        action='query', list='search', srsearch=name, srlimit=5, format='json')))
    titles = [r['title'] for r in (d or {}).get('query', {}).get('search', [])]
    if not titles:
        return []
    d = curl_json(ENWIKI + '?' + urllib.parse.urlencode(dict(
        action='query', titles='|'.join(titles), prop='pageprops', ppprop='wikibase_item',
        redirects=1, format='json')))
    pages = (d or {}).get('query', {}).get('pages', {}).values()
    by_q = {pg.get('pageprops', {}).get('wikibase_item'): pg['title'] for pg in pages}
    ents = entities(list(by_q))
    for q, title in by_q.items():
        e = ents.get(q or '', {})
        born, died = _claim_year(e, 'P569'), _claim_year(e, 'P570')
        if is_person(e) and born in years and died in years and significant(title) & significant(name):
            return [(title, True)]
    return []


def via_exact_title(plaque, pick):
    """An article whose title carries exactly the plaque's name words, found
    by search: for organisations and events, which have no years to match.
    Never for a person (people pass the year check or nothing: "George
    Grossmith" is the father, and the Marylebone plaque is the son's), and
    never on a one-word name ("Lord Wandsworth" found the borough). Dates on
    the plaque are no bar: the Anti-Corn Law League's are the years it ran."""
    name = name_part(plaque['title'])
    words = significant(name)
    if len(words) < 2:
        return []
    d = curl_json(ENWIKI + '?' + urllib.parse.urlencode(dict(
        action='query', list='search', srsearch=name, srlimit=5, format='json')))
    for r in (d or {}).get('query', {}).get('search', []):
        if significant(r['title']) != words:
            continue
        d2 = curl_json(ENWIKI + '?' + urllib.parse.urlencode(dict(
            action='query', titles=r['title'], prop='pageprops', ppprop='wikibase_item',
            redirects=1, format='json')))
        q = next((pg.get('pageprops', {}).get('wikibase_item')
                  for pg in (d2 or {}).get('query', {}).get('pages', {}).values()), None)
        if q and is_person(entities([q]).get(q, {})):
            return []
        return [(r['title'], False)]
    return []


def card(title):
    """Wikipedia's own summary: (url, title, description, commons thumbnail or None)."""
    d = curl_json(SUMMARY + urllib.parse.quote(title.replace(' ', '_'), safe=''))
    if not d or d.get('type') == 'disambiguation' or 'content_urls' not in d:
        return None
    thumb = (d.get('thumbnail') or {}).get('source', '')
    original = (d.get('originalimage') or {}).get('source', '')
    commons_thumb = thumb if '/wikipedia/commons/' in thumb else None
    if commons_thumb and original and '/wikipedia/commons/' not in original:
        commons_thumb = None
    desc = d.get('description') or (d.get('extract') or '').split('. ')[0]
    return {'url': d['content_urls']['desktop']['page'], 'title': d.get('title') or title,
            'description': desc, 'thumb': commons_thumb}


def find(plaque, pick):
    """Up to MAX_SUBJECTS cards, people first. None when a lookup could not
    be made (so the caller can retry later); [] when there is honestly no
    confident match."""
    name = significant(name_part(plaque['title']))
    routed = via_commons(plaque)
    if routed is None:
        return None
    scored = [(len(significant(t) & name), t, person) for t, person in routed]
    people = [(t, True) for n, t, person in scored if n and person]
    things = [(n, t) for n, t, person in scored if n and not person]
    # Of the non-person articles keep only the closest: St Gabriel Fenchurch's
    # category also reaches "30 Fenchurch Street", the office block on its site.
    best = max((n for n, _ in things), default=0)
    keep = people + [(t, False) for n, t in things if n == best][:1]
    def leads(title):
        """Does the article carry the FIRST name word of the plaque's name, or
        of one of its names ("Malcolm and Donald Campbell")? "Henry Thomas
        Austen" fails for Jane Austen's plaque; "Robert Clive" passes for
        "Clive of India"."""
        parts = re.split(r'\s+and\s+|\s*,\s*|\s*&\s*', name_part(plaque['title']))
        firsts = [next((w for w in re.findall(r'[a-z]+', p.lower()) if w in significant(p)), None)
                  for p in parts]
        return any(f and f in significant(title) for f in firsts)

    people = [(t, p) for t, p in people if name <= significant(t) or leads(t)]
    keep = people + [x for x in keep if not x[1]]
    if people and not any(name <= significant(t) for t, _ in people):
        # Only partial person matches ("Henry Thomas Austen" on the plaque to
        # Jane Austen, who stayed with her brother): try the full name by its
        # years too, and lead with it when it is found.
        people = [x for x in via_search(plaque, pick) if x[0] not in dict(people)] + people
        keep = people + keep[len([p for p in keep if p[1]]):]
    if not people:
        # Commons' link is trusted when its title holds every name word ("The
        # Magic Circle (organisation)"). When it holds only some ("Corn Laws"
        # for the Anti-Corn Law League), an article titled with exactly the
        # name words is preferred to it.
        if not any(name <= significant(t) for t, _ in keep):
            exact = via_exact_title(plaque, pick)
            if exact:
                keep = exact
        keep = via_search(plaque, pick) + keep
    out, seen = [], set()
    for title, _ in keep:
        if title in seen:
            continue
        seen.add(title)
        c = card(title)
        if c:
            out.append(c)
        if len(out) >= MAX_SUBJECTS:
            break
    return out
