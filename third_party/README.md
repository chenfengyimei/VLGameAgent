# Third-party artifacts

Before adding any code, model, dataset, or game-derived media, record its name,
version, source, code/model/dataset licenses, distribution and commercial-use
permissions, and review date under `third_party/licenses/`.

No third-party artifact is currently vendored.

Release bundles include `third-party-inventory.json`, generated from installed
Python distribution metadata and locked Cargo metadata. It records exact
resolved versions, locked npm build dependencies, upstream sources, declared
license expressions, and unknown declarations. A complete declaration inventory
is not legal approval; release qualification still requires human review of
applicable license texts, model cards, dataset terms, and game-content rights.
