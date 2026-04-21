#!/usr/bin/env bash

# SPDX-FileCopyrightText: 2019 Antonin Bas
#
# SPDX-License-Identifier: Apache-2.0

source $VENV/bin/activate
python3 -m p4runtime_sh "$@"
