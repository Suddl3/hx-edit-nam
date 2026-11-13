"""Keep destination language explicit in the actual rendered catalog card."""
from html.parser import HTMLParser
import unittest

from tone_preview import render_card


class CardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.footer = False
        self.button = None
        self.buttons = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'footer':
            self.footer = True
        if tag == 'button' and self.footer:
            attributes = dict(attrs)
            self.button = attributes.get('id')
            self.buttons[self.button] = {'label':'', 'title':attributes.get('title','')}

    def handle_data(self, value):
        if self.button:
            self.buttons[self.button]['label'] += value

    def handle_endtag(self, tag):
        if tag == 'button':
            self.button = None
        if tag == 'footer':
            self.footer = False


class ToneCardContractTests(unittest.TestCase):
    def test_catalog_card_distinguishes_preview_pc_and_device_destinations(self):
        for tone in (None, {'id':1,'title':'Demo'}):
            with self.subTest(has_tone=bool(tone)):
                card = CardParser()
                card.feed(render_card(tone, [{'id':2,'name':'Clean'}], library=True))
                expected = {'back':'Browse TONE3000', 'retry':'Browse TONE3000', 'library':'NAM library',
                            'keep':'Save to PC', 'auditionSelected':'Preview', 'save':'Save to PC + Floor', 'finish':'Close'}
                self.assertEqual({key:card.buttons[key]['label'] for key in expected}, expected)
                self.assertIn('preset Save is separate',card.buttons['save']['title'])
                self.assertIn('without installing',card.buttons['keep']['title'])


if __name__ == '__main__':
    unittest.main()
