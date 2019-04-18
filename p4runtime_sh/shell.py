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
from collections import Counter, namedtuple, OrderedDict
import logging
from IPython import start_ipython
from traitlets.config.loader import Config
from IPython.terminal.prompts import Prompts, Token
import os.path
import sys
from p4runtime_sh.p4runtime import P4RuntimeClient, P4RuntimeException
from p4.v1 import p4runtime_pb2
from p4.config.v1 import p4info_pb2
from . import bytes
from .context import P4RuntimeEntity, P4Type, Context
from .utils import UserError
import google.protobuf.text_format
from google.protobuf import descriptor


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
    action_name = pcontext.find_table()
    if action_name is None:
        logging.error("Cannot find any action in context")
        return
    return context.get_param_name(action_name, id_)


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
        if field.containing_type is not None:
            subs = substitutions.get(field.containing_type.name, [])
        else:
            subs = []
        if field.name in subs and value != 0:
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
        "ActionProfile": {"table_ids", _sub_object},
        "DirectCounter": {"direct_table_id", _sub_object},
        "DirectMeter": {"direct_table_id": _sub_object},
    }
    return _repr_pretty_proto(msg, substitutions)


def _repr_pretty_p4runtime(msg):
    substitutions = {
        "TableEntry": {"table_id": _sub_object},
        "FieldMatch": {"field_id": _sub_mf},
        "Action": {"action_id": _sub_object},
        "Param": {"param_id": _sub_ap},
        "MeterEntry": {"meter_id": _sub_object},
        "CounterEntry": {"counter_id": _sub_object},
        "ValueSetEntry": {"value_set_id": _sub_object},
        "RegisterEntry": {"register_id": _sub_object},
        "DigestEntry": {"digest_id": _sub_object},
        "DigestListAck": {"digest_id": _sub_object},
        "DigestList": {"digest_id": _sub_object},
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
        if self._obj_type != P4Type.table:
            raise UserError("'actions' is only available for tables")
        for action in self._obj.action_refs:
            print(context.get_name_from_id(action.id))


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

If it's inconvenient to use the whole field name, you can use a unique suffix.

You may also use <self>.set(<f>='<value>')
\t(<f> must not include a '.' in this case, but remember that you can use a unique suffix)
"""

    def _ipython_key_completions_(self):
        return self._fields.keys()

    def __dir__(self):
        return ["reset"]

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
        if field_info.match_type == p4info_pb2.MatchField.EXACT:
            return self._parse_mf_exact(s, field_info)
        elif field_info.match_type == p4info_pb2.MatchField.LPM:
            return self._parse_mf_lpm(s, field_info)
        elif field_info.match_type == p4info_pb2.MatchField.TERNARY:
            return self._parse_mf_ternary(s, field_info)
        elif field_info.match_type == p4info_pb2.MatchField.RANGE:
            return self._parse_mf_range(s, field_info)
        else:
            raise UserError("Unsupported match type for field:\n{}".format(field_info))

    def _parse_mf_exact(self, s, field_info):
        v = bytes.parse_value(s.strip(), field_info.bitwidth)
        mf = p4runtime_pb2.FieldMatch()
        mf.field_id = field_info.id
        mf.exact.value = v
        return mf

    # TODO(antonin): validate inputs to conform to P4Runtime spec
    def _parse_mf_lpm(self, s, field_info):
        try:
            prefix, length = s.split('/')
            prefix, length = prefix.strip(), length.strip()
        except ValueError:
            prefix = s
            length = str(field_info.bitwidth)

        prefix = bytes.parse_value(prefix, field_info.bitwidth)
        try:
            length = int(length)
        except ValueError:
            raise UserError("'{}' is not a valid prefix length").format(length)
        mf = p4runtime_pb2.FieldMatch()
        mf.field_id = field_info.id
        mf.lpm.value = prefix
        mf.lpm.prefix_len = length
        return mf

    # TODO(antonin): validate inputs to conform to P4Runtime spec
    def _parse_mf_ternary(self, s, field_info):
        try:
            value, mask = s.split('&&&')
            value, mask = value.strip(), mask.strip()
        except ValueError:
            value = s.strip()
            mask = "0b" + ("1" * field_info.bitwidth)

        value = bytes.parse_value(value, field_info.bitwidth)
        mask = bytes.parse_value(mask, field_info.bitwidth)
        mf = p4runtime_pb2.FieldMatch()
        mf.field_id = field_info.id
        mf.ternary.value = value
        mf.ternary.mask = mask
        return mf

    # TODO(antonin): validate inputs to conform to P4Runtime spec
    def _parse_mf_range(self, s, field_info):
        try:
            start, end = s.split('..')
            start, end = start.strip(), end.strip()
        except ValueError:
            raise UserError("'{}' does not specify a valid range, use '<start>..<end>'").format(
                s)

        start = bytes.parse_value(start, field_info.bitwidth)
        end = bytes.parse_value(end, field_info.bitwidth)
        mf = p4runtime_pb2.FieldMatch()
        mf.field_id = field_info.id
        mf.range.low = start
        mf.range.high = end
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
        for name, mf in self._mk.items():
            print(str(mf))

    def _repr_pretty_(self, p, cycle):
        for name, mf in self._mk.items():
            p.text(str(mf))

    def set(self, **kwargs):
        for name, value in kwargs.items():
            self[name] = value

    def reset(self):
        self._mk.clear()

    def _count(self):
        return len(self._mk)


class Action:
    def __init__(self, action_name=None):
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

    def _set_docstring(self):
        self.__doc__ = "Action parameters for action '{}':\n\n".format(self.action_name)
        for name, info in self._params.items():
            self.__doc__ += str(info)
        self.__doc__ += "\n\n"
        self.__doc__ += "Set a param value with <self>['<param_name>'] = '<value>'\n"
        self.__doc__ += "You may also use <self>.set(<param_name>='<value>')\n"

    def _ipython_key_completions_(self):
        return self._params.keys()

    def _get_param(self, name):
        if name not in self._params:
            raise UserError(
                "'{}' is not a valid action parameter name for action '{}'".format(
                    name, self._action_name))
        return self._params[name]

    def __setitem__(self, name, value):
        param_info = self._get_param(name)
        self._param_values[name] = self._parse_param(value, param_info)
        print(self._param_values[name])

    def __getitem__(self, name):
        _ = self._get_param(name)
        print(self._param_values.get(name, "Unset"))

    def _parse_param(self, s, param_info):
        v = bytes.parse_value(s, param_info.bitwidth)
        p = p4runtime_pb2.Action.Param()
        p.param_id = param_info.id
        p.value = v
        return p

    def __str__(self):
        for name, p in self._param_values.items():
            print(str(p))

    def _repr_pretty_(self, p, cycle):
        for name, p in self._param_values.items():
            p.text(str(p))

    def set(self, **kwargs):
        for name, value in kwargs.items():
            self[name] = value


class TableEntry:
    def __init__(self, table_name=None):
        self._init = False
        if table_name is None:
            raise UserError("Please provide name for table")
        self.table_name = table_name
        table_info = P4Objects(P4Type.table)[table_name]
        self._table_id = table_info.preamble.id
        self.match = MatchKey(table_name, table_info.match_fields)
        self.action = None
        self.member_id = None  # TODO(antonin)
        self.group_id = None  # TODO(antonin)
        self.oneshot = None  # TODO(antonin)
        self.priority = 0
        self.is_default = False
        self._entry = p4runtime_pb2.TableEntry()
        self._table_info = table_info
        self.__doc__ = """
An entry for table '{}'

Use <self>.info to display the P4Info entry for this table.

To set the match key, use <self>.match['<field name>'] = <expr>.
Type <self>.match? for more details.

To set the action specification <self>.action = <instance of type Action>.
To set the value of action parameters, use <self>.action['<param name>'] = <expr>.
Type <self>.action? for more details.

To set the priority, use <self>.priority = <expr>.

To mark the entry as default, use <self>.is_default = True.

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

For information about how to read table entries, use <self>.read?
""".format(table_name)
        self._init = True

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
        if name == "table_name":
            raise UserError("Cannot change table name")
        if name == "priority":
            if type(value) is not int:
                raise UserError("priority must be an integer")
        if name == "match" and not isinstance(value, MatchKey):
            raise UserError("match must be an instance of MatchKey")
        if name == "is_default":
            if type(value) is not bool:
                raise UserError("is_default must be a boolean")
            # TODO(antonin): should we do a better job and handle other cases (a field is set while
            # is_default is set to True)?
            if value is True and self.match._count() > 0:
                print("Resetting match key because entry is now default")
                self.match.reset()
        if name == "member_id":
            raise NotSupportedYet("Setting 'member_id'")
        if name == "group_id":
            raise NotSupportedYet("Setting 'group_id'")
        if name == "oneshot":
            raise NotSupportedYet("Setting 'oneshot'")
        if name == "action" and value is not None:
            if not isinstance(value, Action):
                raise UserError("action must be an instance of Action")
            if not self._is_valid_action_id(value._action_id):
                raise UserError("action '{}' is not a valid action for this table".format(
                    value.action_name))
        super().__setattr__(name, value)

    def _is_valid_action_id(self, action_id):
        for action_ref in self._table_info.action_refs:
            if action_id == action_ref.id:
                return True
        return False

    # Not really needed
    # def set_match(self, **kwargs):
    #     self.match.set(**kwargs)

    def _write(self, type_):
        self._update_msg()
        self._validate_msg()
        update = p4runtime_pb2.Update()
        update.type = type_
        update.entity.table_entry.CopyFrom(self._entry)
        client.write_update(update)

    def info(self):
        """Display P4Info entry for the table"""
        return self._table_info

    def insert(self):
        logging.debug("Inserting entry")
        self._write(p4runtime_pb2.Update.INSERT)

    def delete(self):
        logging.debug("Deleting entry")
        self._write(p4runtime_pb2.Update.DELETE)

    def modify(self):
        logging.debug("Modifying entry")
        self._write(p4runtime_pb2.Update.MODIFY)

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
        self._update_msg()
        self._validate_msg()
        entity = p4runtime_pb2.Entity()
        entity.table_entry.CopyFrom(self._entry)
        iterator = client.read_one(entity)

        def gen(it):
            for rep in iterator:
                for entity in rep.entities:
                    te = TableEntry(self.table_name)
                    self.priority = entity.table_entry.priority
                    self.is_default = entity.table_entry.is_default_action
                    for mf in entity.table_entry.match:
                        mf_name = context.get_mf_name(self.table_name, mf.field_id)
                        te.match._mk[mf_name] = mf
                    if entity.table_entry.action.HasField('action'):
                        action = entity.table_entry.action.action
                        action_name = context.get_name_from_id(action.action_id)
                        te.action = Action(action_name)
                        for p in action.params:
                            p_name = context.get_param_name(action_name, p.param_id)
                            te.action._param_values[p_name] = p
                    self._entry.CopyFrom(entity.table_entry)
                    yield te

        if function is None:
            return gen(iterator)
        else:
            for x in gen(iterator):
                function(x)

    def _update_msg(self):
        entry = p4runtime_pb2.TableEntry()
        entry.table_id = self._table_id
        entry.match.extend(self.match._mk.values())
        entry.priority = self.priority
        entry.is_default_action = self.is_default
        if self.action is not None:
            entry.action.action.action_id = self.action._action_id
            entry.action.action.params.extend(self.action._param_values.values())
        self._entry = entry

    # to be called before issueing a P4Runtime request
    # enforces checks that cannot be performed when setting individual fields
    def _validate_msg(self):
        if self.is_default and self.match._count() > 0:
            raise UserError(
                "Match key must be empty for default entry, use <self>.is_default = False "
                "or <self>.match.reset (whichever one is appropriate)")

    def __str__(self):
        self._update_msg()
        return str(_repr_pretty_p4runtime(self._entry))

    def _repr_pretty_(self, p, cycle):
        self._update_msg()
        p.text(_repr_pretty_p4runtime(self._entry))

    def msg(self):
        self._update_msg()
        return self._entry


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
                        type=str, action='store', default='localhost:50051')
    parser.add_argument('-v', '--verbose', help='Increase output verbosity',
                        action='store_true')
    parser.add_argument('--election-id',
                        help='Election id to use',
                        metavar='<Hi>,<Lo>',
                        type=election_id, action='store', default=(1, 0))
    parser.add_argument('--config',
                        help='If you want the shell to push a pipeline config to the server first',
                        metavar='<p4info path (text)>,<binary config path>',
                        type=pipe_config, action='store', default=None)

    return parser


def main():
    parser = get_arg_parser()
    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    logging.debug("Creating P4Runtime client")
    global client
    client = P4RuntimeClient(args.device_id, args.grpc_addr, args.election_id)

    if args.config is not None:
        try:
            client.set_fwd_pipe_config(args.config.p4info, args.config.bin)
        except FileNotFoundError as e:
            logging.critical(e)
            client.tear_down()
            sys.exit(1)
        except P4RuntimeException as e:
            logging.critical("Error when setting config")
            logging.critical(e)
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

    c = Config()
    c.TerminalInteractiveShell.banner1 = '*** Welcome to the IPython shell for P4Runtime ***'
    c.TerminalInteractiveShell.prompts_class = MyPrompt
    c.TerminalInteractiveShell.autocall = 2
    c.TerminalInteractiveShell.show_rewritten_input = False

    user_ns = {
        "TableEntry": TableEntry,
        "MatchKey": MatchKey,
        "Action": Action,
        "p4info": context.p4info,
        "Write": Write,
    }

    for obj_type in P4Type:
        user_ns[obj_type.p4info_name] = P4Objects(obj_type)

    user_ns[P4RuntimeEntity.table_entry.name] = P4RuntimeEntityBuilder(
        P4Type.table, P4RuntimeEntity.table_entry, TableEntry)

    start_ipython(user_ns=user_ns, config=c, argv=[])

    client.tear_down()


if __name__ == '__main__':
    main()
