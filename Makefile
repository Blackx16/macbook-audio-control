.PHONY: all build install test clean uninstall

all: build

build:
	cd rust && cargo build --release

install:
	./install.sh

test:
	python3 audio_backend.py --test

clean:
	cd rust && cargo clean

uninstall:
	./uninstall.sh
