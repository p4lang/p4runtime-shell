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
```
[sudo] docker run -ti p4lang/p4runtime-sh \
  --grpc-addr <server IP>:<server port> \
  --device-id 0 --election-id 0,1
```

The above command will retrieve the forwarding pipeline configuration from the
P4Runtime server. You can also push a forwarding pipeline configuration with the
shell (you will need to mount the directory containing the P4Info and binary
device config into the docker):
```
[sudo] docker run -ti -v /tmp/:/tmp/ p4lang/p4runtime-sh \
  --grpc-addr <server IP>:<server port> \
  --device-id 0 --election-id 0,1 --config /tmp/p4info.txt,/tmp/bmv2.json
```
The above command assumes that the P4Info (p4info.txt) and the binary device
config (bmv2.json) are under /tmp/.

TODO(antonin): add wrapper scripts for docker commands

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
P4Runtime CLI >>> tables
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

P4Runtime CLI >>> tables["FabricIngress.forwarding.routing_v4"]
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
  id: 16777434
}
action_refs {
  id: 16804187
}
action_refs {
  id: 16819938
  annotations: "@defaultonly"
  scope: DEFAULT_ONLY
}
const_default_action_id: 16819938
direct_resource_ids: 318811107
size: 1024


P4Runtime CLI >>> tables["FabricIngress.forwarding.routing_v4"].actions()
FabricIngress.forwarding.set_next_id_routing_v4
FabricIngress.forwarding.nop_routing_v4
nop

P4Runtime CLI >>> t = tables["FabricIngress.forwarding.routing_v4"]

P4Runtime CLI >>> t.actions
FabricIngress.forwarding.set_next_id_routing_v4
FabricIngress.forwarding.nop_routing_v4
nop

P4Runtime CLI >>> te =
table_entry["FabricIngress.forwarding.routing_v4"](action="set_next_id_routing_v4")

P4Runtime CLI >>> te?
Signature:   te(**kwargs)
Type:        TableEntry
String form:
table_id: 33562650
action {
  action {
    action_id: 16777434
  }
}
File:        /p4runtime-sh/p4runtime_sh/shell.py
Docstring:
An entry for table 'FabricIngress.forwarding.routing_v4'

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

P4Runtime CLI >>> te.match?
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

P4Runtime CLI >>> te.match["ipv4_dst"] = "10.0.0.0/16"
field_id: 1
lpm {
  value: "\n\000\000\000"
  prefix_len: 16
}


P4Runtime CLI >>> te.action?
Type:      Action
File:      /p4runtime-sh/p4runtime_sh/shell.py
Docstring:
Action parameters for action 'set_next_id_routing_v4':

id: 1
name: "next_id"
bitwidth: 32


Set a param value with <self>['<param_name>'] = '<value>'
You may also use <self>.set(<param_name>='<value>')

P4Runtime CLI >>> te.action["next_id"] = "10"
param_id: 1
value: "\000\000\000\n"


P4Runtime CLI >>> te.insert

P4Runtime CLI >>> for te in table_entry["FabricIngress.forwarding.routing_v4"].read():
             ...:     print(te)
             ...:
table_id: 33562650
match {
  field_id: 1
  lpm {
    value: "\n\000\000\000"
    prefix_len: 16
  }
}
action {
  action {
    action_id: 16777434
    params {
      param_id: 1
      value: "\000\000\000\n"
    }
  }
}


P4Runtime CLI >>> table_entry["FabricIngress.forwarding.routing_v4"].read(lambda te: te.delete())

P4Runtime CLI >>> for te in table_entry["FabricIngress.forwarding.routing_v4"].read():
             ...:     print(te)
             ...:

P4Runtime CLI >>>
