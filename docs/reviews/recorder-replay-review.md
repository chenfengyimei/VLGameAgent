# Recorder + Replay implementation review

Date: 2026-09-09

| Issue | Status | Evidence / remaining gate |
|---|---|---|
| UGA-030 | implemented | monotonic absolute/elapsed timeline with deterministic tie order |
| UGA-031 | implemented | one-to-one action provenance and observation linkage validation |
| UGA-032 | implemented | transactional V1.1 Episode layout, explicit Parquet schemas, SHA-256 manifest |
| UGA-033 | implemented | PyAV H.264 MP4 with source-derived timestamps and padded-stride support |
| UGA-034 | implemented | checksum/schema/reference validation, seek, step, lookup, stable replay digest |

## Review outcome

Recorder + Replay passes implementation review. Integration coverage writes and
reads a complete Episode, decodes its MP4 frames, reads all Parquet tables,
checks raw-input state plus agent-action provenance, verifies stable replay, and
detects file corruption. Empty Parquet tables retain declared types, so schema
does not depend on episode content.

The design's supervised 10-minute gameplay acceptance run remains a release
qualification gate. It requires a selected target game and operator session;
the automated suite instead uses deterministic owned pixel buffers and sends no
OS input.
