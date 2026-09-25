"""CI/CD generator (activity O1 Configure CI/CD)."""
from __future__ import annotations

from amlops.dsl.model import ResolvedPipeline
from amlops.placement.optimizer import Placement

from . import GeneratedFile, dump_yaml_docs, register_generator, slug


@register_generator("cicd")
def generate_cicd(rp: ResolvedPipeline, placement: Placement) -> list[GeneratedFile]:
    name = slug(rp.name)
    wf = {
        "name": f"amlops-{name}",
        "on": {"push": {"paths": ["pipelines/**", "generated/**"]}, "workflow_dispatch": {}},
        "jobs": {
            "validate": {
                "runs-on": "ubuntu-latest",
                "steps": [
                    {"uses": "actions/checkout@v4"},
                    {"uses": "actions/setup-python@v5", "with": {"python-version": "3.11"}},
                    {"run": "pip install amlops"},
                    {"name": "Re-derive and regenerate (model is the source of truth)",
                     "run": f"amlops generate pipelines/{name}.amlops.yaml -o generated/{name} && "
                            f"git diff --exit-code generated/{name}"},
                    {"uses": "hashicorp/setup-terraform@v3"},
                    {"run": f"terraform -chdir=generated/{name}/terraform init -backend=false && "
                            f"terraform -chdir=generated/{name}/terraform validate"},
                    {"name": "Schema-check Kubernetes manifests",
                     "run": f"docker run --rm -v $PWD:/w ghcr.io/yannh/kubeconform:latest -ignore-missing-schemas "
                            f"-summary /w/generated/{name}/k8s"},
                ],
            },
            "apply": {
                "needs": "validate",
                "if": "github.ref == 'refs/heads/main'",
                "runs-on": "ubuntu-latest",
                "environment": "production",
                "steps": [
                    {"uses": "actions/checkout@v4"},
                    {"uses": "hashicorp/setup-terraform@v3"},
                    {"run": f"terraform -chdir=generated/{name}/terraform init && "
                            f"terraform -chdir=generated/{name}/terraform apply -auto-approve"},
                ],
            },
        },
    }
    return [GeneratedFile(f".github/workflows/amlops-{name}.yaml", dump_yaml_docs([wf]),
                          ["pipeline"], ["O1", "S4", "S5"])]
