# Example: "Why did this CI job fail?"

## Scenario

A PR comment asks why CI started failing after a workflow edit.

## Example response

### Symptoms
- The `test` workflow fails in the `unit-tests` job on the PR branch.
- The visible error is that the job cannot find an expected environment
  variable before running the test command.

### Confirmed facts
- The failure only appears after the workflow file was edited in this PR.
- The changed files include `.gitea/workflows/test.yaml` and no application code.
- The failing log shows the variable lookup happens before any tests execute.
- The previous successful run on the base branch used a step that exported the
  variable from repository configuration.

### Hypotheses
- Leading hypothesis: the workflow edit removed or bypassed the step that sets
  the required variable.
- Secondary hypothesis: the variable name changed in configuration but the job
  still reads the old name.

### Most likely root cause
The strongest explanation is a workflow wiring regression rather than a product
code regression. The failure starts before test execution, only the workflow was
changed in this PR, and the missing-variable error matches the removed export
step in the diff.

### Next checks
- Confirm in the workflow diff whether the export step was deleted or moved
  behind a condition that no longer matches.
- If needed, compare the failing PR workflow with the last green run on the base
  branch.

### Proposed fix direction
- Restore the step that exports the required variable, or move the variable
  initialization earlier so the test command always receives it.
- Re-run only the affected workflow to verify the root-cause fix.
