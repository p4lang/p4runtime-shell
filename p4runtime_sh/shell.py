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
import time
from collections import Counter, namedtuple, OrderedDict
import enum
import logging
from threading import Thread
from IPython import start_ipython
from traitlets.config.loader import Config
from IPython.terminal.prompts import Prompts, Token
import os.path
import sys
from p4runtime_sh.p4runtime import P4RuntimeClient, P4RuntimeException, parse_p4runtime_error
from p4.v1 import p4runtime_pb2
from p4.config.v1 import p4info_pb2
from . import bytes_utils
from . global_options import global_options
from .context import P4RuntimeEntity, P4Type, Context
from .utils import UserError, InvalidP4InfoError
import google.protobuf.text_format
from google.protobuf import descriptor
import queue


context = Context()
client = None


class UserUsageError(UserError):
    def __init__(self, usage):
        self.usage = usage

    def __str__(self):
        return "Usage: " + self.usage


class NotSupportedYet(UserError):
    def __init__(self, what):
        self.what = what

    def __str__(self):
        return "{} is not supported yet".format(self.what)


class _PrintContext:
    def __init__(self):
        self.skip_one = False
        self.stack = []

    def find_table(self):
        for msg in reversed(self.stack):
            if msg.DESCRIPTOR.name == "TableEntry":
                try:
                    return context.get_name_from_id(msg.table_id)
                except KeyError:
                    return None
        return None

    def find_action(self):
        for msg in reversed(self.stack):
            if msg.DESCRIPTOR.name == "Action":
                try:
                    return context.get_name_from_id(msg.action_id)
                except KeyError:
                    return None
        return None

    def find_controller_packet_metadata(self):
        for msg in reversed(self.stack):
            if msg.DESCRIPTOR.name == "PacketIn":
                return "packet_in"
            if msg.DESCRIPTOR.name == "PacketOut":
                return "packet_out"
        return None


def _sub_object(field, value, pcontext):
    id_ = value
    try:
        return context.get_name_from_id(id_)
    except KeyError:
        logging.error("Unknown object id {}".format(id_))


def _sub_mf(field, value, pcontext):
    id_ = value
    table_name = pcontext.find_table()
    if table_name is None:
        logging.error("Cannot find any table in context")
        return
    return context.get_mf_name(table_name, id_)


def _sub_ap(field, value, pcontext):
    id_ = value
    action_name = pcontext.find_action()
    if action_name is None:
        logging.error("Cannot find any action in context")
        return
    return context.get_param_name(action_name, id_)


def _sub_pkt_md(field, value, pcontext):
    id_ = value
    ctrl_pkt_md_name = pcontext.find_controller_packet_metadata()
    return context.get_packet_metadata_name_from_id(ctrl_pkt_md_name, id_)


def _gen_pretty_print_proto_field(substitutions, pcontext):
    def myPrintField(self, field, value):
        self._PrintFieldName(field)
        self.out.write(' ')
        if field.type == descriptor.FieldDescriptor.TYPE_BYTES:
            # TODO(antonin): any kind of checks required?
            self.out.write('\"')
            self.out.write(''.join('\\\\x{:02x}'.format(b) for b in value))
            self.out.write('\"')
        else:
            self.PrintFieldValue(field, value)
        subs = None
        if field.containing_type is not None:
            subs = substitutions.get(field.containing_type.name, None)
        if subs and field.name in subs and value != 0:
            name = subs[field.name](field, value, pcontext)
            self.out.write(' ("{}")'.format(name))
        self.out.write(' ' if self.as_one_line else '\n')

    return myPrintField


def _repr_pretty_proto(msg, substitutions):
    """A custom version of google.protobuf.text_format.MessageToString which represents Protobuf
    messages with a more user-friendly string. In particular, P4Runtime ids are supplemented with
    the P4 name and binary strings are displayed in hexadecimal format."""
    pcontext = _PrintContext()

    def message_formatter(message, indent, as_one_line):
        # For each messages we do 2 passes: the first one updates the _PrintContext instance and
        # calls MessageToString again. The second pass returns None immediately (default handling by
        # text_format).
        if pcontext.skip_one:
            pcontext.skip_one = False
            return
        pcontext.stack.append(message)
        pcontext.skip_one = True
        s = google.protobuf.text_format.MessageToString(
            message, indent=indent, as_one_line=as_one_line, message_formatter=message_formatter)
        s = s[indent:-1]
        pcontext.stack.pop()
        return s

    # We modify the "internals" of the text_format module which is not great as it may break in the
    # future, but this enables us to keep the code fairly small.
    saved_printer = google.protobuf.text_format._Printer.PrintField
    google.protobuf.text_format._Printer.PrintField = _gen_pretty_print_proto_field(
        substitutions, pcontext)

    s = google.protobuf.text_format.MessageToString(msg, message_formatter=message_formatter)

    google.protobuf.text_format._Printer.PrintField = saved_printer

    return s


def _repr_pretty_p4info(msg):
    substitutions = {
        "Table": {"const_default_action_id": _sub_object,
                  "implementation_id": _sub_object,
                  "direct_resource_ids": _sub_object},
        "ActionRef": {"id": _sub_object},
        "ActionProfile": {"table_ids": _sub_object},
        "DirectCounter": {"direct_table_id": _sub_object},
        "DirectMeter": {"direct_table_id": _sub_object},
    }
    return _repr_pretty_proto(msg, substitutions)


def _repr_pretty_p4runtime(msg):
    substitutions = {
        "TableEntry": {"table_id": _sub_object},
        "FieldMatch": {"field_id": _sub_mf},
        "Action": {"action_id": _sub_object},
        "Param": {"param_id": _sub_ap},
        "ActionProfileMember": {"action_profile_id": _sub_object},
        "ActionProfileGroup": {"action_profile_id": _sub_object},
        "MeterEntry": {"meter_id": _sub_object},
        "CounterEntry": {"counter_id": _sub_object},
        "ValueSetEntry": {"value_set_id": _sub_object},
        "RegisterEntry": {"register_id": _sub_object},
        "DigestEntry": {"digest_id": _sub_object},
        "DigestListAck": {"digest_id": _sub_object},
        "DigestList": {"digest_id": _sub_object},
        "PacketMetadata": {"metadata_id": _sub_pkt_md}
    }
    return _repr_pretty_proto(msg, substitutions)


class P4Object:
    def __init__(self, obj_type, obj):
        self.name = obj.preamble.name
        self.id = obj.preamble.id
        self._obj_type = obj_type
        self._obj = obj
        self.__doc__ = """
A wrapper around the P4Info Protobuf message for {} '{}'.
You can access any field from the message with <self>.<field name>.
You can access the name directly with <self>.name.
You can access the id directly with <self>.id.
If you need the underlying Protobuf message, you can access it with msg().
""".format(obj_type.pretty_name, self.name)

    def __dir__(self):
        d = ["info", "msg", "name", "id"]
        if self._obj_type == P4Type.table:
            d.append("actions")
        return d

    def _repr_pretty_(self, p, cycle):
        p.text(_repr_pretty_p4info(self._obj))

    def __str__(self):
        return _repr_pretty_p4info(self._obj)

    def __getattr__(self, name):
        return getattr(self._obj, name)

    def __settattr__(self, name, value):
        return UserError("Operation not supported")

    def msg(self):
        """Get Protobuf message object"""
        return self._obj

    def info(self):
        print(_repr_pretty_p4info(self._obj))

    def actions(self):
        """Print list of actions, only for tables and action profiles."""
        if self._obj_type == P4Type.table:
            for action in self._obj.action_refs:
                print(context.get_name_from_id(action.id))
        elif self._obj_type == P4Type.action_profile:
            t_id = self._obj.table_ids[0]
            t_name = context.get_name_from_id(t_id)
            t = context.get_table(t_name)
            for action in t.action_refs:
                print(context.get_name_from_id(action.id))
        else:
            raise UserError("'actions' is only available for tables and action profiles")


class P4Objects:
    def __init__(self, obj_type):
        self._obj_type = obj_type
        self._names = sorted([name for name, _ in context.get_objs(obj_type)])
        self._iter = None
        self.__doc__ = """
All the {pnames} in the P4 program.
To access a specific {pname}, use {p4info}['<name>'].
You can use this class to iterate over all {pname} instances:
\tfor x in {p4info}:
\t\tprint(x.id)
""".format(pname=obj_type.pretty_name, pnames=obj_type.pretty_names, p4info=obj_type.p4info_name)

    def __call__(self):
        for name in self._names:
            print(name)

    def _ipython_key_completions_(self):
        return self._names

    def __getitem__(self, name):
        obj = context.get_obj(self._obj_type, name)
        if obj is None:
            raise UserError("{} '{}' does not exist".format(
                self._obj_type.pretty_name, name))
        return P4Object(self._obj_type, obj)

    def __setitem__(self, name, value):
        raise UserError("Operation not allowed")

    def _repr_pretty_(self, p, cycle):
        p.text(self.__doc__)

    def __iter__(self):
        self._iter = iter(self._names)
        return self

    def __next__(self):
        name = next(self._iter)
        return self[name]


class MatchKey:
    def __init__(self, table_name, match_fields):
        self._table_name = table_name
        self._fields = OrderedDict()
        self._fields_suffixes = {}
        for mf in match_fields:
            self._add_field(mf)
        self._mk = OrderedDict()
        self._set_docstring()

    def _set_docstring(self):
        self.__doc__ = "Match key fields for table '{}':\n\n".format(self._table_name)
        for name, info in self._fields.items():
            self.__doc__ += str(info)
        self.__doc__ += """
Set a field value with <self>['<field_name>'] = '...'
  * For exact match: <self>['<f>'] = '<value>'
  * For ternary match: <self>['<f>'] = '<value>&&&<mask>'
  * For LPM match: <self>['<f>'] = '<value>/<mask>'
  * For range match: <self>['<f>'] = '<value>..<mask>'
  * For optional match: <self>['<f>'] = '<value>'

If it's inconvenient to use the whole field name, you can use a unique suffix.

You may also use <self>.set(<f>='<value>')
\t(<f> must not include a '.' in this case, but remember that you can use a unique suffix)
"""

    def _ipython_key_completions_(self):
        return self._fields.keys()

    def __dir__(self):
        return ["clear"]

    def _get_mf(self, name):
        if name in self._fields:
            return self._fields[name]
        if name in self._fields_suffixes:
            return self._fields[self._fields_suffixes[name]]
        raise UserError(
            "'{}' is not a valid match field name, nor a valid unique suffix, "
            "for table '{}'".format(name, self._table_name))

    def __setitem__(self, name, value):
        field_info = self._get_mf(name)
        self._mk[name] = self._parse_mf(value, field_info)
        print(self._mk[name])

    def __getitem__(self, name):
        _ = self._get_mf(name)
        print(self._mk.get(name, "Unset"))

    def _parse_mf(self, s, field_info):
        if type(s) is not str:
            raise UserError("Match field value must be a string")
        if field_info.match_type == p4info_pb2.MatchField.EXACT:
            return self._parse_mf_exact(s, field_info)
        elif field_info.match_type == p4info_pb2.MatchField.LPM:
            return self._parse_mf_lpm(s, field_info)
        elif field_info.match_type == p4info_pb2.MatchField.TERNARY:
            return self._parse_mf_ternary(s, field_info)
        elif field_info.match_type == p4info_pb2.MatchField.RANGE:
            return self._parse_mf_range(s, field_info)
        elif field_info.match_type == p4info_pb2.MatchField.OPTIONAL:
            return self._parse_mf_optional(s, field_info)
        else:
            raise UserError("Unsupported match type for field:\n{}".format(field_info))

    def _parse_mf_exact(self, s, field_info):
        v = bytes_utils.parse_value(s.strip(), field_info.bitwidth)
        return self._sanitize_and_convert_mf_exact(v, field_info)

    def _sanitize_and_convert_mf_exact(self, value, field_info):
        mf = p4runtime_pb2.FieldMatch()
        mf.field_id = field_info.id
        mf.exact.value = bytes_utils.make_canonical_if_option_set(value)
        return mf

    def _parse_mf_optional(self, s, field_info):
        v = bytes_utils.parse_value(s.strip(), field_info.bitwidth)
        return self._sanitize_and_convert_mf_optional(v, field_info)

    def _sanitize_and_convert_mf_optional(self, value, field_info):
        mf = p4runtime_pb2.FieldMatch()
        mf.field_id = field_info.id
        mf.optional.value = bytes_utils.make_canonical_if_option_set(value)
        return mf

    def _parse_mf_lpm(self, s, field_info):
        try:
            prefix, length = s.split('/')
            prefix, length = prefix.strip(), length.strip()
        except ValueError:
            prefix = s
            length = str(field_info.bitwidth)

        prefix = bytes_utils.parse_value(prefix, field_info.bitwidth)
        try:
            length = int(length)
        except ValueError:
            raise UserError("'{}' is not a valid prefix length").format(length)

        return self._sanitize_and_convert_mf_lpm(prefix, length, field_info)

    def _sanitize_and_convert_mf_lpm(self, prefix, length, field_info):
        if length == 0:
            raise UserError(
                "Ignoring LPM don't care match (prefix length of 0) as per P4Runtime spec")

        mf = p4runtime_pb2.FieldMatch()
        mf.field_id = field_info.id
        mf.lpm.prefix_len = length

        first_byte_masked = length // 8
        if first_byte_masked == len(prefix):
            mf.lpm.value = prefix
            return mf

        barray = bytearray(prefix)
        transformed = False
        r = length % 8
        byte_mask = 0xff & ((0xff << (8 - r)))
        if barray[first_byte_masked] & byte_mask != barray[first_byte_masked]:
            transformed = True
            barray[first_byte_masked] = barray[first_byte_masked] & byte_mask

        for i in range(first_byte_masked + 1, len(prefix)):
            if barray[i] != 0:
                transformed = True
                barray[i] = 0
        if transformed:
            print("LPM value was transformed to conform to the P4Runtime spec "
                  "(trailing bits must be unset)")
        mf.lpm.value = bytes(bytes_utils.make_canonical_if_option_set(barray))
        return mf

    def _parse_mf_ternary(self, s, field_info):
        try:
            value, mask = s.split('&&&')
            value, mask = value.strip(), mask.strip()
        except ValueError:
            value = s.strip()
            mask = "0b" + ("1" * field_info.bitwidth)

        value = bytes_utils.parse_value(value, field_info.bitwidth)
        mask = bytes_utils.parse_value(mask, field_info.bitwidth)

        return self._sanitize_and_convert_mf_ternary(value, mask, field_info)

    def _sanitize_and_convert_mf_ternary(self, value, mask, field_info):
        if int.from_bytes(mask, byteorder='big') == 0:
            raise UserError("Ignoring ternary don't care match (mask of 0s) as per P4Runtime spec")

        mf = p4runtime_pb2.FieldMatch()
        mf.field_id = field_info.id

        barray = bytearray(value)
        transformed = False
        for i in range(len(value)):
            if barray[i] & mask[i] != barray[i]:
                transformed = True
                barray[i] = barray[i] & mask[i]
        if transformed:
            print("Ternary value was transformed to conform to the P4Runtime spec "
                  "(masked off bits must be unset)")
        mf.ternary.value = bytes(bytes_utils.make_canonical_if_option_set(barray))
        mf.ternary.mask = bytes_utils.make_canonical_if_option_set(mask)
        return mf

    def _parse_mf_range(self, s, field_info):
        try:
            start, end = s.split('..')
            start, end = start.strip(), end.strip()
        except ValueError:
            raise UserError("'{}' does not specify a valid range, use '<start>..<end>'").format(
                s)

        start = bytes_utils.parse_value(start, field_info.bitwidth)
        end = bytes_utils.parse_value(end, field_info.bitwidth)

        return self._sanitize_and_convert_mf_range(start, end, field_info)

    def _sanitize_and_convert_mf_range(self, start, end, field_info):
        # It's a bit silly: the fields are converted from str to int to bytes by bytes_utils, then
        # converted back to int here...
        start_ = int.from_bytes(start, byteorder='big')
        end_ = int.from_bytes(end, byteorder='big')
        if start_ > end_:
            raise UserError("Invalid range match: start is greater than end")
        if start_ == 0 and end_ == ((1 << field_info.bitwidth) - 1):
            raise UserError(
                "Ignoring range don't care match (all possible values) as per P4Runtime spec")
        mf = p4runtime_pb2.FieldMatch()
        mf.field_id = field_info.id
        mf.range.low = bytes_utils.make_canonical_if_option_set(start)
        mf.range.high = bytes_utils.make_canonical_if_option_set(end)
        return mf

    def _add_field(self, field_info):
        self._fields[field_info.name] = field_info
        self._recompute_suffixes()

    def _recompute_suffixes(self):
        suffixes = {}
        suffix_count = Counter()
        for fname in self._fields:
            suffix = None
            for s in reversed(fname.split(".")):
                suffix = s if suffix is None else s + "." + suffix
                suffixes[suffix] = fname
                suffix_count[suffix] += 1
        for suffix, c in suffix_count.items():
            if c > 1:
                del suffixes[suffix]
        self._fields_suffixes = suffixes

    def __str__(self):
        return '\n'.join([str(mf) for name, mf in self._mk.items()])

    def _repr_pretty_(self, p, cycle):
        for name, mf in self._mk.items():
            p.text(str(mf))

    def set(self, **kwargs):
        for name, value in kwargs.items():
            self[name] = value

    def clear(self):
        self._mk.clear()

    def _count(self):
        return len(self._mk)


class Action:
    def __init__(self, action_name=None):
        self._init = False
        if action_name is None:
            raise UserError("Please provide name for action")
        self.action_name = action_name
        action_info = context.get_action(action_name)
        if action_info is None:
            raise UserError("Unknown action '{}'".format(action_name))
        self._action_id = action_info.preamble.id
        self._params = OrderedDict()
        for param in action_info.params:
            self._params[param.name] = param
        self._action_info = action_info
        self._param_values = OrderedDict()
        self._set_docstring()
        self._init = True

    def _set_docstring(self):
        self.__doc__ = "Action parameters for action '{}':\n\n".format(self.action_name)
        for name, info in self._params.items():
            self.__doc__ += str(info)
        self.__doc__ += "\n\n"
        self.__doc__ += "Set a param value with <self>['<param_name>'] = '<value>'\n"
        self.__doc__ += "You may also use <self>.set(<param_name>='<value>')\n"

    def _ipython_key_completions_(self):
        return self._params.keys()

    def __dir__(self):
        return ["action_name", "msg", "set"]

    def _get_param(self, name):
        if name not in self._params:
            raise UserError(
                "'{}' is not a valid action parameter name for action '{}'".format(
                    name, self._action_name))
        return self._params[name]

    def __setattr__(self, name, value):
        if name[0] == "_" or not self._init:
            super().__setattr__(name, value)
            return
        if name == "action_name":
            raise UserError("Cannot change action name")
        super().__setattr__(name, value)

    def __setitem__(self, name, value):
        param_info = self._get_param(name)
        self._param_values[name] = self._parse_param(value, param_info)
        print(self._param_values[name])

    def __getitem__(self, name):
        _ = self._get_param(name)
        print(self._param_values.get(name, "Unset"))

    def _parse_param(self, s, param_info):
        if type(s) is not str:
            raise UserError("Action parameter value must be a string")
        v = bytes_utils.parse_value(s, param_info.bitwidth)
        p = p4runtime_pb2.Action.Param()
        p.param_id = param_info.id
        p.value = bytes_utils.make_canonical_if_option_set(v)
        return p

    def msg(self):
        msg = p4runtime_pb2.Action()
        msg.action_id = self._action_id
        msg.params.extend(self._param_values.values())
        return msg

    def _from_msg(self, msg):
        assert(self._action_id == msg.action_id)
        self._params.clear()
        for p in msg.params:
            p_name = context.get_param_name(self.action_name, p.param_id)
            self._param_values[p_name] = p

    def __str__(self):
        return str(self.msg())

    def _repr_pretty_(self, p, cycle):
        p.text(str(self.msg()))

    def set(self, **kwargs):
        for name, value in kwargs.items():
            self[name] = value


class _EntityBase:
    def __init__(self, entity_type, p4runtime_cls, modify_only=False):
        self._init = False
        self._entity_type = entity_type
        self._entry = p4runtime_cls()
        self._modify_only = modify_only

    def __dir__(self):
        d = ["msg", "read"]
        if self._modify_only:
            d.append("modify")
        else:
            d.extend(["insert", "modify", "delete"])
        return d

    # to be called before issueing a P4Runtime request
    # enforces checks that cannot be performed when setting individual fields
    def _validate_msg(self):
        return True

    def _update_msg(self):
        pass

    def __str__(self):
        self._update_msg()
        return str(_repr_pretty_p4runtime(self._entry))

    def _repr_pretty_(self, p, cycle):
        self._update_msg()
        p.text(_repr_pretty_p4runtime(self._entry))

    def __getattr__(self, name):
        raise AttributeError("'{}' object has no attribute '{}'".format(
            self.__class__.__name__, name))

    def msg(self):
        self._update_msg()
        return self._entry

    def _write(self, type_):
        self._update_msg()
        self._validate_msg()
        update = p4runtime_pb2.Update()
        update.type = type_
        getattr(update.entity, self._entity_type.name).CopyFrom(self._entry)
        client.write_update(update)

    def insert(self):
        if self._modify_only:
            raise NotImplementedError("Insert not supported for {}".format(self._entity_type.name))
        logging.debug("Inserting entry")
        self._write(p4runtime_pb2.Update.INSERT)

    def delete(self):
        if self._modify_only:
            raise NotImplementedError("Delete not supported for {}".format(self._entity_type.name))
        logging.debug("Deleting entry")
        self._write(p4runtime_pb2.Update.DELETE)

    def modify(self):
        logging.debug("Modifying entry")
        self._write(p4runtime_pb2.Update.MODIFY)

    def _from_msg(self, msg):
        raise NotImplementedError

    def read(self, function=None):
        # Entities should override this method and provide a helpful docstring
        self._update_msg()
        self._validate_msg()
        entity = p4runtime_pb2.Entity()
        getattr(entity, self._entity_type.name).CopyFrom(self._entry)

        iterator = client.read_one(entity)

        # Cannot use a (simpler) generator here as we need to decorate __next__ with
        # @parse_p4runtime_error.
        class _EntryIterator:
            def __init__(self, entity, it):
                self._entity = entity
                self._it = it
                self._entities_it = None

            def __iter__(self):
                return self

            @parse_p4runtime_error
            def __next__(self):
                if self._entities_it is None:
                    rep = next(self._it)
                    self._entities_it = iter(rep.entities)
                try:
                    entity = next(self._entities_it)
                except StopIteration:
                    self._entities_it = None
                    return next(self)

                if isinstance(self._entity, _P4EntityBase):
                    e = type(self._entity)(self._entity.name)  # create new instance of same entity
                else:
                    e = type(self._entity)()
                msg = getattr(entity, self._entity._entity_type.name)
                e._from_msg(msg)
                # neither of these should be needed
                # e._update_msg()
                # e._entry.CopyFrom(msg)
                return e

        if function is None:
            return _EntryIterator(self, iterator)
        else:
            for x in _EntryIterator(self, iterator):
                function(x)


class _P4EntityBase(_EntityBase):
    def __init__(self, p4_type, entity_type, p4runtime_cls, name=None, modify_only=False):
        super().__init__(entity_type, p4runtime_cls, modify_only)
        self._p4_type = p4_type
        if name is None:
            raise UserError("Please provide name for {}".format(p4_type.pretty_name))
        self.name = name
        self._info = P4Objects(p4_type)[name]
        self.id = self._info.id

    def __dir__(self):
        return super().__dir__() + ["name", "id", "info"]

    def info(self):
        """Display P4Info entry for the object"""
        return self._info


class ActionProfileMember(_P4EntityBase):
    def __init__(self, action_profile_name=None):
        super().__init__(
            P4Type.action_profile, P4RuntimeEntity.action_profile_member,
            p4runtime_pb2.ActionProfileMember, action_profile_name)
        self.member_id = 0
        self.action = None
        self._valid_action_ids = self._get_action_set()
        self.__doc__ = """
An action profile member for '{}'

Use <self>.info to display the P4Info entry for the action profile.

Set the member id with <self>.member_id = <expr>.

To set the action specification <self>.action = <instance of type Action>.
To set the value of action parameters, use <self>.action['<param name>'] = <expr>.
Type <self>.action? for more details.


Typical usage to insert an action profile member:
m = action_profile_member['<action_profile_name>'](action='<action_name>', member_id=1)
m.action['<p1>'] = ...
...
m.action['<pM>'] = ...
# OR m.action.set(p1=..., ..., pM=...)
m.insert

For information about how to read members, use <self>.read?
""".format(action_profile_name)
        self._init = True

    def __dir__(self):
        return super().__dir__() + ["member_id", "action"]

    def _get_action_set(self):
        t_id = self._info.table_ids[0]
        t_name = context.get_name_from_id(t_id)
        t = context.get_table(t_name)
        return set([action.id for action in t.action_refs])

    def __call__(self, **kwargs):
        for name, value in kwargs.items():
            if name == "action" and type(value) is str:
                value = Action(value)
            setattr(self, name, value)
        return self

    def __setattr__(self, name, value):
        if name[0] == "_" or not self._init:
            super().__setattr__(name, value)
            return
        if name == "name":
            raise UserError("Cannot change action profile name")
        if name == "member_id":
            if type(value) is not int:
                raise UserError("member_id must be an integer")
        if name == "action" and value is not None:
            if not isinstance(value, Action):
                raise UserError("action must be an instance of Action")
            if not self._is_valid_action_id(value._action_id):
                raise UserError("action '{}' is not a valid action for this action profile".format(
                    value.action_name))
        super().__setattr__(name, value)

    def _is_valid_action_id(self, action_id):
        return action_id in self._valid_action_ids

    def _update_msg(self):
        self._entry.action_profile_id = self.id
        self._entry.member_id = self.member_id
        if self.action is not None:
            self._entry.action.CopyFrom(self.action.msg())

    def _from_msg(self, msg):
        self.member_id = msg.member_id
        if msg.HasField('action'):
            action = msg.action
            action_name = context.get_name_from_id(action.action_id)
            self.action = Action(action_name)
            self.action._from_msg(action)

    def read(self, function=None):
        """Generate a P4Runtime Read RPC. Supports wildcard reads (just leave
        the appropriate fields unset).

        If function is None, returns an iterator. Iterate over it to get all the
        members (as ActionProfileMember instances) returned by the
        server. Otherwise, function is applied to all the members returned
        by the server.
        """
        return super().read(function)


class GroupMember:
    """
    A member in an ActionProfileGroup.
    Construct with GroupMember(<member_id>, weight=<weight>, watch=<watch>,
    watch_port=<watch_port>).
    You can set / get attributes member_id (required), weight (default 1), watch (default 0),
    watch_port (default "").
    """
    def __init__(self, member_id=None, weight=1, watch=0, watch_port=b""):
        if member_id is None:
            raise UserError("member_id is required")
        self._msg = p4runtime_pb2.ActionProfileGroup.Member()
        self._msg.member_id = member_id
        self._msg.weight = weight
        if watch:
            self._msg.watch = watch
        if watch_port:
            self._msg.watch_port = watch_port

    def __dir__(self):
        return ["member_id", "weight", "watch", "watch_port"]

    def __setattr__(self, name, value):
        if name[0] == "_":
            super().__setattr__(name, value)
            return
        if name == "member_id":
            if type(value) is not int:
                raise UserError("member_id must be an integer")
            self._msg.member_id = value
            return
        if name == "weight":
            if type(value) is not int:
                raise UserError("weight must be an integer")
            self._msg.weight = value
            return
        if name == "watch":
            if type(value) is not int:
                raise UserError("watch must be an integer")
            self._msg.watch = value
            return
        if name == "watch_port":
            if type(value) is not bytes:
                raise UserError("watch_port must be a byte string")
            self._msg.watch_port = value
            return
        super().__setattr__(name, value)

    def __getattr__(self, name):
        if name == "member_id":
            return self._msg.member_id
        if name == "weight":
            return self._msg.weight
        if name == "watch":
            return self._msg.watch
        if name == "watch_port":
            return self._msg.watch_port
        return super().__getattr__(name)

    def __str__(self):
        return str(self._msg)

    def _repr_pretty_(self, p, cycle):
        p.text(str(p))


class ActionProfileGroup(_P4EntityBase):
    def __init__(self, action_profile_name=None):
        super().__init__(
            P4Type.action_profile, P4RuntimeEntity.action_profile_group,
            p4runtime_pb2.ActionProfileGroup, action_profile_name)
        self.group_id = 0
        self.max_size = 0
        self.members = []
        self.__doc__ = """
An action profile group for '{}'

Use <self>.info to display the P4Info entry for the action profile.

Set the group id with <self>.group_id = <expr>. Default is 0.
Set the max size with <self>.max_size = <expr>. Default is 0.

Add members to the group with <self>.add(<member_id>, weight=<weight>, watch=<watch>,
watch_port=<watch_port>).
weight, watch and watch port are optional (default to 1, 0 and "" respectively).

Typical usage to insert an action profile group:
g = action_profile_group['<action_profile_name>'](group_id=1)
g.add(<member id 1>)
g.add(<member id 2>)
# OR g.add(<member id 1>).add(<member id 2>)

For information about how to read groups, use <self>.read?
""".format(action_profile_name)
        self._init = True

    def __dir__(self):
        return super().__dir__() + ["group_id", "max_size", "members", "add", "clear"]

    def __call__(self, **kwargs):
        for name, value in kwargs.items():
            setattr(self, name, value)
        return self

    def __setattr__(self, name, value):
        if name[0] == "_" or not self._init:
            super().__setattr__(name, value)
            return
        if name == "name":
            raise UserError("Cannot change action profile name")
        elif name == "group_id":
            if type(value) is not int:
                raise UserError("group_id must be an integer")
        elif name == "members":
            if type(value) is not list:
                raise UserError("members must be a list of GroupMember objects")
            for m in value:
                if type(m) is not GroupMember:
                    raise UserError("members must be a list of GroupMember objects")
        super().__setattr__(name, value)

    def add(self, member_id=None, weight=1, watch=0, watch_port=b""):
        """Add a member to the members list."""
        self.members.append(GroupMember(member_id, weight, watch, watch_port))
        return self

    def clear(self):
        """Empty members list."""
        self.members = []

    def _update_msg(self):
        self._entry.action_profile_id = self.id
        self._entry.group_id = self.group_id
        self._entry.max_size = self.max_size
        del self._entry.members[:]
        for member in self.members:
            if type(member) is not GroupMember:
                raise UserError("members must be a list of GroupMember objects")
            m = self._entry.members.add()
            m.CopyFrom(member._msg)

    def _from_msg(self, msg):
        self.group_id = msg.group_id
        self.max_size = msg.max_size
        self.members = []
        for member in msg.members:
            self.add(member.member_id, member.weight, member.watch, member.watch_port)

    def read(self, function=None):
        """Generate a P4Runtime Read RPC. Supports wildcard reads (just leave
        the appropriate fields unset).

        If function is None, returns an iterator. Iterate over it to get all the
        members (as ActionProfileGroup instances) returned by the
        server. Otherwise, function is applied to all the groups returned by the
        server.
        """
        return super().read(function)


def _get_action_profile(table_name):
    table = context.get_table(table_name)
    implementation_id = table.implementation_id
    if implementation_id == 0:
        return None
    try:
        implementation_name = context.get_name_from_id(implementation_id)
    except KeyError:
        raise InvalidP4InfoError(
            "Invalid implementation_id {} for table '{}'".format(
                implementation_id, table_name))
    ap = context.get_obj(P4Type.action_profile, implementation_name)
    if ap is None:
        raise InvalidP4InfoError("Unknown implementation for table '{}'".format(table_name))
    return ap


class OneshotAction:
    """
    An action in a oneshot action set.
    Construct with OneshotAction(<action (Action instance)>, weight=<weight>, watch=<watch>,
    watch_port=<watch_port>).
    You can set / get attributes action (required), weight (default 1), watch (default 0),
    watch_port (default "").
    """
    def __init__(self, action=None, weight=1, watch=0, watch_port=b""):
        if action is None:
            raise UserError("action is required")
        self.action = action
        self.weight = weight
        self.watch = watch
        self.watch_port = watch_port

    def __dir__(self):
        return ["action", "weight", "watch", "watch_port", "msg"]

    def __setattr__(self, name, value):
        if name[0] == "_":
            super().__setattr__(name, value)
            return
        if name == "action":
            if not isinstance(value, Action):
                raise UserError("action must be an instance of Action")
        elif name == "weight":
            if type(value) is not int:
                raise UserError("weight must be an integer")
        elif name == "watch":
            if type(value) is not int:
                raise UserError("watch must be an integer")
        elif name == "watch_port":
            print(type(value), value)
            if type(value) is not bytes:
                raise UserError("watch_port must be a byte string")
        super().__setattr__(name, value)

    def msg(self):
        msg = p4runtime_pb2.ActionProfileAction()
        msg.action.CopyFrom(self.action.msg())
        msg.weight = self.weight
        if self.watch:
            msg.watch = self.watch
        if self.watch_port:
            msg.watch_port = self.watch_port
        return msg

    def __str__(self):
        return str(self.msg())

    def _repr_pretty_(self, p, cycle):
        p.text(str(self.msg()))


class Oneshot:
    def __init__(self, table_name=None):
        self._init = False
        if table_name is None:
            raise UserError("Please provide table name")
        self.table_name = table_name
        self.actions = []
        self._table_info = P4Objects(P4Type.table)[table_name]
        ap = _get_action_profile(table_name)
        if not ap:
            raise UserError("Cannot create Oneshot instance for a direct table")
        if not ap.with_selector:
            raise UserError(
                "Cannot create Oneshot instance for a table with an action profile "
                "without selector")
        self.__doc__ = """
A "oneshot" action set for table '{}'.

To add an action to the set, use <self>.add(<Action instance>).
You can also access the set of actions with <self>.actions (which is a Python list).
""".format(self.table_name)
        self._init = True

    def __dir__(self):
        return ["table_name", "actions", "add", "msg"]

    def __setattr__(self, name, value):
        if name[0] == "_" or not self._init:
            super().__setattr__(name, value)
            return
        if name == "table_name":
            raise UserError("Cannot change table name")
        elif name == "actions":
            if type(value) is not list:
                raise UserError("actions must be a list of OneshotAction objects")
            for m in value:
                if type(m) is not OneshotAction:
                    raise UserError("actions must be a list of OneshotAction objects")
                if not self._is_valid_action_id(value.action._action_id):
                    raise UserError("action '{}' is not a valid action for table {}".format(
                        value.action.action_name, self.table_name))
        super().__setattr__(name, value)

    def _is_valid_action_id(self, action_id):
        for action_ref in self._table_info.action_refs:
            if action_id == action_ref.id:
                return True
        return False

    def add(self, action=None, weight=1, watch=0, watch_port=b""):
        """Add an action to the oneshot action set."""
        self.actions.append(OneshotAction(action, weight, watch, watch_port))
        return self

    def msg(self):
        msg = p4runtime_pb2.ActionProfileActionSet()
        msg.action_profile_actions.extend([action.msg() for action in self.actions])
        return msg

    def _from_msg(self, msg):
        for action in msg.action_profile_actions:
            action_name = context.get_name_from_id(action.action.action_id)
            a = Action(action_name)
            a._from_msg(action.action)
            self.actions.append(OneshotAction(a, action.weight, action.watch, action.watch_port))

    def __str__(self):
        return str(self.msg())

    def _repr_pretty_(self, p, cycle):
        p.text(str(self.msg()))


class _CounterData:
    @staticmethod
    def attrs_for_counter_type(counter_type):
        attrs = []
        if counter_type in {p4info_pb2.CounterSpec.BYTES, p4info_pb2.CounterSpec.BOTH}:
            attrs.append("byte_count")
        if counter_type in {p4info_pb2.CounterSpec.PACKETS, p4info_pb2.CounterSpec.BOTH}:
            attrs.append("packet_count")
        return attrs

    def __init__(self, counter_name, counter_type):
        self._counter_name = counter_name
        self._counter_type = counter_type
        self._msg = p4runtime_pb2.CounterData()
        self._attrs = _CounterData.attrs_for_counter_type(counter_type)

    def __dir__(self):
        return self._attrs

    def __setattr__(self, name, value):
        if name[0] == "_":
            super().__setattr__(name, value)
            return
        if name not in self._attrs:
            type_name = p4info_pb2._COUNTERSPEC_UNIT.values_by_number[self._counter_type].name
            raise UserError("Counter '{}' is of type '{}', you cannot set '{}'".format(
                self._counter_name, type_name, name))
        if type(value) is not int:
            raise UserError("{} must be an integer".format(name))
        setattr(self._msg, name, value)

    def __getattr__(self, name):
        if name == "byte_count" or name == "packet_count":
            return getattr(self._msg, name)
        raise AttributeError("'{}' object has no attribute '{}'".format(
            self.__class__.__name__, name))

    def msg(self):
        return self._msg

    def _from_msg(self, msg):
        self._msg.CopyFrom(msg)

    def __str__(self):
        return str(self.msg())

    def _repr_pretty_(self, p, cycle):
        p.text(str(self.msg()))

    @classmethod
    def set_count(cls, instance, counter_name, counter_type, name, value):
        if instance is None:
            d = cls(counter_name, counter_type)
        else:
            d = instance
        setattr(d, name, value)
        return d

    @classmethod
    def get_count(cls, instance, counter_name, counter_type, name):
        if instance is None:
            d = cls(counter_name, counter_type)
        else:
            d = instance
        r = getattr(d, name)
        return d, r


class _MeterConfig:
    @staticmethod
    def attrs():
        return ["cir", "cburst", "pir", "pburst"]

    def __init__(self, meter_name, meter_type):
        self._meter_name = meter_name
        self._meter_type = meter_type
        self._msg = p4runtime_pb2.MeterConfig()
        self._attrs = _MeterConfig.attrs()

    def __dir__(self):
        return self._attrs

    def __setattr__(self, name, value):
        if name[0] == "_":
            super().__setattr__(name, value)
            return
        if name in self._attrs:
            if type(value) is not int:
                raise UserError("{} must be an integer".format(name))
        setattr(self._msg, name, value)

    def __getattr__(self, name):
        if name in self._attrs:
            return getattr(self._msg, name)
        raise AttributeError("'{}' object has no attribute '{}'".format(
            self.__class__.__name__, name))

    def msg(self):
        return self._msg

    def _from_msg(self, msg):
        self._msg.CopyFrom(msg)

    def __str__(self):
        return str(self.msg())

    def _repr_pretty_(self, p, cycle):
        p.text(str(self.msg()))

    @classmethod
    def set_param(cls, instance, meter_name, meter_type, name, value):
        if instance is None:
            d = cls(meter_name, meter_type)
        else:
            d = instance
        setattr(d, name, value)
        return d

    @classmethod
    def get_param(cls, instance, meter_name, meter_type, name):
        if instance is None:
            d = cls(meter_name, meter_type)
        else:
            d = instance
        r = getattr(d, name)
        return d, r


class TableEntry(_P4EntityBase):
    @enum.unique
    class _ActionSpecType(enum.Enum):
        NONE = 0
        DIRECT_ACTION = 1
        MEMBER_ID = 2
        GROUP_ID = 3
        ONESHOT = 4

    @classmethod
    def _action_spec_name_to_type(cls, name):
        return {
            "action": cls._ActionSpecType.DIRECT_ACTION,
            "member_id": cls._ActionSpecType.MEMBER_ID,
            "group_id": cls._ActionSpecType.GROUP_ID,
            "oneshot": cls._ActionSpecType.ONESHOT,
        }.get(name, None)

    def __init__(self, table_name=None):
        super().__init__(
            P4Type.table, P4RuntimeEntity.table_entry,
            p4runtime_pb2.TableEntry, table_name)
        self.match = MatchKey(table_name, self._info.match_fields)
        self._action_spec_type = self._ActionSpecType.NONE
        self._action_spec = None
        self.priority = 0
        self.is_default = False
        ap = _get_action_profile(table_name)
        if ap is None:
            self._support_members = False
            self._support_groups = False
        else:
            self._support_members = True
            self._support_groups = ap.with_selector
        self._direct_counter = None
        self._direct_meter = None
        for res_id in self._info.direct_resource_ids:
            prefix = (res_id & 0xff000000) >> 24
            if prefix == p4info_pb2.P4Ids.DIRECT_COUNTER:
                self._direct_counter = context.get_obj_by_id(res_id)
            elif prefix == p4info_pb2.P4Ids.DIRECT_METER:
                self._direct_meter = context.get_obj_by_id(res_id)
        self._counter_data = None
        self._meter_config = None
        self.metadata = b""
        self.__doc__ = """
An entry for table '{}'

Use <self>.info to display the P4Info entry for this table.

To set the match key, use <self>.match['<field name>'] = <expr>.
Type <self>.match? for more details.
""".format(table_name)
        if self._direct_counter is not None:
            self.__doc__ += """
To set the counter spec, use <self>.counter_data.byte_count and/or <self>.counter_data.packet_count.
To unset it, use <self>.counter_data = None or <self>.clear_counter_data().
"""
        if self._direct_meter is not None:
            self.__doc__ += """
To access the meter config, use <self>.meter_config.<cir|cburst|pir|pburst>.
To unset it, use <self>.meter_config = None or <self>.clear_meter_config().
"""
        if ap is None:
            self.__doc__ += """
To set the action specification (this is a direct table):
<self>.action = <instance of type Action>.
To set the value of action parameters, use <self>.action['<param name>'] = <expr>.
Type <self>.action? for more details.
"""
        if self._support_members:
            self.__doc__ += """
Access the member_id with <self>.member_id.
"""
        if self._support_groups:
            self.__doc__ += """
Or access the group_id with <self>.group_id.
"""
        self.__doc__ += """
To set the priority, use <self>.priority = <expr>.

To mark the entry as default, use <self>.is_default = True.

To add metadata to the entry, use <self>.metadata = <expr>.
"""
        if ap is None:
            self.__doc__ += """
Typical usage to insert a table entry:
t = table_entry['<table_name>'](action='<action_name>')
t.match['<f1>'] = ...
...
t.match['<fN>'] = ...
# OR t.match.set(f1=..., ..., fN=...)
t.action['<p1>'] = ...
...
t.action['<pM>'] = ...
# OR t.action.set(p1=..., ..., pM=...)
t.insert

Typical usage to set the default entry:
t = table_entry['<table_name>'](is_default=True)
t.action['<p1>'] = ...
...
t.action['<pM>'] = ...
# OR t.action.set(p1=..., ..., pM=...)
t.modify
"""
        else:
            self.__doc__ += """
Typical usage to insert a table entry:
t = table_entry['<table_name>']
t.match['<f1>'] = ...
...
t.match['<fN>'] = ...
# OR t.match.set(f1=..., ..., fN=...)
t.member_id = <expr>
"""
        self.__doc__ += """
For information about how to read table entries, use <self>.read?
"""

        self._init = True

    def __dir__(self):
        d = super().__dir__() + [
            "match", "priority", "is_default", "metadata",
            "clear_action", "clear_match", "clear_counter_data", "clear_meter_config"]
        if self._support_groups:
            d.extend(["member_id", "group_id", "oneshot"])
        elif self._support_members:
            d.append("member_id")
        else:
            d.append("action")
        if self._direct_counter is not None:
            d.append("counter_data")
        if self._direct_meter is not None:
            d.append("meter_config")
        return d

    def __call__(self, **kwargs):
        for name, value in kwargs.items():
            if name == "action" and type(value) is str:
                value = Action(value)
            setattr(self, name, value)
        return self

    def _action_spec_set_member(self, member_id):
        if type(member_id) is None:
            if self._action_spec_type == self._ActionSpecType.MEMBER_ID:
                super().__setattr__("_action_spec_type", self._ActionSpecType.NONE)
                super().__setattr__("_action_spec", None)
            return
        if type(member_id) is not int:
            raise UserError("member_id must be an integer")
        if not self._support_members:
            raise UserError(
                "Table does not have an action profile and therefore does not support members")
        super().__setattr__("_action_spec_type", self._ActionSpecType.MEMBER_ID)
        super().__setattr__("_action_spec", member_id)

    def _action_spec_set_group(self, group_id):
        if type(group_id) is None:
            if self._action_spec_type == self._ActionSpecType.GROUP_ID:
                super().__setattr__("_action_spec_type", self._ActionSpecType.NONE)
                super().__setattr__("_action_spec", None)
            return
        if type(group_id) is not int:
            raise UserError("group_id must be an integer")
        if not self._support_groups:
            raise UserError(
                "Table does not have an action profile with selector "
                "and therefore does not support groups")
        super().__setattr__("_action_spec_type", self._ActionSpecType.GROUP_ID)
        super().__setattr__("_action_spec", group_id)

    def _action_spec_set_action(self, action):
        if type(action) is None:
            if self._action_spec_type == self._ActionSpecType.DIRECT_ACTION:
                super().__setattr__("_action_spec_type", self._ActionSpecType.NONE)
                super().__setattr__("_action_spec", None)
            return
        if not isinstance(action, Action):
            raise UserError("action must be an instance of Action")
        if self._info.implementation_id != 0:
            raise UserError(
                "Table has an implementation and therefore does not support direct actions "
                "(P4Runtime 1.0 doesn't support writing the default action for indirect tables")
        if not self._is_valid_action_id(action._action_id):
            raise UserError("action '{}' is not a valid action for this table".format(
                action.action_name))
        super().__setattr__("_action_spec_type", self._ActionSpecType.DIRECT_ACTION)
        super().__setattr__("_action_spec", action)

    def _action_spec_set_oneshot(self, oneshot):
        if type(oneshot) is None:
            if self._action_spec_type == self._ActionSpecType.ONESHOT:
                super().__setattr__("_action_spec_type", self._ActionSpecType.NONE)
                super().__setattr__("_action_spec", None)
            return
        if not isinstance(oneshot, Oneshot):
            raise UserError("oneshot must be an instance of Oneshot")
        if not self._support_groups:
            raise UserError(
                "Table does not have an action profile with selector "
                "and therefore does not support oneshot programming")
        if self.name != oneshot.table_name:
            raise UserError("This Oneshot instance was not created for this table")
        super().__setattr__("_action_spec_type", self._ActionSpecType.ONESHOT)
        super().__setattr__("_action_spec", oneshot)

    def __setattr__(self, name, value):
        if name[0] == "_" or not self._init:
            super().__setattr__(name, value)
            return
        elif name == "name":
            raise UserError("Cannot change table name")
        elif name == "priority":
            if type(value) is not int:
                raise UserError("priority must be an integer")
        elif name == "match" and not isinstance(value, MatchKey):
            raise UserError("match must be an instance of MatchKey")
        elif name == "is_default":
            if type(value) is not bool:
                raise UserError("is_default must be a boolean")
            # TODO(antonin): should we do a better job and handle other cases (a field is set while
            # is_default is set to True)?
            if value is True and self.match._count() > 0:
                print("Clearing match key because entry is now default")
                self.match.clear()
        elif name == "member_id":
            self._action_spec_set_member(value)
            return
        elif name == "group_id":
            self._action_spec_set_group(value)
            return
        elif name == "oneshot":
            self._action_spec_set_oneshot(value)
        elif name == "action" and value is not None:
            self._action_spec_set_action(value)
            return
        elif name == "counter_data":
            if self._direct_counter is None:
                raise UserError("Table has no direct counter")
            if value is None:
                self._counter_data = None
                return
            raise UserError("Cannot set 'counter_data' directly")
        elif name == "meter_config":
            if self._direct_meter is None:
                raise UserError("Table has no direct meter")
            if value is None:
                self._meter_config = None
                return
            raise UserError("Cannot set 'meter_config' directly")
        elif name == "metadata":
            if type(value) is not bytes:
                raise UserError("metadata must be a byte string")
        super().__setattr__(name, value)

    def __getattr__(self, name):
        if name == "counter_data":
            if self._direct_counter is None:
                raise UserError("Table has no direct counter")
            if self._counter_data is None:
                self._counter_data = _CounterData(
                    self._direct_counter.preamble.name, self._direct_counter.spec.unit)
            return self._counter_data
        if name == "meter_config":
            if self._direct_meter is None:
                raise UserError("Table has no direct meter")
            if self._meter_config is None:
                self._meter_config = _MeterConfig(
                    self._direct_meter.preamble.name, self._direct_meter.spec.unit)
            return self._meter_config

        t = self._action_spec_name_to_type(name)
        if t is None:
            return super().__getattr__(name)
        if self._action_spec_type == t:
            return self._action_spec
        if t == self._ActionSpecType.ONESHOT:
            self._action_spec_type = self._ActionSpecType.ONESHOT
            self._action_spec = Oneshot(self.name)
            return self._action_spec
        return None

    def _is_valid_action_id(self, action_id):
        for action_ref in self._info.action_refs:
            if action_id == action_ref.id:
                return True
        return False

    def _from_msg(self, msg):
        self.priority = msg.priority
        self.is_default = msg.is_default_action
        self.metadata = msg.metadata
        for mf in msg.match:
            mf_name = context.get_mf_name(self.name, mf.field_id)
            self.match._mk[mf_name] = mf
        if msg.action.HasField('action'):
            action = msg.action.action
            action_name = context.get_name_from_id(action.action_id)
            self.action = Action(action_name)
            self.action._from_msg(action)
        elif msg.action.HasField('action_profile_member_id'):
            self.member_id = msg.action.action_profile_member_id
        elif msg.action.HasField('action_profile_group_id'):
            self.group_id = msg.action.action_profile_group_id
        elif msg.action.HasField('action_profile_action_set'):
            self.oneshot = Oneshot(self.name)
            self.oneshot._from_msg(msg.action.action_profile_action_set)
        if msg.HasField('counter_data'):
            self._counter_data = _CounterData(
                self._direct_counter.preamble.name, self._direct_counter.spec.unit)
            self._counter_data._from_msg(msg.counter_data)
        else:
            self._counter_data = None
        if msg.HasField('meter_config'):
            self._meter_config = _MeterConfig(
                self._direct_meter.preamble.name, self._direct_meter.spec.unit)
            self._meter_config._from_msg(msg.meter_config)
        else:
            self._meter_config = None

    def read(self, function=None):
        """Generate a P4Runtime Read RPC. Supports wildcard reads (just leave
        the appropriate fields unset).
        If function is None, returns an iterator. Iterate over it to get all the
        table entries (TableEntry instances) returned by the server. Otherwise,
        function is applied to all the table entries returned by the server.

        For example:
        for te in <self>.read():
            print(te)
        The above code is equivalent to the following one-liner:
        <self>.read(lambda te: print(te))

        To delete all the entries from a table, simply use:
        table_entry['<table_name>'].read(function=lambda x: x.delete())
        """
        return super().read(function)

    def _update_msg(self):
        entry = p4runtime_pb2.TableEntry()
        entry.table_id = self.id
        entry.match.extend(self.match._mk.values())
        entry.priority = self.priority
        entry.is_default_action = self.is_default
        entry.metadata = self.metadata
        if self._action_spec_type == self._ActionSpecType.DIRECT_ACTION:
            entry.action.action.CopyFrom(self._action_spec.msg())
        elif self._action_spec_type == self._ActionSpecType.MEMBER_ID:
            entry.action.action_profile_member_id = self._action_spec
        elif self._action_spec_type == self._ActionSpecType.GROUP_ID:
            entry.action.action_profile_group_id = self._action_spec
        elif self._action_spec_type == self._ActionSpecType.ONESHOT:
            entry.action.action_profile_action_set.CopyFrom(self._action_spec.msg())
        if self._counter_data is None:
            entry.ClearField('counter_data')
        else:
            entry.counter_data.CopyFrom(self._counter_data.msg())
        if self._meter_config is None:
            entry.ClearField('meter_config')
        else:
            entry.meter_config.CopyFrom(self._meter_config.msg())
        self._entry = entry

    def _validate_msg(self):
        if self.is_default and self.match._count() > 0:
            raise UserError(
                "Match key must be empty for default entry, use <self>.is_default = False "
                "or <self>.match.clear (whichever one is appropriate)")

    def clear_action(self):
        """Clears the action spec for the TableEntry."""
        super().__setattr__("_action_spec_type", self._ActionSpecType.NONE)
        super().__setattr__("_action_spec", None)

    def clear_match(self):
        """Clears the match spec for the TableEntry."""
        self.match.clear()

    def clear_counter_data(self):
        """Clear all counter data, same as <self>.counter_data = None"""
        self._counter_data = None

    def clear_meter_config(self):
        """Clear the meter config, same as <self>.meter_config = None"""
        self._meter_config = None


class _CounterEntryBase(_P4EntityBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._counter_type = self._info.spec.unit
        self._data = None

    def __dir__(self):
        return super().__dir__() + _CounterData.attrs_for_counter_type(self._counter_type) + [
            "clear_data"]

    def __call__(self, **kwargs):
        for name, value in kwargs.items():
            setattr(self, name, value)
        return self

    def __setattr__(self, name, value):
        if name[0] == "_" or not self._init:
            super().__setattr__(name, value)
            return
        if name == "name":
            raise UserError("Cannot change counter name")
        if name == "byte_count" or name == "packet_count":
            self._data = _CounterData.set_count(
                self._data, self.name, self._counter_type, name, value)
            return
        if name == "data":
            if value is None:
                self._data = None
                return
            raise UserError("Cannot set 'data' directly")
        super().__setattr__(name, value)

    def __getattr__(self, name):
        if name == "byte_count" or name == "packet_count":
            self._data, r = _CounterData.get_count(
                self._data, self.name, self._counter_type, name)
            return r
        if name == "data":
            if self._data is None:
                self._data = _CounterData(self.name, self._counter_type)
            return self._data
        return super().__getattr__(name)

    def _from_msg(self, msg):
        self._entry.CopyFrom(msg)
        if msg.HasField('data'):
            self._data = _CounterData(self.name, self._counter_type)
            self._data._from_msg(msg.data)
        else:
            self._data = None

    def _update_msg(self):
        if self._data is None:
            self._entry.ClearField('data')
        else:
            self._entry.data.CopyFrom(self._data.msg())

    def clear_data(self):
        """Clear all counter data, same as <self>.data = None"""
        self._data = None


class CounterEntry(_CounterEntryBase):
    def __init__(self, counter_name=None):
        super().__init__(
            P4Type.counter, P4RuntimeEntity.counter_entry,
            p4runtime_pb2.CounterEntry, counter_name,
            modify_only=True)
        self._entry.counter_id = self.id
        self.__doc__ = """
An entry for counter '{}'

Use <self>.info to display the P4Info entry for this counter.

Set the index with <self>.index = <expr>.
To reset it (e.g. for wildcard read), set it to None.

Access byte count and packet count with <self>.byte_count / <self>.packet_count.

To read from the counter, use <self>.read
To write to the counter, use <self>.modify
""".format(counter_name)
        self._init = True

    def __dir__(self):
        return super().__dir__() + ["index", "data"]

    def __setattr__(self, name, value):
        if name == "index":
            if value is None:
                self._entry.ClearField('index')
                return
            if type(value) is not int:
                raise UserError("index must be an integer")
            self._entry.index.index = value
            return
        super().__setattr__(name, value)

    def __getattr__(self, name):
        if name == "index":
            return self._entry.index.index
        return super().__getattr__(name)

    def read(self, function=None):
        """Generate a P4Runtime Read RPC. Supports wildcard reads (just leave
        the index unset).
        If function is None, returns an iterator. Iterate over it to get all the
        counter entries (CounterEntry instances) returned by the
        server. Otherwise, function is applied to all the counter entries
        returned by the server.

        For example:
        for c in <self>.read():
            print(c)
        The above code is equivalent to the following one-liner:
        <self>.read(lambda c: print(c))
        """
        return super().read(function)


class DirectCounterEntry(_CounterEntryBase):
    def __init__(self, direct_counter_name=None):
        super().__init__(
            P4Type.direct_counter, P4RuntimeEntity.direct_counter_entry,
            p4runtime_pb2.DirectCounterEntry, direct_counter_name,
            modify_only=True)
        self._direct_table_id = self._info.direct_table_id
        try:
            self._direct_table_name = context.get_name_from_id(self._direct_table_id)
        except KeyError:
            raise InvalidP4InfoError("direct_table_id {} is not a valid table id".format(
                self._direct_table_id))
        self._table_entry = TableEntry(self._direct_table_name)
        self.__doc__ = """
An entry for direct counter '{}'

Use <self>.info to display the P4Info entry for this direct counter.

Set the table_entry with <self>.table_entry = <TableEntry instance>.
The TableEntry instance must be for the table to which the direct counter is attached.
To reset it (e.g. for wildcard read), set it to None. It is the same as:
<self>.table_entry = TableEntry({})

Access byte count and packet count with <self>.byte_count / <self>.packet_count.

To read from the counter, use <self>.read
To write to the counter, use <self>.modify
""".format(direct_counter_name, self._direct_table_name)
        self._init = True

    def __dir__(self):
        return super().__dir__() + ["table_entry"]

    def __setattr__(self, name, value):
        if name == "index":
            raise UserError("Direct counters are not index-based")
        if name == "table_entry":
            if value is None:
                self._table_entry = TableEntry(self._direct_table_name)
                return
            if not isinstance(value, TableEntry):
                raise UserError("table_entry must be an instance of TableEntry")
            if value.name != self._direct_table_name:
                raise UserError("This DirectCounterEntry is for table '{}'".format(
                    self._direct_table_name))
            self._table_entry = value
            return
        super().__setattr__(name, value)

    def __getattr__(self, name):
        if name == "index":
            raise UserError("Direct counters are not index-based")
        if name == "table_entry":
            return self._table_entry
        return super().__getattr__(name)

    def _update_msg(self):
        super()._update_msg()
        if self._table_entry is None:
            self._entry.ClearField('table_entry')
        else:
            self._entry.table_entry.CopyFrom(self._table_entry.msg())

    def _from_msg(self, msg):
        super()._from_msg(msg)
        if msg.HasField('table_entry'):
            self._table_entry._from_msg(msg.table_entry)
        else:
            self._table_entry = None

    def read(self, function=None):
        """Generate a P4Runtime Read RPC. Supports wildcard reads (just leave
        the index unset).
        If function is None, returns an iterator. Iterate over it to get all the
        direct counter entries (DirectCounterEntry instances) returned by the
        server. Otherwise, function is applied to all the direct counter entries
        returned by the server.

        For example:
        for c in <self>.read():
            print(c)
        The above code is equivalent to the following one-liner:
        <self>.read(lambda c: print(c))
        """
        return super().read(function)


class _MeterEntryBase(_P4EntityBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._meter_type = self._info.spec.unit
        self._config = None

    def __dir__(self):
        return super().__dir__() + _MeterConfig.attrs() + ["clear_config"]

    def __call__(self, **kwargs):
        for name, value in kwargs.items():
            setattr(self, name, value)
        return self

    def __setattr__(self, name, value):
        if name[0] == "_" or not self._init:
            super().__setattr__(name, value)
            return
        if name == "name":
            raise UserError("Cannot change meter name")
        if name in _MeterConfig.attrs():
            self._config = _MeterConfig.set_param(
                self._config, self.name, self._meter_type, name, value)
            return
        if name == "config":
            if value is None:
                self._config = None
                return
            raise UserError("Cannot set 'config' directly")
        super().__setattr__(name, value)

    def __getattr__(self, name):
        if name in _MeterConfig.attrs():
            self._config, r = _MeterConfig.get_param(
                self._config, self.name, self._meter_type, name)
            return r
        if name == "config":
            if self._config is None:
                self._config = _MeterConfig(self.name, self._meter_type)
            return self._config
        return super().__getattr__(name)

    def _from_msg(self, msg):
        self._entry.CopyFrom(msg)
        if msg.HasField('config'):
            self._config = _MeterConfig(self.name, self._meter_type)
            self._config._from_msg(msg.config)
        else:
            self._config = None

    def _update_msg(self):
        if self._config is None:
            self._entry.ClearField('config')
        else:
            self._entry.config.CopyFrom(self._config.msg())

    def clear_config(self):
        """Clear the meter config, same as <self>.config = None"""
        self._config = None


class MeterEntry(_MeterEntryBase):
    def __init__(self, meter_name=None):
        super().__init__(
            P4Type.meter, P4RuntimeEntity.meter_entry,
            p4runtime_pb2.MeterEntry, meter_name,
            modify_only=True)
        self._entry.meter_id = self.id
        self.__doc__ = """
An entry for meter '{}'

Use <self>.info to display the P4Info entry for this meter.

Set the index with <self>.index = <expr>.
To reset it (e.g. for wildcard read), set it to None.

Access meter rates and burst sizes with:
<self>.cir
<self>.cburst
<self>.pir
<self>.pburst

To read from the meter, use <self>.read
To write to the meter, use <self>.modify
""".format(meter_name)
        self._init = True

    def __dir__(self):
        return super().__dir__() + ["index", "config"]

    def __setattr__(self, name, value):
        if name == "index":
            if value is None:
                self._entry.ClearField('index')
                return
            if type(value) is not int:
                raise UserError("index must be an integer")
            self._entry.index.index = value
            return
        super().__setattr__(name, value)

    def __getattr__(self, name):
        if name == "index":
            return self._entry.index.index
        return super().__getattr__(name)

    def read(self, function=None):
        """Generate a P4Runtime Read RPC. Supports wildcard reads (just leave
        the index unset).
        If function is None, returns an iterator. Iterate over it to get all the
        meter entries (MeterEntry instances) returned by the
        server. Otherwise, function is applied to all the meter entries
        returned by the server.

        For example:
        for c in <self>.read():
            print(c)
        The above code is equivalent to the following one-liner:
        <self>.read(lambda c: print(c))
        """
        return super().read(function)


class DirectMeterEntry(_MeterEntryBase):
    def __init__(self, direct_meter_name=None):
        super().__init__(
            P4Type.direct_meter, P4RuntimeEntity.direct_meter_entry,
            p4runtime_pb2.DirectMeterEntry, direct_meter_name,
            modify_only=True)
        self._direct_table_id = self._info.direct_table_id
        try:
            self._direct_table_name = context.get_name_from_id(self._direct_table_id)
        except KeyError:
            raise InvalidP4InfoError("direct_table_id {} is not a valid table id".format(
                self._direct_table_id))
        self._table_entry = TableEntry(self._direct_table_name)
        self.__doc__ = """
An entry for direct meter '{}'

Use <self>.info to display the P4Info entry for this direct meter.

Set the table_entry with <self>.table_entry = <TableEntry instance>.
The TableEntry instance must be for the table to which the direct meter is attached.
To reset it (e.g. for wildcard read), set it to None. It is the same as:
<self>.table_entry = TableEntry({})

Access meter rates and burst sizes with:
<self>.cir
<self>.cburst
<self>.pir
<self>.pburst

To read from the meter, use <self>.read
To write to the meter, use <self>.modify
""".format(direct_meter_name, self._direct_table_name)
        self._init = True

    def __dir__(self):
        return super().__dir__() + ["table_entry"]

    def __setattr__(self, name, value):
        if name == "index":
            raise UserError("Direct meters are not index-based")
        if name == "table_entry":
            if value is None:
                self._table_entry = TableEntry(self._direct_table_name)
                return
            if not isinstance(value, TableEntry):
                raise UserError("table_entry must be an instance of TableEntry")
            if value.name != self._direct_table_name:
                raise UserError("This DirectMeterEntry is for table '{}'".format(
                    self._direct_table_name))
            self._table_entry = value
            return
        super().__setattr__(name, value)

    def __getattr__(self, name):
        if name == "index":
            raise UserError("Direct meters are not index-based")
        if name == "table_entry":
            return self._table_entry
        return super().__getattr__(name)

    def _update_msg(self):
        super()._update_msg()
        if self._table_entry is None:
            self._entry.ClearField('table_entry')
        else:
            self._entry.table_entry.CopyFrom(self._table_entry.msg())

    def _from_msg(self, msg):
        super()._from_msg(msg)
        if msg.HasField('table_entry'):
            self._table_entry._from_msg(msg.table_entry)
        else:
            self._table_entry = None

    def read(self, function=None):
        """Generate a P4Runtime Read RPC. Supports wildcard reads (just leave
        the index unset).
        If function is None, returns an iterator. Iterate over it to get all the
        direct meter entries (DirectMeterEntry instances) returned by the
        server. Otherwise, function is applied to all the direct meter entries
        returned by the server.

        For example:
        for c in <self>.read():
            print(c)
        The above code is equivalent to the following one-liner:
        <self>.read(lambda c: print(c))
        """
        return super().read(function)


class P4RuntimeEntityBuilder:
    def __init__(self, obj_type, entity_type, entity_cls):
        self._obj_type = obj_type
        self._names = sorted([name for name, _ in context.get_objs(obj_type)])
        self._entity_type = entity_type
        self._entity_cls = entity_cls
        self.__doc__ = """Construct a {} entity
Usage: <var> = {}["<{} name>"]
This is equivalent to <var> = {}(<{} name>)
Use command '{}' to see list of {}
        """.format(entity_cls.__name__, entity_type.name, obj_type.pretty_name,
                   entity_cls.__name__, obj_type.pretty_name,
                   obj_type.p4info_name, obj_type.pretty_names)

    def _ipython_key_completions_(self):
        return self._names

    def __getitem__(self, name):
        obj = context.get_obj(self._obj_type, name)
        if obj is None:
            raise UserError("{} '{}' does not exist".format(
                self._obj_type.pretty_name, name))
        return self._entity_cls(name)

    def __setitem__(self, name, value):
        raise UserError("Operation not allowed")

    def _repr_pretty_(self, p, cycle):
        p.text(self.__doc__)

    def __str__(self):
        return "Construct a {} entity".format(self.entity_cls.__name__)


class Replica:
    """
    A port "replica" (port number + instance id) used for multicast and clone session programming.
    Construct with Replica(egress_port, instance=<instance>).
    You can set / get attributes egress_port (required), instance (default 0).
    """
    def __init__(self, egress_port=None, instance=0):
        if egress_port is None:
            raise UserError("egress_port is required")
        self._msg = p4runtime_pb2.Replica()
        self._msg.egress_port = egress_port
        self._msg.instance = instance

    def __dir__(self):
        return ["port", "egress_port", "instance"]

    def __setattr__(self, name, value):
        if name[0] == "_":
            super().__setattr__(name, value)
            return
        if name == "egress_port" or name == "port":
            if type(value) is not int:
                raise UserError("egress_port must be an integer")
            self._msg.egress_port = value
            return
        if name == "instance":
            if type(value) is not int:
                raise UserError("instance must be an integer")
            self._msg.instance = value
            return
        super().__setattr__(name, value)

    def __getattr__(self, name):
        if name == "egress_port" or name == "port":
            return self._msg.egress_port
        if name == "instance":
            return self._msg.instance
        return super().__getattr__(name)

    def __str__(self):
        return str(self._msg)

    def _repr_pretty_(self, p, cycle):
        p.text(str(p))


class MulticastGroupEntry(_EntityBase):
    def __init__(self, group_id=0):
        super().__init__(
            P4RuntimeEntity.packet_replication_engine_entry,
            p4runtime_pb2.PacketReplicationEngineEntry)
        self.group_id = group_id
        self.replicas = []
        self.__doc__ = """
Multicast group entry.
Create an instance with multicast_group_entry(<group_id>).
Add replicas with <self>.add(<eg_port_1>, <instance_1>).add(<eg_port_2>, <instance_2>)...
"""
        self._init = True

    def __dir__(self):
        return ["group_id", "replicas"]

    def __setattr__(self, name, value):
        if name[0] == "_":
            super().__setattr__(name, value)
            return
        elif name == "group_id":
            if type(value) is not int:
                raise UserError("group_id must be an integer")
        elif name == "replicas":
            if type(value) is not list:
                raise UserError("replicas must be a list of Replica objects")
            for r in value:
                if type(r) is not Replica:
                    raise UserError("replicas must be a list of Replica objects")
        super().__setattr__(name, value)

    def _from_msg(self, msg):
        self.group_id = msg.multicast_group_entry.multicast_group_id
        for r in msg.multicast_group_entry.replicas:
            self.add(r.egress_port, r.instance)

    def read(self, function=None):
        """Generate a P4Runtime Read RPC to read a single MulticastGroupEntry
        (wildcard reads not supported).
        If function is None, return a MulticastGroupEntry instance (or None if
        the provided group id does not exist). If function is not None, function
        is applied to the MulticastGroupEntry instance (if any).
        """
        if function is None:
            return next(super().read())
        else:
            super().read(function)

    def _update_msg(self):
        entry = p4runtime_pb2.PacketReplicationEngineEntry()
        mcg_entry = entry.multicast_group_entry
        mcg_entry.multicast_group_id = self.group_id
        for replica in self.replicas:
            r = mcg_entry.replicas.add()
            r.CopyFrom(replica._msg)
        self._entry = entry

    def _validate_msg(self):
        if self.group_id == 0:
            raise UserError("0 is not a valid group_id for MulticastGroupEntry")

    def add(self, egress_port=None, instance=0):
        """Add a replica to the multicast group."""
        self.replicas.append(Replica(egress_port, instance))
        return self


class CloneSessionEntry(_EntityBase):
    def __init__(self, session_id=0):
        super().__init__(
            P4RuntimeEntity.packet_replication_engine_entry,
            p4runtime_pb2.PacketReplicationEngineEntry)
        self.session_id = session_id
        self.replicas = []
        self.cos = 0
        self.packet_length_bytes = 0
        self.__doc__ = """
Clone session entry.
Create an instance with clone_session_entry(<session_id>).
Add replicas with <self>.add(<eg_port_1>, <instance_1>).add(<eg_port_2>, <instance_2>)...
Access class of service with <self>.cos.
Access truncation length with <self>.packet_length_bytes.
"""
        self._init = True

    def __dir__(self):
        return ["session_id", "replicas", "cos", "packet_length_bytes"]

    def __setattr__(self, name, value):
        if name[0] == "_":
            super().__setattr__(name, value)
            return
        elif name == "session_id":
            if type(value) is not int:
                raise UserError("session_id must be an integer")
        elif name == "replicas":
            if type(value) is not list:
                raise UserError("replicas must be a list of Replica objects")
            for r in value:
                if type(r) is not Replica:
                    raise UserError("replicas must be a list of Replica objects")
        elif name == "cos":
            if type(value) is not int:
                raise UserError("cos must be an integer")
        elif name == "packet_length_bytes":
            if type(value) is not int:
                raise UserError("packet_length_bytes must be an integer")
        super().__setattr__(name, value)

    def _from_msg(self, msg):
        self.session_id = msg.clone_session_entry.session_id
        for r in msg.clone_session_entry.replicas:
            self.add(r.egress_port, r.instance)
        self.cos = msg.clone_session_entry.class_of_service
        self.packet_length_bytes = msg.clone_session_entry.packet_length_bytes

    def read(self, function=None):
        """Generate a P4Runtime Read RPC to read a single CloneSessionEntry
        (wildcard reads not supported).
        If function is None, return a CloneSessionEntry instance (or None if
        the provided group id does not exist). If function is not None, function
        is applied to the CloneSessionEntry instance (if any).
        """
        if function is None:
            return next(super().read())
        else:
            super().read(function)

    def _update_msg(self):
        entry = p4runtime_pb2.PacketReplicationEngineEntry()
        cs_entry = entry.clone_session_entry
        cs_entry.session_id = self.session_id
        for replica in self.replicas:
            r = cs_entry.replicas.add()
            r.CopyFrom(replica._msg)
        cs_entry.class_of_service = self.cos
        cs_entry.packet_length_bytes = self.packet_length_bytes
        self._entry = entry

    def add(self, egress_port=None, instance=0):
        """Add a replica to the clone session."""
        self.replicas.append(Replica(egress_port, instance))
        return self


class PacketMetadata:
    def __init__(self, metadata_info_list):
        self._md_info = OrderedDict()
        self._md = OrderedDict()
        # Initialize every metadata to zero value
        for md in metadata_info_list:
            self._md_info[md.name] = md
            self._md[md.name] = self._parse_md('0', md)
        self._set_docstring()

    def _set_docstring(self):
        self.__doc__ = "Available metadata:\n\n"
        for name, info in self._md_info.items():
            self.__doc__ += str(info)
        self.__doc__ += """
Set a metadata value with <self>.['<metadata_name>'] = '...'

You may also use <self>.set(<md_name>='<value>')
"""

    def __dir__(self):
        return ["clear"]

    def _get_md_info(self, name):
        if name in self._md_info:
            return self._md_info[name]
        raise UserError("'{}' is not a valid metadata name".format(name))

    def __getitem__(self, name):
        _ = self._get_md_info(name)
        print(self._md.get(name, "Unset"))

    def _parse_md(self, value, md_info):
        if type(value) is not str:
            raise UserError("Metadata value must be a string")
        md = p4runtime_pb2.PacketMetadata()
        md.metadata_id = md_info.id
        md.value = bytes_utils.parse_value(value.strip(), md_info.bitwidth)
        return md

    def __setitem__(self, name, value):
        md_info = self._get_md_info(name)
        self._md[name] = self._parse_md(value, md_info)

    def _ipython_key_completions_(self):
        return self._md_info.keys()

    def set(self, **kwargs):
        for name, value in kwargs.items():
            self[name] = value

    def clear(self):
        self._md.clear()

    def values(self):
        return self._md.values()


class PacketIn():
    def __init__(self):
        ctrl_pkt_md = P4Objects(P4Type.controller_packet_metadata)
        self.md_info_list = {}
        if "packet_in" in ctrl_pkt_md:
            self.p4_info = ctrl_pkt_md["packet_in"]
            for md_info in self.p4_info.metadata:
                self.md_info_list[md_info.name] = md_info
        self.packet_in_queue = queue.Queue()

        def _packet_in_recv_func(packet_in_queue):
            while True:
                msg = client.get_stream_packet("packet", timeout=None)
                if not msg:
                    break
                packet_in_queue.put(msg)

        self.recv_t = Thread(target=_packet_in_recv_func, args=(self.packet_in_queue, ))
        self.recv_t.start()

    def sniff(self, function=None, timeout=None):
        """
        Return an iterator of packet-in messages.
        If the function is provided, we do not return an iterator and instead we apply
        the function to every packet-in message.
        """
        msgs = []

        if timeout is not None and timeout < 0:
            raise ValueError("Timeout can't be a negative number.")

        if timeout is None:
            while True:
                try:
                    msgs.append(self.packet_in_queue.get(block=True))
                except KeyboardInterrupt:
                    # User sends a Ctrl+C -> breaking
                    break

        else:  # timeout parameter is provided
            deadline = time.time() + timeout
            remaining_time = timeout
            while remaining_time > 0:
                try:
                    msgs.append(self.packet_in_queue.get(block=True, timeout=remaining_time))
                    remaining_time = deadline - time.time()
                except KeyboardInterrupt:
                    # User sends an interrupt(e.g., Ctrl+C).
                    break
                except queue.Empty:
                    # No item available on timeout. Exiting
                    break

        if function is None:
            return iter(msgs)
        else:
            for msg in msgs:
                function(msg)


class PacketOut:
    def __init__(self, payload=b'', **kwargs):

        self.p4_info = P4Objects(P4Type.controller_packet_metadata)["packet_out"]
        self.payload = payload
        self.metadata = PacketMetadata(self.p4_info.metadata)
        if kwargs:
            for key, value in kwargs.items():
                self.metadata[key] = value

    def _update_msg(self):
        self._entry = p4runtime_pb2.PacketOut()
        self._entry.payload = self.payload
        self._entry.metadata.extend(self.metadata.values())

    def __setattr__(self, name, value):
        if name == "payload" and type(value) is not bytes:
            raise UserError("payload must be a bytes type")
        if name == "metadata" and type(value) is not PacketMetadata:
            raise UserError("metadata must be a PacketMetadata type")
        return super().__setattr__(name, value)

    def __dir__(self):
        return ["metadata", "send", "payload"]

    def __str__(self):
        self._update_msg()
        return str(_repr_pretty_p4runtime(self._entry))

    def _repr_pretty_(self, p, cycle):
        self._update_msg()
        p.text(_repr_pretty_p4runtime(self._entry))

    def send(self):
        self._update_msg()
        msg = p4runtime_pb2.StreamMessageRequest()
        msg.packet.CopyFrom(self._entry)
        client.stream_out_q.put(msg)


def Write(input_):
    """
    Reads a WriteRequest from a file (text format) and sends it to the server.
    It rewrites the device id and election id appropriately.
    """
    req = p4runtime_pb2.WriteRequest()
    if os.path.isfile(input_):
        with open(input_, 'r') as f:
            google.protobuf.text_format.Merge(f.read(), req)
        client.write(req)
    else:
        raise UserError(
            "Write only works with files at the moment and '{}' is not a file".format(
                input_))


def APIVersion():
    """
    Returns the version of the P4Runtime API implemented by the server, using
    the Capabilities RPC.
    """
    return client.api_version()


# see https://ipython.readthedocs.io/en/stable/config/details.html
class MyPrompt(Prompts):
    def in_prompt_tokens(self, cli=None):
        return [(Token.Prompt, 'P4Runtime sh'),
                (Token.PrompSeparator, ' >>> ')]


FwdPipeConfig = namedtuple('FwdPipeConfig', ['p4info', 'bin'])


def get_arg_parser():
    def election_id(arg):
        try:
            nums = tuple(int(x) for x in arg.split(','))
            if len(nums) != 2:
                raise argparse.ArgumentError
            return nums
        except Exception:
            raise argparse.ArgumentError(
                "Invalid election id, expected <Hi>,<Lo>")

    def pipe_config(arg):
        try:
            paths = FwdPipeConfig(*[x for x in arg.split(',')])
            if len(paths) != 2:
                raise argparse.ArgumentError
            return paths
        except Exception:
            raise argparse.ArgumentError(
                "Invalid pipeline config, expected <p4info path>,<binary config path>")

    parser = argparse.ArgumentParser(description='P4Runtime shell')
    parser.add_argument('--device-id',
                        help='Device id',
                        type=int, action='store', default=1)
    parser.add_argument('--grpc-addr',
                        help='P4Runtime gRPC server address',
                        metavar='<IP>:<port>',
                        type=str, action='store', default='localhost:9559')
    parser.add_argument('-v', '--verbose', help='Increase output verbosity',
                        action='store_true')
    parser.add_argument('--election-id',
                        help='Election id to use',
                        metavar='<Hi>,<Lo>',
                        type=election_id, action='store', default=(1, 0))
    parser.add_argument('--role-name',
                        help='Role name of this client',
                        type=str, action='store')
    parser.add_argument('--config',
                        help='If you want the shell to push a pipeline config to the server first',
                        metavar='<p4info path (text)>,<binary config path>',
                        type=pipe_config, action='store', default=None)

    return parser


def setup(device_id=1, grpc_addr='localhost:9559', election_id=(1, 0), role_name=None, config=None):
    global client
    logging.debug("Creating P4Runtime client")
    client = P4RuntimeClient(device_id, grpc_addr, election_id, role_name)

    if config is not None:
        try:
            p4info_path = config.p4info
            bin_path = config.bin
        except Exception:
            raise ValueError("Argument 'config' must be a FwdPipeConfig namedtuple")

        try:
            client.set_fwd_pipe_config(p4info_path, bin_path)
        except FileNotFoundError as e:
            logging.critical(e)
            client.tear_down()
            sys.exit(1)
        except P4RuntimeException as e:
            logging.critical("Error when setting config")
            logging.critical(e)
            client.tear_down()
            sys.exit(1)
        except Exception:
            logging.critical("Error when setting config")
            client.tear_down()
            sys.exit(1)

    try:
        p4info = client.get_p4info()
    except P4RuntimeException as e:
        logging.critical("Error when retrieving P4Info")
        logging.critical(e)
        client.tear_down()
        sys.exit(1)

    logging.debug("Parsing P4Info message")
    context.set_p4info(p4info)


def teardown():
    global client
    logging.debug("Tearing down P4Runtime client")
    client.tear_down()
    client = None


def main():
    parser = get_arg_parser()
    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    setup(args.device_id, args.grpc_addr, args.election_id, args.role_name, args.config)

    c = Config()
    c.TerminalInteractiveShell.banner1 = '*** Welcome to the IPython shell for P4Runtime ***'
    c.TerminalInteractiveShell.prompts_class = MyPrompt
    c.TerminalInteractiveShell.autocall = 2
    c.TerminalInteractiveShell.show_rewritten_input = False

    user_ns = {
        "TableEntry": TableEntry,
        "MatchKey": MatchKey,
        "Action": Action,
        "CounterEntry": CounterEntry,
        "DirectCounterEntry": DirectCounterEntry,
        "MeterEntry": MeterEntry,
        "DirectMeterEntry": DirectMeterEntry,
        "ActionProfileMember": ActionProfileMember,
        "GroupMember": GroupMember,
        "ActionProfileGroup": ActionProfileGroup,
        "OneshotAction": OneshotAction,
        "Oneshot": Oneshot,
        "p4info": context.p4info,
        "Write": Write,
        "Replica": Replica,
        "MulticastGroupEntry": MulticastGroupEntry,
        "CloneSessionEntry": CloneSessionEntry,
        "APIVersion": APIVersion,
        "global_options": global_options,
    }

    for obj_type in P4Type:
        user_ns[obj_type.p4info_name] = P4Objects(obj_type)

    supported_entities = [
        (P4RuntimeEntity.table_entry, P4Type.table, TableEntry),
        (P4RuntimeEntity.counter_entry, P4Type.counter, CounterEntry),
        (P4RuntimeEntity.direct_counter_entry, P4Type.direct_counter, DirectCounterEntry),
        (P4RuntimeEntity.meter_entry, P4Type.meter, MeterEntry),
        (P4RuntimeEntity.direct_meter_entry, P4Type.direct_meter, DirectMeterEntry),
        (P4RuntimeEntity.action_profile_member, P4Type.action_profile, ActionProfileMember),
        (P4RuntimeEntity.action_profile_group, P4Type.action_profile, ActionProfileGroup),
    ]
    for entity, p4type, cls in supported_entities:
        user_ns[entity.name] = P4RuntimeEntityBuilder(p4type, entity, cls)

    user_ns["multicast_group_entry"] = MulticastGroupEntry
    user_ns["clone_session_entry"] = CloneSessionEntry
    user_ns["packet_in"] = PacketIn()  # Singleton packet_in object to handle all packet-in cases
    user_ns["packet_out"] = PacketOut

    start_ipython(user_ns=user_ns, config=c, argv=[])

    client.tear_down()


if __name__ == '__main__':  # pragma: no cover
    main()
