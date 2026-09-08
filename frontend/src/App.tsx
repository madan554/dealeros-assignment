import { useCallback, useEffect, useMemo, useState } from "react";
import {
  clearSession,
  fetchExceptions,
  fetchLocations,
  fetchReasonCodes,
  fetchSummary,
  isAbortError,
  loadSession,
  type ExceptionsResponse,
  type Filters,
  type Location,
  type ReasonCode,
  type Session,
} from "./api";
import { AskPanel } from "./components/AskPanel";
import { ExceptionsTable } from "./components/ExceptionsTable";
import { Login } from "./components/Login";

const EMPTY_FILTERS: Filters = {
  reasonCodes: [],
  locationIds: [],
  search: "",
  ordering: "record_ref",
};

/** The disagreements the reconciliation decided were not errors.
 *
 * Shown in the UI rather than buried in a log because "we looked at this and
 * it was fine" is the other half of trusting the list above it. */
function NotErrors({ summary }: { summary: Record<string, any> }) {
  const notErrors = summary.not_errors ?? {};
  const notes: { record_ref: string; code: string; summary: string }[] = notErrors.notes ?? [];
  if (notes.length === 0) {
    return (
      <p className="muted">
        Nothing in this organisation's data was a disagreement that turned out
        to be benign.
      </p>
    );
  }
  return (
    <>
      <p className="muted">
        These are differences between the two systems that were examined and
        judged not to be errors, so they are deliberately absent from the
        exceptions list.
      </p>
      <table className="exceptions">
        <thead>
          <tr>
            <th>Record</th>
            <th>Why it is not an error</th>
          </tr>
        </thead>
        <tbody>
          {notes.map((note) => (
            <tr key={`${note.record_ref}-${note.code}`}>
              <td>
                <code>{note.record_ref}</code>
              </td>
              <td className="summary">{note.summary}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export default function App() {
  const [session, setSession] = useState<Session | null>(loadSession());
  const [data, setData] = useState<ExceptionsResponse | null>(null);
  const [reasonCodes, setReasonCodes] = useState<ReasonCode[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [summary, setSummary] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);

  const token = session?.token ?? null;

  const signOut = useCallback(() => {
    // Drop every org-scoped piece of state. Leaving Alice's location filter
    // or in-flight responses around is how Bob would either see zero rows
    // or, worse, briefly see Alice's exceptions including REC-1077.
    clearSession();
    setSession(null);
    setData(null);
    setSummary(null);
    setReasonCodes([]);
    setLocations([]);
    setError(null);
    setLoading(false);
    setFilters(EMPTY_FILTERS);
  }, []);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    Promise.all([
      fetchReasonCodes(token, controller.signal),
      fetchLocations(token, controller.signal),
      fetchSummary(token, controller.signal),
    ])
      .then(([codes, locs, sum]) => {
        setReasonCodes(codes);
        setLocations(locs);
        setSummary(sum);
      })
      .catch((caught) => {
        if (isAbortError(caught)) return;
        setError(caught instanceof Error ? caught.message : "Load failed");
      });
    return () => controller.abort();
  }, [token]);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    setLoading(true);
    fetchExceptions(token, filters, controller.signal)
      .then((body) => {
        setData(body);
        setError(null);
      })
      .catch((caught) => {
        if (isAbortError(caught)) return;
        setError(caught instanceof Error ? caught.message : "Load failed");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [token, filters]);

  const visibleReasonCodes = useMemo(() => {
    if (!data) return reasonCodes;
    // Only offer codes that actually occur for this org: a filter that can
    // only ever return nothing is noise.
    return reasonCodes.filter((code) => data.counts_by_reason_code[code.code]);
  }, [reasonCodes, data]);

  if (!session) {
    return <Login onSignedIn={setSession} />;
  }

  function toggleReason(code: string) {
    setFilters((current) => ({
      ...current,
      reasonCodes: current.reasonCodes.includes(code)
        ? current.reasonCodes.filter((item) => item !== code)
        : [...current.reasonCodes, code],
    }));
  }

  function toggleLocation(id: string) {
    setFilters((current) => ({
      ...current,
      locationIds: current.locationIds.includes(id)
        ? current.locationIds.filter((item) => item !== id)
        : [...current.locationIds, id],
    }));
  }

  const filtered = filters.reasonCodes.length > 0 || filters.locationIds.length > 0 || filters.search;

  return (
    <div className="app">
      <header>
        <div>
          <strong>Reconciliation exceptions</strong>
          <span className="muted">
            {" "}
            — {session.org_name} ({session.org_id})
          </span>
        </div>
        <div>
          <span className="muted">{session.username}</span>
          <button className="link" onClick={signOut}>
            sign out
          </button>
        </div>
      </header>

      {error && <p className="error">{error}</p>}

      <section className="filters">
        <div className="filter-group">
          <span className="filter-label">Reason code</span>
          {visibleReasonCodes.map((code) => (
            <button
              key={code.code}
              title={code.what_it_means}
              className={filters.reasonCodes.includes(code.code) ? "chip on" : "chip"}
              onClick={() => toggleReason(code.code)}
            >
              {code.label}
              <span className="chip-count">{data?.counts_by_reason_code[code.code] ?? 0}</span>
            </button>
          ))}
        </div>
        <div className="filter-group">
          <span className="filter-label">Location</span>
          {locations.map((location) => (
            <button
              key={location.id}
              className={filters.locationIds.includes(location.id) ? "chip on" : "chip"}
              onClick={() => toggleLocation(location.id)}
            >
              {location.name}
              <span className="chip-count">{data?.counts_by_location_id[location.id] ?? 0}</span>
            </button>
          ))}
        </div>
        <div className="filter-group">
          <input
            className="search"
            placeholder="Search record, summary or location"
            value={filters.search}
            onChange={(event) =>
              setFilters((current) => ({ ...current, search: event.target.value }))
            }
          />
          {filtered && (
            <button
              className="link"
              onClick={() => setFilters({ ...EMPTY_FILTERS, ordering: filters.ordering })}
            >
              clear filters
            </button>
          )}
        </div>
      </section>

      <p className="count">
        {loading ? "Loading…" : `${data?.count ?? 0} of ${data?.total_for_org ?? 0} exceptions`}
        <span className="muted"> — click a row to see what each system holds</span>
      </p>

      {token && data && (
        <ExceptionsTable
          rows={data.results}
          token={token}
          ordering={filters.ordering}
          onOrderingChange={(ordering) => setFilters((current) => ({ ...current, ordering }))}
        />
      )}

      {token && <AskPanel token={token} orgId={session.org_id} />}

      {summary && (
        <section className="not-errors">
          <h2>Not errors</h2>
          <NotErrors summary={summary} />
        </section>
      )}
    </div>
  );
}
