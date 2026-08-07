# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1](https://github.com/deliro/corrode/compare/v1.0.0...v1.0.1) (2026-08-07)


### Bug Fixes

* **ci:** chain publish and release docs from release-please ([#23](https://github.com/deliro/corrode/issues/23)) ([47b480d](https://github.com/deliro/corrode/commit/47b480d25cc92c9b42aec1c77a9eccb5304961b0))

## [1.0.0](https://github.com/deliro/corrode/compare/v0.1.2...v1.0.0) (2026-08-07)


### ⚠ BREAKING CHANGES

* bool(Result) now raises TypeError; as_result/ as_async_result reject BaseException-only types; simultaneous task exceptions arrive as ExceptionGroup (always, even a single one); the private _DoError is renamed to public DoError.

### Features

* strictness guarantees, task-safe async iterators, new API ([a81025f](https://github.com/deliro/corrode/commit/a81025f1cc25e44caa98b37169d3aa104fd4a187))


### Bug Fixes

* **docs:** publish versioned docs, repair broken site rendering ([#21](https://github.com/deliro/corrode/issues/21)) ([9ce43be](https://github.com/deliro/corrode/commit/9ce43be13f4f90cc2c67fa0ff0dd2a36b648e0a6))


### Documentation

* add acknowledgements ([68bf8b0](https://github.com/deliro/corrode/commit/68bf8b0d5d408acbd32d4a05cfcdb77a69c7db4f))
* add The Zen of Python quote ([6f3bb5e](https://github.com/deliro/corrode/commit/6f3bb5ebc457b64f96e2b06229bf6c770057b2d6))
* generated docs site, slimmer README, contributing guide ([5bbbc59](https://github.com/deliro/corrode/commit/5bbbc59a8ab13543e08c808905530be9577afa56))
* README improved & `do` deprecated ([baef922](https://github.com/deliro/corrode/commit/baef9223318ef9b90f00265898fb5424a92251ac))

## [0.1.2](https://github.com/deliro/corrode/compare/v0.1.1...v0.1.2) (2026-02-19)


### Bug Fixes

* **types:** narrow as_async_result return type from Awaitable to ([ea5d40c](https://github.com/deliro/corrode/commit/ea5d40c28b589ba292aad5eef1ad166888d1a622))

## [0.1.1](https://github.com/deliro/corrode/compare/v0.1.0...v0.1.1) (2026-02-19)


### Bug Fixes

* **ci:** trigger pypi publish on release ([c7f273d](https://github.com/deliro/corrode/commit/c7f273d54a7a1137a9f4c34614e2f312b40e17df))

## [0.1.0](https://github.com/deliro/corrode/compare/v0.0.1...v0.1.0) (2026-02-19)


### Features

* `Result.zip` & more async iterators ([2976fa0](https://github.com/deliro/corrode/commit/2976fa037b74bf95c9f614514859f8f5f2a36e42))
* **iterator:** async functions that collect lists (`collect`, ([dced816](https://github.com/deliro/corrode/commit/dced816128776d0ff50e88349aaffdfcebed20fe))
* iterators ([a79c97d](https://github.com/deliro/corrode/commit/a79c97d1fc7eac18406224f70dcaf4f2a382d4f7))


### Bug Fixes

* **tests:** README.md code blocks run only on Python3.12+ versions ([b57b0ed](https://github.com/deliro/corrode/commit/b57b0eddcb94a734fe37ef45017d18f889e01ec6))
* update uv.lock on release ([891e8cd](https://github.com/deliro/corrode/commit/891e8cda2c0dcba9a67bf1ceae0f0883691ec7a4))


### Documentation

* rewrite documentation and validate code blocks ([8c1ff91](https://github.com/deliro/corrode/commit/8c1ff91c5ebbc84f05ccdcc467825f47170f3c68))

## [0.0.1](https://github.com/deliro/corrode/compare/v0.0.0...v0.0.1) (2026-02-16)


### Bug Fixes

* anchor release-please to v0.0.0 tag ([57a322f](https://github.com/deliro/corrode/commit/57a322fbe90a6c8cc4006f64991a1f48ef52c10f))
* use conditional import for TypeIs to support Python 3.13+ ([4f49686](https://github.com/deliro/corrode/commit/4f49686cac0adbaee641b58251b6f89f3c005594))

## [Unreleased]

[Unreleased]: https://github.com/deliro/corrode/commits/main
