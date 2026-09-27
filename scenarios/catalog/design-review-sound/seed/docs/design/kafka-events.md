# Moving registry event processing to Kafka

Status: proposed, revision 3. Author: platform team.

## Goals

- **Throughput.** 5,000 events/s at peak (the single Postgres-table consumer
  manages about 800/s today).
- **Replay.** A consumer can re-read the last 7 days of events after a bug fix.
- **Independent consumers.** Billing, notifications and audit each read the
  same stream at their own pace without slowing each other down.
- **Per-domain ordering.** `events/processor.py` requires a domain's events in
  sequence order; this must hold for every consumer.

## Design

**Producer.** The registry gateway publishes one message per registry event to
the topic `registry-events` (24 partitions), keyed by **domain name**. Kafka
preserves order within a partition, so all events for one domain are consumed
in publication order. The gateway publishes with `acks=all` and
`enable.idempotence=true`, so a producer retry cannot duplicate a message.

**Consumers.** Each service is its own consumer group: `billing`,
`notifications`, `audit`. Delivery is at-least-once, so every consumer is
idempotent on `event_id`: before acting it records `event_id` in its own
`processed_events` table in the same transaction as its side effect (billing:
the charge row; notifications: the outbox row; audit: the audit row). A
redelivered event finds its `event_id` already recorded and is skipped. On a
processing error the consumer retries the message up to 5 times, then moves it
to `registry-events.dlq` together with its partition and offset; the on-call
runbook drains the DLQ in offset order per partition so per-domain order holds.

**Retention.** 7 days on `registry-events`, 30 days on the DLQ.

**Sizing.** 24 partitions × ~400 events/s per partition consumer gives
headroom above the 5,000/s target; brokers are the managed 3-node cluster.
Hot domains are bounded: the registry rate-limits a single domain to 10
events/s.

## Migration

1. Dual-write for two weeks: the gateway writes every event to both the
   existing Postgres queue table and Kafka, in that order; a Kafka publish
   failure is logged and retried by a sweeper that republishes table rows
   missing from the topic (matched on `event_id`).
2. A nightly reconciliation job compares `event_id` sets between the table and
   the topic for the previous day and pages on any difference.
3. Consumers switch by feature flag, one service at a time. **Rollback**: flip
   the flag back; because every consumer is idempotent on `event_id`, resuming
   from the table re-delivers at most the events already processed from Kafka
   and they are skipped.
4. After two clean weeks of reconciliation, remove the table writes and the old
   consumer.

## Non-goals

- Exactly-once delivery end to end (idempotent consumers make it unnecessary).
- Changing the event schema.
