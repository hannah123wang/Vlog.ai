"""Command line.  python -m vlogai run ~/Movies/Iceland --brief "..." --minutes 4 --music song.mp3"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import describe, export_xml, frames, probe, render, story, storyboard, transcribe
from .common import Project, log, require_ffmpeg
from .llm import get_llm

LANGS = {"zh": "Simplified Chinese", "en": "English"}


def _project(args) -> Project:
    if args.work:
        return Project(args.work)
    if getattr(args, "footage", None):
        return Project(Path("projects") / Path(args.footage).expanduser().resolve().name)
    raise SystemExit("请用 --work 指定项目文件夹，例如 --work projects/Iceland")


def _opts(args, p: Project) -> dict:
    """CLI flags win; otherwise reuse what this project used last time."""
    s = p.settings()
    return p.save_settings(
        brief=args.brief if getattr(args, "brief", None) is not None else s.get("brief", ""),
        minutes=getattr(args, "minutes", None) or s.get("minutes", 3.0),
        lang=getattr(args, "lang", None) or s.get("lang", "zh"),
        music=str(Path(args.music).expanduser().resolve()) if getattr(args, "music", None) else s.get("music"),
        vertical=args.vertical if getattr(args, "vertical", None) is not None else s.get("vertical", False),
    )


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vlogai", description="AI 旅行 vlog 粗剪工具（MVP）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, help_, footage=False):
        sp = sub.add_parser(name, help=help_)
        if footage:
            sp.add_argument("footage", help="素材文件夹")
        sp.add_argument("--work", "-w", help="项目文件夹（中间文件与输出都在这里）")
        sp.add_argument("--mock", action="store_true", help="离线模拟模式，不调用 AI，不花钱")
        sp.add_argument("--brief", help="用一两句话告诉 AI 这趟旅行的故事/感觉")
        sp.add_argument("--minutes", type=float, help="目标时长（分钟），默认 3")
        sp.add_argument("--lang", choices=LANGS, help="字幕和笔记语言 zh/en，默认 zh")
        sp.add_argument("--music", help="背景音乐文件（mp3/m4a/wav），会自动卡点")
        sp.add_argument("--vertical", action=argparse.BooleanOptionalAction, default=None,
                        help="输出竖屏 1080x1920（--no-vertical 切回横屏）")
        sp.add_argument("--hd", action="store_true", help="预览用 1080p（默认 720p，更快）")
        sp.add_argument("--no-snap", action="store_true", help="不按音乐节拍调整剪辑点")
        sp.add_argument("--font", help="字幕字体文件路径")
        sp.add_argument("--whisper", default="small", help="Whisper 模型大小 tiny/base/small/medium")
        sp.add_argument("--workers", type=int, default=4)
        return sp

    add("run", "一键跑完整流程", footage=True)
    add("scan", "1 扫描素材、读元数据", footage=True)
    add("frames", "2 切镜头、抽关键帧、质量打分")
    add("transcribe", "3 语音转文字（可选，需要 faster-whisper）")
    add("describe", "4 AI 逐个镜头写日志")
    add("story", "5 AI 编故事，生成 story.json")
    rv = add("revise", "用一句话让 AI 修改剪辑")
    rv.add_argument("instruction", help='例如 "开头更有冲击力，删掉重复的日落"')
    add("recheck", "手改 story.json 之后，重新校验")
    add("board", "生成 storyboard.html 故事板")
    add("render", "6 渲染预览 mp4")
    add("export", "7 导出 Resolve/Premiere 时间线 XML")

    args = ap.parse_args(argv)
    from . import applog, config
    applog.setup(config.get("log_level"), console=False)   # print 照常上屏，同时写进 logs/vlogai.log
    if not isinstance(sys.stdout, applog.TeeStdout):
        sys.stdout = applog.TeeStdout(sys.stdout)
    applog.log.info(f"命令行：vlogai {' '.join(argv if argv is not None else sys.argv[1:])}")
    require_ffmpeg()
    p = _project(args)
    o = _opts(args, p)
    lang = LANGS[o["lang"]]
    target = float(o["minutes"]) * 60
    llm = get_llm(args.mock) if args.cmd in ("run", "describe", "story", "revise") else None
    log(f"项目文件夹：{p.root}")

    if args.cmd in ("run", "scan"):
        probe.scan(args.footage, p)
    if args.cmd in ("run", "frames"):
        frames.extract(p, workers=args.workers)
    if args.cmd in ("run", "transcribe"):
        transcribe.transcribe(p, args.whisper)
    if args.cmd in ("run", "describe"):
        describe.describe_all(p, llm, lang=lang, workers=args.workers)
    if args.cmd in ("run", "story"):
        story.build_story(p, llm, o["brief"], target, lang)
    if args.cmd == "revise":
        story.revise_story(p, llm, args.instruction, target, lang)
    if args.cmd == "recheck":
        story.recheck(p)
        log("story.json 校验完成")
    if args.cmd in ("run", "story", "revise", "recheck", "board"):
        storyboard.storyboard(p)
    if args.cmd in ("run", "render", "revise", "recheck"):
        render.render(p, music=o.get("music"), vertical=o.get("vertical", False), hd=args.hd,
                      font=args.font, snap=not args.no_snap)
    if args.cmd in ("run", "export", "render", "revise", "recheck"):
        if args.cmd == "export":
            render.final_timeline(p, o.get("music"), snap=not args.no_snap)
        export_xml.export_xml(p, music=o.get("music"))
    if llm is not None and llm.usage["input_tokens"]:
        log(f"本次 AI 用量合计：输入 {llm.usage['input_tokens']:,} / 输出 {llm.usage['output_tokens']:,} tokens")


if __name__ == "__main__":
    main()
