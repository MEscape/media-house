# UNIVERSAL GROUND RULES — APPLY TO EVERY TASK

These rules apply to the entire implementation. They are mandatory and override shortcuts, assumptions, and “quick feature” approaches.

## 1. Read the Existing Architecture First

Before changing or creating code:

* Read `AI_CONTEXT.md` completely.
* Inspect the existing repository structure.
* Inspect the relevant existing modules, services, models, utilities, configuration, storage, Media Library, processing infrastructure, logging, caching, testing, and CLI/API interfaces.
* Identify existing abstractions that the new work should reuse.
* Understand how the requested capability fits into the current architecture.

**The existing architecture is authoritative unless there is a clear, documented reason to change it.**

Do not begin implementation before understanding the surrounding system.

---

## 2. NEVER Treat a New Feature as an Isolated Feature

The requested capability is not a standalone bolt-on.

It must become a **native part of the existing system**.

Before implementing, ask:

> If three more related capabilities were added six months from now, would they naturally extend this subsystem, or would we need another parallel implementation?

Design for the former.

Do not create:

* parallel managers
* duplicate storage systems
* duplicate Media Libraries
* duplicate databases
* duplicate caching
* duplicate configuration systems
* duplicate processing pipelines
* duplicate asset models
* duplicate timeline models
* feature-specific versions of existing infrastructure
* unnecessary `*_new`, `*_v2`, `*_old`, `*_utils` structures
* one-off abstractions that cannot be reused

If an existing subsystem already owns the responsibility, extend it rather than creating another owner.

---

## 3. Preserve Clear Architectural Boundaries

Maintain clear separation between:

**Domain models**
→ what the system knows

**Processing / Analysis**
→ how information is generated

**Orchestration**
→ how processing stages are coordinated

**Persistence / Media Library / Cache**
→ how results and assets are stored and reused

**External interfaces**
→ CLI, API, GUI, future consumers

Do not mix these responsibilities unnecessarily.

Business/domain logic should not depend directly on CLI/UI concerns.

Processing engines should not own persistence.

Persistence should not contain feature-specific business decisions.

External consumers should consume stable public contracts rather than internal implementation details.

---

## 4. Reuse Existing Infrastructure

Before introducing a new dependency, abstraction, service, manager, cache, storage mechanism, helper, or utility:

1. Search the repository for an existing equivalent.
2. Determine whether it can be reused or extended.
3. Prefer integration over duplication.
4. Only introduce something new when there is a genuine architectural reason.

Do not reinvent infrastructure that already exists.

---

## 5. Design for Future Extension

Do not optimize only for today's exact requirement.

Public interfaces, models, configuration, processing stages, and result objects should be designed so future related functionality can be added without restructuring the subsystem.

However:

**Do not over-engineer speculative functionality.**

Build the smallest architecture that is genuinely extensible.

Avoid:

* unnecessary generic frameworks
* excessive inheritance
* abstract classes without a real need
* configuration fields that have no current meaning
* premature plugin systems
* unnecessary microservices
* unnecessary factories
* excessive indirection

Use simple extension points with clear contracts.

---

## 6. One Canonical Source of Truth

Every important concept should have one canonical owner.

Examples:

* one canonical asset identity
* one canonical Media Library
* one canonical configuration model
* one canonical timeline representation
* one canonical processing result
* one canonical cache/fingerprint strategy
* one canonical metadata model

Derived representations may exist for performance or interoperability, but they must clearly derive from the canonical representation.

Never allow two competing representations to silently diverge.

---

## 7. Standalone + Pipeline Compatibility

Every substantial processing module should support both:

### Standalone

`Input → Module → Result`

and:

### Pipeline

`Previous Result → Module → Result`

When previous processing results already exist:

* reuse them
* do not repeat expensive work
* do not silently recompute information
* do not create duplicate assets
* do not create duplicate analysis

At the same time, the module must remain independently usable when upstream results are unavailable.

---

## 8. Deterministic, Reproducible Processing

Processing should be reproducible.

Prefer:

* explicit configuration
* versioned profiles
* deterministic algorithms
* explicit defaults
* stable processing parameters
* recorded engine/model versions
* recorded input/output assumptions

Do not depend on uncontrolled AI randomness for production behavior.

If adaptive behavior is required, it must be:

* measurable
* bounded
* explainable
* reproducible
* controlled by configuration

---

## 9. Profiles and Configuration Are First-Class

Use structured configuration/profile objects where appropriate.

Separate:

**What the input is**

from:

**How we want to process it**

For example:

`Source Profile → Processing Profile → Runtime Configuration`

Configuration should be usable by:

* Python/API
* CLI
* future GUI
* automated pipelines
* tests

Do not bury important behavior inside hard-coded constants.

Avoid scattered configuration.

---

## 10. GPU + CPU Must Be Supported

Where computation benefits from acceleration:

* use GPU when available
* fall back cleanly to CPU
* do not require GPU hardware
* do not duplicate the implementation unnecessarily
* avoid introducing heavy GPU frameworks without justification
* reuse existing acceleration infrastructure when available

GPU availability must not change the correctness of the result.

It should primarily affect performance, unless explicitly documented otherwise.

---

## 11. Performance Is an Architectural Concern

Consider:

* large files
* long videos
* high-resolution media
* memory usage
* streaming
* batching
* repeated processing
* model loading
* disk I/O
* GPU memory
* CPU fallback
* concurrent jobs

Avoid:

* unnecessary copies
* loading entire large assets into memory when streaming is possible
* repeatedly loading models
* repeatedly probing the same media
* repeated decoding
* duplicate transformations

Performance optimizations must remain understandable and maintainable.

---

## 12. Cache Expensive Work

Expensive processing must be cacheable when appropriate.

Use stable processing fingerprints based on the relevant inputs, such as:

* source asset identity
* source version
* configuration/profile
* relevant parameters
* model/engine version
* dependency version
* processing version
* LUT/configuration identity where relevant

A cache hit must be trustworthy.

Never return stale results simply because a filename happens to match.

---

## 13. Version Everything That Affects Reproducibility

Important schemas, processing logic, configurations, models, and result formats should have explicit versions where appropriate.

Examples:

* `schema_version`
* `processing_version`
* `profile_version`
* model/engine version
* configuration version

Changing behavior in a way that affects output should be detectable.

---

## 14. Preserve Originals

Never destructively modify original source assets unless explicitly required.

Prefer:

`Original Asset → Derived/Processed Asset`

The relationship between them must be recorded.

Original metadata, identity, timing, and provenance should remain recoverable.

---

## 15. Media Library Integration

For media-related functionality:

* use the existing Media Library
* register derived assets correctly
* preserve relationships between source and derived assets
* reuse existing assets when possible
* avoid duplicate files
* avoid duplicate asset records
* support future asset variants/derivatives

Do not create a feature-specific media storage mechanism.

---

## 16. Public Contracts Must Be Clean

External consumers should receive stable, typed, understandable interfaces.

Prefer:

* typed domain models
* explicit result objects
* small public APIs
* clear serialization boundaries
* validated inputs
* predictable errors

Do not expose internal implementation details unnecessarily.

JSON is an interchange/serialization format, not automatically the internal domain model.

---

## 17. Validation and Error Handling

Validate:

* input existence
* input type
* supported formats
* configuration
* incompatible combinations
* missing dependencies
* corrupted inputs
* invalid states
* version compatibility

Failures should be:

* explicit
* actionable
* structured where appropriate
* logged correctly

Do not silently continue after a condition that can corrupt the result.

For non-critical stages, partial failure may be represented explicitly rather than destroying the entire pipeline.

---

## 18. Observability

Important processing should provide useful:

* structured logging
* progress information
* timing information
* processing decisions
* warnings
* errors
* cache hits/misses
* selected profile/configuration
* version/provenance information

Do not flood logs with meaningless noise.

---

## 19. Tests Are Part of the Implementation

Do not consider a feature complete until it has appropriate tests.

At minimum consider:

* unit tests
* validation tests
* contract tests
* integration tests
* pipeline tests
* regression tests
* cache/reuse tests
* failure-path tests
* backward-compatibility tests where relevant

Test the architecture, not only the happy-path function.

Important tests should verify that:

* existing infrastructure is reused
* duplicate processing does not occur
* standalone execution works
* pipeline execution works
* outputs are deterministic
* cache invalidation works
* public contracts remain stable

---

## 20. Do Not Hide Architectural Problems

If implementation reveals that the existing architecture is insufficient:

**Stop and reason about the architecture before adding another workaround.**

Prefer a small, clean refactor over a permanent workaround.

If an existing implementation is clearly wrong for the new requirement:

* refactor it
* migrate callers
* remove obsolete code
* update tests
* verify the complete system

Do not leave two competing architectures alive unless there is a documented transitional reason.

---

## 21. No Feature-Specific Hacks

Avoid code such as:

* special-case branches scattered across unrelated modules
* camera-specific logic outside camera/source profiles
* format-specific behavior duplicated in multiple places
* hard-coded paths
* magic numbers
* global mutable state
* hidden environment assumptions
* copy-pasted processing pipelines

If behavior is genuinely source/profile-specific, model it explicitly.

---

## 22. Dependency Discipline

Before adding a dependency:

* verify whether an existing dependency already solves the problem
* verify license/compatibility
* consider installation complexity
* consider CPU/GPU support
* consider maintenance status
* consider runtime performance
* consider whether it is actually necessary

Prefer high-quality free/open-source/local solutions where they provide the required quality.

Do not add technology merely because it is popular.

---

## 23. Keep Third-Party Libraries Behind Internal Boundaries

Do not let third-party APIs leak throughout the codebase unnecessarily.

Use adapters/wrappers when appropriate.

This allows the underlying technology to change later without rewriting the entire application.

Example:

`Internal Interface → Engine Adapter → Third-Party Library`

rather than:

`Application → Third-Party Library everywhere`

---

## 24. Do Not Mix Responsibilities

A module should not quietly become responsible for unrelated work.

For example:

* inspection should inspect
* improvement should improve
* intelligence should analyze
* editing intelligence should make editing decisions
* rendering should render
* persistence should persist

Related internal stages are fine, but responsibility must remain clear.

If functionality belongs to another subsystem, integrate through a clean contract instead of duplicating it.

---

## 25. Preserve Information

Never throw away useful source information merely because the current feature does not need it.

Prefer:

`Raw → Normalized → Derived`

rather than:

`Raw → Destructive transformation`

Preserve:

* timestamps
* source identity
* metadata
* provenance
* original measurements
* configuration
* relationships
* quality information

Derived results should remain traceable to their source.

---

## 26. Make Future GUI Integration Natural

The architecture should not depend on a GUI.

But anything configurable or reusable programmatically should be represented in a way that a future GUI can consume naturally.

The GUI should eventually be another consumer of the same:

* configuration models
* profiles
* validation
* processing APIs
* result models

Do not create GUI-specific business logic.

---

## 27. Documentation Is Part of the Architecture

Update documentation when the new capability changes:

* architecture
* public APIs
* configuration
* workflows
* module responsibilities
* data models
* processing pipelines
* installation/dependencies

Keep `AI_CONTEXT.md` accurate if the project uses it as architectural context.

---

## 28. Before Coding: Explain the Integration Point

Before implementation, identify:

1. Existing subsystem that owns this responsibility
2. Existing infrastructure that will be reused
3. New components that are genuinely necessary
4. Data entering the module
5. Data leaving the module
6. Cache/reuse strategy
7. Extension points
8. Testing strategy
9. Potential architectural risks

Do not produce an implementation plan that ignores the existing architecture.

---

## 29. After Coding: Perform an Architecture Review

Before declaring the task complete, inspect your own implementation again.

Ask:

* Did I duplicate anything that already existed?
* Did I create a second source of truth?
* Did I create a feature-specific manager?
* Did I create unnecessary abstractions?
* Can another developer extend this naturally?
* Can another related feature reuse this code?
* Are public APIs clean?
* Are responsibilities clear?
* Is configuration centralized?
* Is caching correct?
* Is versioning sufficient?
* Is the module standalone?
* Does it integrate correctly into pipelines?
* Does it preserve original data?
* Are tests covering architecture and reuse?
* Did I accidentally introduce technical debt?

---

## 30. Final Quality Gate

The feature is **NOT complete** merely because it works.

It is complete only when it is:

* functionally correct
* architecturally integrated
* reusable
* extensible
* maintainable
* deterministic
* versioned where necessary
* cacheable where appropriate
* tested
* observable
* performant
* standalone-capable
* pipeline-compatible
* non-destructive where applicable
* compatible with existing infrastructure
* ready for future GUI/API consumers
* free of unnecessary duplication
* free of feature-specific architectural debt

### Final Question

Before finishing, answer:

> **“Would I be comfortable having another senior engineer extend this subsystem six months from now without first having to redesign what I just built?”**

If the answer is no, refactor before declaring the task complete.
