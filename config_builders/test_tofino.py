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

from io import StringIO
import nose2.tools
from tempfile import NamedTemporaryFile
import tofino
import unittest
import unittest.mock


class TestTofinoConfigBuilder(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prog_name = "myprog"

    def setUp(self):
        super().setUp()
        self.ctx_json_f = NamedTemporaryFile(mode='w', delete=True)
        self.bin_f = NamedTemporaryFile(mode='wb', delete=True)
        self.out_f = NamedTemporaryFile(mode='rb', delete=True)

    def tearDown(self):
        # There is no harm in calling close multiple times.
        self.ctx_json_f.close()
        self.bin_f.close()
        self.out_f.close()
        super().tearDown()

    def write_inputs(self):
        self.ctx_json_f.write("{}")
        self.bin_f.write(b"\xab")
        # we need to flush since build_config will open and read these files,
        # while we keep them also open here until we tear down the test.
        self.ctx_json_f.flush()
        self.bin_f.flush()

        expected_out = b""
        expected_out += b"\x06\x00\x00\x00"  # length of "myprog"
        expected_out += b"myprog"
        expected_out += b"\x01\x00\x00\x00"  # length of "\xab"
        expected_out += b"\xab"
        expected_out += b"\x02\x00\x00\x00"  # length of "{}"
        expected_out += b"{}"

        return expected_out

    def test_build_config(self):
        expected_out = self.write_inputs()
        tofino.build_config(self.prog_name, self.ctx_json_f.name, self.bin_f.name, self.out_f.name)
        self.assertEqual(self.out_f.read(), expected_out)

    def make_args(self):
        args = ["tofino.py",
                "--ctx-json", self.ctx_json_f.name,
                "--tofino-bin", self.bin_f.name,
                "-o", self.out_f.name,
                "-p", self.prog_name]
        return args

    def test_build_config_main(self):
        expected_out = self.write_inputs()
        args = self.make_args()
        with unittest.mock.patch('sys.argv', args):
            try:
                tofino.main()
            except SystemExit:
                self.fail("Error when calling tofino.main")
        self.assertEqual(self.out_f.read(), expected_out)

    @nose2.tools.params('ctx_json_f', 'bin_f')
    def test_bad_input_path(self, input_f):
        self.write_inputs()
        args = self.make_args()
        # We will call close() again in tearDown(), which is fine.
        getattr(self, input_f).close()
        with unittest.mock.patch('sys.argv', args):
            with unittest.mock.patch('sys.stdout', new_callable=StringIO) as mock_stdout:
                with self.assertRaises(SystemExit):
                    tofino.main()
                self.assertIn("is not a valid file", mock_stdout.getvalue())
