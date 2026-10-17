import json
import tempfile
import unittest
from pathlib import Path

from asr_abschnitte import abschnitt
from mosslaeufe import MossLaeufe, REZEPT
from wortzeiten import zuordnen
import test_aufteilung


class MossTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.audio=self.root/'a.ogg';self.audio.write_bytes(b'audio')
        self.store=MossLaeufe(self.root/'runs.sqlite3')
        self.store.einreihen('a',self.audio,300)

    def job(self,provider):
        return {'name':'a','basis':self.store.basis(self.audio),'anbieter':provider}

    def ready(self,provider='groq',word='Hallo'):
        data={'rezept':REZEPT,'anbieter':provider,'duration':300,
              'segments':[{'text':word,'start':1,'end':2}],
              'words':[{'word':word,'start':1,'end':2}]}
        self.store.speichern({**self.job(provider),'status':'bereit','ergebnis':data},self.audio)
        return data

    def test_both_providers_are_required_and_wait_for_enhancement(self):
        recordings=[{'name':'a','dauer':300}]
        self.assertEqual([j['anbieter'] for j in self.store.jobs(recordings,lambda _:self.audio)],['filter'])
        for ext in ('.flac','.mp3'):(self.store.audio/(self.store.basis(self.audio)+ext)).touch()
        self.store.speichern({**self.job('filter'),'status':'bereit'},self.audio)
        self.assertEqual([j['anbieter'] for j in self.store.jobs(recordings,lambda _:self.audio)],['groq','macbook'])
        self.ready()
        self.assertEqual(self.store.statistik()['macbook']['fertig'],0)
        self.ready('macbook')
        restarted=MossLaeufe(self.store.pfad)
        self.assertTrue(all(s['fertig']==1 for s in restarted.statistik().values()))

    def test_corrections_freeze_their_own_text_and_word_times(self):
        self.ready('macbook','Mac')
        active=self.store.aktive_fassung('a',self.audio,{},self.store.lesen('a',self.audio))
        self.assertEqual(active['segments'][0]['text'],'Mac')
        self.ready('groq','Cloud')
        frozen=self.store.aktive_fassung('a',self.audio,{0:'edited'},self.store.lesen('a',self.audio))
        self.assertEqual(frozen['words'][0]['word'],'Mac')
        fresh=self.store.aktive_fassung('a',self.audio,{},self.store.lesen('a',self.audio))
        self.assertEqual(fresh['words'][0]['word'],'Cloud')

    def test_preexisting_corrections_and_changed_source_remain_protected(self):
        self.ready()
        self.assertIsNone(self.store.aktive_fassung('a',self.audio,{0:'old edit'},self.store.lesen('a',self.audio)))
        self.audio.write_bytes(b'changed recording')
        self.assertEqual(self.store.lesen('a',self.audio),{})
        with self.assertRaises(ValueError):self.ready()

    def test_backoff_does_not_block_macbook(self):
        for ext in ('.flac','.mp3'):(self.store.audio/(self.store.basis(self.audio)+ext)).touch()
        self.store.speichern({**self.job('filter'),'status':'bereit'},self.audio)
        self.store.speichern({**self.job('groq'),'status':'fehler','retry':3600},self.audio)
        self.assertEqual([j['anbieter'] for j in self.store.jobs([{'name':'a','dauer':300}],lambda _:self.audio)],['macbook'])

    def test_overlap_owns_words_instead_of_dropping_boundary_segments(self):
        result={'segments':[{'start':238,'end':243,'text':'eins zwei drei'}],
                'words':[{'word':'eins','start':238,'end':239},{'word':'zwei','start':239.8,'end':240.1},
                         {'word':'drei','start':241,'end':242}]}
        left,lwords=abschnitt(result,0,0,240,500)
        right,rwords=abschnitt(result,0,240,480,500)
        self.assertEqual(left[0]['text'],'eins zwei')
        self.assertEqual(right[0]['text'],'drei')
        self.assertEqual(len(lwords+rwords),3)

    def test_repeated_phrases_cannot_jump_to_distant_audio(self):
        segments=[{'text':'Hallo Welt','start':100,'end':102}]
        words=[{'word':'Hallo','start':1,'end':2},{'word':'Welt','start':2,'end':3},
               {'word':'Hallo','start':100,'end':101},{'word':'Welt','start':101,'end':102}]
        self.assertEqual([w['start'] for w in zuordnen(segments,words)[0]],[100,101])


class MossIntegrationTest(unittest.TestCase):
    def setUp(self):
        test_aufteilung.AufteilungTest.setUp(self)

    def test_new_word_times_and_text_stay_together_after_manual_edit(self):
        audio=self.root/'audio/test.wav';store=self.app.moss
        store.einreihen('test',audio,20)
        job={'name':'test','basis':store.basis(audio),'anbieter':'groq','status':'bereit'}
        result={'rezept':REZEPT,'anbieter':'groq','duration':20,
                'segments':[{'text':'Hallo Welt','start':1,'end':3}],
                'words':[{'word':'Hallo','start':1,'end':2},{'word':'Welt','start':2,'end':3}]}
        store.speichern({**job,'ergebnis':result},audio)
        detail=self.app.detail('test')
        self.assertEqual(detail['transkript'][0]['text'],'Hallo Welt')
        self.assertEqual([w['start'] for w in detail['transkript'][0]['words']],[1,2])
        self.stimmen.text_speichern('test','test.gruendlich.json',0,'Hallo Welt','Hallo schöne Welt')
        result['segments']=[{'text':'Andere Wörter','start':5,'end':7}]
        result['words']=[{'word':'Andere','start':5,'end':6},{'word':'Wörter','start':6,'end':7}]
        store.speichern({**job,'ergebnis':result},audio)
        detail=self.app.detail('test')
        self.assertEqual(detail['transkript'][0]['text'],'Hallo schöne Welt')
        self.assertEqual([w['start'] for w in detail['transkript'][0]['words']],[1,2])
        self.assertEqual(detail['sprecher'][0]['name'],'Anna')
        self.assertEqual(json.loads((self.root/'transkripte/test.gruendlich.json').read_text()),self.original)


if __name__=='__main__':unittest.main()
