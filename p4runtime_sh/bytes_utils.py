# SPDX-FileCopyrightText: 2019 Barefoot Networks, Inc.
#
# SPDX-License-Identifier: Apache-2.0

from ipaddress import IPv4Address, IPv6Address, AddressValueError
from . global_options import global_options, Options
from .utils import UserError


class UserBadIPv4Error(UserError):
    def __init__(self, addr):
        self.addr = addr

    def __str__(self):
        return "'{}' is not a valid IPv4 address".format(self.addr)

    def _render_traceback_(self):
        return [str(self)]


class UserBadIPv6Error(UserError):
    def __init__(self, addr):
        self.addr = addr

    def __str__(self):
        return "'{}' is not a valid IPv6 address".format(self.addr)

    def _render_traceback_(self):
        return [str(self)]


class UserBadMacError(UserError):
    def __init__(self, addr):
        self.addr = addr

    def __str__(self):
        return "'{}' is not a valid MAC address".format(self.addr)

    def _render_traceback_(self):
        return [str(self)]


class UserBadValueError(UserError):
    def __init__(self, info=""):
        self.info = info

    def __str__(self):
        return self.info

    def _render_traceback_(self):
        return [str(self)]


def ipv4Addr_to_bytes(addr):
    try:
        ip = IPv4Address(addr)
    except AddressValueError:
        raise UserBadIPv4Error(addr)
    return ip.packed


def ipv6Addr_to_bytes(addr):
    try:
        ip = IPv6Address(addr)
    except AddressValueError:
        raise UserBadIPv6Error(addr)
    return ip.packed


def macAddr_to_bytes(addr):
    bytes_ = [int(b, 16) for b in addr.split(':')]
    if len(bytes_) != 6:
        raise UserBadMacError(addr)
    return bytes(bytes_)


def str_to_bytes(value_str):
    return bytes(value_str, 'utf-8')


def to_canonical_bytes(bytes_):
    if len(bytes_) == 0:
        return bytes_
    num_zeros = 0
    for b in bytes_:
        if b != 0:
            break
        num_zeros += 1
    if num_zeros == len(bytes_):
        return bytes_[:1]
    return bytes_[num_zeros:]


def make_canonical_if_option_set(bytes_):
    if global_options.get_option(Options.canonical_bytestrings):
        return to_canonical_bytes(bytes_)
    return bytes_


def parse_value(value_str, bitwidth, base=0):
    if bitwidth == 0:
        return str_to_bytes(value_str)
    if bitwidth == 32 and '.' in value_str:
        return ipv4Addr_to_bytes(value_str)
    elif bitwidth == 48 and ':' in value_str:
        return macAddr_to_bytes(value_str)
    elif bitwidth == 128 and ':' in value_str:
        return ipv6Addr_to_bytes(value_str)
    try:
        value = int(value_str, base)
    except ValueError:
        raise UserBadValueError(
            "Invalid value '{}': could not cast to integer, try in hex with 0x prefix".format(
                value_str))
    nbytes = (bitwidth + 7) // 8
    try:
        return value.to_bytes(nbytes, byteorder='big')
    except OverflowError:
        raise UserBadValueError(
            "Invalid value '{}': cannot be represented with '{}' bytes".format(
                value_str, nbytes))
