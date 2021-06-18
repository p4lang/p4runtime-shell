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

## Receive packet-in messages

The `sniff` function will return an iterator which contains packet-in messages when:

- The timeout expires (based on the `timeout` parameter)
- A keyboard interrupt occurs (Ctrl + C)

```python
# To print all packet-in messages.
P4Runtime sh >>> for msg in packet_in.sniff(timeout=1):
             ...:    print(msg)


# Prints packet-in messages by using the custom function.
P4Runtime sh >>> packet_in.sniff(lambda m: print(m), timeout=1)
Out[2]:
payload: "AAAA"
metadata {
  metadata_id: 1
  value: "\000\001"
}

# By setting timeout to `None`, it will wait until user sends a keyboard
# interrupt(Ctrl + C) to the shell.
P4Runtime sh >>> packet_in.sniff(lambda m: print(m), timeout=None)
```
