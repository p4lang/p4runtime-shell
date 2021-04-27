## Read `counter_data` for `table_entries`

This example uses the [basic tutorial for Stratum](https://github.com/stratum/tutorial/tree/master/basic).
The goal is to read out the byte and packet counts for a specific table entry.

```python
*** Welcome to the IPython shell for P4Runtime ***
P4Runtime sh >>> ########################## Add table entries
            ...: te = table_entry["ingress.table0_control.table0"](action = "ingress.table0_control.set_egress_port")
            ...: te.priority = 1
            ...: te.match["standard_metadata.ingress_port"] = ("1")
            ...: te.action['port'] = ("2")
            ...: te.insert()
            ...:
            ...: te = table_entry["ingress.table0_control.table0"](action = "ingress.table0_control.set_egress_port")
            ...: te.priority = 1
            ...: te.match["standard_metadata.ingress_port"] = ("2")
            ...: te.action['port'] = ("1")
            ...: te.insert()
            ...:
field_id: 1
ternary {
  value: "\000\001"
  mask: "\001\377"
}

param_id: 1
value: "\000\002"

field_id: 1
ternary {
  value: "\000\002"
  mask: "\001\377"
}

param_id: 1
value: "\000\001"


P4Runtime sh >>> ########################## Retrieve all table entries and print out counter_data (byte and packet counts)
            ...: ########################## (Note: you HAVE to generate traffic on some table entries to see non-zero counters)
            ...:
            ...: for te in table_entry['ingress.table0_control.table0'].read():
            ...:       # You HAVE to set some te.counter_data field to trigger reading out the counter_data
            ...:       te.counter_data.byte_count = 0
            ...:       for x in te.read():
            ...:             if x.counter_data.byte_count == 0:
            ...:                   print('te.counter_data.byte_count == 0 -> Generate some traffic before reading out the counters')
            ...:             else:
            ...:                   print('Counter data', x.counter_data)
            ...:
Counter data byte_count: 1022
packet_count: 11

Counter data byte_count: 1022
packet_count: 11


P4Runtime sh >>>
```
