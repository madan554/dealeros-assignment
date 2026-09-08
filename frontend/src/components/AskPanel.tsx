import { useState } from "react";
import { askQuestion, type AskResponse } from "../api";

const SUGGESTIONS = [
  "How many exceptions are there?",
  "Give me a breakdown by reason code",
  "Which location has the most exceptions?",
  "What is the total value at stake?",
  "What is the biggest difference?",
  // The two below are meant to be refused, and are here so the refusal path
  // is one click away rather than something you have to think up.
  "What commission is owed on these records?",
  "How many exceptions does ORG-B have?",
];

function Figures({ response }: { response: AskResponse }) {
  return (
    <ul className="figures">
      {response.figures.map((figure, index) => (
        <li key={index}>
          <div className="figure-head">
            <span className="figure-label">{figure.label}</span>
            <span className="figure-value">
              {figure.unit === "currency"
                ? Number(figure.value).toLocaleString(undefined, {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })
                : figure.value}
            </span>
          </div>
          <div className="citations">
            <span className="muted">from </span>
            {figure.citations.map((citation) => (
              <code key={citation.exception_id} title={citation.reason_code}>
                {citation.record_ref}
                {citation.contribution !== undefined && (
                  <span className="contribution"> {Number(citation.contribution).toFixed(2)}</span>
                )}
              </code>
            ))}
            {figure.citations.length === 0 && <span className="muted">no rows</span>}
          </div>
          {figure.citation_note && <p className="muted">{figure.citation_note}</p>}
        </li>
      ))}
    </ul>
  );
}

export function AskPanel({ token, orgId }: { token: string; orgId: string }) {
  const [question, setQuestion] = useState("");
  const [response, setResponse] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(text: string) {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setResponse(await askQuestion(token, text));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The question failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="ask">
      <h2>Ask about {orgId}'s exceptions</h2>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          run(question);
        }}
      >
        <input
          value={question}
          placeholder="e.g. how many exceptions are missing from system b?"
          onChange={(event) => setQuestion(event.target.value)}
        />
        <button type="submit" disabled={busy}>
          {busy ? "Asking…" : "Ask"}
        </button>
      </form>

      <div className="suggestions">
        {SUGGESTIONS.map((suggestion) => (
          <button
            key={suggestion}
            className="link"
            onClick={() => {
              setQuestion(suggestion);
              run(suggestion);
            }}
          >
            {suggestion}
          </button>
        ))}
      </div>

      {error && <p className="error">{error}</p>}

      {response && (
        <div className={response.answered ? "answer" : "answer refused"}>
          {response.answered ? (
            <>
              <p className="answer-text">{response.answer}</p>
              <Figures response={response} />
            </>
          ) : (
            <>
              <p className="answer-text">
                <strong>Cannot answer.</strong> {response.refusal?.message}
              </p>
              <p className="muted">Reason: {response.refusal?.code}</p>
            </>
          )}
          <details>
            <summary>
              How this was produced (planner: {response.planner})
            </summary>
            <p className="muted">{String(response.grounding.note ?? "")}</p>
            <pre>{JSON.stringify(response.plan, null, 2)}</pre>
          </details>
        </div>
      )}
    </section>
  );
}
