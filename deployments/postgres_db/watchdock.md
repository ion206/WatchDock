---
service: postgres_db
host: local
endpoint: tcp://localhost:5432
stateful: true
guardrails:
  min_replicas: 1
  max_replicas: 1           # one writer per volume: never scale Postgres horizontally
  max_memory: 1g            # per container
  max_total_memory: 1g      # replicas × memory; the device budget for this service
  max_cpus: 2.0
  allowed_actions: [set_memory, set_cpus]
  max_heals_per_hour: 3
---
# postgres_db

## Purpose
Postgres 16 test database for the demo API. Throwaway data in the `pgdata` volume.

## Hardware
Edge device budget for this service: 1 GiB RAM, 2 CPUs.

## Notes for the agent
- Stateful: never add replicas. Two Postgres containers on one volume corrupt it.
- Memory sizing: shared_buffers + max_connections × work_mem + about 100 MiB of overhead.
  Size the limit to that, within max_memory.
- Postgres flags in `command` belong to a human. Recommend flag changes; do not edit them.

## Incident ledger
| when (UTC) | trigger | evidence | action | result | commit |
|---|---|---|---|---|---|
| 2026-10-22 18:04 | Crash (OOM kill) | peak 127.9/128 MiB during pg_dump | memory 128 MiB → 256 MiB | resolved | 3f9c2e1 |
