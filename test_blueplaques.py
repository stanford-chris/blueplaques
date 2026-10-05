"""Tests for the London Blue Plaques bot's text handling. Stdlib only; the
descriptions and transcriptions are real, from the 5 October 2026 trial."""

import unittest

import plaques as P

CUBITT_DESC = ('Blue plaque erected in 1959 by London County Council at 3 Lyall Street, '
               'Belgravia, London SW1X 8DW, City of Westminster')
SEACOLE_DESC = ('"Mary Seacole 1805-1881 Jamaican nurse heroine of the Crimean War lived here" '
                'Blue plaque erected 2007 by English Heritage at 14 Soho Square, Soho, London, '
                'W1D 3QG, City of Westminster. Originally erected by the Greater London Council '
                'in 1985 at  157 George Street but removed when the area including the house was '
                'scheduled for redeveloped in 1998.')
MILTON_DESC = 'Blue plaque erected by City of London at Bow Bells House, 1 Bread Street, London EC4M 9BE'
JEROME_DESC = ('Blue plaque erected on 21st May 2014 by The Marchmont Association at '
               '32 Tavistock Place, London WC1H 9RH')


def plaque(desc, borough='Westminster', licence='CC BY-SA 4.0', artist='Spudgun67'):
    f = {'title': 'File:X.jpg', 'page': 'https://commons.wikimedia.org/wiki/File:X.jpg',
         'artist': artist, 'licence': licence, 'description': desc, 'lat': 51.5, 'lon': -0.1}
    return {'title': 'Category:X', 'borough': borough, 'files': [f]}


def pick(**kw):
    p = {'ok': True, 'file': 'File:X.jpg', 'scheme_rim': 'LONDON COUNTY COUNCIL',
         'lines': ['THOMAS', 'CUBITT', '1788-1855', 'Master Builder', 'lived here'], 'extra': [],
         'prose': 'London County Council. Thomas Cubitt, 1788-1855, Master Builder, lived here.',
         'plaque': 'a round blue plaque'}
    p.update(kw)
    return p


class Descriptions(unittest.TestCase):
    def test_lcc_with_year(self):
        d = P.parse_description(CUBITT_DESC)
        self.assertEqual(d['address'], '3 Lyall Street, Belgravia, SW1X 8DW')
        self.assertEqual(d['scheme'], 'London County Council')
        self.assertEqual(d['when'], '1959')

    def test_bare_year_and_earlier_plaque(self):
        d = P.parse_description(SEACOLE_DESC)
        self.assertEqual(d['address'], '14 Soho Square, Soho, W1D 3QG')
        self.assertEqual(d['scheme'], 'English Heritage')
        self.assertEqual(d['when'], '2007')
        self.assertEqual(d['earlier'], {'scheme': 'Greater London Council', 'year': '1985',
                                        'address': '157 George Street'})

    def test_no_date(self):
        d = P.parse_description(MILTON_DESC)
        self.assertEqual(d['address'], 'Bow Bells House, 1 Bread Street, EC4M 9BE')
        self.assertEqual(d['scheme'], 'City of London')
        self.assertEqual(d['when'], '')

    def test_full_date_and_leading_the(self):
        d = P.parse_description(JEROME_DESC)
        self.assertEqual(d['scheme'], 'Marchmont Association')
        self.assertEqual(d['when'], '21 May 2014')
        self.assertEqual(d['address'], '32 Tavistock Place, WC1H 9RH')

    def test_unstructured_description_gives_nothing(self):
        self.assertEqual(P.parse_description('Blue plaque dedicated to Thomas Cubitt.'), {})


class SchemeLine(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(P.scheme_line(P.parse_description(CUBITT_DESC)),
                         'London County Council plaque, put up in 1959.')
        self.assertEqual(P.scheme_line(P.parse_description(MILTON_DESC)), 'City of London plaque.')
        self.assertEqual(P.scheme_line(P.parse_description(JEROME_DESC)),
                         'Marchmont Association plaque, put up on 21 May 2014.')

    def test_seacole_history_line(self):
        self.assertEqual(P.scheme_line(P.parse_description(SEACOLE_DESC)),
                         'English Heritage, 2007. The Greater London Council’s plaque of 1985 '
                         'was at 157 George Street.')

    def test_rim_is_the_fallback(self):
        self.assertEqual(P.scheme_line({}, 'Greater London Council'), 'Greater London Council plaque.')
        self.assertEqual(P.scheme_line({}, None), '')


class Licences(unittest.TestCase):
    def test_free_licences(self):
        for lic in ('CC BY-SA 4.0', 'CC BY 2.0', 'CC0', 'Public domain'):
            self.assertTrue(P.licence_ok(lic, 'Someone'), lic)

    def test_refused(self):
        for lic in ('CC BY-NC-SA 2.0', 'CC BY-ND 2.0', '', 'All rights reserved'):
            self.assertFalse(P.licence_ok(lic, 'Someone'), lic)

    def test_attribution_needs_a_name(self):
        self.assertFalse(P.licence_ok('CC BY-SA 4.0', ''))
        self.assertFalse(P.licence_ok('CC BY-SA 4.0', 'x' * 61))
        self.assertTrue(P.licence_ok('CC0', ''))


class Transcription(unittest.TestCase):
    def test_dashes(self):
        self.assertEqual(P.normalise_dashes('1710 ~ 1778'), '1710–1778')
        self.assertEqual(P.normalise_dashes('c.1717 ~ 1791'), 'c.1717–1791')
        self.assertEqual(P.normalise_dashes('1901 - 2'), '1901–2')
        self.assertEqual(P.normalise_dashes('Horn~player'), 'Horn-player')

    def test_quotes(self):
        self.assertEqual(P.curl_quotes("Author of 'Three Men in a Boat'"), 'Author of ‘Three Men in a Boat’')
        self.assertEqual(P.curl_quotes('ARTIST\'S MODEL "Little Tich"'), 'ARTIST’S MODEL “Little Tich”')

    def test_prose_guard_accepts_recasing_and_punctuation(self):
        lines = ['GREATER LONDON COUNCIL', 'MARY', 'SEACOLE', '1805-1881', 'Jamaican Nurse',
                 'HEROINE OF THE', 'CRIMEAN WAR', 'lived here']
        self.assertTrue(P.prose_matches(lines, 'Greater London Council. Mary Seacole, 1805–1881, '
                                               'Jamaican Nurse, Heroine of the Crimean War, lived here.'))

    def test_prose_guard_refuses_an_added_or_dropped_word(self):
        lines = ['THOMAS', 'CUBITT', '1788-1855', 'Master Builder', 'lived here']
        self.assertFalse(P.prose_matches(lines, 'Thomas Cubitt, 1788–1855, the Master Builder, lived here.'))
        self.assertFalse(P.prose_matches(lines, 'Thomas Cubitt, 1788–1855, Builder, lived here.'))
        self.assertFalse(P.prose_matches(lines, 'Thomas Cubitt, 1788–1856, Master Builder, lived here.'))

    def test_ampersand_reads_as_and(self):
        self.assertTrue(P.prose_matches(['Poet & Painter'], 'Poet and painter'))

    def test_alt(self):
        self.assertEqual(P.build_alt(pick()),
                         'A round blue plaque. A.I.-transcribed: “London County Council. '
                         'Thomas Cubitt, 1788–1855, Master Builder, lived here.”')

    def test_free_text_plaque_phrase_is_replaced(self):
        self.assertEqual(P.plaque_phrase('This photo shows a lovely plaque, honestly'), 'A plaque')
        self.assertEqual(P.plaque_phrase('a rectangular blue plaque in a green ceramic frame'),
                         'A rectangular blue plaque in a green ceramic frame')


class Post(unittest.TestCase):
    def test_cubitt_reads_as_approved(self):
        text = P.render(P.compose(plaque(CUBITT_DESC), pick()))
        self.assertEqual(text, '3 Lyall Street, Belgravia, SW1X 8DW\n'
                               'London County Council plaque, put up in 1959.\n'
                               '📍 Map\n\nPhoto: Spudgun67, CC BY-SA 4.0\n'
                               '#BluePlaques #London #Westminster')

    def test_links_and_tags(self):
        parts = P.compose(plaque(CUBITT_DESC, borough='Kensington and Chelsea'), pick())
        links = [(t, u) for k, t, u in parts if k == 'link']
        self.assertEqual(links[0], ('📍 Map', 'https://www.google.com/maps?q=51.5,-0.1'))
        self.assertEqual(links[1], ('Spudgun67', 'https://commons.wikimedia.org/wiki/File:X.jpg'))
        self.assertEqual([u for k, t, u in parts if k == 'tag'], ['BluePlaques', 'London', 'KensingtonAndChelsea'])

    def test_no_borough_no_third_tag(self):
        parts = P.compose(plaque(CUBITT_DESC, borough=None), pick())
        self.assertEqual([u for k, t, u in parts if k == 'tag'], ['BluePlaques', 'London'])

    def test_postable(self):
        self.assertTrue(P.postable(plaque(CUBITT_DESC), pick()))
        self.assertFalse(P.postable(plaque(CUBITT_DESC), pick(ok=False)))
        self.assertFalse(P.postable(plaque(CUBITT_DESC, licence='CC BY-NC 2.0'), pick()))
        self.assertFalse(P.postable(plaque(CUBITT_DESC), None))


if __name__ == '__main__':
    unittest.main()
