```python
*** Welcome to the IPython shell for P4Runtime ***
P4Runtime sh >>> t = table_entry["FabricIngress.next.hashed"]

P4Runtime sh >>> a = Action("nop")

P4Runtime sh >>> t.oneshot.add(a)
Out[3]:
action_profile_actions {
  action {
    action_id: 16819938
  }
  weight: 1
}


P4Runtime sh >>> t.match["next_id"] = "10"
field_id: 1
exact {
  value: "\000\000\000\n"
}


P4Runtime sh >>> t.insert

P4Runtime sh >>> t.read(lambda e: print(e))
table_id: 33608588 ("FabricIngress.next.hashed")
match {
  field_id: 1 ("next_id")
  exact {
    value: "\\x00\\x00\\x00\\x0a"
  }
}
action {
  action_profile_action_set {
    action_profile_actions {
      action {
        action_id: 16819938 ("nop")
      }
      weight: 1
    }
  }
}


P4Runtime sh >>>
```
