import numpy as np, wave, os

files = {
 'bgzps': r'D:\heygem_data\gpt_sovits\voice_raw\bgzps.wav',
 '公转私': r'D:\heygem_data\gpt_sovits\voice_raw\公转私.wav',
 'BGZSP20260721': r'D:\heygem_data\gpt_sovits\voice_raw\BGZSP20260721.wav',
}

def analyze(path):
    w = wave.open(path,'rb'); sr=w.getframerate(); n=w.getnframes()
    data = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32)/32768
    dur = n/sr
    win = int(0.025*sr); hop=int(0.010*sr)
    eng=[]
    for i in range(0,len(data)-win,hop):
        seg=data[i:i+win]; eng.append(np.sqrt(np.mean(seg**2)))
    eng=np.array(eng); db=20*np.log10(eng+1e-9)
    silent=np.mean(db<-45)
    speak=np.mean(db>-20)
    mean_db=float(np.mean(db)); std_db=float(np.std(db))
    seg=data[:sr*10]
    spec=np.abs(np.fft.rfft(seg)); fr=np.fft.rfftfreq(len(seg),1/sr)
    low=float(np.mean(spec[(fr>80)&(fr<400)])); high=float(np.mean(spec[(fr>400)&(fr<4000)]))
    lowr=low/(high+1e-9)
    print(f"[{os.path.basename(path)}]")
    print(f"  时长={dur:.1f}s  静音占比={silent*100:.1f}%  语音占比={speak*100:.1f}%  均值={mean_db:.1f}dB 起伏std={std_db:.1f}dB 低/中频比={lowr:.2f}")
    if silent < 0.01:
        print("  -> 几乎无停顿, 疑似含BGM(持续背景声)")
    else:
        print("  -> 有正常说话停顿, 无持续BGM概率高")
    return dur, silent, mean_db

for k,v in files.items():
    analyze(v)
