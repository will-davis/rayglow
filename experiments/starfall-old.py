from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics
from time import sleep
import random
import colorsys

mode = 0
maxdrops = 1000

options = RGBMatrixOptions()
options.rows = 32
options.cols = 64
options.chain_length = 5
options.parallel = 1
options.disable_hardware_pulsing = 0      # hardware pulsing OK now that snd_bcm2835 is blacklisted
options.gpio_slowdown = 5                   # tuned: 5 + lsb 130 ~eliminates panel-1 end-of-line artifact
options.brightness = 100
options.pwm_bits = 10
options.hardware_mapping = "adafruit-hat-pwm"  # GPIO4->GPIO18 jumper installed (hardware OE pulsing)
# options.pixel_mapper_config = "Rotate:180"  # disabled: panel physically flipped when 5th panel removed

width = options.cols * options.chain_length   # 320
height = options.rows                          # 32
pixwidth = width - 1                           # max valid x index
print(width, "x", height)


class Drop:
    def __init__(self):
        global mode
        self.x = random.randint(0, pixwidth)
        self.y = 0.0
        self.r = self.b = self.g = 0
        self.generateColor()
        self.speed = 1 + (random.random() * 4)
        self.strength = random.randint(40, 100) / 100.0

    def generateColor(self):
        (r, g, b) = colorsys.hsv_to_rgb(random.random(), 1, 1)
        if (mode == 0):
            # hue spread across the full width of the display
            (r, g, b) = colorsys.hsv_to_rgb((self.x / float(pixwidth)), 1, 1)
        # mode == 2 leaves it at the random hsv above
        self.r = int(r * 255)
        self.g = int(g * 255)
        self.b = int(b * 255)

        if (mode == 1):
            self.r = random.randint(0, 255)
            self.g = random.randint(0, 255)
            self.b = random.randint(0, 255)
        self.altr = int(r * 255)
        self.altg = int(g * 255)
        self.altb = int(b * 255)
        if (random.random() > .01 and mode == 3):
            self.r = self.g = self.b = random.randint(0, 255)

    def tick(self):
        self.erase()
        self.y += (self.speed / 2.0)
        if self.y >= height:          # fell past the last visible row -> recycle
            self.strength = 0
        self.strength = self.strength * .96
        if (self.strength < 0):
            self.strength = 0
        self.draw()

    def draw(self):
        y = int(self.y)
        if y < 0 or y >= height:      # off-screen; nothing to draw
            return
        x = int(self.x)
        matrix.SetPixel(x, y,
                        int(self.r * self.strength),
                        int(self.g * self.strength),
                        int(self.b * self.strength))
        if (random.random() < .001):
            matrix.SetPixel(x, y, self.altr, self.altg, self.altb)

    def erase(self):
        y = int(self.y)
        if y < 0 or y >= height:
            return
        matrix.SetPixel(int(self.x), y, 0, 0, 0)


matrix = RGBMatrix(options=options)
print("Matrix initialized\n")


drops = []
for k in range(0, maxdrops):
    drops.append(Drop())

modeTicks = 1000
while (True):
    for j in range(0, len(drops)):
        drops[j].tick()
        if drops[j].strength == 0:
            drops[j].erase()
            drops[j] = Drop()

    sleep(.01)
    modeTicks -= 1
    if modeTicks < 0:
        modeTicks = 1000
        mode += 1
        mode = mode % 4
