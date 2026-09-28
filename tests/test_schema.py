from pathlib import Path

import pytest
import yaml

from amlops.dsl import parse_file
from amlops.dsl.schema import dsl_schema

jsonschema = pytest.importorskip("jsonschema")
EXAMPLES = sorted((Path(__file__).parents[1] / "examples").glob("*.amlops.yaml"))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_examples_validate_against_schema(path):
    jsonschema.validate(yaml.safe_load(path.read_text(encoding="utf-8")), dsl_schema())
    parse_file(path)


def test_schema_rejects_unknown_keys_and_features():
    schema = dsl_schema()
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"pipeline": "x", "unknown": 1}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"pipeline": "x", "features": {"select": ["NotAFeature"]}}, schema)


def test_published_schema_is_up_to_date():
    import json
    published = json.loads((Path(__file__).parents[1] / "schema" / "amlops-0.1.schema.json").read_text())
    assert published == dsl_schema()
