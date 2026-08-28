# H8 Single Runtime Spine Dataset

This deterministic dataset verifies control ownership, not model capability.

```bash
python -B benchmarks/h8/offline_dryrun.py
```

The oracle requires all eight cases to be present and passing.  H8 keeps
ordinary action failures outside Reflection/Decision, routes structural
failures through `FailurePolicy`, rejects missing scoped workspace and unknown
tools, and verifies that the result-driven runtime does not use Planner
replanning as the ordinary failure path.
