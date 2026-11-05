CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -pedantic
CPPFLAGS += -D_POSIX_C_SOURCE=200809L
LDLIBS += -lz -lbz2 -llzma -lpthread

hashfasta: hashfasta.c pgzip.c pgzip.h sha256.c sha256.h
	$(CC) -std=c99 $(CPPFLAGS) $(CFLAGS) -o $@ hashfasta.c pgzip.c sha256.c $(LDFLAGS) $(LDLIBS)

clean:
	rm -f hashfasta

.PHONY: clean
