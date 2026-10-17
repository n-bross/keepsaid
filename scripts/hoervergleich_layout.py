"""Kompakte Darstellung vorhandener Hörproben, ohne Audiodateien neu zu berechnen."""
import html
import re


def plain(value):
    return html.unescape(re.sub(r'<[^>]+>', '', value))


def compact(document):
    date = r'\b\d{1,2}\.\s+[A-Za-zÄÖÜäöüß]+\s*·\s*\d+:\d+(?:\.\d+)?[–-]\d+:\d+(?:\.\d+)?'
    overall = re.search(date, plain(document))
    groups = re.findall(r'<section\b[^>]*>(.*?)</section>', document, re.S)
    if not groups:
        groups = [document]
    rows = []
    for group in groups:
        variants = re.findall(r'<label class="variant">(.*?)</label>', group, re.S)
        if not variants:
            if '<section' in document:
                continue
            variants = ['<span>MossFormer2</span>' + a for a in re.findall(r'<audio\b.*?</audio>', group, re.S)]
        if not variants:
            continue
        items = []
        for variant in variants:
            caption = re.search(r'<span\b[^>]*>(.*?)</span>', variant, re.S)
            audio = re.search(r'<audio\b.*?</audio>', variant, re.S)
            if not audio:
                continue
            label = plain(caption[1]) if caption else 'Audio'
            for before, after in [
                (' · bisheriger Favorit', ''), (' · abschnittsweise', ''),
                (' · ohne Textvorgabe', ''), (' · bisherige Stärke', ''),
                ('Bisheriger Filter · RNNoise', 'RNNoise'), ('Bisheriger Filter', 'RNNoise'),
                ('DeepFilterNet3 · mild (max. 6 dB)', 'DeepFilterNet3 · mild'),
                ('Mild + Stimme anheben', 'DeepFilterNet3 · mild + lauter'),
                (' + Stimme anheben', ' + lauter'),
            ]:
                label = label.replace(before, after)
            name = html.escape(label, quote=True)
            player = re.sub(r' aria-label="[^"]*"', '', audio[0])
            player = player.replace('<audio', f'<audio aria-label="{name}"', 1)
            items.append(f'<label class="variant"><span>{name}</span>{player}</label>')
        local = re.search(date, plain(group))
        meta = local or (overall if len(groups) == 1 else None)
        context = f'<small class="clip-meta">{html.escape(meta[0])}</small>' if meta else ''
        rows.append(f'<section>{context}{"".join(items)}</section>')
    return '''<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Soundcore · Hörvergleich</title><style>
    *{box-sizing:border-box}body{margin:0;font-family:system-ui,sans-serif;color:#292738;background:#f7f7f9}
    main{max-width:920px;margin:0 auto;padding:32px 24px}header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:20px}
    h1{margin:0;font-size:22px;font-weight:650;letter-spacing:-.5px}button{font:inherit;font-size:13px;border:1px solid #e2e0e8;border-radius:8px;padding:9px 12px;background:#fff;color:inherit;cursor:pointer}
    section{background:#fff;border:1px solid #e7e6ed;border-radius:12px;padding:12px 18px;margin:0 0 16px}.clip-meta{display:block;color:#77717e;font-size:12px;margin:5px 0 12px}
    .variant{display:grid;grid-template-columns:190px minmax(0,1fr);align-items:center;gap:16px;padding:8px 0;font-size:13px}audio{width:100%;height:38px;min-width:0}
    @media(max-width:540px){main{padding:22px 16px}.variant{grid-template-columns:1fr;gap:6px;padding:12px 0}section{padding:8px 16px}h1{font-size:20px}}
    </style><main><header><h1>Hörvergleich</h1><button id="reset" type="button">Zurücksetzen</button></header>''' + ''.join(rows) + '''</main><script>
    let active=null;
    document.querySelectorAll('audio').forEach(a=>a.addEventListener('play',()=>{
      if(active&&active!==a&&active.closest('section')===a.closest('section')&&active.currentTime<active.duration-.2)a.currentTime=active.currentTime;
      document.querySelectorAll('audio').forEach(b=>{if(b!==a)b.pause()});active=a;
    }));
    document.querySelector('#reset').onclick=()=>{active=null;document.querySelectorAll('audio').forEach(a=>{a.pause();a.currentTime=0})};
    </script></html>'''
