"""Offizieller FlowSE-Checkpoint, ohne Textvorgabe, 32 Schritte, lokale Hörprobe."""
import gc
import hashlib
import json
import sys
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / 'tools/speech-lab/FlowSE'
sys.path.insert(0, str(REPO))


def main():
    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    import yaml
    from huggingface_hub import hf_hub_download
    from vocos import Vocos
    from model import CFM, DiT
    from model.model_utils import get_tokenizer

    torch.set_num_threads(4)
    torch.manual_seed(0)
    out = ROOT / 'beispiele/filtervergleich'
    conf = yaml.safe_load((REPO / 'config/train.yaml').read_text())['model']
    vocab, size = get_tokenizer(str(REPO / conf['tokenizer_path']), 'custom')
    checkpoint = ROOT / 'tools/speech-lab/flowse-model/wenetspeech4tts_Premium.pt.tar'
    print('Lade FlowSE-Gewichte', flush=True)
    state = torch.load(checkpoint, map_location='cpu', mmap=True, weights_only=False)
    network = CFM(transformer=DiT(**conf['arch'], text_num_embeds=size, mel_dim=100),
                  mel_spec_kwargs=conf['mel_spec'], vocab_char_map=vocab).eval()
    network.load_state_dict(state['model_state_dict'])
    del state
    gc.collect()
    device = 'mps' if torch.backends.mps.is_available() else 'cpu'
    network.to(device)
    print('FlowSE-Gerät:', device, flush=True)
    vocoder = Vocos.from_hparams(str(REPO / 'vocos-mel-24khz/config.yaml'))
    vocoder_path = hf_hub_download('charactr/vocos-mel-24khz', 'pytorch_model.bin')
    vocoder.load_state_dict(torch.load(vocoder_path, map_location='cpu', weights_only=True))
    # iSTFT benötigt auf dieser PyTorch/Mac-Version CPU-Unterstützung.
    vocoder = vocoder.eval()

    # Dieselbe 16k→24k-Eingangskonvertierung wie im offiziellen Inferenzskript.
    audio, sr = sf.read(out / 'anfang-original.wav', dtype='float32')
    audio16 = librosa.resample(audio, orig_sr=sr, target_sr=16000)
    audio24 = torch.from_numpy(audio16).unsqueeze(0)
    import torchaudio
    audio24 = torchaudio.transforms.Resample(16000, 24000)(audio24).to(device)
    print('Verarbeite 30 Sekunden in 5-Sekunden-Abschnitten, ohne Textvorgabe, 32 Schritte', flush=True)
    calls = 0
    def progress(module, args):
        nonlocal calls
        calls += 1
        if calls % 8 == 0:
            print(f'FlowSE-Modellaufrufe: {calls}/64', flush=True)
    hook = network.transformer.register_forward_pre_hook(progress)
    expected = 30 * 24000
    restored = np.zeros(expected, dtype=np.float32)
    weights = np.zeros(expected, dtype=np.float32)
    chunk_info = []
    context = 12000
    source_hash = hashlib.sha256((out / 'anfang-original.wav').read_bytes()).hexdigest()
    cache = out / ('flowse-chunks-amp-v1-' + source_hash[:16])
    cache.mkdir(exist_ok=True)
    for start in range(0, expected, 5 * 24000):
        ab, end = max(0, start-context), min(expected, start+5*24000+context)
        print(f'FlowSE-Abschnitt: {ab/24000:.1f}–{end/24000:.1f} s', flush=True)
        calls = 0
        cached = cache / f'{start}.npy'
        if cached.exists():
            part = np.load(cached, allow_pickle=False)
            print('Gespeicherten Abschnitt übernommen', flush=True)
        else:
            torch.manual_seed(start // (5*24000))
            with torch.inference_mode():
                amp = torch.autocast('mps', dtype=torch.float16) if device == 'mps' else nullcontext()
                with amp:
                    result, _ = network.sample(cond=audio24[:, ab:end], text=[' '], drop_text=True, steps=32)
                part = vocoder.decode(result.transpose(-1, -2).float().cpu()).squeeze().numpy()
            if abs(len(part)-(end-ab)) > 1024 or not np.isfinite(part).all():
                raise ValueError('Ungültiger FlowSE-Abschnitt; nicht gespeichert.')
            with cached.with_suffix('.tmp').open('wb') as handle:
                np.save(handle, part, allow_pickle=False)
            cached.with_suffix('.tmp').replace(cached)
        length = end-ab
        delta = len(part)-length
        if abs(delta) > 1024 or not np.isfinite(part).all():
            raise ValueError(f'Unerwartete Ausgabe: {len(part)} Samples')
        part = np.pad(part[:length], (0, max(0, length-len(part))))
        fade = np.ones(length, dtype=np.float32)
        overlap = 2*context
        if ab:
            fade[:overlap] = np.linspace(0, 1, overlap)
        if end < expected:
            fade[-overlap:] = np.linspace(1, 0, overlap)
        restored[ab:end] += part*fade
        weights[ab:end] += fade
        chunk_info.append({'start': ab/24000, 'end': end/24000, 'length_difference_samples': delta})
    hook.remove()
    if not np.all(weights > 0):
        raise ValueError('Lücke zwischen Audioabschnitten.')
    restored /= weights
    # Float-Ausgabe; Lautheitsabgleich erfolgt erst für die Vergleichsdateien.
    sf.write(out / 'anfang-flowse.wav', restored, 24000, subtype='FLOAT')
    (out / 'flowse-einstellungen.json').write_text(json.dumps({
        'checkpoint': str(checkpoint.relative_to(ROOT)), 'device': device,
        'checkpoint_sha256': 'c342175051d1ca38338993228e8a632181b9b556f9f1e310ac3e07bef54f446f',
        'steps': 32, 'cfg_strength': 1.0, 'drop_text': True, 'seed': 0,
        'chunks': chunk_info, 'core_seconds': 5, 'context_seconds': 0.5,
        'precision': 'MPS autocast float16' if device == 'mps' else 'float32',
        'vocoder_device': 'cpu', 'source_sha256': source_hash,
        'chunk_seed': '0 + chunk_index',
    }, indent=2))
    print('FlowSE fertig', flush=True)


if __name__ == '__main__':
    main()
