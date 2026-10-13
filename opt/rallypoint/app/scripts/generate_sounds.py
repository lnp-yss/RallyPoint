"""Create small offline confirmation tones with the Python standard library."""

import math
import struct
import wave
from pathlib import Path


def main():
    directory = (
        Path(__file__).resolve().parent.parent
        / "pickleball_scoreboard/assets/sounds"
    )
    directory.mkdir(parents=True, exist_ok=True)
    for name, frequency, duration in [
        ("point", 880, 0.10),
        ("service", 520, 0.16),
        ("game", 660, 0.45),
        ("match", 990, 0.7),
    ]:
        with wave.open(str(directory / f"{name}.wav"), "wb") as stream:
            stream.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
            samples = []
            for i in range(int(duration * 22050)):
                t = i / 22050
                envelope = min(1, t / 0.01, (duration - t) / 0.03)
                tone = frequency * (
                    1.25 if name == "match" and t > 0.35 else 1
                )
                samples.append(
                    struct.pack(
                        "<h",
                        int(
                            6500 * envelope * math.sin(2 * math.pi * tone * t)
                        ),
                    )
                )
            stream.writeframes(b"".join(samples))


if __name__ == "__main__":
    main()
