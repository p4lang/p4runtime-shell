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

import os

from callee import Matcher
from concurrent import futures
import google.protobuf.text_format
from google.rpc import code_pb2
import grpc
from io import StringIO
import itertools
import logging
import unittest
from unittest.mock import ANY, Mock, patch
from p4.v1 import p4runtime_pb2, p4runtime_pb2_grpc
from p4.config.v1 import p4info_pb2
from p4runtime_sh.context import P4Type, P4RuntimeEntity
from p4runtime_sh.global_options import global_options
from p4runtime_sh.p4runtime import P4RuntimeException
from p4runtime_sh.utils import UserError
import nose2.tools
from threading import Thread
import queue

# ensures that IPython uses a "simple prompt"
# see run_sh() in BaseTestCase for more details
os.environ['IPY_TEST_SIMPLE_PROMPT'] = '1'
import p4runtime_sh.shell as sh  # noqa: E402


class P4RuntimeServicer(p4runtime_pb2_grpc.P4RuntimeServicer):
    def __init__(self):
        self.p4info = p4info_pb2.P4Info()
        self.p4runtime_api_version = "1.3.0"
        self.stored_packet_out = queue.Queue()

    def GetForwardingPipelineConfig(self, request, context):
        rep = p4runtime_pb2.GetForwardingPipelineConfigResponse()
        if self.p4info is not None:
            rep.config.p4info.CopyFrom(self.p4info)
        return rep

    def SetForwardingPipelineConfig(self, request, context):
        self.p4info.CopyFrom(request.config.p4info)
        return p4runtime_pb2.SetForwardingPipelineConfigResponse()

    def Write(self, request, context):
        return p4runtime_pb2.WriteResponse()

    def Read(self, request, context):
        yield p4runtime_pb2.ReadResponse()

    def StreamChannel(self, request_iterator, context):
        for req in request_iterator:
            if req.HasField('arbitration'):
                rep = p4runtime_pb2.StreamMessageResponse()
                rep.arbitration.CopyFrom(req.arbitration)
                rep.arbitration.status.code = code_pb2.OK
                yield rep
            elif req.HasField('packet'):
                self.stored_packet_out.put(req)

    def Capabilities(self, request, context):
        rep = p4runtime_pb2.CapabilitiesResponse()
        rep.p4runtime_api_version = self.p4runtime_api_version
        return rep


class BaseTestCase(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.server = None
        self._p4info_path = "p4runtime_sh/testdata/unittest.p4info.pb.txt"
        self._config_path = "p4runtime_sh/testdata/unittest.bin"

    def serve(self):
        self.server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        self.port = self.server.add_insecure_port('[::]:0')
        self.grpc_addr = "localhost:{}".format(self.port)
        logging.debug("Using port {}".format(self.port))
        self.server.start()

    def setUp(self):
        super().setUp()
        self.serve()

    def run_sh(self, args=[]):
        new_args = ["p4runtime-sh", "--grpc-addr", self.grpc_addr] + args
        rc = 0
        stdout = None
        with patch('sys.argv', new_args):
            with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
                # patching input() only works in simple_prompt mode
                # we could also raise EOFError as a side_effect, it seems that it does not required
                # confirmation in simple_prompt mode
                # https://ipython.readthedocs.io/en/stable/config/options/terminal.html#configtrait-TerminalInteractiveShell.simple_prompt
                # the best way to do that is to set IPY_TEST_SIMPLE_PROMPT, but this needs to be
                # done before importing IPython, which we do at the beginning of the file
                # an alternative is to patch the
                # IPython.terminal.interactiveshell.TerminalInteractiveShell object so that the
                # 'simple_prompt' attribute is True.
                with patch('builtins.input', return_value="exit"):
                    try:
                        sh.main()
                    except SystemExit as e:
                        rc = e.code
                    stdout = mock_stdout.getvalue()
        return rc, stdout

    def tearDown(self):
        self.server.stop(None)
        super().tearDown()


class IPythonTestCase(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.servicer = P4RuntimeServicer()
        p4runtime_pb2_grpc.add_P4RuntimeServicer_to_server(self.servicer, self.server)

    def test_run_and_exit(self):
        rc, _ = self.run_sh(args=["--config", ",".join([self._p4info_path, self._config_path])])
        self.assertEqual(rc, 0)

    def test_run_no_config(self):
        rc, _ = self.run_sh(args=[])
        self.assertEqual(rc, 0)


class ProtoCmp(Matcher):
    def __init__(self, expected):
        self.expected = expected

    def match(self, value):
        return value.SerializeToString() == self.expected.SerializeToString()

    def __repr__(self):
        return str(self.expected)


class UnitTestCase(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.device_id = 0
        self.election_id = (0, 1)

        self.servicer = P4RuntimeServicer()
        self.servicer.Write = Mock(spec=[], return_value=p4runtime_pb2.WriteResponse())
        self.servicer.Read = Mock(spec=[], return_value=p4runtime_pb2.ReadResponse())
        p4runtime_pb2_grpc.add_P4RuntimeServicer_to_server(self.servicer, self.server)

        global_options.reset()

        sh.setup(device_id=self.device_id,
                 grpc_addr=self.grpc_addr,
                 election_id=self.election_id,
                 config=sh.FwdPipeConfig(self._p4info_path, self._config_path))

    def tearDown(self):
        sh.teardown()
        super().tearDown()

    def make_write_request(self, update_type, entity_type, expected_txt):
        req = p4runtime_pb2.WriteRequest()
        req.device_id = self.device_id
        req.election_id.high = self.election_id[0]
        req.election_id.low = self.election_id[1]
        update = req.updates.add()
        update.type = update_type
        google.protobuf.text_format.Merge(expected_txt, getattr(update.entity, entity_type.name))
        return req

    def make_read_mock(self, entity):
        def _Read(request, context):
            rep = p4runtime_pb2.ReadResponse()
            rep.entities.add().CopyFrom(entity)
            yield rep
        return _Read

    def simple_read_check(self, entity, obj, entity_type, expect_iterator=True):
        """A very simple and generic check for the read() operation on every entity. It builds a
        Read mock that will return the desired entity. It then calls read() on the provided object
        (TableEntry, CounterEntry, ...) and makes sure that the returned entity is converted
        properly to a Python object."""
        self.servicer.Read.side_effect = self.make_read_mock(entity)
        if expect_iterator:
            entity_read = next(obj.read())
        else:
            entity_read = obj.read()
        self.servicer.Read.assert_called_once_with(ANY, ANY)
        self.assertEqual(str(entity_read.msg()), str(getattr(entity, entity_type.name)))

    @nose2.tools.params((1, 100), (100, 1), (10, 100))
    def test_read_iterator(self, num_reps, num_entities_per_rep):
        ce = sh.CounterEntry("CounterA")

        def gen_entities():
            for i in itertools.count():
                x = p4runtime_pb2.Entity()
                counter_entry = x.counter_entry
                counter_entry.counter_id = 302055013
                counter_entry.index.index = i
                counter_entry.data.packet_count = 100
                yield x

        def make_read_mock(num_reps, num_entities_per_rep):
            it = gen_entities()

            def _Read(request, context):
                for i in range(num_reps):
                    rep = p4runtime_pb2.ReadResponse()
                    for j in range(num_entities_per_rep):
                        rep.entities.add().CopyFrom(next(it))
                    yield rep

            return _Read

        self.servicer.Read.side_effect = make_read_mock(num_reps, num_entities_per_rep)

        cnt = [0]

        def inc(x):
            cnt[0] += 1

        for x in ce.read():
            inc(x)
        self.assertEqual(cnt[0], num_reps * num_entities_per_rep)

        cnt[0] = 0
        ce.read(inc)
        self.assertEqual(cnt[0], num_reps * num_entities_per_rep)

    def test_read_error(self):
        ce = sh.CounterEntry("CounterA")

        def _Read(request, context):
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            yield p4runtime_pb2.ReadResponse()

        self.servicer.Read.side_effect = _Read

        with self.assertRaises(P4RuntimeException):
            next(ce.read())

        with self.assertRaises(P4RuntimeException):
            ce.read(lambda _: True)

    def test_table_entry_exact(self):
        te = sh.TableEntry("ExactOne")(action="actionA")
        te.match["header_test.field32"] = "0x123456"
        te.action["param"] = "aa:bb:cc:dd:ee:ff"
        te.insert()

        expected_entry = """
table_id: 33582705
match {
  field_id: 1
  exact {
    value: "\\x12\\x34\\x56"
  }
}
action {
  action {
    action_id: 16783703
    params {
      param_id: 1
      value: "\\xaa\\xbb\\xcc\\xdd\\xee\\xff"
    }
  }
}
"""

        expected_req = self.make_write_request(
            p4runtime_pb2.Update.INSERT, P4RuntimeEntity.table_entry, expected_entry)

        self.servicer.Write.assert_called_once_with(ProtoCmp(expected_req), ANY)

    def test_canonical_bytestrings_on_off(self):
        def get_te():
            te = sh.TableEntry("ExactOne")(action="actionA")
            te.match["header_test.field32"] = "0x0"
            te.action["param"] = "00:00:11:00:22:33"
            return te

        expected_entry = """
table_id: 33582705
match {
  field_id: 1
  exact {
    value: "\\x00"
  }
}
action {
  action {
    action_id: 16783703
    params {
      param_id: 1
      value: "\\x11\\x00\\x22\\x33"
    }
  }
}
"""
        get_te().insert()
        expected_req = self.make_write_request(
            p4runtime_pb2.Update.INSERT, P4RuntimeEntity.table_entry, expected_entry)
        self.servicer.Write.assert_called_with(ProtoCmp(expected_req), ANY)

        sh.global_options["canonical_bytestrings"] = False  # enable legacy (byte-padded) format

        expected_entry = """
table_id: 33582705
match {
  field_id: 1
  exact {
    value: "\\x00\\x00\\x00\\x00"
  }
}
action {
  action {
    action_id: 16783703
    params {
      param_id: 1
      value: "\\x00\\x00\\x11\\x00\\x22\\x33"
    }
  }
}
"""
        get_te().insert()
        expected_req = self.make_write_request(
            p4runtime_pb2.Update.INSERT, P4RuntimeEntity.table_entry, expected_entry)
        self.servicer.Write.assert_called_with(ProtoCmp(expected_req), ANY)

    @nose2.tools.params(("10.0.0.0/16", "\\x0a\\x00\\x00\\x00", 16),
                        ("10.0.240.0/20", "\\x0a\\x00\\xf0\\x00", 20),
                        ("10.0.15.0/20", "\\x0a\\x00\\x00\\x00", 20))
    def test_table_entry_lpm(self, input_, value, length):
        te = sh.TableEntry("LpmOne")(action="actionA")
        te.match["header_test.field32"] = input_
        te.action["param"] = "aa:bb:cc:dd:ee:ff"
        te.insert()

        # Cannot use format here because it would require escaping all braces,
        # which would make wiriting tests much more annoying
        expected_entry = """
table_id: 33567650
match {
  field_id: 1
  lpm {
    value: "%s"
    prefix_len: %s
  }
}
action {
  action {
    action_id: 16783703
    params {
      param_id: 1
      value: "\\xaa\\xbb\\xcc\\xdd\\xee\\xff"
    }
  }
}
""" % (value, length)

        expected_req = self.make_write_request(
            p4runtime_pb2.Update.INSERT, P4RuntimeEntity.table_entry, expected_entry)

        self.servicer.Write.assert_called_once_with(ProtoCmp(expected_req), ANY)

    def test_table_entry_lpm_dont_care(self):
        te = sh.TableEntry("LpmOne")
        with self.assertRaisesRegex(UserError, "LPM don't care match"):
            te.match["header_test.field32"] = "10.0.0.0/0"

    @nose2.tools.params(("10.0.0.1 &&& 0xff0000ff", "\\x0a\\x00\\x00\\x01", "\\xff\\x00\\x00\\xff"),
                        ("10.0.0.1 &&& 0xff000000", "\\x0a\\x00\\x00\\x00", "\\xff\\x00\\x00\\x00"))
    def test_table_entry_ternary(self, input_, value, mask):
        te = sh.TableEntry("TernaryOne")(action="actionA")
        te.match["header_test.field32"] = input_
        te.action["param"] = "aa:bb:cc:dd:ee:ff"
        te.insert()

        expected_entry = """
table_id: 33584148
match {
  field_id: 1
  ternary {
    value: "%s"
    mask: "%s"
  }
}
action {
  action {
    action_id: 16783703
    params {
      param_id: 1
      value: "\\xaa\\xbb\\xcc\\xdd\\xee\\xff"
    }
  }
}
""" % (value, mask)

        expected_req = self.make_write_request(
            p4runtime_pb2.Update.INSERT, P4RuntimeEntity.table_entry, expected_entry)

        self.servicer.Write.assert_called_once_with(ProtoCmp(expected_req), ANY)

    def test_table_entry_ternary_dont_care(self):
        te = sh.TableEntry("TernaryOne")
        with self.assertRaisesRegex(UserError, "ternary don't care match"):
            te.match["header_test.field32"] = "10.0.0.0&&&0.0.0.0"

    def test_string_match_ekey(self):
        te = sh.TableEntry("StringMatchKeyTable")(action="actionA")
        te.match["f13"] = "16"
        te.action["param"] = "aa:bb:cc:dd:ee:ff"
        te.insert()

        expected_entry = """
table_id: 33554507
match {
  field_id: 1
  exact {
    value: "16"
  }
}
action {
  action {
    action_id: 16783703
    params {
      param_id: 1
      value: "\\xaa\\xbb\\xcc\\xdd\\xee\\xff"
    }
  }
}
"""

        expected_req = self.make_write_request(
            p4runtime_pb2.Update.INSERT, P4RuntimeEntity.table_entry, expected_entry)

        self.servicer.Write.assert_called_once_with(ProtoCmp(expected_req), ANY)

    def test_table_entry_range(self):
        te = sh.TableEntry("RangeOne")(action="actionA")
        te.match["header_test.field32"] = "0..1024"
        te.action["param"] = "aa:bb:cc:dd:ee:ff"
        te.insert()

        expected_entry = """
table_id: 33603558
match {
  field_id: 1
  range {
    low: "\\x00"
    high: "\\x04\\x00"
  }
}
action {
  action {
    action_id: 16783703
    params {
      param_id: 1
      value: "\\xaa\\xbb\\xcc\\xdd\\xee\\xff"
    }
  }
}
"""

        expected_req = self.make_write_request(
            p4runtime_pb2.Update.INSERT, P4RuntimeEntity.table_entry, expected_entry)

        self.servicer.Write.assert_called_once_with(ProtoCmp(expected_req), ANY)

    def test_table_entry_range_dont_care(self):
        te = sh.TableEntry("RangeOne")
        with self.assertRaisesRegex(UserError, "range don't care match"):
            te.match["header_test.field32"] = "0..255.255.255.255"

    def test_table_entry_range_invalid(self):
        te = sh.TableEntry("RangeOne")
        with self.assertRaisesRegex(UserError, "Invalid range match"):
            te.match["header_test.field32"] = "77..22"

    def test_table_entry_optional(self):
        te = sh.TableEntry("OptionalOne")(action="actionA")
        te.match["header_test.field32"] = "0x123456"
        te.action["param"] = "aa:bb:cc:dd:ee:ff"
        te.insert()

        expected_entry = """
table_id: 33611248
match {
  field_id: 1
  optional {
    value: "\\x12\\x34\\x56"
  }
}
action {
  action {
    action_id: 16783703
    params {
      param_id: 1
      value: "\\xaa\\xbb\\xcc\\xdd\\xee\\xff"
    }
  }
}
"""
        expected_req = self.make_write_request(
            p4runtime_pb2.Update.INSERT, P4RuntimeEntity.table_entry, expected_entry)

        self.servicer.Write.assert_called_once_with(ProtoCmp(expected_req), ANY)

    def test_table_direct_with_member_id(self):
        te = sh.TableEntry("ExactOne")
        te.match["header_test.field32"] = "10.0.0.0"
        with self.assertRaisesRegex(UserError, "does not support members"):
            te.member_id = 1

        with self.assertRaisesRegex(UserError, "does not support members"):
            te = sh.TableEntry("ExactOne")(member_id=1)

    def test_table_direct_with_group_id(self):
        te = sh.TableEntry("ExactOne")
        te.match["header_test.field32"] = "10.0.0.0"
        with self.assertRaisesRegex(UserError, "does not support groups"):
            te.group_id = 1

        with self.assertRaisesRegex(UserError, "does not support groups"):
            te = sh.TableEntry("ExactOne")(group_id=1)

    def test_table_indirect(self):
        member = sh.ActionProfileMember("ActProfWS")(member_id=1, action="actionA")
        member.action["param"] = "aa:bb:cc:dd:ee:ff"
        group = sh.ActionProfileGroup("ActProfWS")(group_id=1)
        group.add(member.member_id)

        expected_member = """
action_profile_id: 285237193
member_id: 1
action {
  action_id: 16783703
  params {
    param_id: 1
    value: "\\xaa\\xbb\\xcc\\xdd\\xee\\xff"
  }
}
"""

        expected_group = """
action_profile_id: 285237193
group_id: 1
members {
  member_id: 1
  weight: 1
}
"""

        expected_entry_1 = """
table_id: 33586946
match {
  field_id: 1
  exact {
    value: "\\x0a\\x00\\x00\\x00"
  }
}
action {
  action_profile_member_id: 1
}
"""

        expected_entry_2 = """
table_id: 33586946
match {
  field_id: 1
  exact {
    value: "\\x0a\\x00\\x00\\x00"
  }
}
action {
  action_profile_group_id: 1
}
"""

        expected_req = self.make_write_request(
            p4runtime_pb2.Update.INSERT, P4RuntimeEntity.action_profile_member, expected_member)
        member.insert()
        self.servicer.Write.assert_called_with(ProtoCmp(expected_req), ANY)

        expected_req = self.make_write_request(
            p4runtime_pb2.Update.INSERT, P4RuntimeEntity.action_profile_group, expected_group)
        group.insert()
        self.servicer.Write.assert_called_with(ProtoCmp(expected_req), ANY)

        te = sh.TableEntry("IndirectWS")
        te.match["header_test.field32"] = "10.0.0.0"
        te.member_id = member.member_id
        te.insert()

        expected_req = self.make_write_request(
            p4runtime_pb2.Update.INSERT, P4RuntimeEntity.table_entry, expected_entry_1)
        self.servicer.Write.assert_called_with(ProtoCmp(expected_req), ANY)

        te.group_id = group.group_id
        te.modify()

        expected_req = self.make_write_request(
            p4runtime_pb2.Update.MODIFY, P4RuntimeEntity.table_entry, expected_entry_2)
        self.servicer.Write.assert_called_with(ProtoCmp(expected_req), ANY)

    def test_table_indirect_with_direct_action(self):
        te = sh.TableEntry("IndirectWS")
        te.match["header_test.field32"] = "10.0.0.0"
        with self.assertRaisesRegex(UserError, "does not support direct actions"):
            te.action = sh.Action("actionA")

        with self.assertRaisesRegex(UserError, "does not support direct actions"):
            te = sh.TableEntry("IndirectWS")(action="actionA")

    def test_table_metadata(self):
        te = sh.TableEntry("ExactOne")(action="actionA")
        te.metadata = b"abcdef\x00\xff"
        te.insert()

        expected_entry = """
table_id: 33582705
action {
  action {
    action_id: 16783703
  }
}
metadata: "abcdef\\x00\\xff"
"""

        expected_req = self.make_write_request(
            p4runtime_pb2.Update.INSERT, P4RuntimeEntity.table_entry, expected_entry)
        self.servicer.Write.assert_called_once_with(ProtoCmp(expected_req), ANY)

    def test_table_indirect_oneshot(self):
        te = sh.TableEntry("IndirectWS")
        te.match["header_test.field32"] = "10.0.0.0"
        a1 = sh.Action("actionA")
        a1["param"] = "aa:bb:cc:dd:ee:ff"
        a2 = sh.Action("actionB")
        a2["param"] = "10"
        te.oneshot.add(a1).add(a2, weight=2)

        expected_entry = """
table_id: 33586946
match {
  field_id: 1
  exact {
    value: "\\x0a\\x00\\x00\\x00"
  }
}
action {
  action_profile_action_set {
    action_profile_actions {
      action {
        action_id: 16783703
        params {
          param_id: 1
          value: "\\xaa\\xbb\\xcc\\xdd\\xee\\xff"
        }
      }
      weight: 1
    }
    action_profile_actions {
      action {
        action_id: 16809468
        params {
          param_id: 1
          value: "\\x0a"
        }
      }
      weight: 2
    }
  }
}
"""

        te.insert()

        expected_req = self.make_write_request(
            p4runtime_pb2.Update.INSERT, P4RuntimeEntity.table_entry, expected_entry)
        self.servicer.Write.assert_called_once_with(ProtoCmp(expected_req), ANY)

        self.simple_read_check(expected_req.updates[0].entity, te, P4RuntimeEntity.table_entry)

    def test_table_info(self):
        t = sh.P4Objects(P4Type.table)["ExactOne"]
        expected = """
preamble {
  id: 33582705
  name: "ExactOne"
  alias: "ExactOne"
}
match_fields {
  id: 1
  name: "header_test.field32"
  bitwidth: 32
  match_type: EXACT
}
action_refs {
  id: 16783703 ("actionA")
}
action_refs {
  id: 16809468 ("actionB")
}
action_refs {
  id: 16800567 ("NoAction")
  annotations: "@defaultonly"
  scope: DEFAULT_ONLY
}
direct_resource_ids: 318768298 ("ExactOne_counter")
direct_resource_ids: 352326600 ("ExactOne_meter")
size: 512
"""
        self.assertIn(str(t), expected)

    def test_counter_entry(self):
        ce = sh.CounterEntry("CounterA")
        ce.index = 99
        ce.packet_count = 100
        expected_entry = """
counter_id: 302055013
index {
  index: 99
}
data {
  packet_count: 100
}
"""
        expected_req = self.make_write_request(
            p4runtime_pb2.Update.MODIFY, P4RuntimeEntity.counter_entry, expected_entry)
        ce.modify()
        self.servicer.Write.assert_called_with(ProtoCmp(expected_req), ANY)

        self.simple_read_check(expected_req.updates[0].entity, ce, P4RuntimeEntity.counter_entry)

        ce.index = None
        expected_entry = """
counter_id: 302055013
data {
  packet_count: 100
}
"""
        expected_req = self.make_write_request(
            p4runtime_pb2.Update.MODIFY, P4RuntimeEntity.counter_entry, expected_entry)
        ce.modify()
        self.servicer.Write.assert_called_with(ProtoCmp(expected_req), ANY)

    def test_counter_entry_invalid(self):
        ce = sh.CounterEntry("CounterA")
        ce.index = 99
        with self.assertRaisesRegex(UserError, "Counter 'CounterA' is of type 'PACKETS'"):
            ce.byte_count = 1
        self.assertIsNone(ce._data)
        with self.assertRaisesRegex(UserError, "Counter 'CounterA' is of type 'PACKETS'"):
            ce.data.byte_count = 1
        self.assertIsNotNone(ce._data)

    def test_direct_counter_entry(self):
        ce = sh.DirectCounterEntry("ExactOne_counter")
        ce.table_entry.match["header_test.field32"] = "10.0.0.0"
        ce.packet_count = 100
        expected_entry = """
table_entry {
  table_id: 33582705
  match {
    field_id: 1
    exact {
      value: "\\x0a\\x00\\x00\\x00"
    }
  }
}
data {
  packet_count: 100
}
"""
        expected_req = self.make_write_request(
            p4runtime_pb2.Update.MODIFY, P4RuntimeEntity.direct_counter_entry, expected_entry)
        ce.modify()
        self.servicer.Write.assert_called_with(ProtoCmp(expected_req), ANY)

        self.simple_read_check(
            expected_req.updates[0].entity, ce, P4RuntimeEntity.direct_counter_entry)

        ce.table_entry = None
        expected_entry = """
table_entry {
  table_id: 33582705
}
data {
  packet_count: 100
}
"""
        expected_req = self.make_write_request(
            p4runtime_pb2.Update.MODIFY, P4RuntimeEntity.direct_counter_entry, expected_entry)
        ce.modify()
        self.servicer.Write.assert_called_with(ProtoCmp(expected_req), ANY)

    def test_direct_counter_entry_2(self):
        te = sh.TableEntry("ExactOne")(action="actionA")
        te.match["header_test.field32"] = "10.0.0.0"
        te.action["param"] = "aa:bb:cc:dd:ee:ff"
        te.counter_data.packet_count = 100
        expected_entry = """
table_id: 33582705
match {
  field_id: 1
  exact {
    value: "\\x0a\\x00\\x00\\x00"
  }
}
action {
  action {
    action_id: 16783703
    params {
      param_id: 1
      value: "\\xaa\\xbb\\xcc\\xdd\\xee\\xff"
    }
  }
}
counter_data {
  packet_count: 100
}
"""
        expected_req = self.make_write_request(
            p4runtime_pb2.Update.INSERT, P4RuntimeEntity.table_entry, expected_entry)
        te.insert()
        self.servicer.Write.assert_called_once_with(ProtoCmp(expected_req), ANY)

        self.simple_read_check(expected_req.updates[0].entity, te, P4RuntimeEntity.table_entry)

    def test_direct_counter_entry_invalid(self):
        ce = sh.DirectCounterEntry("ExactOne_counter")
        with self.assertRaisesRegex(UserError, "table_entry must be an instance of TableEntry"):
            ce.table_entry = 0xbad
        with self.assertRaisesRegex(UserError, "This DirectCounterEntry is for table"):
            ce.table_entry = sh.TableEntry("TernaryOne")
        with self.assertRaisesRegex(UserError, "Direct counters are not index-based"):
            ce.index = 1

        te = sh.TableEntry("LpmOne")(action="actionA")
        with self.assertRaisesRegex(UserError, "Table has no direct counter"):
            te.counter_data.packet_count = 100

        te = sh.TableEntry("ExactOne")(action="actionA")
        with self.assertRaisesRegex(UserError, "Counter 'ExactOne_counter' is of type 'PACKETS"):
            te.counter_data.byte_count = 100

    def test_meter_entry(self):
        ce = sh.MeterEntry("MeterA")
        ce.index = 99
        ce.cir = 1
        ce.cburst = 2
        ce.pir = 3
        ce.pburst = 4
        expected_entry = """
meter_id: 335597387
index {
  index: 99
}
config {
  cir: 1
  cburst: 2
  pir: 3
  pburst: 4
}
"""
        expected_req = self.make_write_request(
            p4runtime_pb2.Update.MODIFY, P4RuntimeEntity.meter_entry, expected_entry)
        ce.modify()
        self.servicer.Write.assert_called_with(ProtoCmp(expected_req), ANY)

        self.simple_read_check(expected_req.updates[0].entity, ce, P4RuntimeEntity.meter_entry)

        ce.index = None
        expected_entry = """
meter_id: 335597387
config {
  cir: 1
  cburst: 2
  pir: 3
  pburst: 4
}
"""
        expected_req = self.make_write_request(
            p4runtime_pb2.Update.MODIFY, P4RuntimeEntity.meter_entry, expected_entry)
        ce.modify()
        self.servicer.Write.assert_called_with(ProtoCmp(expected_req), ANY)

    def test_direct_meter_entry(self):
        ce = sh.DirectMeterEntry("ExactOne_meter")
        ce.table_entry.match["header_test.field32"] = "10.0.0.0"
        ce.cir = 1
        ce.cburst = 2
        ce.pir = 3
        ce.pburst = 4
        expected_entry = """
table_entry {
  table_id: 33582705
  match {
    field_id: 1
    exact {
      value: "\\x0a\\x00\\x00\\x00"
    }
  }
}
config {
  cir: 1
  cburst: 2
  pir: 3
  pburst: 4
}
"""
        expected_req = self.make_write_request(
            p4runtime_pb2.Update.MODIFY, P4RuntimeEntity.direct_meter_entry, expected_entry)
        ce.modify()
        self.servicer.Write.assert_called_with(ProtoCmp(expected_req), ANY)

        self.simple_read_check(
            expected_req.updates[0].entity, ce, P4RuntimeEntity.direct_meter_entry)

        ce.table_entry = None
        expected_entry = """
table_entry {
  table_id: 33582705
}
config {
  cir: 1
  cburst: 2
  pir: 3
  pburst: 4
}
"""
        expected_req = self.make_write_request(
            p4runtime_pb2.Update.MODIFY, P4RuntimeEntity.direct_meter_entry, expected_entry)
        ce.modify()
        self.servicer.Write.assert_called_with(ProtoCmp(expected_req), ANY)

    def test_direct_meter_entry_2(self):
        te = sh.TableEntry("ExactOne")(action="actionA")
        te.match["header_test.field32"] = "10.0.0.0"
        te.action["param"] = "aa:bb:cc:dd:ee:ff"
        te.meter_config.cir = 1
        te.meter_config.cburst = 2
        te.meter_config.pir = 3
        te.meter_config.pburst = 4
        expected_entry = """
table_id: 33582705
match {
  field_id: 1
  exact {
    value: "\\x0a\\x00\\x00\\x00"
  }
}
action {
  action {
    action_id: 16783703
    params {
      param_id: 1
      value: "\\xaa\\xbb\\xcc\\xdd\\xee\\xff"
    }
  }
}
meter_config {
  cir: 1
  cburst: 2
  pir: 3
  pburst: 4
}
"""
        expected_req = self.make_write_request(
            p4runtime_pb2.Update.INSERT, P4RuntimeEntity.table_entry, expected_entry)
        te.insert()
        self.servicer.Write.assert_called_once_with(ProtoCmp(expected_req), ANY)

        self.simple_read_check(expected_req.updates[0].entity, te, P4RuntimeEntity.table_entry)

    def test_direct_meter_entry_invalid(self):
        ce = sh.DirectMeterEntry("ExactOne_meter")
        with self.assertRaisesRegex(UserError, "table_entry must be an instance of TableEntry"):
            ce.table_entry = 0xbad
        with self.assertRaisesRegex(UserError, "This DirectMeterEntry is for table"):
            ce.table_entry = sh.TableEntry("TernaryOne")
        with self.assertRaisesRegex(UserError, "Direct meters are not index-based"):
            ce.index = 1

        te = sh.TableEntry("LpmOne")(action="actionA")
        with self.assertRaisesRegex(UserError, "Table has no direct meter"):
            te.meter_config.cir = 100

    @nose2.tools.params((sh.CounterEntry, "CounterA"), (sh.DirectCounterEntry, "ExactOne_counter"),
                        (sh.MeterEntry, "MeterA"), (sh.DirectMeterEntry, "ExactOne_meter"))
    def test_modify_only(self, cls, name):
        e = cls(name)

        with self.assertRaisesRegex(NotImplementedError, "Insert not supported"):
            e.insert()
        self.assertNotIn("insert", dir(e))

        with self.assertRaisesRegex(NotImplementedError, "Delete not supported"):
            e.delete()
        self.assertNotIn("delete", dir(e))

    def test_multicast_group_entry(self):
        mcge = sh.MulticastGroupEntry(1)
        mcge.add(1, 1).add(1, 2).add(2, 3)

        expected_entry = """
multicast_group_entry {
  multicast_group_id: 1
  replicas {
    egress_port: 1
    instance: 1
  }
  replicas {
    egress_port: 1
    instance: 2
  }
  replicas {
    egress_port: 2
    instance: 3
  }
}
"""

        mcge.insert()

        expected_req = self.make_write_request(
            p4runtime_pb2.Update.INSERT,
            P4RuntimeEntity.packet_replication_engine_entry,
            expected_entry)
        self.servicer.Write.assert_called_once_with(ProtoCmp(expected_req), ANY)

        self.simple_read_check(
            expected_req.updates[0].entity, mcge, P4RuntimeEntity.packet_replication_engine_entry,
            expect_iterator=False)

    def test_multicast_group_entry_invalid(self):
        mcge = sh.MulticastGroupEntry()
        mcge.add(1, 1)
        with self.assertRaisesRegex(UserError, "0 is not a valid group_id"):
            mcge.insert()

    def test_clone_session_entry(self):
        cse = sh.CloneSessionEntry(1)
        cse.add(1, 1).add(1, 2).add(2, 3)

        expected_entry = """
clone_session_entry {
  session_id: 1
  replicas {
    egress_port: 1
    instance: 1
  }
  replicas {
    egress_port: 1
    instance: 2
  }
  replicas {
    egress_port: 2
    instance: 3
  }
}
"""

        cse.insert()

        expected_req = self.make_write_request(
            p4runtime_pb2.Update.INSERT,
            P4RuntimeEntity.packet_replication_engine_entry,
            expected_entry)
        self.servicer.Write.assert_called_once_with(ProtoCmp(expected_req), ANY)

        self.simple_read_check(
            expected_req.updates[0].entity, cse, P4RuntimeEntity.packet_replication_engine_entry,
            expect_iterator=False)

    def test_p4runtime_api_version(self):
        version = sh.APIVersion()
        self.assertEqual(version, self.servicer.p4runtime_api_version)

    def test_global_options(self):
        option_name = "canonical_bytestrings"
        options = sh.global_options
        self.assertEqual(options[option_name], True)
        options[option_name] = False
        self.assertEqual(options.get(option_name), False)
        options.reset()
        self.assertEqual(options.get(option_name), True)
        options.set(option_name, False)
        self.assertEqual(options[option_name], False)

    def test_global_options_invalid(self):
        with self.assertRaisesRegex(UserError, "Unknown option name"):
            sh.global_options["foo"]
        with self.assertRaisesRegex(UserError, "Invalid value type"):
            sh.global_options["canonical_bytestrings"] = "bar"

    def test_packet_in(self):
        # In this tests we will send a packet-in message from the servicer and check if
        # packet_in.sniff method works
        msg = p4runtime_pb2.StreamMessageResponse()
        msg.packet.payload = b'Random packet-in payload'
        md = p4runtime_pb2.PacketMetadata()
        md.metadata_id = 1
        md.value = b'\x00\x01'
        msg.packet.metadata.append(md)

        # Have to sniff the packet in another thread since this blocks the thread
        packet_in = sh.PacketIn()
        captured_packet = []

        def _sniff_packet(captured_packet):
            captured_packet += packet_in.sniff(timeout=1)
        _t = Thread(target=_sniff_packet, args=(captured_packet, ))
        _t.start()

        # TODO: modify the servicer to send stream message?
        sh.client.stream_in_q["packet"].put(msg)
        _t.join()

        self.assertEqual(len(captured_packet), 1)
        self.assertEqual(captured_packet[0],  msg)

    def test_packet_out(self):
        expected_msg = p4runtime_pb2.StreamMessageRequest()
        expected_msg.packet.payload = b'Random packet-out payload'
        md = p4runtime_pb2.PacketMetadata()
        md.metadata_id = 1
        md.value = b'\x00\x01'
        expected_msg.packet.metadata.append(md)

        packet_out = sh.PacketOut()
        packet_out.payload = b'Random packet-out payload'
        packet_out.metadata['egress_port'] = '1'
        packet_out.send()

        actual_msg = self.servicer.stored_packet_out.get(block=True, timeout=1)
        self.assertEqual(actual_msg, expected_msg)


class P4RuntimeClientTestCase(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.device_id = 0
        self.election_id = (0, 1)

        self.servicer = P4RuntimeServicer()
        self.servicer.Write = Mock(spec=[], return_value=p4runtime_pb2.WriteResponse())
        self.servicer.Read = Mock(spec=[], return_value=p4runtime_pb2.ReadResponse())
        # Starting with gRPC 1.20.0, the server code checks for the presence of an
        # experimental_non_blocking
        # (https://github.com/grpc/grpc/blob/v1.20.0/src/python/grpcio/grpc/_server.py#L532). We
        # need to make sure it is *not* present with spec=[].
        self.servicer.StreamChannel = Mock(spec=[])
        p4runtime_pb2_grpc.add_P4RuntimeServicer_to_server(self.servicer, self.server)

    def test_arbitration_backup(self):
        def StreamChannelMock(request_iterator, context):
            for req in request_iterator:
                if req.HasField('arbitration'):
                    rep = p4runtime_pb2.StreamMessageResponse()
                    rep.arbitration.CopyFrom(req.arbitration)
                    rep.arbitration.status.code = code_pb2.ALREADY_EXISTS
                    yield rep
        self.servicer.StreamChannel.side_effect = StreamChannelMock

        with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
            client = sh.P4RuntimeClient(self.device_id, self.grpc_addr, (0, 1))
            self.assertIn("You are not the primary client", mock_stdout.getvalue())
            self.servicer.StreamChannel.assert_called_once_with(ANY, ANY)
            client.tear_down()
