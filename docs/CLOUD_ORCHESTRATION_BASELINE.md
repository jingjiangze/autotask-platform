# CLOUD_ORCHESTRATION_BASELINE.md

> Baseline for the cloud-test control-plane project.
> Scope: Linux portability + test infrastructure + Cloudflare/GitHub orchestration.
> This document does **not** authorize moving the real course-execution service to the cloud.

## 1. Repository fact baseline

Repository:

- `jingjiangze/autotask-platform`
- default branch: `main`
- current baseline commit at project start: `bd4df06d2c420b65498d142ea4e876df2001125b`
- repository visibility: private

The current repository contains the existing Flask/Waitress + SQLite + worker-thread + per-task subprocess architecture, together with the existing Cloudflare helper code and tests.

## 2. Existing task execution model

The current platform already has:

- SQLite WAL;
- `claim_order()` CAS-style claim protection;
- `worker_running`;
- `attempt`;
- `pid`;
- `heartbeat_at`;
- `recover_stale_orders()`;
- `order_watchdog()`;
- `concurrency_manager()`;
- per-order working directories;
- rolling task logs;
- automatic retry for selected process-failure cases;
- QR-session cleanup;
- online SQLite backup and integrity checking.

Conclusion:

> The cloud scheduler should reuse the existing order/task state semantics through an adapter instead of replacing the state machine wholesale.

## 3. Current local architecture

Current high-level runtime:

```
order_platform.py
  ├─ Flask / Waitress
  ├─ SQLite
  ├─ concurrency_manager
  │    └─ worker threads
  │         └─ one Python subprocess per running order
  ├─ order_watchdog
  ├─ qr_janitor
  └─ housekeeping

health_manager.py
  └─ Windows-only process/tunnel supervision
```

## 4. Windows coupling that blocks direct Linux execution

The current `order_platform.py` contains several Windows-specific paths/APIs, including:

- hard-coded Windows Python executable path;
- Win32 memory-status API;
- Win32 process probing;
- Windows `taskkill`;
- Windows process-memory inspection;
- `tasklist` based cloudflared detection;
- Windows-only process creation flags;
- Windows Task Scheduler integration through the existing health-manager chain.

The migration must isolate these behind a runtime adapter.

`health_manager.py` is not to be ported line-for-line to Linux. The cloud runtime will use systemd/Codespace lifecycle controls instead.

## 5. Existing crypto boundary

`crypto_manager.py` uses Windows Credential Manager for its local DEK storage.

Therefore:

- Windows-encrypted local artifacts are not the cloud-test bootstrap format;
- real local recovery bundles are not to be uploaded;
- cloud-test mode uses a separate runtime Secret;
- cloud test data is independently generated.

This is intentional isolation, not a missing migration feature.

## 6. Cloud-test security boundary

Cloud execution is limited to:

- synthetic users;
- synthetic orders;
- mock/test adapters;
- test-only credentials without production value.

The cloud test environment must not contain:

- real PII;
- real production passwords;
- browser profiles;
- production cookies;
- production tokens;
- production recovery bundles.

## 7. CLOUD_TEST_MODE

Required:

`CLOUD_TEST_MODE=1`

The mode is **fail-closed**.

A configuration flag alone is not a security boundary.

Before any test adapter is started, the runner must prove that its egress deny guard is installed and active.

## 8. Required egress deny policy

At minimum the following production destinations must be blocked:

- `zhihuishu.com` and subdomains;
- `chaoxing.com` and subdomains;
- `api.openai.com`;
- any other production LLM endpoint discovered during adapter review.

Required enforcement layers:

1. kernel/host egress deny rules for resolved blocked destinations when the Codespace environment permits them;
2. a process-level hostname/socket guard as defense in depth for Python-based subprocesses;
3. a startup smoke test that intentionally targets a blocked hostname;
4. runner startup must fail closed if the deny layer cannot be installed or verified.

A blocked-destination smoke test must demonstrate refusal for a target such as:

`hike.zhihuishu.com:443`

The acceptance criterion is not "the adapter chose not to call it"; it is "the runner cannot successfully establish the connection".

## 9. LLM boundary

The repository includes LLM-related dependencies.

In cloud-test mode:

- no production LLM endpoint is permitted;
- mock/local LLM adapters must be used;
- any attempt to reach the production LLM endpoint must be rejected by the egress guard.

## 10. Cloudflare current baseline

Stage-0 agent report (provided as evidence) states:

- Access currently has 4 existing applications, including the existing OrderPlatform application;
- Workers currently has 0 deployed workers;
- the existing read-only Cloudflare credential has insufficient permissions for creating/editing the planned Pages/KV/R2/Durable Objects/Workers resources.

This is recorded as **stage-0 evidence**, not as a newly granted capability.

No new Cloudflare resources are to be created until the required token is deliberately provisioned.

## 11. GitHub Codespaces current baseline

Stage-0 agent report states:

- an available 2C/8G Codespace machine type was confirmed;
- the current GitHub credential cannot call the Codespaces lifecycle API because the required Codespaces permission/scope is missing.

The lifecycle controller therefore remains disabled until a dedicated fine-grained credential is created.

No GitHub credential is to be copied into the repository, frontend, or Codespace filesystem.

## 12. Codespaces budget constraint

The project assumes GitHub Free personal Codespaces usage of 120 core-hours/month and 15 GB-month storage.

Therefore:

- runners are STOPPED by default;
- human approval is mandatory before a runner start;
- idle runners must be stopped automatically;
- usage is tracked by the control plane;
- no unbounded runner creation is permitted;
- Codespace prebuilds are not part of the initial implementation.

Because the lifecycle credential currently lacks permission, actual account usage cannot yet be read programmatically. The first implementation will use local usage estimation/snapshots and later reconcile against GitHub once the required scope exists.

## 13. Runner model target

Target model:

```
Cloudflare control plane
        |
        +---- GitHub Codespaces lifecycle API
                     |
          +----------+----------+
          |                     |
      runner-01             runner-02
      2 slots               2 slots

Total target initial parallel capacity:
4 synthetic test tasks
```

This is a scheduler target, not an unconditional commitment. The safe parallelism value must be measured after Linux/runtime tests.

## 14. Control-plane target

Cloudflare Worker:

- request API;
- approval API;
- runner lifecycle API;
- reconciliation;
- usage guard;
- health probe;
- R2 backup ingress.

Durable Object:

- request state;
- runner registry;
- task leases;
- approval events;
- audit events;
- usage snapshots.

KV:

- latest health summary only.

R2:

- encrypted test backups/reports only.

## 15. Frontend target

Cloudflare Pages/Worker frontend:

- permanently available;
- shows runner status;
- accepts test requests;
- shows approval queue;
- never exposes GitHub lifecycle credentials;
- does not directly start Codespaces.

The browser can request:

`REQUESTED`

but only an authorized approval action may transition to:

`APPROVED`

which permits the control Worker to start a runner.

## 16. Default lifecycle target

```
STOPPED
  ↓
REQUESTED
  ↓
APPROVED
  ↓
STARTING
  ↓
READY
  ↓
RUNNING
  ↓
IDLE
  ↓
STOPPING
  ↓
STOPPED
```

Any task start without the APPROVED transition is a security/control-plane defect.

## 17. Scope exclusions

This repository change set does not authorize:

- production course execution in cloud infrastructure;
- real user migration;
- proxy-pool migration;
- UA spoofing migration;
- speed/jitter migration;
- production endpoint access from cloud-test runners;
- production account/cookie migration.

## 18. Current stage decision

Stage 0 is complete for repository baseline purposes.

Next permitted commits:

- Commit 01: privacy/gitignore boundary;
- Commit 02: this baseline document;
- Commit 03: fail-closed cloud-test mode and egress guard.

Cloud resource provisioning is intentionally deferred.
