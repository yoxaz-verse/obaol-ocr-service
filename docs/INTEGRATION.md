# OBAOL integration

Call OCR only from the main backend. Never expose its URL or API key to browsers.

```ts
type OcrJob = { id: string; status: "queued"|"processing"|"completed"|"failed"|"canceled"|"expired"; result?: unknown; errorCode?: string };

const base = process.env.OCR_SERVICE_URL!;
const auth = { "X-API-Key": process.env.OCR_SERVICE_API_KEY! };

export async function submitOcr(file: Blob, name: string, documentRevisionId: string) {
  const form = new FormData();
  form.append("file", file, name);
  const useAsync = file.size > 3 * 1024 * 1024 || file.type === "application/pdf";
  const path = useAsync ? "/v1/jobs" : "/v1/ocr";
  const response = await fetch(base + path, {
    method: "POST", body: form,
    headers: { ...auth, ...(useAsync ? { "Idempotency-Key": documentRevisionId } : {}) },
    signal: AbortSignal.timeout(useAsync ? 30_000 : 130_000),
  });
  if (!response.ok) throw await toOcrError(response);
  return { asynchronous: useAsync, payload: await response.json() };
}

async function pollJob(id: string): Promise<OcrJob> {
  for (const delay of [2, 4, 8, 10, 10, 10, 10, 10, 10, 10]) {
    const response = await fetch(`${base}/v1/jobs/${id}`, { headers: auth, signal: AbortSignal.timeout(10_000) });
    if (!response.ok) throw await toOcrError(response);
    const state = await response.json() as OcrJob;
    if (["completed", "failed", "canceled", "expired"].includes(state.status)) return state;
    await new Promise(resolve => setTimeout(resolve, delay * 1000 + Math.random() * 500));
  }
  throw new Error("OCR polling deadline exceeded");
}

async function toOcrError(response: Response) {
  const body = await response.json().catch(() => ({}));
  return Object.assign(new Error(body.detail ?? `OCR HTTP ${response.status}`), {
    code: body.code ?? "OCR_UNAVAILABLE", retryable: [429, 503, 504].includes(response.status)
  });
}
```

Store returned OCR output, confidence, warnings, and provenance on OBAOL's extraction-review record. Do not use the OCR job state as the trade document lifecycle. Never automatically issue, verify, accept, or send a document from OCR. On any terminal failure, keep the original OBAOL upload and open manual review.

Retry 429/503/504 with capped exponential backoff and the same idempotency key. Do not retry 400/413/415/422 until the input changes. The OBAOL backend should stop polling after its own bounded deadline; the OCR job remains queryable until TTL expiry.
