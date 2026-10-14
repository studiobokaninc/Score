"""cmd_323: 動画の中身(映像コーデック)を見て、ブラウザで映る形かを判じる。

拡張子や申告MIMEでは判じず、入れ物(MP4/MOV の stsd・WebM の CodecID)から映像の符号を読む。
ffmpeg には頼らぬ純粋な Python(数十行・ミリ秒)。判定不能な物は None を返す。
"""
import struct
from typing import Optional

# ブラウザ(Chrome/Edge/Safari/Firefox)で広く映る符号。HEVC 等は環境で不定ゆえ映らぬ側へ倒す。
_PLAYABLE_MP4 = {"avc1", "avc3"}
_PLAYABLE_WEBM = {"V_VP8", "V_VP9", "V_AV1"}
_CONTAINER_BOXES = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}
_VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".wmv", ".mpg", ".mpeg", ".flv", ".3gp")


def looks_like_video(filename: str, content_type: str) -> bool:
    """判定にかける対象か(拡張子または申告MIMEが動画)。"""
    return (filename or "").lower().endswith(_VIDEO_EXTS) or (content_type or "").lower().startswith("video/")


def _iter_boxes(data: bytes, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        size, btype = struct.unpack(">I4s", data[pos:pos + 8])
        hdr = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr or pos + size > end:
            size = end - pos
        yield btype, pos + hdr, pos + size
        pos += size


def _find_video_sample_entry(data: bytes, start: int, end: int) -> Optional[str]:
    for btype, bs, be in _iter_boxes(data, start, end):
        if btype in _CONTAINER_BOXES:
            r = _find_video_sample_entry(data, bs, be)
            if r:
                return r
        elif btype == b"hdlr":
            pass
        elif btype == b"stsd":
            # version/flags(4) + entry_count(4) の後に sample entry(size,type,...)
            if bs + 16 <= be:
                fourcc = data[bs + 12:bs + 16]
                # 映像の sample entry か音声かは、直後の trak の hdlr で見るのが正道だが、
                # 音声の符号(mp4a 等)は映像符号の表に無いので、音声は無視して映像のみ返す。
                code = fourcc.decode("latin-1")
                if code not in {"mp4a", "ac-3", "ec-3", "alac", "sowt", "twos", "lpcm", "Opus", "fLaC", "samr", "sawb", "tx3g", "text", "c608", "c708", "mett", "tmcd", "gpmd"}:
                    return code
    return None


def _webm_codec(data: bytes) -> Optional[str]:
    head = data[: 1024 * 1024]
    for cid in (b"V_VP8", b"V_VP9", b"V_AV1", b"V_MPEG4", b"V_MPEG2", b"V_MS/VFW/FOURCC", b"V_THEORA"):
        if cid in head:
            return cid.decode("ascii")
    return None


def detect_video_codec(content: bytes) -> Optional[str]:
    """映像の符号名を返す(例: 'avc1' / 'mp4v' / 'hvc1' / 'V_VP9')。判定不能なら None。"""
    try:
        if content[:4] == b"\x1a\x45\xdf\xa3":  # EBML (WebM/Matroska)
            return _webm_codec(content)
        return _find_video_sample_entry(content, 0, len(content))
    except Exception:
        return None


def is_browser_playable(codec: Optional[str]) -> Optional[bool]:
    """True=映る / False=映らぬ / None=判定不能(触らず素通りさせる)。"""
    if codec is None:
        return None
    return codec in _PLAYABLE_MP4 or codec in _PLAYABLE_WEBM


def _ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def transcode_to_h264(content: bytes, filename: str, remux_only: bool = False, timeout: int = 300):
    """動画を H.264/AAC の .mp4 へ変える(同期・重いので呼び側でスレッドへ逃がすこと)。
    remux_only=True は映像が既に H.264 の物の詰め替え(再エンコードせず劣化なし)。
    成功で (bytes, 新ファイル名) を返す。失敗は RuntimeError、ffmpeg 不在は FileNotFoundError。"""
    import os
    import subprocess
    import tempfile

    fd_in, tmp_in = tempfile.mkstemp(suffix=os.path.splitext(filename or "")[1] or ".bin")
    tmp_out = tmp_in + ".out.mp4"
    try:
        with os.fdopen(fd_in, "wb") as f:
            f.write(content)
        if remux_only:
            cmd = [_ffmpeg_exe(), "-y", "-i", tmp_in, "-map", "0:v:0", "-map", "0:a:0?",
                   "-c", "copy", "-movflags", "+faststart", tmp_out]
        else:
            cmd = [_ffmpeg_exe(), "-y", "-i", tmp_in, "-map", "0:v:0", "-map", "0:a:0?",
                   "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-pix_fmt", "yuv420p",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                   "-c:a", "aac", "-movflags", "+faststart", tmp_out]
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if proc.returncode != 0 or not os.path.exists(tmp_out):
            raise RuntimeError(proc.stderr.decode("utf-8", errors="replace")[-300:])
        with open(tmp_out, "rb") as f:
            out = f.read()
        base = os.path.splitext(filename or "video")[0]
        return out, base + ".mp4"
    finally:
        for p in (tmp_in, tmp_out):
            if os.path.exists(p):
                os.unlink(p)


def prepare_video_for_browser(content: bytes, filename: str, content_type: str):
    """アップロードされた動画を中身で判じ、映らぬ物は H.264 の .mp4 へ変えて返す。
    戻り値: dict(content, filename, content_type, converted(bool), notice(str|None), error(str|None))
    - 動画でない/判定不能(.mov 以外)/既に映る形(.mov 以外): 素通り(converted=False)
    - .mov: 従来どおり必ず .mp4 へ(H.264 なら詰め替えのみ・劣化なし)
    - 映らぬ形の .mp4 等: H.264 へ再エンコード
    - 変換失敗: 原本のまま素通りし error に理由(.mov は従来どおり呼び側で拒否できるよう error を返す)"""
    out = dict(content=content, filename=filename, content_type=content_type,
               converted=False, notice=None, error=None)
    if not looks_like_video(filename, content_type):
        return out
    codec = detect_video_codec(content)
    playable = is_browser_playable(codec)
    is_mov = (filename or "").lower().endswith(".mov") or (content_type or "").lower() == "video/quicktime"
    if playable and not is_mov:
        return out
    if playable is None and not is_mov:
        return out
    try:
        new_bytes, new_name = transcode_to_h264(content, filename, remux_only=bool(playable))
    except FileNotFoundError:
        out["error"] = "ffmpeg 未インストール"
        return out
    except Exception as e:
        out["error"] = str(e)[:300]
        return out
    out.update(content=new_bytes, filename=new_name, content_type="video/mp4", converted=True,
               notice=f"この動画は映像形式({codec or '不明'})が画面で再生できないため、再生できる形式(H.264)へ自動変換しました。原本はダウンロードで取得できます。")
    return out
