# BOSS API Documentation

Base path: `/api`

Auth: `Authorization: Bearer <JWT>`

Content type: `application/json`

Tenant scoping: every request enforces that `:tenantId` equals the authenticated user’s stable bigint `id`.

---

## Shared Rules

* **IDs**: All bigint values (`tenantId`, `id`, `sourceId`, `targetId`, `time`, `position`) are accepted as bigint literals or decimal strings. Responses always render them as strings.
* **Objects**

  * Schema: `(tenant BIGINT, type INT, id BIGSERIAL, version INT, attributes JSONB)`
  * Primary key: `(tenant, type, id)`
  * Concurrency: `version` increments on every update. Updates must supply the expected version or fail with `40001`.
* **Associations**

  * Schema: `(tenant BIGINT, type TEXT, source_id BIGINT, target_id BIGINT, time BIGINT, position BIGINT, attributes JSONB)`
  * Primary key: `(tenant, type, source_id, target_id)`
  * Secondary index: `(tenant, type, source_id, position DESC)` for fast paging.

---

## Endpoints

### Get Object

`GET /api/objects/:tenantId/:otype/:id`

Retrieve one object by type and id.

**Path**

* `:tenantId` bigint string
* `:otype` integer
* `:id` bigint string

**200**

```json
{
  "type": 5001,
  "version": 1,
  "id": "1234567890123456789",
  "attrs": { "name": "my-agent", "status": "active" }
}
```

**Notes**

* Enforces tenant ownership.
* Returns `404` if the object does not exist.
* Logs include `[tenantId/otype/id]` context.

**Errors** 401, 403, 400, 404, 500

---

### Upsert Object

`PUT /api/objects/:tenantId`

Create or update an object.

**Path**

* `:tenantId` bigint string

**Body**

```json
{
  "type": 5001,
  "id": "0",
  "version": 1,
  "attrs": { "name": "agent-updated", "status": "inactive" }
}
```

**Behavior**

* If `id` is `0` or omitted ⇒ insert (DB auto-generates ID).
* If `id` is supplied ⇒ update with optimistic check. Fails if `version` does not match current row version.
* Version bump is automatic.

**201**

```json
{ "success": true, "id": "1234567890123456789" }
```

**Errors**

* `400` invalid body
* `403` cross-tenant write
* `500` version clash or storage error

**Guidance**

* Always read the object first to get latest `version` before attempting an update.
* Treat version clashes as retry signals.

---

### Delete Object

`DELETE /api/objects/:tenantId/:otype/:id`

Delete the object and cascade all its associations.

**200**

```json
{ "success": true }
```

or

```json
{ "success": false, "message": "Object may not have existed" }
```

**Guidance**

* Deletion is hard, not soft. Once gone, the id cannot be reused reliably.
* All inbound and outbound associations for `(tenant, type, id)` are removed.

**Errors** 401, 403, 400, 500

---

### List Associations

`GET /api/objects/:tenantId/associations/:type/:sourceId`

List associations by type from a source object.

**Path**

* `:tenantId` bigint string
* `:type` string
* `:sourceId` bigint string

**Query**

* `targetId` bigint string optional filter
* `after` bigint string pagination cursor (position)
* `limit` integer, default 50, max 1000

**200**

```json
{
  "count": 1,
  "associations": [
    {
      "type": "authored_by",
      "sourceId": "101",
      "targetId": "202",
      "time": "1756515600000",
      "position": "1756515600000",
      "attrs": { "role": "author" }
    }
  ]
}
```

**Guidance**

* `after` is a monotonic bigint cursor (often millisecond epoch). Use it for forward paging.
* Limit queries to ≤1000 to avoid throttling.
* Ordering is descending by `position`.

**Errors** 401, 403, 400, 500

---

### Create Association

`PUT /api/objects/:tenantId/associations`

Create or overwrite a directed association.

**Body**

```json
{
  "type": "monitors_model",
  "sourceId": "12345",
  "targetId": "67890",
  "time": "1756515600000",
  "position": "1756515600000",
  "attrs": {}
}
```

**Behavior**

* PK `(tenant, type, sourceId, targetId)` is unique. PUT is idempotent.
* If an association already exists, `time`, `position`, and `attrs` are updated.

**201**

```json
{ "success": true }
```

**Guidance**

* Use monotonic `position` for ordered sequences (e.g. timelines).
* Use `time` for wall-clock semantics.

**Errors** 401, 403, 400, 500

---

### Delete Association

`DELETE /api/objects/:tenantId/associations/:type/:sourceId/:targetId`

Delete one association.

**200**

```json
{ "success": true }
```

**Guidance**

* Removal is absolute. Use re-PUT to restore.
* Efficient due to primary key lookup.

**Errors** 401, 403, 400, 500

---

## Operational Insights

* **Security**: Helmet, CORS, and JWT are enforced before handlers run.
* **Serialization**: BigInt polyfill ensures compatibility with JSON clients. All bigint outputs are stringified.
* **Error Semantics**:

  * `401` ⇒ no or invalid JWT
  * `403` ⇒ tenant mismatch
  * `400` ⇒ invalid param or body
  * `404` ⇒ object missing
  * `500` ⇒ DB exception (including version clash)
* **Logging**: All route errors include contextual `[tenant/type/id]` in stderr.
* **Storage Backends**: Works with PostgreSQL ≥12 and YugabyteDB ≥2.17. Uses JSONB and GIN indexes for schemaless queries outside this API’s scope.