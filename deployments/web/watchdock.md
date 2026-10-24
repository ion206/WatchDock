---
service: web
host: pi-01
endpoint: http://localhost:8080/
stateful: false
guardrails:
  min_replicas: 1
  max_replicas: 3
  max_memory: 256m          # per container
  max_total_memory: 768m    # replicas × memory, must fit the device with headroom
  max_cpus: 1.0             # per container
  allowed_actions: [scale, set_memory, set_cpus]
  max_heals_per_hour: 6
probe:
  threshold_ms: 800
  recover_ms: 500
---
# web

## Purpose
Demo API behind Traefik on pi-01. Stateless, so horizontal scaling is safe.
Each request is CPU-bound (~20 ms of CPU, stretched by the 0.5 CPU limit).

## Notes for the agent
- Latency under load is queueing at too few replicas: prefer `scale` over memory.
- Never change image, ports, labels or environment. Traefik finds new replicas by label.

## Incident ledger
| when (UTC) | trigger | evidence | action | result | commit |
|---|---|---|---|---|---|
