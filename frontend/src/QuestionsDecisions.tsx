import { useState } from "react";
import { AgentTieFold, useTiesOn } from "./AgentTies";
import { ErrorMessage, Markdown } from "./components";
import { useAction } from "./hooks";
import type { RoadmapDecision, RoadmapQuestion } from "./apiTypes";

/** A roadmap's questions: open ones first, then the answered ones with their answers. */
export function QuestionsSection({
  roadmapId,
  questions = [],
  disabled = false,
}: {
  roadmapId: string;
  questions?: RoadmapQuestion[];
  disabled?: boolean;
}) {
  const action = useAction();
  const [title, setTitle] = useState("");
  const [editing, setEditing] = useState<{
    id: string;
    mode: "edit" | "answer";
  } | null>(null);
  const active = questions.filter((q) => !q.archived);
  const open = active.filter((q) => q.status === "open");
  const answered = active.filter((q) => q.status === "answered");
  const ties = useTiesOn(
    "question",
    active.map((q) => q.id),
  );
  const save = (q: RoadmapQuestion, fields: Partial<RoadmapQuestion>) =>
    action
      .mutateAsync({
        path: "/questions/" + q.id,
        method: "PATCH",
        body: { revision: q.revision, ...fields },
      })
      .then(() => setEditing(null));
  const row = (q: RoadmapQuestion) =>
    editing?.id === q.id && !disabled ? (
      <QuestionForm
        key={q.id}
        question={q}
        mode={editing.mode}
        onSave={(fields) => save(q, fields)}
        onCancel={() => setEditing(null)}
      />
    ) : (
      <article className="roadmap-record" key={q.id}>
        <div className="roadmap-record-head">
          <strong>{q.title}</strong>
          {!disabled && (
            <>
              {q.status === "open" ? (
                <button
                  type="button"
                  aria-label={"Answer question " + q.title}
                  onClick={() => setEditing({ id: q.id, mode: "answer" })}
                >
                  Answer
                </button>
              ) : (
                <button
                  type="button"
                  className="quiet"
                  aria-label={"Reopen question " + q.title}
                  disabled={action.isPending}
                  onClick={() =>
                    save(q, { status: "open" }).catch(() => undefined)
                  }
                >
                  Reopen
                </button>
              )}
              <button
                type="button"
                className="quiet"
                aria-label={"Edit question " + q.title}
                onClick={() => setEditing({ id: q.id, mode: "edit" })}
              >
                Edit
              </button>
              <button
                type="button"
                className="quiet"
                aria-label={"Archive question " + q.title}
                disabled={action.isPending}
                onClick={() =>
                  action.mutate({
                    path: `/questions/${q.id}/archive`,
                    body: { revision: q.revision },
                  })
                }
              >
                Archive
              </button>
            </>
          )}
        </div>
        {q.details && <Markdown text={q.details} />}
        {q.status === "answered" && (
          <div className="roadmap-answer">
            <small>Answer</small>
            <Markdown text={q.answer} />
          </div>
        )}
        <AgentTieFold
          targetType="question"
          targetId={q.id}
          title={q.title}
          ties={ties.get(q.id)}
          disabled={disabled}
        />
      </article>
    );
  return (
    <section>
      <h3>
        Questions <span className="count">{open.length} open</span>
      </h3>
      {open.map(row)}
      {!open.length && <p className="muted">No open questions.</p>}
      {!disabled && (
        <form
          className="quick-add"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!title.trim()) return;
            await action
              .mutateAsync({
                path: "/questions",
                body: { roadmapId, title: title.trim(), details: "" },
              })
              .then(() => setTitle(""))
              .catch(() => undefined);
          }}
        >
          <input
            aria-label="New question"
            placeholder="Ask a question about this roadmap…"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={1000}
          />
          <button type="submit" disabled={action.isPending || !title.trim()}>
            Add question
          </button>
        </form>
      )}
      {answered.length > 0 && (
        <details className="answered-questions">
          <summary>Answered ({answered.length})</summary>
          {answered.map(row)}
        </details>
      )}
      <ErrorMessage error={action.error} />
    </section>
  );
}
/** Answering writes only the answer; editing changes the question, its details, and an answer already given. */
function QuestionForm({
  question,
  mode,
  onSave,
  onCancel,
}: {
  question: RoadmapQuestion;
  mode: "edit" | "answer";
  onSave: (fields: Partial<RoadmapQuestion>) => Promise<unknown>;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(question.title);
  const [details, setDetails] = useState(question.details);
  const [answer, setAnswer] = useState(question.answer);
  const answering = mode === "answer";
  const showAnswer = answering || question.status === "answered";
  const ready =
    (answering || !!title.trim()) && (!showAnswer || !!answer.trim());
  return (
    <form
      className="roadmap-record roadmap-record-form"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!ready) return;
        await onSave(
          answering
            ? { answer, status: "answered" }
            : {
                title: title.trim(),
                details,
                ...(showAnswer ? { answer } : {}),
              },
        ).catch(() => undefined);
      }}
    >
      {answering ? (
        <div>
          <strong>{question.title}</strong>
          {question.details && <Markdown text={question.details} />}
        </div>
      ) : (
        <>
          <input
            aria-label="Question"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={1000}
            required
          />
          <textarea
            aria-label="Question details"
            placeholder="Details, in Markdown (optional)…"
            value={details}
            onChange={(e) => setDetails(e.target.value)}
          />
        </>
      )}
      {showAnswer && (
        <textarea
          aria-label="Answer"
          placeholder="The answer, in Markdown…"
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          autoFocus={answering}
        />
      )}
      <div className="toolbar">
        <button type="submit" className="primary" disabled={!ready}>
          {answering ? "Save answer" : "Save question"}
        </button>
        <button type="button" className="quiet" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
/** The decisions made on a roadmap, newest first, each with why it was made. */
export function DecisionsSection({
  roadmapId,
  decisions = [],
  disabled = false,
}: {
  roadmapId: string;
  decisions?: RoadmapDecision[];
  disabled?: boolean;
}) {
  const action = useAction();
  const [editing, setEditing] = useState<string | null>(null);
  const active = decisions.filter((d) => !d.archived);
  const ties = useTiesOn(
    "decision",
    active.map((d) => d.id),
  );
  return (
    <section>
      <h3>
        Decisions <span className="count">{active.length}</span>
      </h3>
      {active.map((d) =>
        editing === d.id && !disabled ? (
          <DecisionForm
            key={d.id}
            initial={d}
            submit="Save decision"
            onSave={(fields) =>
              action
                .mutateAsync({
                  path: "/decisions/" + d.id,
                  method: "PATCH",
                  body: { revision: d.revision, ...fields },
                })
                .then(() => setEditing(null))
            }
            onCancel={() => setEditing(null)}
          />
        ) : (
          <article className="roadmap-record" key={d.id}>
            <div className="roadmap-record-head">
              <strong>{d.title}</strong>
              <small>Decided {d.decidedDate}</small>
              {!disabled && (
                <>
                  <button
                    type="button"
                    className="quiet"
                    aria-label={"Edit decision " + d.title}
                    onClick={() => setEditing(d.id)}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="quiet"
                    aria-label={"Archive decision " + d.title}
                    disabled={action.isPending}
                    onClick={() =>
                      action.mutate({
                        path: `/decisions/${d.id}/archive`,
                        body: { revision: d.revision },
                      })
                    }
                  >
                    Archive
                  </button>
                </>
              )}
            </div>
            {d.rationale && <Markdown text={d.rationale} />}
            <AgentTieFold
              targetType="decision"
              targetId={d.id}
              title={d.title}
              ties={ties.get(d.id)}
              disabled={disabled}
            />
          </article>
        ),
      )}
      {!active.length && <p className="muted">No decisions recorded yet.</p>}
      {!disabled && (
        <DecisionForm
          submit="Record decision"
          disabled={action.isPending}
          onSave={(fields) =>
            action.mutateAsync({
              path: "/decisions",
              body: { roadmapId, ...fields },
            })
          }
        />
      )}
      <ErrorMessage error={action.error} />
    </section>
  );
}
function DecisionForm({
  initial,
  submit,
  disabled = false,
  onSave,
  onCancel,
}: {
  initial?: RoadmapDecision;
  submit: string;
  disabled?: boolean;
  onSave: (fields: {
    title: string;
    rationale: string;
    decidedDate?: string;
  }) => Promise<unknown>;
  onCancel?: () => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [rationale, setRationale] = useState(initial?.rationale ?? "");
  // A new decision left undated is dated today by the server, in the configured timezone.
  const [decidedDate, setDecidedDate] = useState(initial?.decidedDate ?? "");
  const ready = !!title.trim() && !disabled;
  return (
    <form
      className={
        initial
          ? "roadmap-record roadmap-record-form"
          : "quick-add roadmap-record-form"
      }
      onSubmit={async (e) => {
        e.preventDefault();
        if (!ready) return;
        await onSave({
          title: title.trim(),
          rationale,
          ...(decidedDate ? { decidedDate } : {}),
        })
          .then(() => {
            if (!initial) {
              setTitle("");
              setRationale("");
              setDecidedDate("");
            }
          })
          .catch(() => undefined);
      }}
    >
      <div className="inline">
        <input
          aria-label={initial ? "Decision" : "New decision"}
          placeholder="What was decided…"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={1000}
          required
        />
        <input
          aria-label="Date decided"
          type="date"
          value={decidedDate}
          onChange={(e) => setDecidedDate(e.target.value)}
        />
      </div>
      <textarea
        aria-label={initial ? "Decision rationale" : "New decision rationale"}
        placeholder="Why, in Markdown (optional)…"
        value={rationale}
        onChange={(e) => setRationale(e.target.value)}
      />
      <div className="toolbar">
        <button
          type="submit"
          className={initial ? "primary" : undefined}
          disabled={!ready}
        >
          {submit}
        </button>
        {onCancel && (
          <button type="button" className="quiet" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
