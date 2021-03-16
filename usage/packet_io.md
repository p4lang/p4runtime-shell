# Packet IO

## Prepare P4 program with packet IO support

To use packet IO, first we need to define packet IO header in the P4 program, for example:

```p4
@controller_header("packet_out")
header packet_out_header_t {
    bit<16> egress_port;
}
@controller_header("packet_in")
header packet_out_header_t {
    bit<16> ingress_port;
}
```

Once compiled, we can get the following part in the p4info file which describes
the packet IO header:

```protobuf
controller_packet_metadata {
  preamble {
    id: 1
    name: "packet_out"
    alias: "packet_out"
    annotations: "@controller_header(\"packet_out\")"
  }
  metadata {
    id: 1
    name: "egress_port"
    bitwidth: 16
  }
}
controller_packet_metadata {
  preamble {
    id: 2
    name: "packet_in"
    alias: "packet_in"
    annotations: "@controller_header(\"packet_in\")"
  }
  metadata {
    id: 1
    name: "ingress_port"
    bitwidth: 16
  }
}
```

## Send a packet-out message

To send a packet-out message, use following commands:

```python
P4Runtime sh >>> p = packet_out()
P4Runtime sh >>> p.payload = b'AAAA'  # Note that the payload must be a byte string
P4Runtime sh >>> p.metadata['egress_port'] = '1'  # Note that the value must be a string
P4Runtime sh >>> p
Out[1]:
payload: "\\x41\\x41\\x41\\x41"
metadata {
  metadata_id: 1 ("egress_port")
  value: "\\x00\\x01"
}
P4Runtime sh >>> p.send  # send the packet-out message

# Another way to create a packet_out object
P4Runtime sh >>> p = packet_out(payload=b'AAAA', egress_port='1')
```

## Receive a packet-in message

There are two ways to handle packet-in messages:

### Use `sniff` function to get packet-ins

The `sniff` function will return a list which contains packet-in messages when:

- Receive enough number packets(by passing the `count` parameter)
- Expired(by passing the `timeout` parameter)
- Keyboard interrupt(Ctrl + C)

```python
P4Runtime sh >>> packet_in.sniff(timeout=1, count=1)
# Will return a list once it reach the timeout time or receive enough packets
Out[2]:
[payload: "AAAA"
 metadata {
   metadata_id: 1
   value: "\000\001"
 }]

# Both timeout and count can be "None".
# It will wait until user send a keyboard interrupt(Ctrl + C) to the shell.
P4Runtime sh >>> packet_in.sniff(timeout=None, count=None)

# To log packet-in to a file, use `to_file` parameter
x = packet_in.sniff(to_file='/tmp/pkt-in.txt')

# To show packet-in messages, set `verbose` to `True`
x = packet_in.sniff(verbose=True)
```

### Set up handlers to handle different types of packet-in

To handle the packet-in asynchronously, we can create handlers/functions to process
packet-ins once we received them.

```python
# Create a handle to print every packet-in messages
P4Runtime sh >>> def handle(x):
            ...:     print(x)
P4Runtime sh >>> packet_in.register_handler(handle)
Out[1]: 1  # Once we register the handler, we will get the handler ID
# The packet-in message will be handled(printed in this example) once we receive it
payload: "AAAA"
metadata {
  metadata_id: 1
  value: "\000\000"
}
# We can use the ID to deregister the handler
P4Runtime sh >>> packet_in.deregister_handler(1)
```

Also, we can add filters to ignore packet-in messages which we don't want to handle.

```python
# Register the handle to handle packet-in messages that contains any string
# matches "AA.*A" in payload with ingress port 0, for example "AAbA".
P4Runtime sh >>> def handle(x):
            ...:     print(x)
P4Runtime sh >>> packet_in.register_handler(handle, payload_regex=b'AA.*A', ingress_port='0')
Out[1]: 2
# The packet-in message will be handled(printed in this example) once we receive it
payload: "AAbA"
metadata {
  metadata_id: 1
  value: "\000\000"
}
```
