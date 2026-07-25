import wave, numpy as np

name = '公转私'
p = f'voice_raw/{name}.wav'
w = wave.open(p, 'rb'); fr = w.getframerate(); n = w.getnframes()
d = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0

win = int(fr * 0.5)
frames = np.array([d[i:i+win] for i in range(0, len(d)-win, win)])
rms = np.array([np.sqrt(np.mean(f**2)) for f in frames])
qmask = rms < np.percentile(rms, 15)
quiet = frames[qmask]
print(f'安静帧数={len(quiet)}')

import numpy.fft as fft
lowE = []; flat = []
for f in quiet[:25]:
    F = np.abs(fft.rfft(f * np.hanning(len(f))))
    freqs = fft.rfftfreq(len(f), 1/fr)
    low = np.sum(F[freqs < 250]) / max(np.sum(F), 1e-9)
    lowE.append(low)
    band = F[F > 0]
    geo = np.exp(np.mean(np.log(band + 1e-10)))
    arith = np.mean(band + 1e-10)
    flat.append(geo / arith)
print(f'安静帧低频(<250Hz)能量占比均值={np.mean(lowE):.2f}  (BGM常>0.3且有贝斯)')
print(f'安静帧谱平坦度均值={np.mean(flat):.3f}  (宽带噪声~1.0, 纯音乐~0.1-0.3)')

# 全曲前30秒低频占比
seg = d[:fr*30] * np.hanning(fr*30)
Fa = np.abs(fft.rfft(seg)); freqsa = fft.rfftfreq(fr*30, 1/fr)
print(f'全曲前30s低频(<250Hz)占比={np.sum(Fa[freqsa<250])/np.sum(Fa):.2f}')
