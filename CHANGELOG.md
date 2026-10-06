# Changelog

Rendered by CI and committed back at the end of a release -- do not edit by
hand. Release notes also appear on the GitHub Releases page, one per tag.

## [0.2.9](https://github.com/hyperi-io/dfe-schemas/compare/v0.2.8...v0.2.9) (2026-10-06)

### Bug Fixes

* **ci:** rename the publish key to release, which is what hyperi-ci reads ([#42](https://github.com/hyperi-io/dfe-schemas/issues/42)) ([df58666](https://github.com/hyperi-io/dfe-schemas/commit/df586660a5a8276d4acf65f74e5e93d9c63c6fbe))
* clear the ruff S, ruff D and vulture findings on the package source ([#52](https://github.com/hyperi-io/dfe-schemas/issues/52)) ([5db7eb3](https://github.com/hyperi-io/dfe-schemas/commit/5db7eb3f27adea74737a0209eb1f0175e7d1b7e8))
* give the hunt runner a ClickHouse user of its own ([#54](https://github.com/hyperi-io/dfe-schemas/issues/54)) ([9f39032](https://github.com/hyperi-io/dfe-schemas/commit/9f39032440de3aaa2465708c603075d37847171f))
* keep ruff and ty out of .worktrees ([#62](https://github.com/hyperi-io/dfe-schemas/issues/62)) ([3d6428e](https://github.com/hyperi-io/dfe-schemas/commit/3d6428e8aabd130d7dcd07730065fa0c0bcca641))
* mark [@generated](https://github.com/generated) and [@config](https://github.com/config) as loader-descriptive, not parsed ([#51](https://github.com/hyperi-io/dfe-schemas/issues/51)) ([3212d18](https://github.com/hyperi-io/dfe-schemas/commit/3212d18528642d0dee12a36d94cdcac5ba2ec987))
* **meta:** add four Beats meta and hunt schemas ([#58](https://github.com/hyperi-io/dfe-schemas/issues/58)) ([462dba8](https://github.com/hyperi-io/dfe-schemas/commit/462dba8d207514a1563c713cb1270de3ed1a4885))
* **meta:** add the ECS 9.5.0 vocabulary as a pre-shipped meta schema ([fccd354](https://github.com/hyperi-io/dfe-schemas/commit/fccd3549cf117d0f1504ee31f77af17613366316))
* **meta:** add the missing cloud fetcher metas ([#56](https://github.com/hyperi-io/dfe-schemas/issues/56)) ([75b51ac](https://github.com/hyperi-io/dfe-schemas/commit/75b51ac6c83c50e8931b30ba70bf438523ef2cc5))
* **meta:** add winlogbeat meta and hunt schemas ([#48](https://github.com/hyperi-io/dfe-schemas/issues/48)) ([6bf2d86](https://github.com/hyperi-io/dfe-schemas/commit/6bf2d86fe917a6effc17725f86f697385cc0e4d1))
* **meta:** declare cardinality in the cloud metas ([#57](https://github.com/hyperi-io/dfe-schemas/issues/57)) ([32b9a75](https://github.com/hyperi-io/dfe-schemas/commit/32b9a75e8c6a7b2921d636e3f0ecc0728256983d))
* **meta:** fit four schemas to the rows DFE lands ([#60](https://github.com/hyperi-io/dfe-schemas/issues/60)) ([6633db4](https://github.com/hyperi-io/dfe-schemas/commit/6633db479c1f3bc271d5166f50151158428692c8)), closes [#55](https://github.com/hyperi-io/dfe-schemas/issues/55)
* point the field maps at real ECS columns ([#63](https://github.com/hyperi-io/dfe-schemas/issues/63)) ([c7428dc](https://github.com/hyperi-io/dfe-schemas/commit/c7428dc6dc3ba01a440a84cda11e43ed23580d1a))
* raise the Python floor to 3.14 ([#32](https://github.com/hyperi-io/dfe-schemas/issues/32)) ([74adcfd](https://github.com/hyperi-io/dfe-schemas/commit/74adcfd8601f7f94d8decc3005d86710a1313ee9))
* **render:** fail on an index use case nothing can render ([da528f0](https://github.com/hyperi-io/dfe-schemas/commit/da528f0f83b973a6d8c51c918c044b5407467951))
* say why dfe-schemas names its own coverage source ([#53](https://github.com/hyperi-io/dfe-schemas/issues/53)) ([a06aa4a](https://github.com/hyperi-io/dfe-schemas/commit/a06aa4a334817641387807f063ec1fd1f8c12eeb)), closes [#52](https://github.com/hyperi-io/dfe-schemas/issues/52)
* **schema:** declare a column's cardinality once, not in two places ([59ad28b](https://github.com/hyperi-io/dfe-schemas/commit/59ad28b13eb4ef36469ad99aca903d56a58a0884))
* **schemas:** ship the filebeat hunt schema, and validate derived schemas at all ([#47](https://github.com/hyperi-io/dfe-schemas/issues/47)) ([1de4218](https://github.com/hyperi-io/dfe-schemas/commit/1de421806dd90eba811509678339a097e617185b))
* **schemas:** ship the generated Elastic integration schemas ([#55](https://github.com/hyperi-io/dfe-schemas/issues/55)) ([672ab11](https://github.com/hyperi-io/dfe-schemas/commit/672ab11a14de372d3baa4a57a358af42cf69dca3))
* stop documenting [@captured](https://github.com/captured) as a loader directive ([#50](https://github.com/hyperi-io/dfe-schemas/issues/50)) ([2460a9d](https://github.com/hyperi-io/dfe-schemas/commit/2460a9dd3aac23a3517f1dcf7cd46e85e0163455))
* **validate:** parse a derived schema's base once per run ([#59](https://github.com/hyperi-io/dfe-schemas/issues/59)) ([fa2d1cb](https://github.com/hyperi-io/dfe-schemas/commit/fa2d1cb9f6593984d248277fce15f821a9c78089))

## [0.2.8](https://github.com/hyperi-io/dfe-schemas/compare/v0.2.7...v0.2.8) (2026-09-21)

### Bug Fixes

* **docs:** add the README Context section and an architecture doc ([#38](https://github.com/hyperi-io/dfe-schemas/issues/38)) ([f7e4d14](https://github.com/hyperi-io/dfe-schemas/commit/f7e4d1419d1bcb0dd0c7c2f73497eab8d21ab754)), closes [#32](https://github.com/hyperi-io/dfe-schemas/issues/32)
* **make:** default PY to uv run python ([6c79570](https://github.com/hyperi-io/dfe-schemas/commit/6c795704f342a82d246039e18add68d52a5b98f6)), closes [#25](https://github.com/hyperi-io/dfe-schemas/issues/25)
* **schema:** name an index use case for the question, not the ClickHouse index ([#39](https://github.com/hyperi-io/dfe-schemas/issues/39)) ([c7ced12](https://github.com/hyperi-io/dfe-schemas/commit/c7ced120c8d4787031f2c3c72555850cf5e5c78e))

## [0.2.7](https://github.com/hyperi-io/dfe-schemas/compare/v0.2.6...v0.2.7) (2026-09-17)

### Bug Fixes

* **header:** name the tokenizer ClickHouse actually has ([8934979](https://github.com/hyperi-io/dfe-schemas/commit/893497998a2674ae8f8a6a506fd7aaed91367ad5))
* **tests:** pass parametrize its argument names as a tuple ([754fdb8](https://github.com/hyperi-io/dfe-schemas/commit/754fdb82250b7a0b67bf0c74cb0f7816389da057))
* **topics:** the landing topic tiers where the deployment does ([1dd39b4](https://github.com/hyperi-io/dfe-schemas/commit/1dd39b4921d686124d16ab4d08a37d0695804549))

## [0.2.6](https://github.com/hyperi-io/dfe-schemas/compare/v0.2.5...v0.2.6) (2026-09-16)

### Bug Fixes

* one apply manifest, and the package is the only schema source of truth ([cb091f5](https://github.com/hyperi-io/dfe-schemas/commit/cb091f504444320a80e6b0cd53a7067d493ba949))

## [0.2.5](https://github.com/hyperi-io/dfe-schemas/compare/v0.2.4...v0.2.5) (2026-09-15)

### Bug Fixes

* **scripts:** an older engine skips the argument check ([c9e5810](https://github.com/hyperi-io/dfe-schemas/commit/c9e581068cf7af3998cc5aeed61c820ae07f1791))
* **scripts:** validate engine arguments on sources ([5f148cb](https://github.com/hyperi-io/dfe-schemas/commit/5f148cb720549df5719f3261f674811e0c4d094b))
* **sources:** main uses timeseries header 1.0.1 ([c8342b5](https://github.com/hyperi-io/dfe-schemas/commit/c8342b50fc8e4badfc6ac5550d64729d91d11bdb))

## [0.2.4](https://github.com/hyperi-io/dfe-schemas/compare/v0.2.3...v0.2.4) (2026-09-15)

### Bug Fixes

* **registries:** schema allow-lists live here ([5bcd003](https://github.com/hyperi-io/dfe-schemas/commit/5bcd0030981a7daba1368b3561b5c33ffac161fe))

## [0.2.3](https://github.com/hyperi-io/dfe-schemas/compare/v0.2.2...v0.2.3) (2026-09-14)

### Bug Fixes

* **meta:** add the runZero meta schemas and the snapshot envelope ([6d672c4](https://github.com/hyperi-io/dfe-schemas/commit/6d672c42b9c162eb9e05f24e0f18630500671605))

## [0.2.2](https://github.com/hyperi-io/dfe-schemas/compare/v0.2.1...v0.2.2) (2026-09-11)

### Bug Fixes

* ship the landing source definition ([0ce2707](https://github.com/hyperi-io/dfe-schemas/commit/0ce2707ceb8d64faab45fe9986b547883688ee33))
* the core landing table is main ([2aaf05b](https://github.com/hyperi-io/dfe-schemas/commit/2aaf05bfa3ce7612bd72a9ef9f5fec84dcf42697))

## [0.2.1](https://github.com/hyperi-io/dfe-schemas/compare/v0.2.0...v0.2.1) (2026-09-08)

### Bug Fixes

* **tables:** time-series tables take the deployment default retention ([bcf64d2](https://github.com/hyperi-io/dfe-schemas/commit/bcf64d2264b136b08b10b5e02db70fc7e01e6038)), closes [#310](https://github.com/hyperi-io/dfe-schemas/issues/310)
