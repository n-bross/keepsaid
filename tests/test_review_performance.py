import gzip
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

import test_aufteilung
from review_server import Handler


class ReviewPerformanceTest(unittest.TestCase):
    def setUp(self):
        test_aufteilung.AufteilungTest.setUp(self)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.app = self.app
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def test_compression_negotiation_and_exact_content_length(self):
        with urllib.request.urlopen(self.base) as r:
            plain = r.read()
            self.assertIsNone(r.headers.get('Content-Encoding'))
        request = urllib.request.Request(self.base, headers={'Accept-Encoding': 'gzip'})
        with urllib.request.urlopen(request) as r:
            packed = r.read()
            self.assertEqual(r.headers['Content-Encoding'], 'gzip')
            self.assertEqual(r.headers['Vary'], 'Accept-Encoding')
            self.assertEqual(int(r.headers['Content-Length']), len(packed))
            self.assertEqual(gzip.decompress(packed), plain)
            self.assertLess(len(packed), len(plain)/3)
        request = urllib.request.Request(self.base, headers={'Accept-Encoding': 'gzip;q=0'})
        with urllib.request.urlopen(request) as r:
            self.assertIsNone(r.headers.get('Content-Encoding'))
            self.assertEqual(r.read(), plain)

    def test_compressed_detail_keeps_word_offsets_and_review_token(self):
        request = urllib.request.Request(self.base+'/api/aufnahme?name=test', headers={'Accept-Encoding': 'gzip'})
        with urllib.request.urlopen(request) as r:
            raw = r.read()
            if r.headers.get('Content-Encoding') == 'gzip':
                raw = gzip.decompress(raw)
            detail = json.loads(raw)
            self.assertEqual(detail['transkript'][0]['text'], 'Zwei Personen sprechen.')
        with urllib.request.urlopen(self.base+'/api/aufnahmen') as r:
            self.assertEqual(json.load(r)['token'], self.app.token)
