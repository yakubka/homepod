import logging
import os
import subprocess

log = logging.getLogger("audio")

EXTS = (".mp3", ".wav", ".ogg", ".flac")


class Player:
    def __init__(self, music_dir="/home/pi/music"):
        self.dir = music_dir
        self.track = None
        self.playing = False
        self.volume = 70
        self.tracks = []
        self.index = 0
        self.proc = None

    def load(self):
        if not os.path.isdir(self.dir):
            log.warning(f"music dir missing, {self.dir}")
            self.tracks = []
            return 0
        self.tracks = [
            os.path.join(self.dir, f)
            for f in sorted(os.listdir(self.dir))
            if f.lower().endswith(EXTS)
        ]
        self.index = 0
        log.info(f"loaded {len(self.tracks)} tracks")
        return len(self.tracks)

    def play(self, path=None):
        self.stop()

        if path is None:
            if not self.tracks:
                self.load()
            if not self.tracks:
                log.warning("nothing to play")
                return False
            self.index %= len(self.tracks)
            path = self.tracks[self.index]

        try:
            self.proc = subprocess.Popen(
                ["mpv", "--no-video", f"--volume={self.volume}", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            if not path.endswith(".wav"):
                log.error("mpv missing and track is not wav")
                return False
            self.proc = subprocess.Popen(
                ["aplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

        self.track = os.path.basename(path)
        self.playing = True
        log.info(f"playing {self.track}")
        return True

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        self.playing = False
        self.track = None

    def next(self):
        if not self.tracks:
            self.load()
        if self.tracks:
            self.index = (self.index + 1) % len(self.tracks)
        return self.play()

    def prev(self):
        if not self.tracks:
            self.load()
        if self.tracks:
            self.index = (self.index - 1) % len(self.tracks)
        return self.play()

    def set_volume(self, level):
        self.volume = max(0, min(100, int(level)))
        log.info(f"volume {self.volume}")
        return self.volume

    def status(self):
        if self.proc and self.proc.poll() is not None:
            self.playing = False
            self.track = None
        return {
            "playing": self.playing,
            "track": self.track,
            "volume": self.volume,
            "tracks": len(self.tracks),
        }
