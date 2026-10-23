# Security policy

## Supported versions

One release line is supported at a time: the latest tag on the default
branch. Older tags receive no security patches; upgrade before reporting
a version-specific issue.

## Reporting a vulnerability

Open a GitHub security advisory on this repository ("Report a
vulnerability" under the Security tab). Do not open a public issue for a
security problem: the advisory keeps it out of search until it is fixed.

Include:

- the affected paths (`runner/` module names) and, where known, the
  function or code path
- the config shape or command line that triggers it
- the impact you believe it has

## What is in scope

- anything that leaks a bearer token: the runner reads tokens from the
  environment (the route-auth chain, `runner/config.py:resolve_auth_token`)
  and must print, log, or store none of them
- anything that executes config as more than data: a pipeline YAML must
  never gain code execution over the caller
- endpoint handling defects in `runner/calls.py` (redirect following,
  stream parsing, retry policy)
- gate bypasses in `runner/gates.py`: a red gate must actually refuse
  the spend, including the fail-closed rule for unknown gate types

## What is out of scope

- the model servers the runner calls: a seat's endpoint is the operator's
  own infrastructure; report its defects to whoever runs it
- YAML bombs / resource exhaustion in `yaml.safe_load` beyond the obvious:
  the runner is a local tool that already runs on the caller's machine
- the Actions composite in `actions/text-gates/` running model stages: it
  deliberately never does (text gates only)

## Handling

Reports are acknowledged within a week. Fixes land with a test that
reproduces the report, and the advisory is published when the fix is
tagged.