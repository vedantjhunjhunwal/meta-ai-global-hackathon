# Media retention and privacy

| Data | Stored? | Where / how long |
|------|---------|------------------|
| Uploaded video/audio | **No, by default.** Deleted right after analysis (`STETHO_RETAIN_MEDIA_HOURS=0`). | `var/media/` only while processing |
| Phone number | **No.** Replaced with HMAC-SHA256(salt, number), truncated. | Pseudonymous id in SQLite |
| Derived features, quality checks, assessment | Yes, needed for baseline comparison | SQLite `var/stetho.db` until the user deletes them |
| View fingerprint (same-spot check) | Yes: up to 300 ORB keypoint positions + binary descriptors from the first frame. **Not an image.** | Stored with the recording's features; removed by `DELETE` |
| Voice note / magnified clip | Temporarily, for delivery | `var/artifacts/`. Web demo removes them after `STETHO_ARTIFACT_TTL_HOURS` (default 24 h) |
| Technician summary of the last result | In memory only | Lost on restart |
| Language-model calls (optional) | The **text template** (no video, audio or phone number) and, only when the user sends a nameplate photo, that photo | Sent to Meta Model API under Meta's terms. Disabled when `MODEL_API_KEY` is unset |

User controls:
- WhatsApp: consent is required (`YES`) before any analysis. `DELETE` removes all machines, baselines and results linked to the number.
- Web: **Delete all my data** button (per-browser pseudonymous session).

Operator responsibilities, before any real deployment:
- Set a strong random `STETHO_ID_SALT` and keep it secret. Changing it unlinks all existing users.
- Serve over HTTPS. Restrict access to `var/`. Back up or encrypt the database at rest according to local law (e.g. India's DPDP Act 2023, GDPR where applicable).
- Videos can show people, faces and property. Tell users to record only the machine, and keep media retention at 0 unless they explicitly agree.
