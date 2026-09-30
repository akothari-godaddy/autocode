"""Script a plan that forgets unittest discovery's required package marker."""
from pathlib import Path


def install(fake, config, trace):
    original = fake.report_for
    repaired = Path(config["root"]) / "planner-scaffolding-fixed"

    def report(stage, data):
        error = str(data.get("error") or "")
        if config["case"] == "repair_scaffolding" and "requires tests/__init__.py" in error:
            repaired.write_text("The Planner adds the required marker before approval.\n")
            trace("scaffolding_repaired", stage=stage, error=error)
        if repaired.exists() and "tests/__init__.py" not in fake.PATHS:
            fake.PATHS.append("tests/__init__.py")
        value = original(stage, data)
        if stage in ("astra_discovery", "glm_revise", "astra_finalize"):
            trace("scaffolding_plan", stage=stage, assigned=list(fake.PATHS), repair=bool(data.get("report_repair")))
        return value

    fake.report_for = report
