# Copyright 2021 VMware, Inc.
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

import enum
from .utils import UserError


@enum.unique
class Options(enum.Enum):
    canonical_bytestrings = bool


class UnknownOptionName(UserError):
    def __init__(self, option_name):
        self.option_name = option_name

    def __str__(self):
        return "Unknown option name: {}".format(self.option_name)


class InvalidOptionValueType(UserError):
    def __init__(self, option, value):
        self.option = option
        self.value = value

    def __str__(self):
        return "Invalid value type for option {}: expected {} but got value {} with type {}".format(
            self.option.name, self.option.value.__name__, self.value, type(self.value).__name__)


class GlobalOptions:
    option_defaults = {
        Options.canonical_bytestrings: True,
    }

    option_helpstrings = {
        Options.canonical_bytestrings: """
Use byte-padded legacy format for binary strings sent to the P4Runtime server,
instead of the canonical representation. See P4Runtime specification for details.
"""
    }

    def __init__(self):
        self._values = {}
        self.reset()
        self._option_names = [option.name for option in Options]
        self._set_docstring()

    def reset(self):
        """Reset all options to their defaults."""
        for option in Options:
            assert(option in GlobalOptions.option_defaults)
            self._values[option] = GlobalOptions.option_defaults[option]

    def _supported_options_as_str(self):
        return ", ".join(["{} ({})".format(o.name, o.value.__name__) for o in Options])

    def _supported_options_as_str_verbose(self):
        s = ""
        for option in Options:
            s += "Option name: {}\n".format(option.name)
            s += "Type: {}\n".format(option.value.__name__)
            s += "Default value: {}\n".format(GlobalOptions.option_defaults[option])
            s += "Description: {}\n".format(GlobalOptions.option_helpstrings.get(option, "N/A"))
            s += "\n"
        return s[:-1]

    def _set_docstring(self):
        self.__doc__ = """
Manage global options for the P4Runtime shell.
Supported options are: {}
To set the value of a global option, use global_options["<option name>"] = <option value>
To access the current value of a global option, use global_options.["<option name>"]
To reset all options to their default value, use global_options.reset

{}
""".format(self._supported_options_as_str(), self._supported_options_as_str_verbose())

    def __dir__(self):
        return ["reset", "set", "get"]

    def _ipython_key_completions_(self):
        return self._option_names

    # Should be used by shell code, not user
    def set_option(self, option, value):
        self._values[option] = value

    # Should be used by shell code, not user
    def get_option(self, option):
        return self._values[option]

    def set(self, name, value):
        """Set the value of specified option."""
        try:
            option = Options[name]
        except KeyError:
            raise UnknownOptionName(name)
        if type(value) != option.value:
            raise InvalidOptionValueType(option, value)
        self.set_option(option, value)

    def get(self, name):
        """Get the value of specified option."""
        try:
            option = Options[name]
        except KeyError:
            raise UnknownOptionName(name)
        return self.get_option(option)

    def __setitem__(self, name, value):
        self.set(name, value)

    def __getitem__(self, name):
        return self.get(name)

    def __str__(self):
        return '\n'.join(["{}: {}".format(o.name, v) for o, v in self._values.items()])

    def _repr_pretty_(self, p, cycle):
        for option, value in self._values.items():
            p.text("{}: {}".format(option.name, value))
            p.breakable("\n")


global_options = GlobalOptions()
