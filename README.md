# A shell for P4Runtime

![Build Status](https://github.com/p4lang/p4runtime-shell/workflows/Test/badge.svg?branch=main&event=push)

**This is still a work in progress. Feedback is welcome.**

p4runtime-sh is an interactive Python shell for
[P4Runtime](https://github.com/p4lang/p4runtime) based on
[IPython](https://ipython.org/).

## Using the shell

### Run with Docker

We recommend that you download the Docker image (~142MB) and use it, but you can
also build the image directly with:

```bash
git clone https://github.com/p4lang/p4runtime-shell
cd p4runtime-shell
docker build -t p4lang/p4runtime-sh .
```

Run the shell as follows:

```bash
[sudo] docker run -ti p4lang/p4runtime-sh \
  --grpc-addr <server IP>:<server port> \
  --device-id 0 --election-id 0,1
```

The above command will retrieve the forwarding pipeline configuration from the
P4Runtime server. You can also push a forwarding pipeline configuration with the
shell (you will need to mount the directory containing the P4Info and binary
device config in the docker):

```bash
[sudo] docker run -ti -v /tmp/:/tmp/ p4lang/p4runtime-sh \
  --grpc-addr <server IP>:<server port> \
  --device-id 0 --election-id 0,1 --config /tmp/p4info.txt,/tmp/bmv2.json
```

The above command assumes that the P4Info (p4info.txt) and the binary device
config (bmv2.json) are under /tmp/.

To make the process more convenient, we provide a wrapper script, which takes
care of running the docker (including mounting the P4Info and binary device
config files in the docker if needed):

```bash
[sudo] ./p4runtime-sh-docker --grpc-addr <server IP>:<server port> \
  --device-id 0 --election-id 0,1 \
  --config <path to p4info>,<path to binary config>
```

*If you are a Linux user, you can follow this
 [guide](https://docs.docker.com/install/linux/linux-postinstall/) to run Docker
 commands without `sudo`. You will be able to use `p4runtime-sh-docker` as a
 non-privileged user.*

*If you are using the Docker image to run p4runtime-shell and you are trying to
 connect to a P4Runtime server running natively on the same system and listening
 on the localhost interface, you will not be able to connect to the server using
 `--grpc-addr localhost:<port>` or `--grpc-addr 127.0.0.1:<port>`. Instead, you
 should have your P4Runtime server listen on all interfaces (`0.0.0.0`) and you
 will need to use the IP address assigned to the Docker bridge (`docker0` by
 default) or the IP address assigned to the local network management interface
 (e.g. `eth0`).*

### Run without Docker

You can also install P4Runtime shell via `pip3` and run it directly.

```bash
# (optional) Set up virtual environment
python3 -m venv venv
source venv/bin/activate

# Install p4runtime-shell package and run it
pip3 install p4runtime-shell
python3 -m p4runtime_sh --grpc-addr <server IP>:<server port> \
  --device-id 0 --election-id 0,1 --config <p4info.txt>,<pipeline config>
```

## Available commands

`tables`, `actions`, `action_profiles`, `counters`, `direct_counters`, `meters`,
`direct_meters` (named after the P4Info message fields) to query information
about P4Info objects.

`table_entry`, `action_profile_member`, `action_profile_group`, `counter_entry`,
`direct_counter_entry`, `meter_entry`, `direct_meter_entry` (named after the
P4Runtime `Entity` fields), along with `multicast_group_entry` and
`clone_session_entry`, for runtime entity programming.

`packet_in` and `packet_out` are commands for packet IO, see the [usage](usage/packet_io.md) for more information.

The `Write` command can be used to read a `WriteRequest` message from a file
(for now, Protobuf text format only) and send it to a server:

```text
Write <path to file encoding WriteRequest message in text format>
```

Type the command name followed by `?` for information on each command,
e.g. `table_entry?`.

## Canonical representation of bytestrings

The [P4Runtime
specification](https://p4.org/p4runtime/spec/v1.3.0/P4Runtime-Spec.html#sec-bytestrings)
defines a canonical representation for binary strings, which all P4Runtime
servers must support. This representation can be used to format all binary
strings (match fields, action parameters, ...) in P4Runtime messages exchanged
between the client and the server. For legacy reasons, some P4Runtime servers do
not support the canonical representation and require binary strings to be
byte-padded according to the bitwidth specified in the P4Info message. While all
P4Runtime-conformant servers must also accept this legacy format, it can lead to
read-write asymmetry for P4Runtime entities. For example a client may insert a
TableEntry using the legacy format for match fields, but when reading the same
TableEntry back, the server may return a message with match field values in the
canonical representation. When a client uses the canonical representation,
read-write symmetry is always guaranteed.

If you are dealing with a legacy server which rejects binary strings formatted
using the canonical representation (making this server non conformant to the
specification), you can revert to the byte-padded format by typing the following
command in the shell:

```python
P4Runtime sh >>> global_options["canonical_bytestrings"] = False
```

## Example usage

Here is some of what you can do when using p4runtime-sh with ONF's
[fabric.p4](https://github.com/opennetworkinglab/onos/blob/master/pipelines/fabric/impl/src/main/resources/fabric.p4).

More examples of usage can be found in the [usage/ folder](usage/).

```python
*** Welcome to the IPython shell for P4Runtime ***
P4Runtime sh >>> tables
FabricEgress.egress_next.egress_vlan
FabricIngress.acl.acl
FabricIngress.filtering.fwd_classifier
FabricIngress.filtering.ingress_port_vlan
FabricIngress.forwarding.bridging
FabricIngress.forwarding.mpls
FabricIngress.forwarding.routing_v4
FabricIngress.next.hashed
FabricIngress.next.multicast
FabricIngress.next.next_vlan
FabricIngress.next.xconnect

P4Runtime sh >>> tables["FabricIngress.forwarding.routing_v4"]
Out[2]:
preamble {
  id: 33562650
  name: "FabricIngress.forwarding.routing_v4"
  alias: "routing_v4"
}
match_fields {
  id: 1
  name: "ipv4_dst"
  bitwidth: 32
  match_type: LPM
}
action_refs {
  id: 16777434 ("FabricIngress.forwarding.set_next_id_routing_v4")
}
action_refs {
  id: 16804187 ("FabricIngress.forwarding.nop_routing_v4")
}
action_refs {
  id: 16819938 ("nop")
  annotations: "@defaultonly"
  scope: DEFAULT_ONLY
}
const_default_action_id: 16819938 ("nop")
direct_resource_ids: 318811107 ("FabricIngress.forwarding.routing_v4_counter")
size: 1024


P4Runtime sh >>> te = table_entry["FabricIngress.forwarding.routing_v4"](action="set_next_id_routing_v4")

P4Runtime sh >>> te?
Signature:   te(**kwargs)
Type:        TableEntry
String form:
table_id: 33562650 ("FabricIngress.forwarding.routing_v4")
action {
  action {
    action_id: 16777434 ("FabricIngress.forwarding.set_next_id_routing_v4")
  }
}
File:        /p4runtime-sh/p4runtime_sh/shell.py
Docstring:
An entry for table 'FabricIngress.forwarding.routing_v4'

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

P4Runtime sh >>> te.match?
Type:      MatchKey
File:      /p4runtime-sh/p4runtime_sh/shell.py
Docstring:
Match key fields for table 'FabricIngress.forwarding.routing_v4':

id: 1
name: "ipv4_dst"
bitwidth: 32
match_type: LPM

Set a field value with <self>['<field_name>'] = '...'
  * For exact match: <self>['<f>'] = '<value>'
  * For ternary match: <self>['<f>'] = '<value>&&&<mask>'
  * For LPM match: <self>['<f>'] = '<value>/<mask>'
  * For range match: <self>['<f>'] = '<value>..<mask>'
  * For optional match: <self>['<f>'] = '<value>'

If it's inconvenient to use the whole field name, you can use a unique suffix.

You may also use <self>.set(<f>='<value>')
        (<f> must not include a '.' in this case, but remember that you can use a unique suffix)

P4Runtime sh >>> te.match["ipv4_dst"] = "10.0.0.0/16"
field_id: 1
lpm {
  value: "\n\000\000\000"
  prefix_len: 16
}


P4Runtime sh >>> te.action?
Type:      Action
File:      /p4runtime-sh/p4runtime_sh/shell.py
Docstring:
Action parameters for action 'set_next_id_routing_v4':

id: 1
name: "next_id"
bitwidth: 32


Set a param value with <self>['<param_name>'] = '<value>'
You may also use <self>.set(<param_name>='<value>')

P4Runtime sh >>> te.action["next_id"] = "10"
param_id: 1
value: "\000\000\000\n"


P4Runtime sh >>> te.insert

P4Runtime sh >>> for te in table_entry["FabricIngress.forwarding.routing_v4"].read():
            ...:     print(te)
            ...:
table_id: 33562650 ("FabricIngress.forwarding.routing_v4")
match {
  field_id: 1 ("ipv4_dst")
  lpm {
    value: "\\x0a\\x00\\x00\\x00"
    prefix_len: 16
  }
}
action {
  action {
    action_id: 16777434 ("FabricIngress.forwarding.set_next_id_routing_v4")
    params {
      param_id: 1 ("next_id")
      value: "\\x00\\x00\\x00\\x0a"
    }
  }
}


P4Runtime sh >>> table_entry["FabricIngress.forwarding.routing_v4"].read(lambda te: te.delete())

P4Runtime sh >>> table_entry["FabricIngress.forwarding.routing_v4"].read(lambda te: print(te))

P4Runtime sh >>>
```

## Using p4runtime-shell in scripts

You can also leverage this project as a convenient P4Runtime wrapper to
programmatically program switches using Pyhton scripts:

```python
import p4runtime_sh.shell as sh

# you can omit the config argument if the switch is already configured with the
# correct P4 dataplane.
sh.setup(
    device_id=1,
    grpc_addr='localhost:9559',
    election_id=(0, 1), # (high, low)
    config=sh.FwdPipeConfig('config/p4info.pb.txt', 'config/device_config.bin')
)

# see p4runtime_sh/test.py for more examples
te = sh.TableEntry('<table_name>')(action='<action_name>')
te.match['<name>'] = '<value>'
te.action['<name>'] = '<value>'
te.insert()

# ...

sh.teardown()
```

Note that at the moment the P4Runtime client object is a global variable, which
means that we only support one P4Runtime connection to a single switch.

## Target-specific support

### P4.org Bmv2

Just use the bmv2 JSON file generated by the
[compiler](https://github.com/p4lang/p4c) as the binary device config.

### Barefoot Tofino

We provide a script which can be used to "pack" the Barefoot p4c compiler output
(part of the Barefoot SDE) into one binary file, to be used as the binary device
config.
```bash
./config_builders/tofino.py --ctx-json <path to context JSON> \
  --tofino-bin <path to tofino.bin> -p <program name> -o out.bin
```

You can then use `out.bin` when invoking `p4runtime-sh-docker`:
```bash
[sudo] ./p4runtime-sh-docker --grpc-addr <server IP>:<server port> \
  --device-id 0 --election-id 0,1 \
  --config <path to p4info>,out.bin
```
