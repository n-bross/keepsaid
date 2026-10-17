import unittest
from transkript_bloecke import bloecke, text_verteilen


def segment(i,text,words,start=0,end=10):
    return {'id':i,'text':text,'original':text,'words':words,'start':start,'end':end,'speaker':'s1','korrigiert':False}


class BloeckeTest(unittest.TestCase):
    def test_same_name_merges_different_ids_and_splits_at_switch(self):
        text='Hallo dort und Ben hier'
        words=[{'word':w,'start':t,'end':t+.5,'char_start':a,'char_end':a+len(w)}
               for w,t,a in [('Hallo',1,0),('dort',2,6),('und',3,11),('Ben',6,15),('hier',7,19)]]
        areas=[{'start':0,'end':2.5,'speaker':'s1'},{'start':2.5,'end':5,'speaker':'s2'},
               {'start':5,'end':10,'speaker':'s3'}]
        result=bloecke([segment(0,text,words)],areas,{'s1':'Nico','s2':'Nico','s3':'Ben'},10)
        self.assertEqual([b['text'] for b in result],['Hallo dort und','Ben hier'])
        self.assertEqual([b['start'] for b in result],[1,6])
        self.assertEqual(result[0]['speaker_ids'],['s1','s2'])
        for b in result:
            for w in b['words']:
                self.assertEqual(b['text'][w['char_start']:w['char_end']],w['word'])
        edits=text_verteilen(result[0],'Hallo Nico', [segment(0,text,words)])
        self.assertEqual(edits[0][2],'Hallo Nico Ben hier')

    def test_edit_merged_block_keeps_other_speaker_and_distributes_words(self):
        segs=[segment(0,'Hallo Welt',[],0,2),segment(1,'weiter reden',[],2,4),segment(2,'Andere Person',[],4,6)]
        areas=[{'start':0,'end':4,'speaker':'s1'},{'start':4,'end':6,'speaker':'s2'}]
        bs=bloecke(segs,areas,{'s1':'Nico','s2':'Ben'},6)
        self.assertEqual(len(bs),2)
        edits=text_verteilen(bs[0],'Hallo liebe Welt weiter reden!',segs)
        self.assertEqual(' '.join(e[2] for e in edits),'Hallo liebe Welt weiter reden!')
        self.assertNotIn(2,[e[0] for e in edits])
        self.assertEqual([e[2] for e in text_verteilen(bs[0],'',segs)],['',''])

    def test_empty_text_is_hidden_and_invalid_times_are_not_fabricated(self):
        bs=bloecke([segment(0,'',[]),segment(1,'Kein Anker',[],100,120)],[],{},10)
        self.assertEqual(len(bs),1)
        self.assertIsNone(bs[0]['playback_start'])
        self.assertEqual(bs[0]['words'],[])

    def test_real_word_anchor_wins_over_old_segment_start(self):
        segs=[segment(0,'Nicht erkannt',[],0,3),segment(1,'Hallo',[{'word':'Hallo','start':50,'end':51,'char_start':0,'char_end':5}],3,7)]
        bs=bloecke(segs,[{'start':0,'end':60,'speaker':'s1'}],{'s1':'Nico'},60)
        self.assertEqual(len(bs),1)
        self.assertEqual(bs[0]['playback_start'],50)
