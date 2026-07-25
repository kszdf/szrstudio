#!/usr/bin/env python3
# 分片下载器：每片固定大小，并发下载，每片校验，失败重试，最后拼接并验证 zip
import requests, os, sys, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_size(url, timeout=30):
    r = requests.head(url, timeout=timeout)
    r.raise_for_status()
    return int(r.headers['Content-Length'])

def dl_chunk(url, tmpdir, i, s, e, retries=25):
    path = os.path.join(tmpdir, f"c{i:05d}.part")
    expect = e - s + 1
    if os.path.exists(path) and os.path.getsize(path) == expect:
        return i, True
    for attempt in range(retries):
        try:
            r = requests.get(url, headers={'Range': f'bytes={s}-{e}'},
                             timeout=90, stream=True)
            if r.status_code not in (200, 206):
                continue
            buf = b''
            for chunk in r.iter_content(chunk_size=1024*1024):
                buf += chunk
            if len(buf) == expect:
                with open(path, 'wb') as f:
                    f.write(buf)
                return i, True
        except Exception as ex:
            pass
    return i, False

def main():
    url, out = sys.argv[1], sys.argv[2]
    chunk = int(sys.argv[3]) * 1024 * 1024 if len(sys.argv) > 3 else 5*1024*1024
    workers = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    tmpdir = out + ".chunks"
    os.makedirs(tmpdir, exist_ok=True)

    total = get_size(url)
    n = (total + chunk - 1) // chunk
    print(f"[info] total={total} chunks={n} chunk={chunk} workers={workers}", flush=True)
    ranges = [(i, i*chunk, min((i+1)*chunk, total)-1) for i in range(n)]

    failed = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(dl_chunk, url, tmpdir, *r) for r in ranges]
        done = 0
        for f in as_completed(futs):
            i, ok = f.result()
            done += 1
            if not ok:
                failed.append(i)
            if done % 20 == 0 or not ok:
                print(f"[prog] {done}/{n}  chunk {i} {'OK' if ok else 'FAIL'}", flush=True)

    if failed:
        print(f"[FAIL] chunks failed: {failed}", flush=True)
        sys.exit(1)

    # 拼接
    with open(out, 'wb') as wf:
        for i in range(n):
            p = os.path.join(tmpdir, f"c{i:05d}.part")
            with open(p, 'rb') as rf:
                wf.write(rf.read())
    print(f"[ok] merged -> {out}", flush=True)

    # 验证 zip
    try:
        z = zipfile.ZipFile(out)
        bad = z.testzip()
        print(f"[zip] files={len(z.namelist())} bad={bad}", flush=True)
    except Exception as e:
        print(f"[zip] INVALID: {e}", flush=True)
        sys.exit(1)

if __name__ == '__main__':
    main()
