"""Step 7 — export the cut as an editable timeline (FCP7 XML / "xmeml").

Opens in DaVinci Resolve (File > Import > Timeline) and Adobe Premiere Pro (File > Import),
linked to your ORIGINAL full-quality files, so you keep editing there: every cut is
a normal clip you can trim, move, colour-grade, re-time.

Tracks:  V1 = picture · A1 = original sound (muted where the AI chose not to keep it —
         just enable it) · A2 = music.  AI chapter names and editor notes become markers.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from .common import Project, log, read_json


def _rate(fps: float) -> tuple[int, bool]:
    tb = int(round(fps))
    return tb, abs(fps - tb) > 0.01  # (timebase, ntsc?)


def _rate_xml(fps: float) -> str:
    tb, ntsc = _rate(fps)
    return f"<rate><timebase>{tb}</timebase><ntsc>{'TRUE' if ntsc else 'FALSE'}</ntsc></rate>"


def _url(path: str) -> str:
    """Resolve/Premiere 要 file:// URL；Windows 的 C:\\x 和 mac 的 /x 格式不同，交给 as_uri()。"""
    uri = Path(path).resolve().as_uri()          # file:///C:/... 或 file:///Users/...
    return uri.replace("file:///", "file://localhost/", 1)


def _file_xml(fid: str, path: str, fps: float, frames: int, w: int, h: int,
              has_video: bool, has_audio: bool, seen: set) -> str:
    if fid in seen:
        return f'<file id="{fid}"/>'
    seen.add(fid)
    media = ""
    if has_video:
        media += (f"<video><samplecharacteristics>{_rate_xml(fps)}<width>{w}</width>"
                  f"<height>{h}</height></samplecharacteristics></video>")
    if has_audio:
        media += ("<audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate>"
                  "</samplecharacteristics><channelcount>2</channelcount></audio>")
    return (f'<file id="{fid}"><name>{escape(Path(path).name)}</name><pathurl>{escape(_url(path))}</pathurl>'
            f"{_rate_xml(fps)}<duration>{frames}</duration><media>{media}</media></file>")


def _marker(name: str, comment: str, frame: int) -> str:
    return (f"<marker><name>{escape(name)}</name><comment>{escape(comment)}</comment>"
            f"<in>{frame}</in><out>-1</out></marker>")


def export_xml(project: Project, music: str | None = None, out_name: str = "timeline.xml") -> Path:
    tl = read_json(project.out("timeline_final.json")) or (read_json(project.story_json) or {}).get("timeline")
    if not tl:
        raise SystemExit("没有时间线，先运行 story")
    story = read_json(project.story_json, {}) or {}
    from .lab import all_clips
    clips = all_clips(project)          # 包括镜头工坊做出的片段

    # sequence frame rate = most common source rate
    rates = [round(clips[t["src"]]["fps"]) for t in tl]
    seq_fps = float(max(set(rates), key=rates.count)) if rates else 30.0
    if seq_fps in (30.0, 24.0, 60.0) and any(abs(clips[t["src"]]["fps"] - seq_fps / 1.001) < 0.01 for t in tl):
        seq_fps = seq_fps / 1.001  # iPhone/Android "30" is usually 29.97
    stb, _ = _rate(seq_fps)

    seen: set = set()
    v_items, a_items, seq_markers = [], [], []
    cur = 0
    last_chapter = None
    for i, t in enumerate(tl, 1):
        c = clips[t["src"]]
        ctb, _ = _rate(c["fps"])
        fid = f"file-{c['id']}"
        f_in = int(round(t["src_in"] * ctb))
        f_out = max(f_in + 1, int(round(t["src_out"] * ctb)))
        length = int(round((t["src_out"] - t["src_in"]) * stb))
        start, end = cur, cur + length
        total_frames = int(round(c["duration"] * ctb))
        fx = _file_xml(fid, c["path"], c["fps"], total_frames, c["width"], c["height"],
                       True, c["has_audio"], seen)
        note = f"[{t.get('role','')}] {t.get('why','')}"
        clip_markers = _marker(t["text"] or t.get("role", ""), note, f_in)
        v_items.append(
            f'<clipitem id="v{i}"><name>{escape(c["name"])}</name><enabled>TRUE</enabled>'
            f"<duration>{total_frames}</duration>{_rate_xml(c['fps'])}"
            f"<start>{start}</start><end>{end}</end><in>{f_in}</in><out>{f_out}</out>"
            f"{fx}{clip_markers}</clipitem>")
        if c["has_audio"]:
            a_items.append(
                f'<clipitem id="a{i}"><name>{escape(c["name"])}</name>'
                f"<enabled>{'TRUE' if t.get('keep_audio') else 'FALSE'}</enabled>"
                f"<duration>{total_frames}</duration>{_rate_xml(c['fps'])}"
                f"<start>{start}</start><end>{end}</end><in>{f_in}</in><out>{f_out}</out>"
                f'<file id="{fid}"/><sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex>'
                f"</sourcetrack></clipitem>")
        if t.get("chapter") and t["chapter"] != last_chapter:
            seq_markers.append(_marker(t["chapter"], "chapter", start))
            last_chapter = t["chapter"]
        cur = end

    music_track = ""
    if music:
        m = Path(music).resolve()
        mfr = cur
        music_track = (
            "<track>"
            f'<clipitem id="music"><name>{escape(m.name)}</name><enabled>TRUE</enabled>'
            f"<duration>{mfr}</duration>{_rate_xml(seq_fps)}<start>0</start><end>{mfr}</end>"
            f"<in>0</in><out>{mfr}</out>"
            f'<file id="file-music"><name>{escape(m.name)}</name><pathurl>{escape(_url(str(m)))}</pathurl>'
            f"{_rate_xml(seq_fps)}<duration>{mfr}</duration><media><audio><samplecharacteristics>"
            "<depth>16</depth><samplerate>48000</samplerate></samplecharacteristics>"
            "<channelcount>2</channelcount></audio></media></file>"
            "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>"
            "</clipitem></track>")

    W, H = 1920, 1080
    if tl:
        c0 = clips[tl[0]["src"]]
        if c0["height"] > c0["width"]:
            W, H = 1080, 1920
    title = story.get("title") or "AI rough cut"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="5">'
        f'<sequence id="seq-1"><name>{escape(title)}</name><duration>{cur}</duration>{_rate_xml(seq_fps)}'
        "<media><video><format><samplecharacteristics>"
        f"{_rate_xml(seq_fps)}<width>{W}</width><height>{H}</height><pixelaspectratio>square</pixelaspectratio>"
        "</samplecharacteristics></format>"
        f"<track>{''.join(v_items)}</track></video>"
        f"<audio><track>{''.join(a_items)}</track>{music_track}</audio></media>"
        f"{''.join(seq_markers)}</sequence></xmeml>\n")
    out = project.out(out_name)
    out.write_text(xml, encoding="utf-8")
    log(f"时间线已导出：{out}（{len(v_items)} 个片段，{cur/seq_fps/60:.1f} 分钟，{seq_fps:.3f} fps）")
    return out
