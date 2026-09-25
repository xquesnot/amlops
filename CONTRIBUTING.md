# Contributing
* `pip install -e ".[dev]"`, then `ruff check src tests` and `pytest -q` must pass.
* Knowledge changes (features, constraints, rules) need a rationale and a source in the YAML.
* New generators: register with `@register_generator("name")`, annotate every
  `GeneratedFile` with `realises` and SkeltyMLOps `activities`, add a test.
* Paper numbers come only from `experiments/run_all.py`; never edit `paper/generated/` by hand.
