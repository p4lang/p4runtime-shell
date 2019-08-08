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

from tempfile import NamedTemporaryFile
from tofino import build_config
import unittest


class TestTofinoConfigBuilder(unittest.TestCase):
    def test_build_config(self):
        prog_name = "myprog"
        # when breaking-up the line with '\', we get a false positive with flake8 (3.7.7) for E122,
        # that cannot be disabled with a noqa comment.
        with NamedTemporaryFile(mode='w') as ctx_json_f, NamedTemporaryFile(mode='wb') as bin_f, NamedTemporaryFile(mode='rb') as out_f:  # noqa: E501
            ctx_json_f.write("{}")
            bin_f.write(b"\xab")
            # we need to flush since build_config will open and read these
            # files, while we keep them also open here until we leave the
            # context manager
            ctx_json_f.flush()
            bin_f.flush()

            build_config(prog_name, ctx_json_f.name, bin_f.name, out_f.name)

            expected_out = b""
            expected_out += b"\x06\x00\x00\x00"  # length of "myprog"
            expected_out += b"myprog"
            expected_out += b"\x01\x00\x00\x00"  # length of "\xab"
            expected_out += b"\xab"
            expected_out += b"\x02\x00\x00\x00"  # length of "{}"
            expected_out += b"{}"

            self.assertEqual(out_f.read(), expected_out)
