"""Step 6 — render a preview MP4 of story.json with ffmpeg (hard cuts, captions, music + ducking)."""
from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .beats import detect_beats, snap_to_beats
from .common import Project, log, read_json, run, write_json

FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
]


def _ff_path(path) -> str:
    """ffmpeg 滤镜里的路径：统一正斜杠，转义盘符冒号和引号（否则 Windows 上字幕会让渲染失败）。"""
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def find_font(user_font: str | None = None) -> str | None:
    for f in ([user_font] if user_font else []) + FONT_CANDIDATES:
        if f and Path(f).exists():
            return f
    return None


def final_timeline(project: Project, music: str | None, snap: bool = True) -> list[dict]:
    """story.json timeline, optionally beat-snapped. Saved so preview and XML always match."""
    story = read_json(project.story_json)
    if not story or not story.get("timeline"):
        raise SystemExit("story.json 为空，先运行 story")
    tl = story["timeline"]
    if music and snap:
        shots = {s["id"]: (s["start"], s["end"]) for s in read_json(project.shots_json, [])}
        tl = snap_to_beats(tl, shots, detect_beats(music))
    write_json(project.out("timeline_final.json"), tl)
    return tl


def _segment(i: int, t: dict, clip: dict, W: int, H: int, fps: int,
             font: str | None, tmp: Path) -> Path:
    dst = tmp / f"seg_{i:04d}.mp4"
    dur = max(0.3, t["src_out"] - t["src_in"])
    same_orientation = (clip["width"] >= clip["height"]) == (W >= H)
    if same_orientation:  # fill the frame (centre crop)
        vf = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"setsar=1,fps={fps},format=yuv420p")
    else:  # vertical phone clip in a horizontal vlog (or vice versa): blurred background
        vf = (f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H},boxblur=20:2[bg];[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps},format=yuv420p")
    if t.get("text") and font:
        txt = tmp / f"seg_{i:04d}.txt"
        txt.write_text(t["text"], encoding="utf-8")
        fs = int(H * 0.065)
        vf += (f",drawtext=fontfile='{_ff_path(font)}':textfile='{_ff_path(txt)}':fontsize={fs}:fontcolor=white:"
               f"borderw=3:bordercolor=black@0.6:x=(w-text_w)/2:y=h*0.78:"
               f"alpha='if(lt(t,0.3),t/0.3,if(gt(t,{dur-0.4:.2f}),({dur:.2f}-t)/0.4,1))'")
    vf += "[v]"

    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{t['src_in']:.3f}", "-t", f"{dur:.3f}",
           "-i", t["src"]]
    use_src_audio = t.get("keep_audio") and clip["has_audio"]
    if use_src_audio:
        af = f"[0:a]aresample=48000,aformat=channel_layouts=stereo,afade=t=in:d=0.08,afade=t=out:st={max(0, dur-0.12):.3f}:d=0.12[a]"
    else:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        af = "[1:a]anull[a]"
    cmd += ["-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]",
            "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-r", str(fps), str(dst)]
    run(cmd)
    return dst


def render(project: Project, music: str | None = None, vertical: bool = False, hd: bool = False,
           font: str | None = None, snap: bool = True, music_volume: float = 0.5,
           workers: int = 2, out_name: str = "preview.mp4", captions: bool = True,
           duck: bool = True) -> Path:
    tl = final_timeline(project, music, snap)
    clips = {c["path"]: c for c in read_json(project.clips_json, [])}
    W, H = (1080, 1920) if vertical else ((1920, 1080) if hd else (1280, 720))
    fps = 30
    font = find_font(font) if captions else None
    if captions and any(t.get("text") for t in tl) and not font:
        log("  找不到中文字体，字幕将跳过（可用 --font 指定 .ttf/.ttc）")
    tmp = project.out("_render_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    log(f"渲染预览：{len(tl)} 段，{W}x{H}…")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        segs = list(ex.map(lambda a: _segment(a[0], a[1], clips[a[1]["src"]], W, H, fps, font, tmp),
                           enumerate(tl)))
    listfile = tmp / "list.txt"
    listfile.write_text("".join(f"file '{p.name}'\n" for p in segs), encoding="utf-8")
    joined = tmp / "joined.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listfile),
         "-c", "copy", str(joined)])
    total = sum(t["src_out"] - t["src_in"] for t in tl)
    out = project.out(out_name)

    if music:
        fade_st = max(0.0, total - 3.0)
        fc = ("[0:a]asplit=2[v1][v2];"
              f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={music_volume}[m];"
              # duck the music whenever original sound/speech is playing
              + ("[m][v1]sidechaincompress=threshold=0.02:ratio=12:attack=20:release=400[md];"
                 if duck else "[v1]anullsink;[m]anull[md];") +
              f"[md]afade=t=in:d=1.5,afade=t=out:st={fade_st:.2f}:d=3[mf];"
              "[v2][mf]amix=inputs=2:duration=first:normalize=0[a]")
        run(["ffmpeg", "-y", "-v", "error", "-i", str(joined), "-stream_loop", "-1", "-i", music,
             "-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)])
    else:
        shutil.move(str(joined), out)
    shutil.rmtree(tmp, ignore_errors=True)
    log(f"预览完成：{out}（{total/60:.1f} 分钟）")
    return out
