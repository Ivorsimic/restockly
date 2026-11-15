#!/usr/bin/env python3
"""
Clipper - od dugog videa (YouTube link ili lokalni file) napravi gotove
vertikalne klipove (9:16) s animiranim titlovima, spremne za TikTok / Reels / Shorts.

Koraci:
  1. skine video (yt-dlp) ili uzme lokalni file
  2. transkribira govor s vremenima svake riječi (faster-whisper, radi lokalno, besplatno)
  3. odabere najbolje momente (Claude AI ako imaš API ključ, inače heuristika)
  4. izreže, prebaci u 9:16, nalijepi titlove riječ-po-riječ + hook naslov na vrhu
  5. uz svaki klip zapiše opis i hashtagove za objavu

Primjeri:
  python clipper.py "https://www.youtube.com/watch?v=XXXX"
  python clipper.py podcast.mp4 --clips 8 --min 20 --max 45 --style crop
  python clipper.py podcast.mp4 --no-ai --language hr
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_W, OUT_H = 1080, 1920


# --------------------------------------------------------------------------- utils

def run(cmd: list[str], cwd: Path | None = None) -> None:
    r = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if r.returncode != 0:
        print(r.stdout[-3000:])
        raise SystemExit(f"Naredba nije uspjela: {' '.join(cmd[:3])} ...")


def probe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


def slugify(s: str, n: int = 50) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", s)[:n] or "video"


def check_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"'{tool}' nije instaliran. Pogledaj README.md -> Instalacija.")


# --------------------------------------------------------------------------- 1. download

def get_source(src: str, work: Path) -> Path:
    if Path(src).exists():
        return Path(src).resolve()
    if not re.match(r"https?://", src):
        raise SystemExit(f"'{src}' nije ni postojeći file ni link.")
    import yt_dlp

    work.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(work / "%(title).80s.%(ext)s"),
        "quiet": True,
        "noprogress": True,
    }
    print("⬇  Skidam video ...")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(src, download=True)
        path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
    if not path.exists():  # yt-dlp ponekad ostavi drugu ekstenziju
        cands = sorted(work.glob("*.*"), key=lambda p: p.stat().st_mtime)
        path = cands[-1]
    return path


# --------------------------------------------------------------------------- 2. transcribe

@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word]


def transcribe(video: Path, work: Path, model_size: str, language: str | None) -> list[Segment]:
    cache = work / "transcript.json"
    if cache.exists():
        print("📝 Koristim spremljeni transkript.")
        data = json.loads(cache.read_text(encoding="utf-8"))
        return [Segment(s["start"], s["end"], s["text"], [Word(**w) for w in s["words"]]) for s in data]

    from faster_whisper import WhisperModel

    audio = work / "audio.wav"
    run(["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(audio)])
    print(f"📝 Transkribiram (model '{model_size}', ovo može potrajati) ...")
    model = WhisperModel(model_size, device="auto", compute_type="auto")
    segs, info = model.transcribe(str(audio), language=language, word_timestamps=True, vad_filter=True)
    out: list[Segment] = []
    for s in segs:
        words = [Word(w.start, w.end, w.word.strip()) for w in (s.words or []) if w.word.strip()]
        out.append(Segment(s.start, s.end, s.text.strip(), words))
        print(f"   [{s.start:7.1f}s] {s.text.strip()[:80]}")
    print(f"   Jezik: {info.language}")
    cache.write_text(json.dumps([asdict(s) for s in out], ensure_ascii=False, indent=1), encoding="utf-8")
    audio.unlink(missing_ok=True)
    return out


# --------------------------------------------------------------------------- 3. pick highlights

@dataclass
class Clip:
    start: float
    end: float
    title: str          # kratki hook koji ide na vrh videa
    caption: str        # opis za objavu
    hashtags: list[str]
    reason: str = ""
    score: int = 0


HOOK_WORDS = {
    # en
    "never", "secret", "crazy", "insane", "money", "million", "why", "how", "truth", "mistake",
    "nobody", "everyone", "worst", "best", "actually", "biggest", "stop", "wrong", "real",
    # hr
    "nikad", "nikada", "tajna", "lud", "ludo", "novac", "novca", "pare", "milijun", "zašto", "kako",
    "istina", "greška", "nitko", "svi", "najgore", "najbolje", "zapravo", "najveći", "stani", "krivo",
}


def pick_heuristic(segs: list[Segment], n: int, lo: float, hi: float) -> list[Clip]:
    """Bez AI-ja: ocijeni prozore po gustoći govora, pitanjima/uzvicima i 'hook' riječima."""
    cands = []
    for i, s in enumerate(segs):
        j, end = i, s.end
        while j + 1 < len(segs) and segs[j + 1].end - s.start <= hi:
            j += 1
            end = segs[j].end
            if end - s.start >= (lo + hi) / 2:
                break
        dur = end - s.start
        if dur < lo:
            continue
        text = " ".join(x.text for x in segs[i:j + 1])
        words = re.findall(r"\w+", text.lower())
        density = len(words) / dur
        score = density * 2 + text.count("?") * 1.5 + text.count("!") * 1.5
        score += sum(1 for w in words if w in HOOK_WORDS) * 1.2
        score += 2 if re.search(r"\d", text) else 0
        score += 1.5 if any(h in s.text.lower() for h in HOOK_WORDS) else 0  # jak početak
        cands.append((score, s.start, end, text))

    cands.sort(reverse=True)
    picked: list[Clip] = []
    for score, a, b, text in cands:
        if all(b <= c.start or a >= c.end for c in picked):
            first = re.split(r"(?<=[.!?])\s", text)[0]
            picked.append(Clip(a, b, " ".join(first.split()[:7]).upper(), first, [], "heuristika", round(score)))
        if len(picked) >= n:
            break
    top = max((c.score for c in picked), default=1) or 1
    for c in picked:  # relativna ocjena 1-10
        c.score = max(1, round(10 * c.score / top))
    return sorted(picked, key=lambda c: c.start)


CLIP_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "title": {"type": "string"},
                    "caption": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                    "score": {"type": "integer"},
                },
                "required": ["start", "end", "title", "caption", "hashtags", "reason", "score"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}

AI_PROMPT = """You are an expert short-form video editor who cuts viral clips (TikTok, Instagram Reels, YouTube Shorts) from long-form content for "clipping" campaigns, where the clipper is paid per view.

Below is a timestamped transcript. Pick the {n} best self-contained moments to post as clips.

What makes a good clip:
- The first 1-3 seconds hook the viewer (bold claim, question, conflict, surprising number, strong emotion). Start the clip exactly on that hook, not on filler.
- It makes sense to someone who has not seen the rest of the video, and has a payoff (punchline, answer, story resolution, strong opinion) before it ends.
- End right after the payoff - do not trail into the next topic.
- Each clip must be between {lo:.0f} and {hi:.0f} seconds long. Clips must not overlap.
- Use the segment timestamps given; start/end are in seconds.

For each clip also write, in the language of the transcript{lang_note}:
- title: an on-screen hook of max 7 words (it is burned onto the top of the video for the first seconds)
- caption: a post caption, 1-2 short sentences, curiosity-driving, no hashtags in it
- hashtags: 3-6 relevant hashtags without the # sign, niche-specific (not just #fyp)
- reason: one sentence on why this moment works
- score: your 1-10 estimate of viral potential

Return the clips sorted by start time.

TRANSCRIPT:
{transcript}"""


def pick_ai(segs: list[Segment], n: int, lo: float, hi: float, lang: str | None, model: str) -> list[Clip]:
    import anthropic

    transcript = "\n".join(f"[{s.start:.1f}-{s.end:.1f}] {s.text}" for s in segs)
    prompt = AI_PROMPT.format(
        n=n, lo=lo, hi=hi, transcript=transcript,
        lang_note=f" (or '{lang}' if set)" if lang else "",
    )
    client = anthropic.Anthropic()
    print(f"🤖 Claude bira najbolje momente ({model}) ...")
    resp = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": CLIP_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("Model je odbio zahtjev")
    text = next(b.text for b in resp.content if b.type == "text")
    raw = json.loads(text)["clips"]
    return [Clip(c["start"], c["end"], c["title"], c["caption"], c["hashtags"], c["reason"], c["score"]) for c in raw]


def snap(clips: list[Clip], segs: list[Segment], total: float, lo: float, hi: float) -> list[Clip]:
    """Poravna početak/kraj na granice rečenica i drži trajanje unutar [lo, hi]."""
    starts = [s.start for s in segs]
    ends = [s.end for s in segs]
    fixed = []
    for c in clips:
        a = min(starts, key=lambda t: abs(t - c.start)) if starts else c.start
        b = min(ends, key=lambda t: abs(t - c.end)) if ends else c.end
        if b - a > hi:
            b = a + hi
        if b - a < lo:
            b = min(total, a + lo)
        a, b = max(0.0, a - 0.15), min(total, b + 0.35)  # mali "zrak" da se riječ ne odsiječe
        if b - a >= 3:
            c.start, c.end = round(a, 2), round(b, 2)
            fixed.append(c)
    fixed.sort(key=lambda c: c.start)
    for prev, cur in zip(fixed, fixed[1:]):  # padding ne smije stvoriti preklapanje
        if cur.start < prev.end:
            mid = round((prev.end + cur.start) / 2, 2)
            prev.end, cur.start = mid, mid
    return fixed


# --------------------------------------------------------------------------- 4. render

def ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def ass_escape(s: str) -> str:
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(words: list[Word], title: str, font: str, words_per_line: int, color: str) -> str:
    # ASS boje su &HAABBGGRR
    hi = {"yellow": "&H0000F0FF&", "green": "&H0055FF55&", "red": "&H004040FF&", "cyan": "&H00FFFF00&"}.get(color, "&H0000F0FF&")
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {OUT_W}
PlayResY: {OUT_H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},86,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,3,2,70,70,560,1
Style: Hook,{font},74,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,18,0,8,90,90,230,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if title:
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(3.5)},Hook,,0,0,0,,{{\\fad(150,250)}}{ass_escape(title)}")
    for i in range(0, len(words), words_per_line):
        chunk = words[i:i + words_per_line]
        for k, w in enumerate(chunk):
            start = w.start
            end = chunk[k + 1].start if k + 1 < len(chunk) else w.end
            if k + 1 == len(chunk) and i + words_per_line < len(words):
                end = min(words[i + words_per_line].start, w.end + 0.6)
            if end <= start:
                end = start + 0.05
            parts = []
            for kk, ww in enumerate(chunk):
                t = ass_escape(ww.text.upper())
                parts.append(f"{{\\c{hi}\\fscx108\\fscy108}}{t}{{\\c&HFFFFFF&\\fscx100\\fscy100}}" if kk == k else t)
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Sub,,0,0,0,,{' '.join(parts)}")
    return head + "\n".join(lines) + "\n"


def render_clip(video: Path, clip: Clip, segs: list[Segment], out_dir: Path, idx: int, args) -> Path:
    words = [
        Word(max(0.0, w.start - clip.start), w.end - clip.start, w.text)
        for s in segs for w in s.words
        if w.start >= clip.start - 0.05 and w.end <= clip.end + 0.05
    ]
    name = f"clip_{idx:02d}"
    ass = out_dir / f"{name}.ass"
    ass.write_text(
        build_ass(words if not args.no_subs else [], "" if args.no_hook else clip.title,
                  args.font, args.words, args.color),
        encoding="utf-8",
    )

    fill = f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H}"
    if args.style == "crop":
        vf = f"[0:v]{fill},setsar=1,subtitles={ass.name}[v]"
    else:  # blur: cijeli kadar u sredini, zamućena pozadina
        vf = (f"[0:v]split=2[a][b];[a]{fill},boxblur=25:5,eq=brightness=-0.08[bg];"
              f"[b]scale={OUT_W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,subtitles={ass.name}[v]")

    out = out_dir / f"{name}.mp4"
    run([
        "ffmpeg", "-y", "-ss", f"{clip.start:.2f}", "-i", str(video), "-t", f"{clip.end - clip.start:.2f}",
        "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-movflags", "+faststart", out.name,
    ], cwd=out_dir)
    if not args.keep_ass:
        ass.unlink(missing_ok=True)

    tags = " ".join("#" + t.lstrip("#").replace(" ", "") for t in clip.hashtags)
    (out_dir / f"{name}.txt").write_text(
        f"{clip.caption}\n\n{tags}\n\n---\nHook: {clip.title}\n"
        f"Izvor: {clip.start:.1f}s - {clip.end:.1f}s ({clip.end - clip.start:.0f}s)\n"
        f"Ocjena: {clip.score}/10  |  {clip.reason}\n",
        encoding="utf-8",
    )
    return out


# --------------------------------------------------------------------------- main

def main() -> None:
    p = argparse.ArgumentParser(description="Napravi vertikalne klipove s titlovima iz dugog videa.")
    p.add_argument("source", help="YouTube/Twitch/... link ili put do lokalnog videa")
    p.add_argument("--clips", type=int, default=5, help="koliko klipova (default 5)")
    p.add_argument("--min", type=float, default=20, help="min trajanje klipa u sekundama (default 20)")
    p.add_argument("--max", type=float, default=60, help="max trajanje klipa u sekundama (default 60)")
    p.add_argument("--style", choices=["blur", "crop"], default="blur",
                   help="blur = cijeli kadar + zamućena pozadina; crop = izreži sredinu (za 1 osobu u kadru)")
    p.add_argument("--language", default=None, help="jezik govora, npr. hr, en (default: auto)")
    p.add_argument("--whisper-model", default="small",
                   help="tiny/base/small/medium/large-v3 (veći = točniji, sporiji; default small)")
    p.add_argument("--no-ai", action="store_true", help="ne koristi Claude, biraj momente heuristikom")
    p.add_argument("--model", default="claude-opus-5-5", help="Claude model za odabir momenata")
    p.add_argument("--words", type=int, default=3, help="riječi po liniji titla (default 3)")
    p.add_argument("--color", default="yellow", choices=["yellow", "green", "red", "cyan"], help="boja aktivne riječi")
    p.add_argument("--font", default="Arial Black", help="font titlova (mora biti instaliran)")
    p.add_argument("--no-subs", action="store_true", help="bez titlova")
    p.add_argument("--no-hook", action="store_true", help="bez hook naslova na vrhu")
    p.add_argument("--keep-ass", action="store_true", help="zadrži .ass titlove (za ručno uređivanje)")
    p.add_argument("--out", default=str(ROOT / "output"), help="izlazna mapa")
    args = p.parse_args()

    check_ffmpeg()
    tmp = ROOT / "work" / slugify(Path(args.source).stem if Path(args.source).exists() else args.source)
    tmp.mkdir(parents=True, exist_ok=True)

    video = get_source(args.source, tmp)
    total = probe_duration(video)
    print(f"🎬 {video.name}  ({total / 60:.1f} min)")

    segs = transcribe(video, tmp, args.whisper_model, args.language)
    if not segs:
        raise SystemExit("Nije pronađen govor u videu.")

    clips: list[Clip] = []
    use_ai = not args.no_ai and (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    if use_ai:
        try:
            clips = pick_ai(segs, args.clips, args.min, args.max, args.language, args.model)
        except Exception as e:  # noqa: BLE001 - uvijek imaj fallback
            print(f"⚠  AI odabir nije uspio ({type(e).__name__}: {e}). Prelazim na heuristiku.")
    elif not args.no_ai:
        print("ℹ  Nema ANTHROPIC_API_KEY - koristim heuristiku (dodaj ključ za puno bolji odabir).")
    if not clips:
        clips = pick_heuristic(segs, args.clips, args.min, args.max)
    clips = snap(clips, segs, total, args.min, args.max)

    out_dir = Path(args.out) / slugify(video.stem)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = [f"# Klipovi: {video.stem}\n"]
    for i, c in enumerate(clips, 1):
        print(f"✂  [{i}/{len(clips)}] {c.start:.1f}s-{c.end:.1f}s  {c.title}")
        path = render_clip(video, c, segs, out_dir, i, args)
        summary.append(f"## {path.name}  ({c.end - c.start:.0f}s, ocjena {c.score}/10)\n"
                       f"- Hook: **{c.title}**\n- Opis: {c.caption}\n"
                       f"- Hashtagovi: {' '.join('#' + h.lstrip('#') for h in c.hashtags)}\n- Zašto: {c.reason}\n")
    (out_dir / "PREGLED.md").write_text("\n".join(summary), encoding="utf-8")
    print(f"\n✅ Gotovo! {len(clips)} klipova u: {out_dir}")


if __name__ == "__main__":
    main()
