# Build libpioshim.so — the ctypes-loadable shim over RP1 piolib (Pi 5 ONLY).
#
# Needs piolib built as a library first (see README.md). Point PIOLIB at your
# piolib checkout (defaults to the in-repo reference copy). The shim links the
# real exported symbols (pio_open/select/close, pio_sm_config_xfer,
# pio_sm_xfer_data, ...) and inlines the static-inline config helpers from the
# headers.
#
#   make                 # -> libpioshim.so
#   make PIOLIB=~/piolib # use a different piolib location

PIOLIB ?= ../../../.reference/rpi5/utils/piolib
CC     ?= cc
CFLAGS ?= -O2 -Wall -fPIC -shared

# -Wl,-rpath bakes piolib's absolute location into libpioshim.so so the loader
# finds libpio.so.0 at runtime (piolib is built in place, not installed; and sudo
# scrubs LD_LIBRARY_PATH). Re-run `make clean && make` after moving the repo.
libpioshim.so: pio_shim.c
	$(CC) $(CFLAGS) -I$(PIOLIB)/include $< \
		-L$(PIOLIB) -Wl,-rpath,$(abspath $(PIOLIB)) -lpio -o $@

clean:
	rm -f libpioshim.so

.PHONY: clean
