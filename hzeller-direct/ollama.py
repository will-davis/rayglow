#!/usr/bin/env python3
"""Interactive ollama shell on a 320x32 HUB75 LED matrix.

Turns the panel into a tiny three-line terminal that talks to an ollama server.

    >>> you type here, letters appear as you press them
    the model's reply streams in underneath, word-wrapped,
    scrolling up a line at a time once it fills the screen.

Flow (mirrors the ollama CLI):
  * A ">>> " prompt waits for input. Keystrokes echo live with a blinking cursor.
  * Press Enter on a non-empty line -> the prompt is sent to ollama and the reply
    is streamed in (newline-delimited JSON, one token per chunk) and drawn as it
    arrives. When the reply is longer than the screen it scrolls up so the newest
    text is always the bottom line.
  * When the reply finishes it stays on screen until you press Enter, which clears
    everything and drops back to a fresh ">>> " prompt.
  * Ctrl-C quits.

Text is drawn with the repo's 6x9 BDF font via the rgbmatrix `graphics` binding
(crisp bitmap glyphs — far better than antialiased TTF at this size). The font is
loaded BEFORE the matrix is constructed, because RGBMatrix drops root privileges
during init and could no longer read the font file out of /home afterward.
"""

import os
import sys
import json
import select
import termios
import threading
import tty
import urllib.request
from time import time, sleep

from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics


# ---------------------------------------------------------------------------
# Ollama config
# ---------------------------------------------------------------------------
OLLAMA_HOST = "192.168.1.101:11434"
OLLAMA_URL = "http://%s/api/generate" % OLLAMA_HOST
MODEL = "gemma3:latest"
REQUEST_TIMEOUT = 120          # seconds of read-inactivity before giving up


# ---------------------------------------------------------------------------
# Matrix hardware config (matches the rest of this rig — see CLAUDE.md / memory)
# ---------------------------------------------------------------------------
options = RGBMatrixOptions()
options.rows = 32
options.cols = 64
options.chain_length = 4
options.parallel = 1
options.disable_hardware_pulsing = 0       # snd_bcm2835 is blacklisted
options.gpio_slowdown = 5                   # tuned: 5 + lsb 130 ~eliminates panel-1 end-of-line artifact
options.brightness = 100
options.pwm_bits = 10
options.hardware_mapping = "adafruit-hat-pwm"  # GPIO4->GPIO18 jumper installed (hardware OE pulsing)
# options.pixel_mapper_config = "Rotate:180"

WIDTH = options.cols * options.chain_length   # 320
HEIGHT = options.rows                         # 32


# ---------------------------------------------------------------------------
# Text layout
# ---------------------------------------------------------------------------
FONT_PATH = "/home/will/rpi-rgb-led-matrix/fonts/6x9.bdf"
CHAR_W = 6                      # advance width of the 6x9 font
MAX_COLS = WIDTH // CHAR_W      # 53 chars per line
MAX_LINES = 3                  # rows of text that fit in 32px with the 6x9 font

COL_PROMPT = graphics.Color(130, 230, 130)   # ">>> question" — soft green
COL_REPLY = graphics.Color(255, 210, 150)    # the model's reply — warm amber
COL_INFO = graphics.Color(120, 170, 255)     # status / errors — cool blue

CURSOR = "_"                   # appended to the input line and blinked
BLINK_PERIOD = 0.5             # seconds per cursor on/off phase
POLL_TIMEOUT = 0.04            # keyboard poll interval (~25 fps idle redraw)

# Reading-pace reveal. Tokens stream from ollama far faster than anyone can read,
# so a background thread buffers them and the main loop reveals one word at a time
# at a comfortable adult silent-reading speed. Press Enter mid-reply to skip ahead.
WORDS_PER_MINUTE = 320
WORD_INTERVAL = 60.0 / WORDS_PER_MINUTE        # seconds between revealed words


# Load the font while we are still root (see module docstring).
font = graphics.Font()
font.LoadFont(FONT_PATH)
LINE_H = font.height + 2                       # vertical pitch between baselines
BASELINES = [font.baseline + i * LINE_H for i in range(MAX_LINES)]


def wrap(text):
    """Word-wrap `text` into a list of lines <= MAX_COLS, honoring '\\n'."""
    out = []
    for para in text.split("\n"):
        if not para:
            out.append("")
            continue
        line = ""
        for word in para.split(" "):
            # Hard-break any single word longer than the screen.
            while len(word) > MAX_COLS:
                if line:
                    out.append(line)
                    line = ""
                out.append(word[:MAX_COLS])
                word = word[MAX_COLS:]
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= MAX_COLS:
                line += " " + word
            else:
                out.append(line)
                line = word
        out.append(line)
    return out


matrix = RGBMatrix(options=options)
canvas = matrix.CreateFrameCanvas()


def draw(question, reply, cursor_on):
    """Render the prompt + reply, scrolled so the newest text is on the bottom line."""
    global canvas
    prompt = ">>> " + question + (CURSOR if cursor_on else "")
    lines = [(t, COL_PROMPT) for t in wrap(prompt)]
    if reply:
        lines += [(t, COL_REPLY) for t in wrap(reply)]

    canvas.Clear()
    for (text, color), y in zip(lines[-MAX_LINES:], BASELINES):
        graphics.DrawText(canvas, font, 0, y, color, text)
    canvas = matrix.SwapOnVSync(canvas)


class Stream:
    """Shared buffer between the network thread (producer) and the reveal loop."""

    def __init__(self):
        self.lock = threading.Lock()
        self.buf = ""          # received from ollama but not yet revealed
        self.done = False      # producer finished (stream closed or errored)


def fetch(question, st):
    """Background worker: drain ollama's stream into st.buf as fast as it arrives."""
    payload = json.dumps({"model": MODEL, "prompt": question, "stream": True}).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            for raw in resp:                       # one JSON object per line
                raw = raw.strip()
                if not raw:
                    continue
                obj = json.loads(raw)
                tok = obj.get("response", "")
                if tok:
                    with st.lock:
                        st.buf += tok
                if obj.get("done"):
                    break
    except Exception as exc:
        with st.lock:
            st.buf += "\n[error: %s]" % exc
    finally:
        st.done = True


def take_word(st):
    """Pop the next whole word (with its leading whitespace) from the buffer.

    Returns the chunk to reveal, or None if no complete word is available yet —
    a word is only "complete" once the whitespace after it has arrived, so we
    never reveal a half-streamed word (unless the producer is finished)."""
    with st.lock:
        buf = st.buf
        n = len(buf)
        i = 0
        while i < n and buf[i].isspace():          # leading whitespace / newlines
            i += 1
        if i == n:                                 # only whitespace buffered
            if st.done and buf:
                st.buf = ""
                return buf                         # flush trailing whitespace at end
            return None
        j = i
        while j < n and not buf[j].isspace():       # the word itself
            j += 1
        if j == n and not st.done:
            return None                            # word may still be growing
        st.buf = buf[j:]
        return buf[:j]


def main():
    fd = sys.stdin.fileno()
    old_term = termios.tcgetattr(fd)
    tty.setcbreak(fd)                              # char-at-a-time, no echo; keeps Ctrl-C
    try:
        mode = "input"         # input -> stream -> await -> input
        question = ""          # current input / last sent prompt
        reply = ""             # revealed portion of the reply
        st = None              # active Stream while mode == "stream"
        skip = False           # True once Enter skips the reading-pace gate
        cursor_on = True
        last_blink = time()
        last_word = 0.0        # when the last word was revealed (0 -> reveal first ASAP)
        draw(question, reply, cursor_on)

        while True:
            ready, _, _ = select.select([sys.stdin], [], [], POLL_TIMEOUT)
            data = os.read(fd, 64).decode("utf-8", "ignore") if ready else ""

            dirty = False
            for ch in data:
                if ch == "\x1b":
                    break                          # drop the rest of an escape sequence
                if ch in ("\r", "\n"):
                    if mode == "await":            # finished reply -> clear & restart
                        question, reply, mode = "", "", "input"
                        cursor_on, dirty = True, True
                    elif mode == "stream":
                        skip = True                # reveal the rest without waiting
                    elif question.strip():         # input -> start streaming
                        st = Stream()
                        threading.Thread(target=fetch, args=(question, st),
                                         daemon=True).start()
                        reply, skip, last_word, mode = "", False, 0.0, "stream"
                        dirty = True
                elif mode != "input":
                    continue                       # ignore typing during/after a reply
                elif ch in ("\x7f", "\x08"):       # backspace / delete
                    question = question[:-1]
                    dirty = True
                elif ch >= " ":                    # printable
                    question += ch
                    dirty = True

            now = time()

            if mode == "stream":
                # Reveal words at reading pace; skip drains everything available now.
                while skip or now - last_word >= WORD_INTERVAL:
                    chunk = take_word(st)
                    if chunk is None:
                        if st.done:
                            mode = "await"         # buffer empty and producer finished
                            dirty = True
                        break                      # nothing ready yet — wait
                    reply += chunk
                    last_word = now
                    dirty = True
                    if not skip:
                        break                      # one word per tick at reading pace

            if mode == "input" and now - last_blink >= BLINK_PERIOD:
                cursor_on = not cursor_on
                last_blink = now
                dirty = True

            if dirty:
                draw(question, reply, cursor_on if mode == "input" else False)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_term)
        canvas.Clear()
        matrix.SwapOnVSync(canvas)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
