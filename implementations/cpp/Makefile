CXX ?= c++
CXXFLAGS ?= -O2 -Wall -Wextra -pedantic
LDLIBS += -lz -lbz2 -llzma -lpthread

hashfasta: hashfasta.cpp pgzip.hpp sha256.hpp
	$(CXX) -std=c++17 $(CPPFLAGS) $(CXXFLAGS) -o $@ hashfasta.cpp $(LDFLAGS) $(LDLIBS)

clean:
	rm -f hashfasta

.PHONY: clean
