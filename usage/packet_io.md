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

After compiled, we can get the following part in the p4info file which describes
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
P4Runtime sh >>> p = packet['packet_out']
P4Runtime sh >>> p.metadata['egress_port'] = '0'  # Note that the value must be a string
P4Runtime sh >>> p.payload = b'AAAA'  # Note that the payload must be a byte string
P4Runtime sh >>> p
Out[1]:
payload: "\\x41\\x41\\x41\\x41"
metadata {
  metadata_id: 1
  value: "\\x00\\x00"
}
p.send  # send the pcaket-out message
```

## Receive a packet-in message

All stream messages will be queued until we read it, to read the packet-in message:

```python
P4Runtime sh >>> packet['packet_in'].receive  # No need to create a variable or set any value
Out[2]:
packet {
  payload: "\101\101\101\101"
  metadata {
    metadata_id: 1
    value: "\000\000"
  }
}
P4Runtime sh >>> packet['packet_in'].receive(timeout=10)  # You can also set a timeout in seconds
```
