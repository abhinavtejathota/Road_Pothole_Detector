import { useEffect, useState } from "react";
import { usePageTitle } from "../../hooks/usePageTitle";
import Loader from "../../components/Loader";
import { reporterApi } from "../../reporter/api";

/**
 * Butterfly workflow:
 *   Submitted → (review) → Accepted path | Rejected path
 * Accepted continues: Work order → In progress → Resolved → Closed
 */
const ACCEPT_STEPS = [
  { status: "WorkOrder_Created", label: "Work order created" },
  { status: "In_Progress", label: "In progress" },
  { status: "Resolved", label: "Resolved" },
  { status: "Closed", label: "Closed" },
];

const ACCEPT_INDEX = {
  Verified: -1,
  WorkOrder_Created: 0,
  In_Progress: 1,
  Resolved: 2,
  Closed: 3,
};

function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function statusLabel(status) {
  if (!status) return "—";
  if (status === "Verified") return "Accepted";
  return status.replace(/_/g, " ");
}

function Dot({ state, num }) {
  return (
    <span className="reporter-workflow-dot" aria-hidden="true">
      {state === "done" ? (
        <svg viewBox="0 0 16 16" width="12" height="12">
          <path
            fill="currentColor"
            d="M6.5 11.5 3 8l1.2-1.2 2.3 2.3 5.3-5.3L13 5.2z"
          />
        </svg>
      ) : state === "rejected" ? (
        <span className="reporter-workflow-x">×</span>
      ) : (
        <span className="reporter-workflow-num">{num}</span>
      )}
    </span>
  );
}

function Pipe({ filled, flowing, rejected }) {
  return (
    <div
      className={[
        "reporter-workflow-pipe",
        filled ? "is-filled" : "",
        flowing ? "is-flowing" : "",
        rejected ? "is-rejected" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      aria-hidden="true"
    >
      <span className="reporter-workflow-pipe-water" />
    </div>
  );
}

function WorkflowProgress({ status, rejectionRemark }) {
  const rejected = status === "Rejected";
  const accepted =
    status === "Verified" ||
    status === "WorkOrder_Created" ||
    status === "In_Progress" ||
    status === "Resolved" ||
    status === "Closed";
  const pendingReview = status === "Submitted";
  const fullyDone = status === "Closed";
  const acceptThrough = ACCEPT_INDEX[status] ?? -2;

  const submittedState = "done";
  const acceptedState = accepted ? "done" : "pending";
  const rejectedState = rejected ? "rejected" : "pending";

  return (
    <div
      className={[
        "reporter-workflow",
        "reporter-workflow-butterfly",
        fullyDone ? "is-complete" : "",
        rejected ? "is-rejected" : "",
        accepted ? "is-accepted" : "",
        pendingReview ? "is-pending-review" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      aria-label="Complaint progress"
    >
      <div className={`reporter-workflow-step is-${submittedState}`}>
        <div className="reporter-workflow-row">
          <Dot state={submittedState} num={1} />
          <span className="reporter-workflow-label">Submitted</span>
        </div>
        <Pipe filled={accepted || rejected} flowing={pendingReview} />
      </div>

      <div className="reporter-bf-fork" aria-hidden="true">
        <div className="reporter-bf-stem" />
        <div
          className={[
            "reporter-bf-arms",
            accepted ? "leans-accept" : "",
            rejected ? "leans-reject" : "",
          ]
            .filter(Boolean)
            .join(" ")}
        />
      </div>

      <div className="reporter-bf-branches">
        <div
          className={`reporter-bf-branch reporter-bf-reject${
            rejected ? " is-active" : ""
          }${accepted ? " is-dim" : ""}`}
        >
          <div className={`reporter-workflow-step is-${rejectedState}`}>
            <div className="reporter-workflow-row">
              <Dot state={rejectedState} num="✕" />
              <span className="reporter-workflow-label">Rejected</span>
            </div>
          </div>
          {rejected && (
            <>
              <p className="reporter-workflow-reject-note">
                Unwanted / invalid — closed by admin
              </p>
              {rejectionRemark && (
                <p className="reporter-reject-remark">
                  <strong>Remark:</strong> {rejectionRemark}
                </p>
              )}
            </>
          )}
          {pendingReview && (
            <p className="reporter-bf-hint">If admin rejects</p>
          )}
        </div>

        <div
          className={`reporter-bf-branch reporter-bf-accept${
            accepted ? " is-active" : ""
          }${rejected ? " is-dim" : ""}`}
        >
          <div className={`reporter-workflow-step is-${acceptedState}`}>
            <div className="reporter-workflow-row">
              <Dot state={acceptedState} num={2} />
              <span className="reporter-workflow-label">Accepted</span>
            </div>
            {(accepted || pendingReview) && !rejected && (
              <Pipe
                filled={acceptThrough >= 0}
                flowing={status === "Verified"}
              />
            )}
          </div>

          {rejected ? (
            <p className="reporter-bf-hint">Accepted path not used</p>
          ) : (
            <ol className="reporter-workflow-steps reporter-bf-accept-steps">
              {ACCEPT_STEPS.map((step, index) => {
                const done = acceptThrough >= index;
                const current =
                  !fullyDone && accepted && acceptThrough === index - 1;
                const state = done ? "done" : current ? "current" : "pending";
                const showPipe = index < ACCEPT_STEPS.length - 1;
                const pipeFilled = acceptThrough > index;
                const pipeFlowing = !fullyDone && acceptThrough === index;

                return (
                  <li
                    key={step.status}
                    className={`reporter-workflow-step is-${state}`}
                  >
                    <div className="reporter-workflow-row">
                      <Dot state={state} num={index + 3} />
                      <span className="reporter-workflow-label">{step.label}</span>
                    </div>
                    {showPipe && (
                      <Pipe filled={pipeFilled} flowing={pipeFlowing} />
                    )}
                  </li>
                );
              })}
            </ol>
          )}

          {pendingReview && (
            <p className="reporter-bf-hint">If admin accepts</p>
          )}
          {fullyDone && (
            <p className="reporter-workflow-complete-note">
              Complaint closed — workflow complete
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export default function TrackComplaint() {
  usePageTitle("Track complaint");
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const out = await reporterApi.trackComplaints();
        if (!cancelled) setItems(out.complaints || []);
      } catch (err) {
        if (!cancelled) setError(err.message || "Could not load complaints");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="reporter-page">
      <div className="reporter-hero">
        <h2>Track complaint</h2>
        <p>
          View status of complaints you have filed. After review, admin may{" "}
          <strong>accept</strong> (continue repair flow) or <strong>reject</strong>{" "}
          (unwanted reports).
        </p>
      </div>

      {loading && <Loader label="Loading complaints" />}
      {error && <div className="alert alert-error">{error}</div>}

      {!loading && !error && items.length === 0 && (
        <div className="reporter-empty card">
          <p>
            No complaints yet. Use <strong>File complaint</strong> to report a road
            issue.
          </p>
        </div>
      )}

      {!loading && items.length > 0 && (
        <div className="reporter-track-list">
          {items.map((c) => (
            <article key={c.id} className="reporter-track-card card">
              <div className="reporter-track-head">
                <strong>{c.tracking_number}</strong>
                <span
                  className={`reporter-status reporter-status-${(c.status || "")
                    .toLowerCase()
                    .replace(/_/g, "-")}`}
                >
                  {statusLabel(c.status)}
                </span>
              </div>
              <p className="reporter-track-meta">
                {c.defect_type} · {formatDate(c.created_at)}
              </p>
              <WorkflowProgress
                status={c.status}
                rejectionRemark={c.rejection_remark}
              />
              {c.description && (
                <p className="reporter-track-desc">{c.description}</p>
              )}
              {c.s3_url && (
                <a
                  href={c.s3_url}
                  target="_blank"
                  rel="noreferrer"
                  className="reporter-track-media-link"
                >
                  View uploaded media
                </a>
              )}
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
