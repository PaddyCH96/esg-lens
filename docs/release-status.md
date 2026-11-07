# ESG Lens release status

The v1 implementation covers collection, NLP, pure scoring, API jobs, evidence snapshots, sensitivity
analysis, and documentation. This release adds a lightweight same-origin browser dashboard and
recording to make the prototype directly usable. This supersedes the original planning assumption
that the repository would only expose an API; no separate frontend service is required.

## Verified release gates

- Offline suite validates collectors, scoring, persistence, jobs, API errors, dashboard assets and methodology.
- Browser checks exercise the actual API with synthetic evidence; the live workflow repeats them with real models and Google News RSS.
- Both scored and insufficient-data results are supported. Missing pillars remain null.
- Live validation results and limitations are documented in README; zero paired external ratings means no external validation claim.
- Source failures are reported as partial or failed jobs. Google RSS availability is best effort.
- MIT license applies to project code; source data and model licenses remain their owners' terms.

## Explicitly deferred

External rating correlation remains blocked on an accessible benchmark dataset. SEC end-to-end
validation requires the operator's real contact setting. Authentication, public hosting, non-US and
non-English coverage, fine tuning, greenwashing models, and job cancellation are outside v1.

The historical `.planning` checklists describe the original design and are not a release status dashboard.
