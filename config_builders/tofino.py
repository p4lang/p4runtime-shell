#!/usr/bin/env python3

# Copyright 2019 Barefoot Networks, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import argparse
import os
import struct
import sys


# TODO: update to use manifest
def get_arg_parser():
    parser = argparse.ArgumentParser(description='Tofino binary config builder')
    parser.add_argument('--ctx-json',
                        help='Path to context JSON file',
                        required=True,
                        action='store')
    parser.add_argument('--tofino-bin',
                        help='Path to Tofino BIN file',
                        required=True,
                        action='store')
    parser.add_argument('--out', '-o',
                        help='Destination binary file',
                        required=True,
                        action='store')
    parser.add_argument('--name', '-p',
                        help='P4 Program name',
                        required=True,
                        action='store')
    return parser


def build_config(prog_name, ctx_json_path, tofino_bin_path, out_path):
    # we open the context JSON file in binary mode so that no encoding step is required
    with open(ctx_json_path, 'rb') as ctx_json_f, \
         open(tofino_bin_path, 'rb') as bin_f, \
         open(out_path, 'wb') as out_f:
        prog_name_bytes = prog_name.encode()
        out_f.write(struct.pack("<i", len(prog_name_bytes)))
        out_f.write(prog_name_bytes)
        tofino_bin = bin_f.read()
        out_f.write(struct.pack("<i", len(tofino_bin)))
        out_f.write(tofino_bin)
        ctx_json = ctx_json_f.read()
        out_f.write(struct.pack("<i", len(ctx_json)))
        out_f.write(ctx_json)


def main():
    parser = get_arg_parser()
    args = parser.parse_args()
    if not os.path.isfile(args.ctx_json):
        print("'{}' is not a valid file".format(args.ctx_json))
        sys.exit(1)
    if not os.path.isfile(args.tofino_bin):
        print("'{}' is not a valid file".format(args.tofino_bin))
        sys.exit(1)

    build_config(args.name, args.ctx_json, args.tofino_bin, args.out)


if __name__ == '__main__':  # pragma: no cover
    main()
