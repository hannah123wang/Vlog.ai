"""Generate fake 'travel footage' + a beat track so you can test the pipeline without real clips.

    python make_test_footage.py test_footage
"""
import subprocess
import sys
from pathlib import Path

out = Path(sys.argv[1] if len(sys.argv) > 1 else "test_footage")
out.mkdir(parents=True, exist_ok=True)

SOURCES = {
    "testsrc": "testsrc2=size={w}x{h}:rate=30",
    "mandel": "mandelbrot=size={w}x{h}:rate=30",
    "life": "life=size={w}x{h}:rate=30:mold=10:ratio=0.1:death_color=#1d3557:life_color=#f1faee",
    "cell": "cellauto=size={w}x{h}:rate=30:rule=110",
    "grad": "gradients=size={w}x{h}:rate=30:speed=0.05",
}

# name, day, time, source, seconds, (w,h), audio?, blurry?
CLIPS = [
    ("IMG_0001_airport", "2026-06-01T08:10:00Z", "testsrc", 6, (1280, 720), True, False),
    ("IMG_0002_plane_window", "2026-06-01T10:30:00Z", "grad", 9, (1280, 720), False, False),
    ("IMG_0003_selfie_vertical", "2026-06-01T15:02:00Z", "life", 7, (720, 1280), True, False),
    ("IMG_0004_blurry", "2026-06-01T15:05:00Z", "mandel", 5, (1280, 720), False, True),
    ("IMG_0005_street_food", "2026-06-01T19:40:00Z", "cell", 8, (1280, 720), True, False),
    ("IMG_0006_sunrise", "2026-06-02T05:50:00Z", "grad", 12, (1280, 720), False, False),
    ("IMG_0007_hike", "2026-06-02T09:15:00Z", "life", 10, (1280, 720), True, False),
    ("IMG_0008_summit", "2026-06-02T12:00:00Z", "mandel", 8, (1280, 720), True, False),
    ("IMG_0009_long_walk_multi_scene", "2026-06-02T16:30:00Z", None, 30, (1280, 720), True, False),
    ("IMG_0010_market", "2026-06-03T10:00:00Z", "testsrc", 7, (1280, 720), True, False),
    ("IMG_0011_beach", "2026-06-03T17:20:00Z", "grad", 11, (1280, 720), False, False),
    ("IMG_0012_goodbye", "2026-06-03T21:00:00Z", "cell", 6, (720, 1280), True, False),
]


def ff(args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


for i, (name, ts, src, secs, (w, h), audio, blurry) in enumerate(CLIPS):
    dst = out / f"{name}.mp4"
    if src is None:  # a long clip with three hard scene changes
        parts = [SOURCES[k].format(w=w, h=h) for k in ("testsrc", "life", "mandel")]
        inputs = []
        for p in parts:
            inputs += ["-f", "lavfi", "-t", str(secs / 3), "-i", p]
        vf = "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]"
    else:
        inputs = ["-f", "lavfi", "-t", str(secs), "-i", SOURCES[src].format(w=w, h=h)]
        vf = "[0:v]" + ("boxblur=12:4," if blurry else "") + "format=yuv420p[v]"
    n_in = 3 if src is None else 1
    cmd = inputs[:]
    if audio:
        cmd += ["-f", "lavfi", "-t", str(secs), "-i", f"sine=frequency={220 + 40 * i}:sample_rate=48000"]
    cmd += ["-filter_complex", vf, "-map", "[v]"]
    if audio:
        cmd += ["-map", f"{n_in}:a", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-metadata", f"creation_time={ts}", str(dst)]
    ff(cmd)
    print("made", dst.name)

# 120 BPM click track, 3 minutes
ff(["-f", "lavfi", "-t", "180", "-i",
    "aevalsrc='0.6*sin(2*PI*880*t)*exp(-40*mod(t,0.5))|0.6*sin(2*PI*880*t)*exp(-40*mod(t,0.5))':s=44100",
    "-c:a", "libmp3lame", "-q:a", "4", str(out.parent / "test_music.mp3")])
print("made test_music.mp3")
