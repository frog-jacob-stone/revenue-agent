import { ApiError, authedFetch } from '../api';

/**
 * POST a JSON body and save the binary response as a file.
 *
 * Beside `apiFetch` rather than inside it: that helper ends in `res.json()`,
 * and everything here is about the response *not* being JSON — the blob, the
 * filename, the headers the server sends alongside it.
 *
 * Two things worth knowing about the headers:
 *
 * - `Content-Disposition` and `X-Contract-Review-Fields` are only readable
 *   because `expose_headers` names them in the CORS middleware. Without that
 *   the browser receives them and hides them from `fetch` — they are absent
 *   from `res.headers` with no error anywhere. `fallbackFilename` covers the
 *   case where that regresses, so a download still lands with a sane name
 *   instead of being called "download".
 * - The error path reads the body as JSON, because a failure from these
 *   endpoints is an ordinary FastAPI `detail` even though success is binary.
 */
export interface DownloadResult {
  filename: string;
  /** Fields the server marked `[REVIEW: …]`, from the response header. */
  reviewFields: string[];
}

export async function downloadJsonPost(
  path: string,
  body: unknown,
  fallbackFilename: string,
): Promise<DownloadResult> {
  const res = await authedFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new ApiError(res.status, (payload as { detail?: unknown }).detail);
  }

  const blob = await res.blob();
  const filename = filenameFrom(res.headers.get('Content-Disposition'), fallbackFilename);
  const reviewFields = (res.headers.get('X-Contract-Review-Fields') ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);

  saveBlob(blob, filename);
  return { filename, reviewFields };
}

/** `attachment; filename="acme-tm-agreement-2026-09-08.docx"` → the filename. */
export function filenameFrom(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const quoted = /filename="([^"]+)"/.exec(header);
  if (quoted) return quoted[1];
  const bare = /filename=([^;]+)/.exec(header);
  return bare ? bare[1].trim() : fallback;
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  // Firefox requires the anchor to be in the document for a synthetic click to
  // trigger a download; Chrome and Safari do not care.
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoking immediately cancels the download in Safari, which reads the blob
  // asynchronously after the click returns.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
