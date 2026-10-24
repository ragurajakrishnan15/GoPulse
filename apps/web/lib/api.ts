export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export type Params = Record<string, string | number | boolean | null | undefined>;

export class ApiError extends Error {
  status: number;
  details?: { field: string; message: string }[];
  retryAfter?: number;
  constructor(status: number, message: string, details?: ApiError["details"], retryAfter?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.details = details;
    this.retryAfter = retryAfter;
  }
  /** Message suitable for showing to a visitor. */
  get friendly(): string {
    if (this.status === 0) return "The analytics API is unreachable. Please try again in a moment.";
    if (this.status === 422 && this.details?.length)
      return `${this.message}: ${this.details.map((d) => `${d.field} - ${d.message}`).join("; ")}`;
    if (this.status >= 500) return "The service had a problem answering this request.";
    return this.message;
  }
}

export function buildQuery(params?: Params): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { ...init, headers: { Accept: "application/json", ...(init?.headers ?? {}) } });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "Network error");
  }
  if (!res.ok) {
    let message = res.statusText || "Request failed";
    let details: ApiError["details"];
    try {
      const body = await res.json();
      message = body?.error?.message ?? message;
      details = body?.error?.details;
    } catch {
      /* non-JSON error body */
    }
    const ra = res.headers.get("retry-after");
    throw new ApiError(res.status, message, details, ra ? Number(ra) : undefined);
  }
  return (await res.json()) as T;
}

export const apiGet = <T,>(path: string, params?: Params, signal?: AbortSignal) =>
  request<T>(`${API_URL}${path}${buildQuery(params)}`, { signal });

export const apiPost = <T,>(path: string, body: unknown, signal?: AbortSignal) =>
  request<T>(`${API_URL}${path}`, {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
    signal,
  });
