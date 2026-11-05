# Build the compiled HashFASTA implementations and run the shared conformance tests.
PYTHON ?= python3
CARGO ?= cargo

build: rust c cpp

rust:
	$(CARGO) build --release --manifest-path implementations/rust/Cargo.toml

c:
	$(MAKE) -C implementations/c

cpp:
	$(MAKE) -C implementations/cpp

test: build
	$(PYTHON) -m unittest discover -s implementations/python/tests
	$(PYTHON) tests/run_conformance.py
	$(PYTHON) tests/run_conformance.py --threads 4

clean:
	$(CARGO) clean --manifest-path implementations/rust/Cargo.toml
	$(MAKE) -C implementations/c clean
	$(MAKE) -C implementations/cpp clean

.PHONY: build rust c cpp test clean
