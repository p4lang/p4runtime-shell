# Copyright 2022 Antonin Bas
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
