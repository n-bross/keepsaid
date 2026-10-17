"""Publish the actual reading-version experiment for listening and comparison."""
import base64
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'beispiele/filtervergleich'


def main():
    results=json.loads((ROOT/'work/whisperx-vergleich/lesefassungen-v5.json').read_text())
    assert set(results)=={'groq','mlx','mitschrift'}
    for key,data in results.items():
        (OUT/f'lesefassung-{key}.txt').write_text(data['lesetext']+'\n')
    payload=json.dumps(results,ensure_ascii=False).replace('<','\\u003c')
    page='''<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Soundcore · Lesefassung testen</title><style>
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#272633;background:#f7f7f9}main{max-width:1100px;margin:auto;padding:30px 24px 50px}h1{font-size:24px;letter-spacing:-.6px;margin:0}header{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:8px}select,button{font:inherit;border:1px solid #e1deeb;background:white;color:inherit;border-radius:8px;padding:9px 12px;font-size:12px;cursor:pointer}select{max-width:290px}.meta{font-size:12px;color:#777582;line-height:1.6}audio{display:block;width:100%;height:42px;margin:22px 0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.card{background:#fff;border:1px solid #e7e6ed;border-radius:14px;padding:22px;min-width:0}h2{font-size:16px;margin:0 0 18px}.text{white-space:pre-wrap;font-size:15px;line-height:1.9}.card-head{display:flex;justify-content:space-between;align-items:baseline;gap:10px}.label{font-size:11px;color:#847b92}.unclear{background:#fff1ce;border-radius:4px;padding:2px 3px;color:#79551d}.links{margin-top:20px;border-top:1px solid #efedf3;padding-top:14px;font-size:12px}a{color:#6254c7;text-decoration:none}.note{font-size:11px;line-height:1.6;color:#8d8798;margin-top:16px}button:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid #bcb3f1;outline-offset:3px}@media(max-width:720px){main{padding:22px 16px}header{flex-wrap:wrap}.grid{grid-template-columns:1fr}.card{padding:18px}}
</style><main><header><h1>Lesefassung testen</h1><select id="source" aria-label="Ausgangstext"><option value="groq">Unser Transkript · Groq</option><option value="mlx">Unser Transkript · MacBook</option><option value="mitschrift">Deine Mitschrift · ungefähr</option></select></header><div class="meta">Hörprobe 3 · 8. Oktober · 0:00–0:30 · MossFormer2</div><audio controls preload="metadata" src="whisperx-vergleich.mp3" aria-label="Originale Hörprobe zum Gegenhören"></audio><div class="grid"><section class="card"><h2>Ausgangstext</h2><div id="original" class="text"></div></section><section class="card"><div class="card-head"><h2>Lesefassung</h2><span class="label">KI-Vorschlag</span></div><div id="reading" class="text"></div><div class="links"><a id="download" download>Text herunterladen</a></div></section></div><p class="note">Lesbarer Text kann weiterhin Erkennungsfehler enthalten. Die Lesefassung ist kein wortgetreues Transkript.</p><p id="hint" class="note"></p></main><script>
const DATA=PAYLOAD;const $=s=>document.querySelector(s);function render(){const key=$('#source').value,d=DATA[key];$('#original').textContent=d.original;const target=$('#reading');target.replaceChildren();for(const part of d.lesetext.split(/(\\[[^\\]]*\\])/g)){const span=document.createElement('span');span.textContent=part;if(part.startsWith('['))span.className='unclear';target.append(span)}$('#download').href='data:text/plain;charset=utf-8,'+encodeURIComponent(d.lesetext);$('#download').download='lesefassung-'+key+'.txt';$('#hint').textContent=key==='mitschrift'?'Deine Mitschrift ist ebenfalls unsicher und dient hier als zusätzlicher Ausgangstext.':''}$('#source').onchange=render;render();
</script></html>'''.replace('PAYLOAD',payload)
    (OUT/'lesefassung-vergleich.html').write_text(page)
    embedded='data:audio/mpeg;base64,'+base64.b64encode((OUT/'whisperx-vergleich.mp3').read_bytes()).decode()
    portable=page.replace('src="whisperx-vergleich.mp3"','src="'+embedded+'"')
    (OUT/'lesefassung-vergleich-portabel.html').write_text(portable)
    print('Fertig:',OUT/'lesefassung-vergleich.html')


if __name__=='__main__':main()
