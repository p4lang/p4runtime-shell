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

from callee import Matcher
from concurrent import futures
import google.protobuf.text_format
from google.rpc import code_pb2
import grpc
import logging
import unittest
from unittest.mock import ANY, Mock
import subprocess
from p4.v1 import p4runtime_pb2, p4runtime_pb2_grpc
from p4.config.v1 import p4info_pb2
import p4runtime_sh.shell as sh
from p4runtime_sh.context import P4Type


class P4RuntimeServicer(p4runtime_pb2_grpc.P4RuntimeServicer):
    def __init__(self):
        self.p4info = p4info_pb2.P4Info()

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
                # rep = p4runtime_pb2.StreamMessageResponse()
                rep = req
                rep.arbitration.status.code = code_pb2.OK
                yield rep


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

    def run_sh(self, input=None):
        r = subprocess.run(
            ["./p4runtime-sh", "--grpc-addr", self.grpc_addr,
             "--config", ",".join([self._p4info_path, self._config_path])],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            input=input)
        return r.returncode, r.stdout

    def tearDown(self):
        self.server.stop(None)
        super().tearDown()


class IPythonTestCase(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.servicer = P4RuntimeServicer()
        p4runtime_pb2_grpc.add_P4RuntimeServicer_to_server(self.servicer, self.server)

    def test_run_and_exit(self):
        rc, _ = self.run_sh()
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
        self.servicer.Write = Mock(return_value=p4runtime_pb2.WriteResponse())
        p4runtime_pb2_grpc.add_P4RuntimeServicer_to_server(self.servicer, self.server)

        sh.client = sh.P4RuntimeClient(self.device_id, self.grpc_addr, (0, 1))
        sh.client.set_fwd_pipe_config(self._p4info_path, self._config_path)
        self.p4info = sh.client.get_p4info()
        sh.context.set_p4info(self.p4info)

    def tearDown(self):
        sh.client.tear_down()
        super().tearDown()

    def make_write_request_from_table_entry(self, type_, expected_txt):
        req = p4runtime_pb2.WriteRequest()
        req.device_id = self.device_id
        req.election_id.high = self.election_id[0]
        req.election_id.low = self.election_id[1]
        update = req.updates.add()
        update.type = type_
        google.protobuf.text_format.Merge(expected_txt, update.entity.table_entry)
        return req

    def test_table_entry_exact(self):
        te = sh.TableEntry("ExactOne")(action="actionA")
        te.match["header_test.field32"] = "0x12345678"
        te.action["param"] = "aa:bb:cc:dd:ee:ff"
        te.insert()

        expected_entry = """
table_id: 33582705
match {
  field_id: 1
  exact {
    value: "\\x12\\x34\\x56\\x78"
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

        expected_req = self.make_write_request_from_table_entry(
            p4runtime_pb2.Update.INSERT, expected_entry)

        self.servicer.Write.assert_called_once_with(ProtoCmp(expected_req), ANY)

    def test_table_entry_lpm(self):
        te = sh.TableEntry("LpmOne")(action="actionA")
        te.match["header_test.field32"] = "10.0.0.0/16"
        te.action["param"] = "aa:bb:cc:dd:ee:ff"
        te.insert()

        expected_entry = """
table_id: 33567650
match {
  field_id: 1
  lpm {
    value: "\\x0a\\x00\\x00\\x00"
    prefix_len: 16
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

        expected_req = self.make_write_request_from_table_entry(
            p4runtime_pb2.Update.INSERT, expected_entry)

        self.servicer.Write.assert_called_once_with(ProtoCmp(expected_req), ANY)

    def test_table_entry_ternary(self):
        te = sh.TableEntry("TernaryOne")(action="actionA")
        te.match["header_test.field32"] = "10.0.0.1 &&& 0xff0000ff"
        te.action["param"] = "aa:bb:cc:dd:ee:ff"
        te.insert()

        expected_entry = """
table_id: 33584148
match {
  field_id: 1
  ternary {
    value: "\\x0a\\x00\\x00\\x01"
    mask: "\\xff\\x00\\x00\\xff"
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

        expected_req = self.make_write_request_from_table_entry(
            p4runtime_pb2.Update.INSERT, expected_entry)

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
    low: "\\x00\\x00\\x00\\x00"
    high: "\\x00\\x00\\x04\\x00"
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

        expected_req = self.make_write_request_from_table_entry(
            p4runtime_pb2.Update.INSERT, expected_entry)

        self.servicer.Write.assert_called_once_with(ProtoCmp(expected_req), ANY)

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
