"""Zeitliche Sprecherkorrekturen, ohne Text oder Wortgrenzen zu erfinden."""
import hashlib
import json
import math


def zeitwert(wert):
    if isinstance(wert, bool):
        raise ValueError('Bitte eine gültige Zeit eingeben.')
    zahl = float(wert)
    if not math.isfinite(zahl) or zahl < 0:
        raise ValueError('Zeiten müssen endlich und mindestens 0 sein.')
    return zahl


def original_bereiche(text):
    bereiche = text.get('sprecher_segmente') or text.get('segments', [])
    return sorted(({'start': float(s['start']), 'end': float(s['end']), 'speaker': s['speaker']}
                   for s in bereiche if s.get('speaker') and s['end'] > s['start'] >= 0),
                  key=lambda s: (s['start'], s['end'], s['speaker']))


def signatur(bereiche):
    return hashlib.sha256(json.dumps(bereiche, sort_keys=True).encode()).hexdigest()


def anwenden(bereiche, korrekturen):
    ergebnis = [dict(s) for s in bereiche]
    for k in korrekturen:
        neu = []
        for s in ergebnis:
            a, b = max(s['start'], k['start']), min(s['end'], k['ende'])
            if k['quelle'] not in {s['speaker'], '__bereich__'} or a >= b:
                neu.append(s)
                continue
            if s['start'] < a:
                neu.append({**s, 'end': a})
            neu.append({**s, 'start': a, 'end': b, 'speaker': k['ziel']})
            if b < s['end']:
                neu.append({**s, 'start': b})
        ergebnis = neu
    return sorted(ergebnis, key=lambda s: (s['start'], s['end'], s['speaker']))


def text_bereiche(segment, bereiche):
    return [{**s, 'start': max(s['start'], segment['start']),
             'end': min(s['end'], segment['end'])}
            for s in bereiche if s['start'] < segment['end'] and s['end'] > segment['start']]


def mit_unbekannten_bereichen(bereiche, text):
    """Transkriptlücken als offen markieren; überlappende Bereiche nur einmal abziehen."""
    from bisect import bisect_right
    abdeckung = []
    for b in sorted(bereiche, key=lambda s: s['start']):
        if abdeckung and b['start'] <= abdeckung[-1][1]:
            abdeckung[-1] = (abdeckung[-1][0], max(abdeckung[-1][1], b['end']))
        else:
            abdeckung.append((b['start'], b['end']))
    enden = [ende for _, ende in abdeckung]
    unbekannt = []
    for segment in text.get('segments', []):
        if not segment.get('text', '').strip():
            continue
        start, ende = float(segment['start']), float(segment['end'])
        if start < 0 or ende <= start:
            continue
        i = bisect_right(enden, start)
        while i < len(abdeckung) and abdeckung[i][0] < ende:
            a, b = abdeckung[i]
            if start < a:
                unbekannt.append((start, min(a, ende)))
            start = max(start, b)
            if start >= ende:
                break
            i += 1
        if start < ende:
            unbekannt.append((start, ende))
    vereint = []
    for start, ende in sorted(unbekannt):
        if vereint and start <= vereint[-1]['end']:
            vereint[-1]['end'] = max(vereint[-1]['end'], ende)
        else:
            vereint.append({'start': start, 'end': ende, 'speaker': '__unzugeordnet__'})
    return sorted([*bereiche, *vereint], key=lambda s: (s['start'], s['end'], s['speaker']))
