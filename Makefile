# SPDX-FileCopyrightText: 2022 Antonin Bas
#
# SPDX-License-Identifier: Apache-2.0

all: build

.PHONY: build
build:
	docker build -t p4lang/p4runtime-sh .

.PHONY: toc
toc:
	@curl -s https://raw.githubusercontent.com/ekalinin/github-markdown-toc/master/gh-md-toc -o gh-md-toc
	@chmod +x gh-md-toc
	@./gh-md-toc --insert --no-backup --hide-footer README.md

.PHONY: clean
clean:
	rm -rf gh-md-toc

.PHONY: set-dev
set-dev:
	@echo "Installing dev-dependencies..."
	# Set up uv for Python dependency management.
	# TODO: Consider using a system-provided package here.
	sudo apt-get install -y curl
	curl -LsSf https://astral.sh/uv/0.6.12/install.sh | sh
	export PATH="${PATH}:${HOME}/.local/bin" && uv venv && uv tool update-shell && uv pip install -r requirements-dev.txt
