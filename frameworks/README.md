# CAF framework definitions

WebCAF currently contains these NCSC Cyber Assessment Framework definitions:

- `cyber-assessment-framework-v3.2.yaml`: CAF 3.2, retained for assessments created against CAF 3.2
- `cyber-assessment-framework-v4.0.yaml`: CAF 4.0, used by the seeded `26/27` assessment-period configuration

Each assessment stores its framework version. The application resolves the corresponding router through
`Assessment.get_router()` rather than applying one framework globally.

## Structure

The YAML follows the CAF hierarchy of objectives, principles, outcomes, and indicators. Indicators are grouped by
status, such as `achieved`, `partially-achieved`, and `not-achieved`. Objectives, principles, outcomes, and indicators
have indexes used by the application and data exports.

## Validation

`tests/test_yaml.py` currently loads the CAF 3.2 file. It checks its schema and verifies that principle, outcome, and
indicator indexes are unique after parsing. It does not verify objective-key uniqueness because duplicate keys in the
same YAML mapping are overwritten while loading.

The pre-commit `check-yaml` hook validates YAML syntax and catches duplicate keys in the same mapping. Run both checks
when changing CAF 3.2 because they cover different classes of error:

```shell
poetry run python -m unittest tests.test_yaml
poetry run pre-commit run check-yaml --files frameworks/cyber-assessment-framework-v3.2.yaml
```

The structural unit test is not yet parameterised for CAF 4.0. Run the YAML hook when changing that definition:

```shell
poetry run pre-commit run check-yaml --files frameworks/cyber-assessment-framework-v4.0.yaml
```

## Reference scripts

`create-schema.py` performed most of the original conversion from the CAF 3.2 PDF. Indicator grouping and content that
crossed PDF pages required manual correction, so the script is retained as a reference rather than a repeatable
generator.

`caf32-reindex.py` replaced the original global numerical indicator indexes with indexes based on their positions in
the nested CAF 3.2 structure, such as `A1.a.1`. It may be useful as a reference if future framework versions need
additional indexes.
