"""Convert looping mp4 clips to GIF so MAX/Telegram can autoplay them as images."""
import logging
import os
import shutil
import subprocess

def gif_path_for(mp4: str) -> str:
    return os.path.splitext(mp4)[0] + ".gif"


def ensure_loop_gif(mp4: str) -> str | None:
    if not mp4 or not os.path.exists(mp4):
        dest = gif_path_for(mp4) if mp4 else ""
        return dest if dest and os.path.exists(dest) else None
    dest = gif_path_for(mp4)
    if os.path.exists(dest) and os.path.getmtime(dest) >= os.path.getmtime(mp4):
        return dest
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logging.warning("нет ffmpeg, не удалось собрать гиф из %s", mp4)
        return dest if os.path.exists(dest) else None
    tmp = dest + ".tmp.gif"
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                mp4,
                "-vf",
                "fps=12,scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
                "-loop",
                "0",
                tmp,
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        os.replace(tmp, dest)
        return dest
    except Exception:
        logging.exception("ffmpeg не собрал гиф %s", mp4)
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return dest if os.path.exists(dest) else None
