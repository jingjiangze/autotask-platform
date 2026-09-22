# CLOUD_TEST_BOUNDARY

## Purpose

The cloud path is a **test infrastructure only**. It is not a migration of the real course-execution service.

Cloud execution may use only:

- synthetic users and synthetic orders;
- mock/test adapters;
- non-production fixtures;
- test-only credentials that have no production value.

Real PII, real account passwords, real cookies, browser profiles, recovery bundles, and production credentials must not enter the cloud test environment.

## Required mode

All cloud runners must start with:

`CLOUD_TEST_MODE=1`

This mode is **fail-closed**.

It is not sufficient for an adapter to read the flag and voluntarily skip production behavior.

## Network egress gate

When `CLOUD_TEST_MODE=1`, the runner must enforce an egress deny policy before any test adapter is started.

The policy must block at minimum:

- `zhihuishu.com` and its subdomains;
- `chaoxing.com` and its subdomains;
- `api.openai.com` and other production LLM endpoints used by the real engine.

The enforcement must operate below the test-adapter configuration layer. A production adapter must not be able to bypass it simply by ignoring `CLOUD_TEST_MODE`.

The preferred implementation is:

1. host/kernel egress deny rules for resolved blocked destinations;
2. a process-level hostname/socket guard as a second layer;
3. a startup self-test that intentionally attempts a blocked connection and requires refusal;
4. failure to install or verify the deny layer means **runner startup fails closed**.

## Allowed network policy

The cloud test runner should default to no external application traffic.

Only explicitly allowlisted test/control endpoints may be reachable.

Loopback traffic for local mock servers is allowed.

DNS access may be needed for resolving control/test infrastructure, but DNS resolution does not grant application egress.

## LLM isolation

The real engine contains LLM-related dependencies. In cloud-test mode:

- no production LLM endpoint may be called;
- tests must use a local/mock LLM adapter;
- any attempt to reach a production LLM endpoint must be blocked by the same egress gate.

## Secret boundary

GitHub tokens, Cloudflare tokens, Access credentials, runner authentication secrets, and test credentials must never be committed.

Local development secrets belong in ignored files or platform Secret stores.

The browser/frontend must never receive GitHub lifecycle tokens.

## Runner lifecycle

All cloud runners are stopped by default.

A task request becomes:

`REQUESTED`

and requires an explicit human approval transition before a runner can start.

## Audit evidence

Every future cloud-test acceptance report must record:

- cloud-test mode enabled;
- egress guard installed;
- blocked destination smoke-test result;
- production LLM endpoint smoke-test result;
- no-real-PII assertion;
- runner approval event;
- runner start/stop result.

A cloud run without a passing egress self-test is **not a valid cloud-test run**.
