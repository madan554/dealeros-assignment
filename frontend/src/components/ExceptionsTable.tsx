import { Fragment, useState } from "react";
import {
  fetchExceptionDetail,
  type ExceptionDetail,
  type ExceptionRow,
} from "../api";

function money(value: string | null) {
  if (value === null) return <span className="muted">—</span>;
  const number = Number(value);
  return (
    <span className={number < 0 ? "negative" : undefined}>
      {number.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })}
    </span>
  );
}

/** Both systems' raw rows, so the reader can check the exception themselves. */
function Detail({ detail }: { detail: ExceptionDetail }) {
  const { exception, system_a_record, system_b_entries } = detail;
  return (
    <div className="detail">
      <p className="what-to-do">
        <strong>What to do:</strong> {exception.what_to_do}
      </p>
      <div className="detail-columns">
        <div>
          <h4>System A</h4>
          {system_a_record ? (
            <table className="kv">
              <tbody>
                <tr>
                  <th>record_id</th>
                  <td>{system_a_record.record_id}</td>
                </tr>
                <tr>
                  <th>location_id</th>
                  <td>{system_a_record.location_id}</td>
                </tr>
                <tr>
                  <th>event_date</th>
                  <td>{system_a_record.event_date}</td>
                </tr>
                <tr>
                  <th>base_value</th>
                  <td>{system_a_record.base_value}</td>
                </tr>
                <tr>
                  <th>adjustment</th>
                  <td>{system_a_record.adjustment}</td>
                </tr>
                <tr>
                  <th>total_value</th>
                  <td>
                    <strong>{system_a_record.total_value}</strong>
                  </td>
                </tr>
                <tr>
                  <th>state</th>
                  <td>{system_a_record.state}</td>
                </tr>
                <tr>
                  <th>csv row</th>
                  <td>{system_a_record.source_row_number}</td>
                </tr>
              </tbody>
            </table>
          ) : (
            <p className="muted">No System A record exists for this reference.</p>
          )}
        </div>
        <div>
          <h4>System B ({system_b_entries.length})</h4>
          {system_b_entries.length === 0 && (
            <p className="muted">System B has no entry for this record.</p>
          )}
          {system_b_entries.map((entry) => (
            <table className="kv" key={entry.entry_id}>
              <tbody>
                <tr>
                  <th>entry_id</th>
                  <td>{entry.entry_id}</td>
                </tr>
                <tr>
                  <th>record_ref</th>
                  <td>
                    {entry.reference_was_normalised ? (
                      <>
                        <code>{entry.raw_record_ref}</code> read as{" "}
                        <strong>{entry.record_ref}</strong>
                      </>
                    ) : (
                      entry.record_ref
                    )}
                  </td>
                </tr>
                <tr>
                  <th>location_id</th>
                  <td>{entry.location_id}</td>
                </tr>
                <tr>
                  <th>recorded_on</th>
                  <td>{entry.recorded_on}</td>
                </tr>
                <tr>
                  <th>value</th>
                  <td>
                    {entry.amount_format_was_normalised ? (
                      <>
                        <code>{entry.raw_value}</code> read as{" "}
                        <strong>{entry.value}</strong>
                      </>
                    ) : (
                      <strong>{entry.value ?? "(blank)"}</strong>
                    )}
                  </td>
                </tr>
                <tr>
                  <th>label</th>
                  <td>{entry.label}</td>
                </tr>
                <tr>
                  <th>csv row</th>
                  <td>{entry.source_row_number}</td>
                </tr>
              </tbody>
            </table>
          ))}
        </div>
      </div>
      <details>
        <summary>Machine detail</summary>
        <pre>{JSON.stringify(exception.detail, null, 2)}</pre>
      </details>
    </div>
  );
}

type Props = {
  rows: ExceptionRow[];
  token: string;
  ordering: string;
  onOrderingChange: (ordering: string) => void;
  highlightIds?: number[];
};

const COLUMNS: { key: string; label: string; sortable: boolean }[] = [
  { key: "record_ref", label: "Record", sortable: true },
  { key: "reason_code", label: "Reason", sortable: true },
  { key: "location_id", label: "Location", sortable: true },
  { key: "event_date", label: "Date", sortable: true },
  { key: "system_a", label: "System A", sortable: false },
  { key: "system_b", label: "System B", sortable: false },
  { key: "difference", label: "Difference", sortable: true },
  { key: "summary", label: "What is wrong", sortable: false },
];

export function ExceptionsTable({
  rows,
  token,
  ordering,
  onOrderingChange,
  highlightIds = [],
}: Props) {
  const [openId, setOpenId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ExceptionDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  async function toggle(row: ExceptionRow) {
    if (openId === row.id) {
      setOpenId(null);
      return;
    }
    setOpenId(row.id);
    setDetail(null);
    setDetailError(null);
    try {
      setDetail(await fetchExceptionDetail(token, row.id));
    } catch (caught) {
      setDetailError(caught instanceof Error ? caught.message : "Could not load detail");
    }
  }

  function sort(key: string) {
    onOrderingChange(ordering === key ? `-${key}` : key);
  }

  if (rows.length === 0) {
    return <p className="empty">No exceptions match these filters.</p>;
  }

  return (
    <table className="exceptions">
      <thead>
        <tr>
          {COLUMNS.map((column) => (
            <th
              key={column.key}
              onClick={column.sortable ? () => sort(column.key) : undefined}
              className={column.sortable ? "sortable" : undefined}
            >
              {column.label}
              {ordering === column.key && " ▲"}
              {ordering === `-${column.key}` && " ▼"}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          // The expanded detail is a sibling <tr>, so each row renders as a
          // keyed fragment rather than a single element.
          <Fragment key={row.id}>
            <tr
              onClick={() => toggle(row)}
              className={highlightIds.includes(row.id) ? "row cited" : "row"}
            >
              <td>
                <code>{row.record_ref}</code>
              </td>
              <td>{row.reason_label}</td>
              <td title={row.location_name}>{row.location_id}</td>
              <td>{row.event_date ?? <span className="muted">—</span>}</td>
              <td className="number">{money(row.system_a_amount)}</td>
              <td className="number">{money(row.system_b_amount)}</td>
              <td className="number">{money(row.difference)}</td>
              <td className="summary">{row.summary}</td>
            </tr>
            {openId === row.id && (
              <tr className="detail-row">
                <td colSpan={COLUMNS.length}>
                  {detailError && <p className="error">{detailError}</p>}
                  {!detail && !detailError && <p className="muted">Loading…</p>}
                  {detail && <Detail detail={detail} />}
                </td>
              </tr>
            )}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}
