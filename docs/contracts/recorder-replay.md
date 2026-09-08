# Recorder and replay contracts

UGA records through a dedicated bounded channel. Unlike the runtime's
latest-state-wins observation slot, this channel never silently drops an item:
producers block or receive an explicit backpressure timeout.

An episode is written under a unique hidden staging directory. Finalization
closes MP4 encoding, writes explicit-schema Zstandard Parquet tables, writes
JSON/JSONL sidecars, hashes every file, and atomically renames the directory to
its public episode ID. A staging directory is incomplete and must never be used
for training.

The V1.1 episode layout is:

```text
episode_id/
|-- metadata.json
|-- timeline.parquet
|-- actions.parquet
|-- observations.parquet
|-- provenance.parquet
|-- tasks.json
|-- events.jsonl
|-- annotations.jsonl
|-- video.mp4
|-- thumbnails/
`-- checksum.json
```

Absolute monotonic timestamps and elapsed nanoseconds are both retained.
Records may arrive out of order from concurrent producers; final timeline order
is `(timestamp_ns, sequence)`, preserving deterministic ties without inventing
time. Every action row has exactly one provenance row, and every non-null
observation reference must resolve.

Replay is read-only: it verifies checksums and schema version, validates the
timeline and references, then supports reset, elapsed-time seek, event stepping,
observation-to-action lookup, and a stable content digest. Replay never emits OS
input.
