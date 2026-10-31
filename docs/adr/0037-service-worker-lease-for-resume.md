# ADR-0037: Lease active Service workers before Run takeover

- Status: Accepted — implemented and offline verified
- Scope: AgentService Run start/resume ownership across Service instances sharing SQLite

## Problem

The durable fence rejected stale database writes, but `resume_run` could still take over a Run whose previous Service process was alive and executing. A database fence alone does not arbitrate active workers or stop their external effects.

## Decision

Service-owned Run contexts acquire a SQLite worker lease together with their writer fence. The lease records the writer, fence token, heartbeat, and expiry. AgentService renews the lease while its launcher is running. A second Service cannot take over an unexpired lease; it may acquire a new fence after expiry. A `RUNNING` fence with no lease evidence fails closed. Releasing the current fence deletes its matching lease.

If renewal fails, the Service cancels its local launcher task and stops accepting that worker as authoritative. Durable writes from an old fence are rejected. This is coordination for Service instances that share the same SQLite database, not a distributed lease service.

## Limits

Lease expiry cannot prove that a paused process or an in-flight external operation has stopped. Cancellation can leave an external effect unknown; the fencing token protects durable Runtime writes, not arbitrary third-party APIs. Hard exactly-once external effects still require cooperation from the destination, such as idempotency keys or destination-side fencing.

## Verification

Offline tests cover two independent SQLite connections, rejection of takeover while a lease is active, takeover after expiry, stale heartbeat rejection, lease release, rejection of a `RUNNING` fence without lease evidence, H3 schema migration, and an AgentService-level active resume attempt. These tests do not simulate OS process suspension/termination or validate third-party effects.
