# Privacy and consent model

KnowledgeDebt is privacy-conscious and self-hostable. It is not “offline-only,” and it never treats self-hosting as permission to process other people's classroom data.

## Legal and social responsibility

Before recording or uploading a class, follow applicable law, university policy, course rules, copyright restrictions, and the reasonable expectations of teachers and classmates. Obtain permission when required. Do not upload private notes or copyrighted course material to a server or provider you are not authorized to use.

## Operation-specific consent

Before an external provider call, the API returns a consent manifest containing:

- the exact operation;
- every provider involved;
- the exact resource IDs, names, and types in scope;
- what derived or original data will be sent;
- what will not be sent;
- whether explicit confirmation is required.

The confirmation flag applies to one request or one job. It is stored with job payload metadata for auditability but does not become a blanket preference.

| Operation | May send | Does not send by design |
| --- | --- | --- |
| Analysis | Session title/notes, retrieved transcript segments, retrieved document chunks, retrieval query when external embeddings are selected | original audio/video binaries, local paths, unselected chunks |
| Assessment / answer evaluation | question, answer, rubric on the server side, relevant Knowledge Points, retrieved evidence | unrelated Sessions, original media binaries, local paths |
| Transcription | one selected original audio/video object, filename and MIME type | other Session resources, course history, local paths |
| Indexing | text from chunks of the listed resources | original documents, media binaries, unrelated resources, local paths |

The default local hash embedding provider avoids external indexing and query calls entirely. If external embeddings are configured, upload only extracts and stores chunks; it does not call the provider automatically.

## Evidence minimization and validation

Retrieval uses two policies. Reconstruction prefers classroom and official Session evidence; learning retrieval may prefer official textbooks and broader course context. Only selected resources and chunks are attached to a provider request.

Provider output is untrusted. The service rejects source references unless they resolve to evidence actually supplied for that operation:

- transcript timestamps must match a stored segment's global time range;
- PDF page numbers and PPT slide numbers must exist in extracted content;
- chunk IDs must belong to the cited resource;
- reconstruction timeline ranges must match a cited transcript segment.

Supplementary sources can support an explanation but cannot be promoted to proof of classroom coverage.

## Secrets and access

- Provider API keys, database credentials, and `KNOWLEDGEDEBT_ACCESS_TOKEN` live in server environment variables.
- The Next.js same-origin proxy adds the optional bearer token server-side.
- The backend never returns provider keys.
- `/health` is public; other endpoints require the token when one is configured.
- The token model is intended for one self-hosted user. Hosted multi-user deployments need separate identity, tenant isolation, authorization, rate limiting, audit logs, and deletion workflows.

## Storage and deletion expectations

Local uploads, derived PDF images, transcript text, chunks, assessments, and evidence remain in the configured data store until the operator deletes them. S3-compatible deployments should use private buckets, server-side encryption, lifecycle policies, and restricted credentials.

The current `0.x` API does not yet expose a polished end-user deletion workflow. Operators must therefore treat filesystem/bucket and database retention as an explicit deployment responsibility, back them up together, and honor deletion requests across both stores.

## Logging

Application code does not intentionally log API keys or resource bodies. Reverse proxies, provider SDKs, observability agents, and infrastructure may have their own logging behavior; review those systems before processing sensitive course material.
