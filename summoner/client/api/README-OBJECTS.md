# BOSS API

Base path `/api`
Auth `Authorization: Bearer <JWT)`
CORS enabled, Helmet on, JSON body limit 50 MB
BigInt outputs are strings

Tenant isolation

* Every route compares `:tenantId` to `authenticate(req).id` bigint
* Mismatch returns `403`

---

## Data contracts

Objects

* Fields: `type` int, `id` bigint string, `version` int, `attrs` object
* PK `(tenant, type, id)` with `id` BIGSERIAL
* Optimistic concurrency on `version` with automatic bump on update

Associations

* Fields: `type` string, `sourceId` bigint string, `targetId` bigint string, `time` bigint string, `position` bigint string, `attrs` object
* PK `(tenant, type, source_id, target_id)`
* Secondary index `(tenant, type, source_id, position DESC)` for paging
* `type` must be URL-safe per `isUrlSafe` in `routes/auth.ts`
  Guidance: restrict to `[A-Za-z0-9._~-]` style tokens to pass

BigInt handling

* Inputs may be bigint or decimal strings
* Outputs are strings
* Request parsers reject non-integers

Common validation

* `attrs` must be a JSON object, not null, not array
* `limit` default 50, max 1000
* `otype` must be an integer
* All IDs and cursors must be integers in string or bigint form

Errors

* `401` invalid or missing JWT
* `403` tenant mismatch
* `400` malformed params or body, limit overflow, invalid `type` per URL-safe check
* `404` object not found
* `500` storage or unexpected error
  Logging includes route context like `[tenant/otype/id]`

---

## Endpoints

### Get Object

`GET /api/objects/:tenantId/:otype/:id`

Fetch a single object.

200

```json
{
  "type": 5001,
  "version": 1,
  "id": "1234567890123456789",
  "attrs": { "name": "my-agent", "status": "active" }
}
```

Guidance

* Use for read-modify-write to obtain the latest `version`
* Expect `404` for misses rather than empty payloads

Errors 401, 403, 400, 404, 500

---

### Upsert Object

`PUT /api/objects/:tenantId`

Create or update an object.

Body

```json
{
  "type": 5001,
  "id": "0",
  "version": 0,
  "attrs": { "name": "agent", "status": "active" }
}
```

Behavior

* Create when `id` omitted or `"0"`
* Update when `id` present and `version` matches current row
* On success returns generated or confirmed `id`

201

```json
{ "success": true, "id": "1234567890123456789" }
```

Guidance

* Treat `40001` DB conflicts as `500` at the HTTP layer here, so implement client retries with fresh `version`
* Never assume `id` reuse after delete

Errors 401, 403, 400, 500

---

### Delete Object

`DELETE /api/objects/:tenantId/:otype/:id`

Hard delete object and all its associations.

200

```json
{ "success": true }
```

or

```json
{ "success": false, "message": "Object may not have existed" }
```

Guidance

* Operation is idempotent
* Plan for eventual re-create at a different `id`

Errors 401, 403, 400, 500

---

### List Associations

`GET /api/objects/:tenantId/associations/:type/:sourceId`

Stream associations from a source node, filtered and paged.

Query

* `targetId` bigint string optional exact filter
* `after` bigint string cursor on `position`
* `limit` int default 50, max 1000

200

```json
{
  "count": 1,
  "associations": [
    {
      "type": "edge.monitor",
      "sourceId": "101",
      "targetId": "202",
      "time": "1756515600000",
      "position": "1756515600000",
      "attrs": { "role": "author" }
    }
  ]
}
```

Guidance

* Ordering defined by `position DESC` in index, but API uses `after` as a monotonic cursor for forward paging patterns
* Use wall-clock `time` for temporal semantics and `position` for strict ordering
* `type` must pass `isUrlSafe`; non-conforming values return `400`

Errors 401, 403, 400, 500

---

### Create Association

`PUT /api/objects/:tenantId/associations`

Create or overwrite a directed edge.

Body

```json
{
  "type": "pipeline.step",
  "sourceId": "12345",
  "targetId": "67890",
  "time": "1756515600000",
  "position": "1756515600000",
  "attrs": {}
}
```

Behavior

* Idempotent on PK `(tenant,type,sourceId,targetId)`
* Existing edge gets `time`, `position`, `attrs` replaced

201

```json
{ "success": true }
```

Guidance

* Ensure `type` is URL-safe before sending
* Choose `position` from a monotonic sequence generator to avoid collisions

Errors 401, 403, 400, 500

---

### Delete Association

`DELETE /api/objects/:tenantId/associations/:type/:sourceId/:targetId`

Remove a single edge.

200

```json
{ "success": true }
```

Guidance

* Idempotent delete
* Recreate by calling the PUT again with desired `time` and `position`

Errors 401, 403, 400, 500

---

## Client patterns

Safe upsert loop

1. `GET object`
2. Modify `attrs`
3. `PUT` with unchanged `id` and fetched `version`
4. On `500`, re-read and retry if policy allows

Paging associations

* Fetch first page with `limit`
* Use the last item’s `position` for the next call’s `after`
* Stop when `count` returns 0

Validation hygiene

* Encode all bigint fields as strings
* Pre-validate association `type` against your URL-safe regex before calling
* Keep `attrs` as a plain object only

Operational

* All errors are JSON with `error` or `{success:false,...}` bodies
* Logs are structured and route-scoped
* Behavior is consistent on PostgreSQL ≥12 and YugabyteDB ≥2.17

Backward-compat notes

* New `isUrlSafe` check rejects previously accepted association types that included spaces or unsafe characters
* BigInt polyfill only affects serialization and matches the documented contract that all bigint outputs are strings
