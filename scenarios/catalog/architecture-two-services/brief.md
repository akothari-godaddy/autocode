Design (do not implement) the architecture for a link-shortener product with these requirements:

R1: The system must resolve a short code to its destination URL and redirect the caller there.
R2: Every resolution must be recorded as a click event for analytics (destination, short code, timestamp).
R3: Click-analytics storage and query load must be owned by a separate component from link resolution, since the two scale independently and analytics queries must never slow down redirects.
R4: Components communicate only through explicitly declared contracts; no component may depend on another component's internal implementation or database.

Deliver only a design, as these files, and nothing else (no application code):

- `architecture/components.json`: a JSON array. Each entry has `id` (short identifier), `description`, `requirements` (the requirement IDs above that this component satisfies), `depends_on` (IDs of components this one depends on, `[]` if none), `publishes_contracts` (names of contracts this component defines, `[]` if none), and `consumes_contracts` (names of contracts this component relies on from another component, `[]` if none). Every requirement R1-R4 must be covered by at least one component's `requirements` list.
- `architecture/dependency_trace.json`: `{"edges": [[consumer_id, provider_id], ...]}` — one edge for every component that consumes a contract another component publishes. This must exactly match the dependency relationships implied by `depends_on`, `publishes_contracts`, and `consumes_contracts` in `components.json`.
- `architecture/contracts/<name>.schema.json`: one file per contract named in any component's `publishes_contracts`, each a JSON Schema object (`"type": "object"`, with a `"properties"` object and a `"required"` array) describing the data crossing that boundary.

Every `consumes_contracts` entry must name a contract some other component `publishes_contracts`, and the dependency graph must have no cycle.
