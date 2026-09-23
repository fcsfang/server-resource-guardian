# System Shape

## Monitoring and Alerts

Beszel owns host monitoring, history, display, and its native CPU, memory, and disk alerts. The repository's Beszel helper builds alert requests and reads back alert records; it does not execute Guardian actions.

## Guardian Runtime

- `guardian-runtime.service` runs `src.guardian_orchestrator`: host observation, risk evaluation, policy, state, and audit.
- `guardian-collector.service` exposes a fixed read-only container snapshot interface. Runtime does not receive the Docker socket.
- Enforce mode delegates an authorized action through the local Broker. Observe and simulate use a fake adapter.

## Administrator Recovery

- `guardian-rescue status` and `top` provide a separate read-only diagnostic path.
- `stop` and `verify` operate on one permitted target through a fixed helper and record the result.
- Local Runtime belongs to `rescue.slice`; its Collector belongs to `guardian-observer.slice`. In the x86 package, Runtime has `guardian-runtime.slice` and Collector has `guardian-observer.slice`.

The default is observe-only. Resource weights and memory protection can improve priority but cannot guarantee SSH under global OOM, kernel, network, power, or disk failure. Production readiness is not established.
