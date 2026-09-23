import unittest
from pathlib import Path
from html.parser import HTMLParser
from jinja2 import Environment, DictLoader


class FrameParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.frames = []

    def handle_starttag(self, tag, attrs):
        if tag == 'iframe':
            self.frames.append(dict(attrs))


class ContactsMapTests(unittest.TestCase):
    def test_contacts_embeds_map_filling_container(self):
        source = (Path(__file__).resolve().parents[1] / 'templates' / 'contacts.html').read_text()
        env = Environment(loader=DictLoader({
            'base.html': '{% block content %}{% endblock %}',
            'contacts.html': source,
        }), autoescape=True)
        page = env.get_template('contacts.html').render(whatsapp_number='')
        parser = FrameParser()
        parser.feed(page)
        self.assertEqual(len(parser.frames), 1)
        frame = parser.frames[0]
        self.assertTrue(frame['src'].startswith('https://www.google.com/maps?q='))
        self.assertIn('&output=embed', frame['src'])
        self.assertTrue(frame['title'])
        self.assertIn('height:100%', frame['style'])
        self.assertIn('width:100%', frame['style'])
        self.assertIn('position:relative', page)
        self.assertIn('min-height:360px', page)


if __name__ == '__main__':
    unittest.main()
