# Contributing

UGA uses contract-first development. A change that crosses a module boundary
must update the relevant contract tests before adding or replacing a backend.

## Local checks

```powershell
ruff check .
mypy uga apps
pytest
cargo fmt --all --manifest-path native/Cargo.toml -- --check
cargo clippy --workspace --all-targets --manifest-path native/Cargo.toml --locked -- -D warnings
cargo test --workspace --manifest-path native/Cargo.toml --locked
```

Keep Win32 calls inside `uga.windows`, `uga.capture`, or native crates. Do not
add input injection during the Capture Foundation milestone.

