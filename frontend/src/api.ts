export type ExceptionRow = {
  id: number;
  record_ref: string;
  reason_code: string;
  reason_label: string;
  what_to_do: string;
  summary: string;
  location_id: string;
  location_name: string;
  event_date: string | null;
  category_code: string;
  system_a_amount: string | null;
  system_b_amount: string | null;
  difference: string | null;
  entry_ids: string[];
  detail: Record<string, unknown>;
};

export type ExceptionsResponse = {
  count: number;
  total_for_org: number;
  counts_by_reason_code: Record<string, number>;
  counts_by_location_id: Record<string, number>;
  results: ExceptionRow[];
};

export type ReasonCode = {
  code: string;
  label: string;
  what_it_means: string;
  what_to_do: string;
};

export type Location = { id: string; name: string };

export type SourceRecordA = {
  record_id: string;
  location_id: string;
  event_date: string | null;
  category_code: string;
  actor_id: string;
  base_value: string | null;
  adjustment: string | null;
  total_value: string | null;
  state: string;
  source_row_number: number;
};

export type SourceEntryB = {
  entry_id: string;
  raw_record_ref: string;
  record_ref: string;
  location_id: string;
  recorded_on: string | null;
  raw_value: string;
  value: string | null;
  label: string;
  reference_was_normalised: boolean;
  amount_format_was_normalised: boolean;
  source_row_number: number;
};

export type ExceptionDetail = {
  exception: ExceptionRow;
  system_a_record: SourceRecordA | null;
  system_b_entries: SourceEntryB[];
};

export type Citation = {
  exception_id: number;
  record_ref: string;
  reason_code: string;
  location_id: string;
  entry_ids: string[];
  contribution?: string;
};

export type Figure = {
  label: string;
  value: number | string;
  unit: string;
  /** "primary" | "breakdown" | "context" — see grounded/executor.py */
  role: string;
  citations: Citation[];
  citation_note: string;
};

export type AskResponse = {
  question: string;
  answered: boolean;
  answer: string | null;
  refusal: { code: string; message: string } | null;
  figures: Figure[];
  rows: Record<string, unknown>[];
  plan: Record<string, unknown> | null;
  planner: string;
  grounding: Record<string, unknown>;
};

export type Session = { token: string; username: string; org_id: string; org_name: string };

const TOKEN_KEY = "dealeros.session";

export function loadSession(): Session | null {
  const raw = localStorage.getItem(TOKEN_KEY);
  return raw ? (JSON.parse(raw) as Session) : null;
}

export function saveSession(session: Session) {
  localStorage.setItem(TOKEN_KEY, JSON.stringify(session));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
}

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, token: string | null, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Token ${token}` } : {}),
        ...(init?.headers ?? {}),
      },
    });
  } catch (caught) {
    // Vite's proxy turns a down backend into a 502. A direct connection
    // refusal arrives here as TypeError. Either way the user needs to know
    // the API is unreachable, not that their password is wrong.
    if (caught instanceof DOMException && caught.name === "AbortError") {
      throw caught;
    }
    throw new ApiError(
      0,
      "Cannot reach the API. Is `make backend` running on 127.0.0.1:8000?",
    );
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    if (response.status === 502 || response.status === 504) {
      detail =
        "Cannot reach the API (proxy 502). Is `make backend` running on 127.0.0.1:8000?";
    } else {
      try {
        const body = await response.json();
        detail = body.detail ?? detail;
      } catch {
        // Response had no JSON body; the status is all we have to report.
      }
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export async function login(username: string, password: string): Promise<Session> {
  return request<Session>("/api/auth/login", null, {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export type Filters = {
  reasonCodes: string[];
  locationIds: string[];
  search: string;
  ordering: string;
};

export async function fetchExceptions(
  token: string,
  filters: Filters,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams();
  filters.reasonCodes.forEach((code) => params.append("reason_code", code));
  filters.locationIds.forEach((id) => params.append("location_id", id));
  if (filters.search.trim()) params.set("search", filters.search.trim());
  if (filters.ordering) params.set("ordering", filters.ordering);
  return request<ExceptionsResponse>(`/api/exceptions?${params}`, token, { signal });
}

export async function fetchExceptionDetail(
  token: string,
  id: number,
  signal?: AbortSignal,
) {
  return request<ExceptionDetail>(`/api/exceptions/${id}`, token, { signal });
}

export async function fetchReasonCodes(token: string, signal?: AbortSignal) {
  const body = await request<{ reason_codes: ReasonCode[] }>(
    "/api/reason-codes",
    token,
    { signal },
  );
  return body.reason_codes;
}

export async function fetchLocations(token: string, signal?: AbortSignal) {
  const body = await request<{ locations: Location[] }>("/api/locations", token, {
    signal,
  });
  return body.locations;
}

export async function fetchSummary(token: string, signal?: AbortSignal) {
  return request<Record<string, any>>("/api/summary", token, { signal });
}

export async function askQuestion(token: string, question: string, signal?: AbortSignal) {
  return request<AskResponse>("/api/ask", token, {
    method: "POST",
    body: JSON.stringify({ question }),
    signal,
  });
}
