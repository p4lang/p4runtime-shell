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


@enum.unique
class Options(enum.Enum):
    canonical_bytestrings = bool


class OptionMap:
    option_defaults = {
        Options.canonical_bytestrings: True,
    }

    def __init__(self):
        self.values = {}
        self.reset_values()

    def reset_values(self):
        for option in Options:
            assert(option in OptionMap.option_defaults)
            self.values[option] = OptionMap.option_defaults[option]

    def get_value(self, option):
        assert(option in self.values)
        return self.values[option]

    def get_all_values(self):
        return self.values

    def set_value(self, option, value):
        assert(option in self.values)
        assert(type(value) == option.value)
        self.values[option] = value


options_map = OptionMap()
