"""Merge overlapping ASR chunks on the unchanged original audio time axis."""
from wortzeiten import gueltige_woerter


def abschnitt(result, offset, start, end, duration):
    raw=result.get('segments') or []
    words=gueltige_woerter(result.get('words') or [w for s in raw for w in s.get('words',[])])
    segments=[];kept=[];seen=set()
    for s in raw:
        if s.get('no_speech_prob',0)>.85 and s.get('avg_logprob',0)<-.6:continue
        a,b=float(s['start'])+offset,float(s['end'])+offset
        owned=[]
        for w in words:
            midpoint=(w['start']+w['end'])/2+offset
            if a<=midpoint<b and start<=midpoint<end:
                absolute={**w,'start':max(0,round(w['start']+offset,3)),
                          'end':min(duration,round(w['end']+offset,3))}
                key=(absolute['word'],absolute['start'],absolute['end'])
                if key not in seen and absolute['start']<absolute['end']:
                    seen.add(key);owned.append(absolute);kept.append(absolute)
        if owned:
            # Derive this chunk's text from the words it owns. Never keep a whole
            # segment twice or drop its boundary words based on a segment midpoint.
            segments.append({'start':owned[0]['start'],'end':owned[-1]['end'],
                             'text':' '.join(w['word'].strip() for w in owned)})
        elif not words and start<=(a+b)/2<end and s.get('text','').strip() and max(0,a)<min(duration,b):
            segments.append({'start':max(0,a),'end':min(duration,b),'text':s['text'].strip()})
    return segments,gueltige_woerter(kept,duration)
