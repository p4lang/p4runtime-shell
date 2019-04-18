# A shell for P4Runtime

[![Build Status](https://travis-ci.org/p4lang/p4runtime-shell.svg?branch=master)](https://travis-ci.org/p4lang/p4runtime-shell)

**This is a work in progress and the number of supported P4Runtime features is
  limited.**

p4runtime-sh is an interactive Python shell for
[P4Runtime](https://github.com/p4lang/p4runtime) based on
[IPython](https://ipython.org/).

## Using the shell

We recommend that you download the Docker image (~200MB) and use it, but you can
also build the image directly with `docker build -t p4lang/p4runtime-sh .`.

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
 [guide](https://docs.docker.com/install/linux/linux-postinstall/) to run docker
 commands without `sudo`. You will be able to use `p4runtime-sh-docker` as a
 non-privileged user.*

## Available commands

`tables`, `actions`, `action_profiles`, `counters`, `direct_counters`, `meters`,
`direct_meters` (named after the P4Info message fields) to query information
about P4Info objects.

`table_entry` for runtime table programming.

Type the command name followed by `?` for information on each command,
e.g. `table_entry?`.

## Example usage

Here is some of what you can do when using p4runtime-sh with ONF's
[fabric.p4](https://github.com/opennetworkinglab/onos/blob/master/pipelines/fabric/src/main/resources/fabric.p4).

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
      param_id: 1 ("None")
      value: "\\x00\\x00\\x00\\x0a"
    }
  }
}


P4Runtime sh >>> table_entry["FabricIngress.forwarding.routing_v4"].read(lambda te: te.delete())

P4Runtime sh >>> table_entry["FabricIngress.forwarding.routing_v4"].read(lambda te: print(te))

P4Runtime sh >>>
```
