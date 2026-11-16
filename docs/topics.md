# Kafka topics (`topics/kafka.yaml`)

`topics/kafka.yaml` is the one definition of the topic naming rule, the topic defaults, the bootstrap set and the rule that derives a deployment's full topic set. dfe-engine creates the bootstrap topics from it at boot, through the manifest, and nothing else creates them. When two components disagree about a topic, a tier ships without a topic its apps write to, and the failure surfaces as produce errors on the DLQ path.

A source's records arrive on `<source>_land`, before any transform. A source with a transform has a `<source>_load` topic as well, which the transform writes and the loader reads. `main` is the `_source` label of the landing table.

A topic takes its partition count from its bootstrap entry, else `defaults`, and its replication factor from `defaults`. The renderer clamps the replication factor to the deployment's broker count, because a topic asking for more replicas than there are brokers never becomes ready. Kafka can raise a topic's partition count and never lower it.

`max_message_bytes` is 16 MiB. Filebeat truncates at 10 MiB, and an event enriched and written back to Kafka is larger than the source event. The broker's `message.max.bytes`, its `replica.fetch.max.bytes` and every topic's `max.message.bytes` must agree: raise one alone and a produce is rejected or a follower stalls.

Landing topics keep 24 hours: Kafka is the buffer in front of dfe-archiver, so time retention is how long a consumer may be down before events are lost. DLQ topics have one partition, because poison messages are few and a person reads them, and keep 7 days, because a dead-lettered record exists nowhere else once its source offset is committed.

`main_land` is created up front so a bare deploy comes up. dfe-loader discovers `*_land` topics and treats finding none as fatal, so without one it restarts until something publishes, and nothing can publish through a loader that is down.

Each fleet app has a dead-letter topic (`dfe_receiver_dlq`, `dfe_loader_dlq`, `dfe_archiver_dlq`, `dfe_fetcher_dlq`), and the transform family shares `dfe_transform_dlq`. The apps run a read-only root filesystem, so Kafka is every app's DLQ backend, and each app chart points its DLQ at the matching topic.

## Tiered storage

Tiered storage is set per topic as well as per broker. With the broker-wide `remote.log.storage.system.enable` on and `remote.storage.enable` absent from a topic, every segment stays on local disk and the first symptom is a full volume.

The `tiered` block renders `remote.storage.enable` on the landing topics when the deployment's `kafka_tiered_storage` parameter says its brokers tier, and renders no key otherwise. An unconditional `false` would fight a broker-level setting on a cluster that does tier. A DLQ topic is never tiered: it is small, and a person reading one would wait on a remote fetch.

## Deriving a deployment's topics

A deployment's full topic set is the bootstrap set plus a landing topic for every active source and a `_load` topic for each source with a transform. The sink of an idle transform is left out: scalo's resolver drops `<base>_land` from an auto-discovered subscription when `<base>_load` exists, so creating the sink of a transform that produces nothing stops the loader reading that source. A source that resolves to the default landing topic adds nothing.

The engine's boot identity may create and alter topics, but not delete them. Altering raises a partition count when a source's throughput grows and destroys nothing. Deleting belongs to a separate operator identity, so a compromised engine principal cannot drop the bus.
