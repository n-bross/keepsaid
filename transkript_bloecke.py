"""Sprecherwechsel als lesbare Blöcke mit Rückverweisen auf Originalabschnitte."""
import difflib
import hashlib
import json
import re


def sprecher_bei(start, ende, bereiche, fallback):
    scores = {}
    for b in bereiche:
        overlap = max(0, min(ende, b['end']) - max(start, b['start']))
        if overlap:
            scores[b['speaker']] = scores.get(b['speaker'], 0) + overlap
    return max(scores, key=lambda s: (scores[s], s == fallback)) if scores else fallback


def bloecke(segmente, bereiche, namen, dauer):
    result = []
    for s in segmente:
        if not s['text'].strip():
            continue
        words = s.get('words') or []
        fallback = s.get('speaker') or '__unzugeordnet__'
        cuts = []
        if words:
            for w in words:
                speaker = sprecher_bei(w['start'], w['end'], bereiche, fallback)
                if not cuts or cuts[-1][1] != speaker:
                    cuts.append((w['char_start'] if cuts else 0, speaker))
        else:
            speaker = sprecher_bei(s['start'], s['end'], bereiche, fallback)
            cuts = [(0, speaker)]
        for j, (a, speaker) in enumerate(cuts):
            b = cuts[j+1][0] if j+1 < len(cuts) else len(s['text'])
            raw = s['text'][a:b]
            if not raw.strip():
                continue
            leading = len(raw) - len(raw.lstrip())
            a += leading
            b = a + len(raw.strip())
            text = s['text'][a:b]
            ws = [{**w, 'char_start': w['char_start']-a, 'char_end': w['char_end']-a}
                  for w in words if a <= w['char_start'] < b]
            start = ws[0]['start'] if ws else (s['start'] if 0 <= s['start'] < dauer else None)
            end = ws[-1]['end'] if ws else min(s['end'], dauer)
            name = namen.get(speaker)
            identity = 'name:'+name.strip().casefold() if name else 'id:'+speaker
            part = {'segment': s['id'], 'char_start': a, 'char_end': b, 'text': text}
            if result and result[-1]['identity'] == identity:
                block = result[-1]
                offset = len(block['text']) + 1
                block['text'] += ' ' + text
                block['words'].extend({**w, 'char_start': w['char_start']+offset,
                                      'char_end': w['char_end']+offset} for w in ws)
                block['parts'].append(part)
                block['end'] = max(block['end'], end)
                if block['start'] is None:
                    block['start'] = start
                if speaker not in block['speaker_ids']:
                    block['speaker_ids'].append(speaker)
                block['korrigiert'] |= s['korrigiert']
            else:
                result.append({'id': f"b-{s['id']}-{a}", 'identity': identity, 'speaker_ids': [speaker],
                               'text': text, 'words': ws, 'start': start, 'end': end,
                               'parts': [part], 'korrigiert': s['korrigiert']})
    for block in result:
        if block['words']:
            block['start'] = block['words'][0]['start']
        block['version'] = hashlib.sha256(json.dumps([block['identity'],block['parts']],sort_keys=True).encode()).hexdigest()
        block['playback_start'] = block['start']
        block['playback_end'] = block['end']
        block['bereiche'] = [{'speaker': sp, 'start': block['start'], 'end': block['end']} for sp in block['speaker_ids']]
        block['original'] = block['text']
        block['wortanzahl'] = len(block['text'].split())
        block['source_segment'] = block['parts'][0]['segment']
    return result


def text_verteilen(block, neu, segmente):
    """Änderungen auf Quellspannen verteilen; andere Sprecher unverändert lassen."""
    if not isinstance(neu, str) or len(neu) > 100000:
        raise ValueError('Text darf höchstens 100.000 Zeichen enthalten.')
    old = list(re.finditer(r'\S+', block['text']))
    new = list(re.finditer(r'\S+', neu))
    matcher = difflib.SequenceMatcher(None, [m.group() for m in old], [m.group() for m in new], autojunk=False)
    ops = matcher.get_opcodes()
    def boundary(n):
        if n <= 0:
            return 0
        if n >= len(old):
            return len(new)
        for tag, a, b, c, d in ops:
            if a <= n < b:
                return c+n-a if tag == 'equal' else c+round((n-a)*(d-c)/(b-a))
        return len(new)
    edits, count = {}, 0
    parts = block['parts']
    for i, part in enumerate(parts):
        first = boundary(count)
        count += len(part['text'].split())
        last = boundary(count) if i+1 < len(parts) else len(new)
        a = new[first].start() if first < len(new) else len(neu)
        b = new[last].start() if last < len(new) else len(neu)
        replacement = neu[a:b].rstrip()
        edits.setdefault(part['segment'], []).append((part['char_start'], part['char_end'], replacement))
    result = []
    for index, changes in edits.items():
        s = segmente[index]
        value = s['text']
        for a, b, replacement in sorted(changes, reverse=True):
            value = value[:a] + replacement + value[b:]
        result.append((index, s['original'], value))
    return result
