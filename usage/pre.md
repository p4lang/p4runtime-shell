```python
*** Welcome to the IPython shell for P4Runtime ***
P4Runtime sh >>> mcge = multicast_group_entry(1)

P4Runtime sh >>> mcge.add(1, 1).add(1, 2).add(2, 3)
Out[2]:
multicast_group_entry {
  multicast_group_id: 1
  replicas {
    egress_port: 1
    instance: 1
  }
  replicas {
    egress_port: 1
    instance: 2
  }
  replicas {
    egress_port: 2
    instance: 3
  }
}


P4Runtime sh >>> mcge.insert

P4Runtime sh >>>
```

```python
*** Welcome to the IPython shell for P4Runtime ***
P4Runtime sh >>> cse = clone_session_entry(1)

P4Runtime sh >>> cse.add(1, 1).add(1, 2).add(2, 3)
Out[2]:
clone_session_entry {
  session_id: 1
  replicas {
    egress_port: 1
    instance: 1
  }
  replicas {
    egress_port: 1
    instance: 2
  }
  replicas {
    egress_port: 2
    instance: 3
  }
}


P4Runtime sh >>> cse.insert

P4Runtime sh >>>
```
