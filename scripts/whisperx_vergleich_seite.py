"""Build a local, portable listening + word timing comparison from real model results."""
import base64
import difflib
import re
import unicodedata
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'work/whisperx-vergleich'
OUT=ROOT/'beispiele/filtervergleich'


def clean(value):
    if isinstance(value,float) and not math.isfinite(value):return None
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
    if isinstance(value,list):return [clean(v) for v in value]
    return value


def main():
    data={}
    for name in ('groq','mlx','whisperx','groq-aligned','mlx-aligned'):
        path=WORK/(name+'.json')
        if not path.exists():continue
        raw=clean(json.loads(path.read_text()))
        data[name]={'words':raw.get('word_segments',raw.get('words',[])),
                    'text':' '.join(s['text'].strip() for s in raw['segments']),
                    'source_sha256':raw['source_sha256']}
        if name.endswith('-aligned'):
            original=name.removesuffix('-aligned')
            data[name]['text']=data[original]['text']
        # Render the actual complete transcript; never hide words the aligner omitted.
        tokens=re.findall(r'\S+',data[name]['text'])
        normal=lambda text: ''.join(c for c in unicodedata.normalize('NFKC',text).casefold() if c.isalnum())
        source_words=data[name]['words'];mapped={}
        matcher=difflib.SequenceMatcher(None,[normal(t) for t in tokens],[normal(w['word']) for w in source_words],autojunk=False)
        for block in matcher.get_matching_blocks():
            for j in range(block.size):mapped[block.a+j]=source_words[block.b+j]
        data[name]['words']=[{**mapped.get(i,{}),'word':text} for i,text in enumerate(tokens)]
        (OUT/('transkript-'+name+'.txt')).write_text(data[name]['text']+'\n')
    assert {'groq','whisperx','groq-aligned'}<=data.keys()
    assert len({v['source_sha256'] for v in data.values()})==1,'Different source audio'
    for variant in data.values():
        for w in variant['words']:
            if w.get('start') is not None and w.get('end') is not None:
                assert 0<=w['start']<=w['end']<=30.05,w
    public={name:{'text':v['text'] if not name.endswith('-aligned') else '', 'words':[{k:w[k] for k in ('word','start','end') if k in w} for w in v['words']]} for name,v in data.items()}
    payload=json.dumps(public,ensure_ascii=False,allow_nan=False,separators=(',',':')).replace('<','\\u003c')
    page='''<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Soundcore · WhisperX vergleichen</title>
<style>
:root{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#272633;background:#f7f7f9;--accent:#6254c7}*{box-sizing:border-box}body{margin:0}main{max-width:1180px;margin:auto;padding:30px 24px 60px}header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:8px}h1{font-size:23px;letter-spacing:-.7px;margin:0}h2{font-size:17px;letter-spacing:-.3px;margin:0}.meta,.intro{font-size:12px;color:#777582;line-height:1.6;margin:0}.intro{margin:6px 0 22px}button,select{font:inherit;color:inherit;border:1px solid #e1deeb;background:white;border-radius:8px;padding:9px 12px;cursor:pointer}button:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid #bcb3f1;outline-offset:3px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.card{background:white;border:1px solid #e7e6ed;border-radius:14px;padding:22px;min-width:0}.card-head{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:16px}select{font-size:12px;max-width:210px;width:100%;min-width:0}.mode{display:flex;gap:8px;align-items:center;margin-bottom:16px}.mode label{font-size:12px;color:#777582;flex-shrink:0}audio{display:block;width:100%;height:40px;margin-bottom:18px}.transcript{font-size:16px;line-height:1.95;min-height:270px}.word{font:inherit;line-height:inherit;background:transparent;border:0;border-radius:4px;padding:1px 2px;margin:0;cursor:pointer;transition:background .08s}.word:hover{background:#f0edf9}.word.active{background:#ded6ff;color:#443094}.untimed{border-bottom:1px dotted #bcb6c9}.links{display:flex;gap:16px;align-items:center;flex-wrap:wrap;border-top:1px solid #efedf3;padding-top:16px;margin-top:20px;font-size:12px}.links a{color:#6254c7;text-decoration:none}.note{font-size:11px;color:#8d8798;line-height:1.6;margin:16px 0 0}details{margin-top:24px;border-top:1px solid #e7e6ed;padding-top:16px;font-size:12px;color:#777582}summary{cursor:pointer}.timings{overflow:auto;max-height:360px;margin-top:15px}table{border-collapse:collapse;width:100%;font-size:12px}th,td{text-align:left;padding:8px;border-bottom:1px solid #e7e6ed;white-space:nowrap}th{position:sticky;top:0;background:#f7f7f9}.top-actions{display:flex;gap:8px;align-items:center}.toggle{font-size:11px;padding:8px}.toggle[aria-pressed=true]{background:#f0edf9;border-color:#d2c8ee;color:#51419d}.status{font-size:12px;min-height:18px;color:#a44744;margin:14px 0}p{margin:0}.changed{background:#fff4d9}#differences{margin-top:14px;line-height:1.8}.tip{margin:12px 0 0;font-size:11px;color:#8d8798}@media(max-width:720px){main{padding:22px 16px}.grid{grid-template-columns:1fr}.card{padding:18px}.transcript{min-height:0}header{align-items:start;flex-wrap:wrap}.top-actions{flex-wrap:wrap}.card-head{flex-wrap:wrap}}@media(prefers-reduced-motion:reduce){*{transition:none!important}}
</style><main><header><h1>WhisperX vergleichen</h1><div class="top-actions"><button id="keep" class="toggle" aria-pressed="true">Stelle beibehalten</button><button id="reset" class="toggle">Zurücksetzen</button></div></header>
<p class="meta">Hörprobe 3 · 8. Oktober · 0:00–0:30 · MossFormer2</p><p class="intro">Identischer Ton. Vergleiche den Text und die markierten Wörter.</p>
<div class="grid"><section class="card"><div class="card-head"><h2>Unser System</h2><select id="provider" aria-label="Transkript unseres Systems"><option value="groq">Groq · Whisper large-v3</option><option value="mlx">MacBook · Whisper large-v3</option></select></div><div class="mode"><label for="left-label">Wortzeiten</label><span id="left-label" class="meta">Direkt aus Whisper</span></div><audio id="audio-left" controls preload="metadata" src="whisperx-vergleich.mp3" aria-label="Hörprobe mit Wortzeiten unseres Systems"></audio><div id="text-left" class="transcript"></div><div class="links"><a id="download-left" href="whisperx-vergleich.wav" download="unser-system.wav">Audio herunterladen</a><a id="text-download-left" download>Text herunterladen</a></div></section>
<section class="card"><div class="card-head"><h2>WhisperX</h2><span class="meta">Whisper large-v3 + Alignment</span></div><div class="mode"><label for="mode">Vergleich</label><select id="mode"><option value="native">Eigene Transkription</option><option value="alignment">Gleicher Text wie links</option></select></div><audio id="audio-right" controls preload="metadata" src="whisperx-vergleich.mp3" aria-label="Identische Hörprobe mit WhisperX-Wortzeiten"></audio><div id="text-right" class="transcript"></div><div class="links"><a id="download-right" href="whisperx-vergleich.wav" download="whisperx.wav">Audio herunterladen</a><a id="text-download-right" download>Text herunterladen</a></div><p id="right-note" class="note"></p></section></div>
<p class="tip">Wort anklicken → Audiostelle. „Gleicher Text wie links“ vergleicht ausschließlich die Wortzeiten.</p><p id="status" class="status" role="status"></p>
<details><summary>Textunterschiede und Wortzeiten</summary><p class="note">Zeitunterschiede sind kein Genauigkeitsnachweis. Entscheidend ist, ob die Markierung zum hörbaren Wort passt.</p><div id="differences"></div><div class="timings"><table><thead><tr><th>Unser Text</th><th>Zeit</th><th>WhisperX-Text</th><th>Zeit</th></tr></thead><tbody id="times"></tbody></table></div></details></main>
<script>const DATA=PAYLOAD;for(const name of Object.keys(DATA))if(name.endsWith('-aligned'))DATA[name].text=DATA[name.slice(0,-8)].text;
const $=s=>document.querySelector(s),players=[$('#audio-left'),$('#audio-right')];let current=null,keep=true,raf=0,views={},active={left:null,right:null};
function time(t){return Number.isFinite(t)?`${Math.floor(t/60)}:${(t%60).toFixed(2).padStart(5,'0')}`:'—'}
function variantKeys(){const left=$('#provider').value;return [left,$('#mode').value==='alignment'?left+'-aligned':'whisperx']}
function drawWords(side,key){const root=$('#text-'+side);root.replaceChildren();const nodes=[];for(const w of DATA[key].words){const timed=Number.isFinite(w.start)&&Number.isFinite(w.end)&&w.end>w.start;const node=document.createElement(timed?'button':'span');node.className='word'+(timed?'':' untimed');node.textContent=w.word;node.title=timed?`${time(w.start)} – ${time(w.end)}`:'Keine verlässliche Wortzeit';if(timed){node.type='button';node.setAttribute('aria-label',`${w.word} ab ${time(w.start)} abspielen`);node.onclick=()=>jump(side,w.start);nodes.push({node,start:w.start,end:w.end})}root.append(node,document.createTextNode(' '))}views[side]=nodes;active[side]=null;$('#text-download-'+side).href='transkript-'+key+'.txt'}
function render(){const [left,right]=variantKeys();drawWords('left',left);drawWords('right',right);$('#right-note').textContent=$('#mode').value==='alignment'?'Text unverändert · nur Wortzeiten neu zugeordnet.':'In diesem Lauf fehlt der Anfangstext der beiden anderen Fassungen. Bitte gegenhören; auch Wortzeiten können dadurch verrutschen.';const body=$('#times');body.replaceChildren();const a=DATA[left].words,b=DATA[right].words;for(let i=0;i<Math.max(a.length,b.length);i++){const tr=document.createElement('tr');for(const value of [a[i]?.word||'',a[i]?`${time(a[i].start)}–${time(a[i].end)}`:'',b[i]?.word||'',b[i]?`${time(b[i].start)}–${time(b[i].end)}`:'']){const td=document.createElement('td');td.textContent=value;tr.append(td)}body.append(tr)}const same=DATA[left].text===DATA[right].text;$('#differences').textContent=same?'Identischer Text – vergleiche die Wortzeiten.':'Die beiden erkannten Texte unterscheiden sich. Die Tabelle zeigt die Wörter jeweils in ihrer Reihenfolge.';update()}
function mark(side,t){const list=views[side]||[];let lo=0,hi=list.length;while(lo<hi){const mid=(lo+hi)>>1;if(list[mid].start<=t)lo=mid+1;else hi=mid}let word=null;for(let i=lo-1;i>=Math.max(0,lo-4);i--)if(t<list[i].end){word=list[i].node;break}if(active[side]===word)return;active[side]?.classList.remove('active');active[side]=word;word?.classList.add('active')}
function update(){const t=current?.currentTime||0;mark('left',t);mark('right',t)}
function tick(){update();if(current&&!current.paused)raf=requestAnimationFrame(tick)}
async function jump(side,t){const a=side==='left'?players[0]:players[1];for(const other of players)other.pause();current=a;const seek=()=>{a.currentTime=Math.max(0,t-.06);update()};if(a.readyState)seek();else a.addEventListener('loadedmetadata',seek,{once:true});try{await a.play()}catch(e){$('#status').textContent='Bitte noch einmal Play drücken.'}}
for(const a of players){a.onplay=()=>{const previous=current;if(previous&&previous!==a&&keep)a.currentTime=previous.currentTime;for(const other of players)if(other!==a)other.pause();current=a;$('#status').textContent='';cancelAnimationFrame(raf);raf=requestAnimationFrame(tick)};a.ontimeupdate=()=>{if(a===current)update()};a.onseeked=()=>{if(!current||current.paused)current=a;update()};a.onpause=()=>{if(a===current){cancelAnimationFrame(raf);update()}};a.onerror=()=>$('#status').textContent='Audio konnte nicht geladen werden. Bitte die Seite neu laden.'}
$('#provider').onchange=render;$('#mode').onchange=render;$('#keep').onclick=e=>{keep=!keep;e.currentTarget.setAttribute('aria-pressed',String(keep))};$('#reset').onclick=()=>{for(const a of players){a.pause();a.currentTime=0}current=null;cancelAnimationFrame(raf);update()};if(!DATA.mlx||!DATA['mlx-aligned'])$('#provider option[value=mlx]').remove();render();
</script></html>'''.replace('PAYLOAD',payload)
    (OUT/'whisperx-vergleich.html').write_text(page)
    embedded='data:audio/mpeg;base64,'+base64.b64encode((OUT/'whisperx-vergleich.mp3').read_bytes()).decode()
    portable=page.replace('src="whisperx-vergleich.mp3"','').replace('href="whisperx-vergleich.wav"','href="#"')
    bootstrap="const AUDIO="+json.dumps(embedded)+";document.querySelectorAll('audio').forEach(a=>a.src=AUDIO);document.querySelectorAll('#download-left,#download-right').forEach(a=>{a.href=AUDIO;a.download=a.id==='download-left'?'unser-system.mp3':'whisperx.mp3'});"
    portable=portable.replace('<script>','<script>'+bootstrap,1)
    # Text downloads are generated in-browser so the portable page works offline too.
    portable=portable.replace("$('#text-download-'+side).href='transkript-'+key+'.txt'", "$('#text-download-'+side).href='data:text/plain;charset=utf-8,'+encodeURIComponent(DATA[key].text);$('#text-download-'+side).download='transkript-'+key+'.txt'")
    (OUT/'whisperx-vergleich-portabel.html').write_text(portable)
    (OUT/'whisperx-ergebnisse.json').write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False))
    print('Vergleich erstellt:',OUT/'whisperx-vergleich.html')


if __name__=='__main__':main()
