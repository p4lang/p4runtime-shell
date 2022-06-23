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

from functools import wraps
import google.protobuf.text_format
from google.rpc import status_pb2, code_pb2
import grpc
import logging
import queue
import sys
import threading
from typing import NamedTuple

from p4.v1 import p4runtime_pb2
from p4.v1 import p4runtime_pb2_grpc


class P4RuntimeErrorFormatException(Exception):
    def __init__(self, message):
        super().__init__(message)


# Used to iterate over the p4.Error messages in a gRPC error Status object
class P4RuntimeErrorIterator:
    def __init__(self, grpc_error):
        assert(grpc_error.code() == grpc.StatusCode.UNKNOWN)
        self.grpc_error = grpc_error

        error = None
        # The gRPC Python package does not have a convenient way to access the
        # binary details for the error: they are treated as trailing metadata.
        for meta in self.grpc_error.trailing_metadata():
            if meta[0] == "grpc-status-details-bin":
                error = status_pb2.Status()
                error.ParseFromString(meta[1])
                break
        if error is None:
            raise P4RuntimeErrorFormatException("No binary details field")

        if len(error.details) == 0:
            raise P4RuntimeErrorFormatException(
                "Binary details field has empty Any details repeated field")
        self.errors = error.details
        self.idx = 0

    def __iter__(self):
        return self

    def __next__(self):
        while self.idx < len(self.errors):
            p4_error = p4runtime_pb2.Error()
            one_error_any = self.errors[self.idx]
            if not one_error_any.Unpack(p4_error):
                raise P4RuntimeErrorFormatException(
                    "Cannot convert Any message to p4.Error")
            if p4_error.canonical_code == code_pb2.OK:
                continue
            v = self.idx, p4_error
            self.idx += 1
            return v
        raise StopIteration


# P4Runtime uses a 3-level message in case of an error during the processing of
# a write batch. This means that if we do not wrap the grpc.RpcError inside a
# custom exception, we can end-up with a non-helpful exception message in case
# of failure as only the first level will be printed. In this custom exception
# class, we extract the nested error message (one for each operation included in
# the batch) in order to print error code + user-facing message.  See P4 Runtime
# documentation for more details on error-reporting.
class P4RuntimeWriteException(Exception):
    def __init__(self, grpc_error):
        assert(grpc_error.code() == grpc.StatusCode.UNKNOWN)
        super().__init__()
        self.errors = []
        try:
            error_iterator = P4RuntimeErrorIterator(grpc_error)
            for error_tuple in error_iterator:
                self.errors.append(error_tuple)
        except P4RuntimeErrorFormatException:
            raise  # just propagate exception for now

    def __str__(self):
        message = "Error(s) during Write:\n"
        for idx, p4_error in self.errors:
            code_name = code_pb2._CODE.values_by_number[
                p4_error.canonical_code].name
            message += "\t* At index {}: {}, '{}'\n".format(
                idx, code_name, p4_error.message)
        return message


class P4RuntimeException(Exception):
    def __init__(self, grpc_error):
        super().__init__()
        self.grpc_error = grpc_error

    def __str__(self):
        message = "P4Runtime RPC error ({}): {}".format(
            self.grpc_error.code().name, self.grpc_error.details())
        return message


def parse_p4runtime_write_error(f):
    @wraps(f)
    def handle(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except grpc.RpcError as e:
            if e.code() != grpc.StatusCode.UNKNOWN:
                raise e
            raise P4RuntimeWriteException(e) from None
    return handle


def parse_p4runtime_error(f):
    @wraps(f)
    def handle(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except grpc.RpcError as e:
            raise P4RuntimeException(e) from None
    return handle


class SSLOptions(NamedTuple):
    insecure: bool
    cacert: str = None
    cert: str = None
    key: str = None


def read_pem_file(path):
    try:
        with open(path, 'rb') as f:
            return f.read()
    except Exception:
        logging.critical("Cannot read from PEM file '{}'".format(path))
        sys.exit(1)


class P4RuntimeClient:
    def __init__(self, device_id, grpc_addr, election_id, role_name=None, ssl_options=None):
        self.device_id = device_id
        self.election_id = election_id
        self.role_name = role_name
        if ssl_options is None:
            self.ssl_options = SSLOptions(True)
        else:
            self.ssl_options = ssl_options
        logging.debug("Connecting to device {} at {}".format(device_id, grpc_addr))
        if self.ssl_options.insecure:
            try:
                logging.debug("Using insecure channel")
                self.channel = grpc.insecure_channel(grpc_addr)
            except Exception:
                logging.critical("Failed to connect to P4Runtime server")
                sys.exit(1)
        else:
            # root certificates are retrieved from a default location chosen by gRPC runtime unless
            # the user provides custom certificates.
            root_certificates = None
            if self.ssl_options.cacert is not None:
                root_certificates = read_pem_file(self.ssl_options.cacert)
            certificate_chain = None
            if self.ssl_options.cert is not None:
                certificate_chain = read_pem_file(self.ssl_options.cert)
            private_key = None
            if self.ssl_options.key is not None:
                private_key = read_pem_file(self.ssl_options.key)
            creds = grpc.ssl_channel_credentials(root_certificates, private_key, certificate_chain)
            try:
                self.channel = grpc.secure_channel(grpc_addr, creds)
            except Exception:
                logging.critical("Failed to connect to P4Runtime server")
                sys.exit(1)

        self.stub = p4runtime_pb2_grpc.P4RuntimeStub(self.channel)
        self.set_up_stream()

    def set_up_stream(self):
        self.stream_out_q = queue.Queue()
        # queues for different messages
        self.stream_in_q = {
            "arbitration": queue.Queue(),
            "packet": queue.Queue(),
            "digest": queue.Queue(),
            "idle_timeout_notification": queue.Queue(),
            "unknown": queue.Queue(),
        }

        def stream_req_iterator():
            while True:
                p = self.stream_out_q.get()
                if p is None:
                    break
                yield p

        def stream_recv_wrapper(stream):
            @parse_p4runtime_error
            def stream_recv():
                for p in stream:
                    if p.HasField("arbitration"):
                        self.stream_in_q["arbitration"].put(p)
                    elif p.HasField("packet"):
                        self.stream_in_q["packet"].put(p)
                    elif p.HasField("digest"):
                        self.stream_in_q["digest"].put(p)
                    elif p.HasField("idle_timeout_notification"):
                        self.stream_in_q["idle_timeout_notification"].put(p)
                    else:
                        self.stream_in_q["unknown"].put(p)
            try:
                stream_recv()
            except P4RuntimeException as e:
                logging.critical("StreamChannel error, closing stream")
                logging.critical(e)
                for k in self.stream_in_q:
                    self.stream_in_q[k].put(None)
        self.stream = self.stub.StreamChannel(stream_req_iterator())
        self.stream_recv_thread = threading.Thread(
            target=stream_recv_wrapper, args=(self.stream,))
        self.stream_recv_thread.start()
        self.handshake()

    def handshake(self):
        req = p4runtime_pb2.StreamMessageRequest()
        arbitration = req.arbitration
        arbitration.device_id = self.device_id
        election_id = arbitration.election_id
        election_id.high = self.election_id[0]
        election_id.low = self.election_id[1]
        if self.role_name is not None:
            arbitration.role.name = self.role_name
        self.stream_out_q.put(req)

        rep = self.get_stream_packet("arbitration", timeout=2)
        if rep is None:
            logging.critical("Failed to establish session with server")
            sys.exit(1)
        is_primary = (rep.arbitration.status.code == code_pb2.OK)
        logging.debug("Session established, client is '{}'".format(
            'primary' if is_primary else 'backup'))
        if not is_primary:
            print("You are not the primary client, you only have read access to the server")

    def get_stream_packet(self, type_, timeout=1):
        if type_ not in self.stream_in_q:
            print("Unknown stream type '{}'".format(type_))
            return None
        try:
            msg = self.stream_in_q[type_].get(timeout=timeout)
            return msg
        except queue.Empty:  # timeout expired
            return None

    @parse_p4runtime_error
    def get_p4info(self):
        logging.debug("Retrieving P4Info file")
        req = p4runtime_pb2.GetForwardingPipelineConfigRequest()
        req.device_id = self.device_id
        req.response_type = p4runtime_pb2.GetForwardingPipelineConfigRequest.P4INFO_AND_COOKIE
        rep = self.stub.GetForwardingPipelineConfig(req)
        return rep.config.p4info

    @parse_p4runtime_error
    def set_fwd_pipe_config(self, p4info_path, bin_path):
        logging.debug("Setting forwarding pipeline config")
        req = p4runtime_pb2.SetForwardingPipelineConfigRequest()
        req.device_id = self.device_id
        if self.role_name is not None:
            req.role = self.role_name
        election_id = req.election_id
        election_id.high = self.election_id[0]
        election_id.low = self.election_id[1]
        req.action = p4runtime_pb2.SetForwardingPipelineConfigRequest.VERIFY_AND_COMMIT
        with open(p4info_path, 'r') as f1:
            with open(bin_path, 'rb') as f2:
                try:
                    google.protobuf.text_format.Merge(f1.read(), req.config.p4info)
                except google.protobuf.text_format.ParseError:
                    logging.error("Error when parsing P4Info")
                    raise
                req.config.p4_device_config = f2.read()
        return self.stub.SetForwardingPipelineConfig(req)

    def tear_down(self):
        if self.stream_out_q:
            logging.debug("Cleaning up stream")
            self.stream_out_q.put(None)
        if self.stream_in_q:
            for k in self.stream_in_q:
                self.stream_in_q[k].put(None)
        if self.stream_recv_thread:
            self.stream_recv_thread.join()
        self.channel.close()
        del self.channel  # avoid a race condition if channel deleted when process terminates

    @parse_p4runtime_write_error
    def write(self, req):
        req.device_id = self.device_id
        if self.role_name is not None:
            req.role = self.role_name
        election_id = req.election_id
        election_id.high = self.election_id[0]
        election_id.low = self.election_id[1]
        return self.stub.Write(req)

    @parse_p4runtime_write_error
    def write_update(self, update):
        req = p4runtime_pb2.WriteRequest()
        req.device_id = self.device_id
        if self.role_name is not None:
            req.role = self.role_name
        election_id = req.election_id
        election_id.high = self.election_id[0]
        election_id.low = self.election_id[1]
        req.updates.extend([update])
        return self.stub.Write(req)

    # Decorator is useless here: in case of server error, the exception is raised during the
    # iteration (when next() is called).
    @parse_p4runtime_error
    def read_one(self, entity):
        req = p4runtime_pb2.ReadRequest()
        if self.role_name is not None:
            req.role = self.role_name
        req.device_id = self.device_id
        req.entities.extend([entity])
        return self.stub.Read(req)

    @parse_p4runtime_error
    def api_version(self):
        req = p4runtime_pb2.CapabilitiesRequest()
        rep = self.stub.Capabilities(req)
        return rep.p4runtime_api_version
