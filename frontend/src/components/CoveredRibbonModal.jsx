import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import RoadSurveyMap from "./RoadSurveyMap";
import { COMPLETED_COLOR, ASSIGNMENT_COLOR, STATE_META } from "../data/surveyConstants";
import "./CoveredRibbonModal.css";
function isCoveredFeature(f) {
  const p = f?.properties || {};
  const st = String(p.status || "");
  const kind = String(p.route_kind || "");
  return st === "completed" || st === "verified" || kind === "covered";
}

function isContinuousFeature(f) {
  return String(f?.properties?.route_kind || "") === "continuous";
}

/**
 * After a successful Capture upload: paint only GPS-covered pieces indigo → asphalt grey.
 * The continuous assigned corridor stays indigo (same as admin Tracking).
 */
export default function CoveredRibbonModal({
  open,
  features = [],
  onClose,
  message = "Roads sealed — they stay covered until an admin reopens the district.",
}) {
  const [sealedCount, setSealedCount] = useState(0);
  const [done, setDone] = useState(false);

  const coveredFeatures = useMemo(
    () => features.filter(isCoveredFeature),
    [features],
  );
  const continuousFeatures = useMemo(
    () => features.filter(isContinuousFeature),
    [features],
  );
  // Prefer continuous + covered; if no continuous (legacy), show only covered / assigned pieces
  const mapBase = useMemo(() => {
    if (continuousFeatures.length || coveredFeatures.length) {
      return [...continuousFeatures, ...coveredFeatures];
    }
    return features;
  }, [continuousFeatures, coveredFeatures, features]);

  const total = coveredFeatures.length;

  useEffect(() => {
    if (!open) {
      setSealedCount(0);
      setDone(false);
      return undefined;
    }
    if (!total) {
      setSealedCount(0);
      setDone(true);
      return undefined;
    }
    setSealedCount(0);
    setDone(false);
    let i = 0;
    const stepMs = Math.max(35, Math.min(90, Math.floor(2200 / total)));
    const t = setInterval(() => {
      i += 1;
      setSealedCount(i);
      if (i >= total) {
        clearInterval(t);
        setDone(true);
      }
    }, stepMs);
    return () => clearInterval(t);
  }, [open, total]);

  const animatedFeatures = useMemo(() => {
    let coveredIdx = 0;
    return mapBase.map((f) => {
      const props = { ...(f.properties || {}), _drawn: true };
      if (isContinuousFeature(f)) {
        // Assigned corridor always stays indigo during the seal animation
        props.status = "assigned";
        props.route_kind = "continuous";
      } else if (isCoveredFeature(f)) {
        const reveal = coveredIdx < sealedCount;
        coveredIdx += 1;
        props.status = reveal ? "completed" : "assigned";
        props.route_kind = "covered";
      }
      return { ...f, properties: props };
    });
  }, [mapBase, sealedCount]);

  const center = useMemo(() => {
    const sk = mapBase[0]?.properties?.state_key || features[0]?.properties?.state_key;
    if (sk && STATE_META[sk]) return STATE_META[sk].center;
    return STATE_META.telangana.center;
  }, [mapBase, features]);

  if (!open) return null;

  return (
    <div className="modal-backdrop covered-ribbon-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal covered-ribbon-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="covered-ribbon-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="covered-ribbon-title" className="modal-title">Corridor sealed</h2>
        <div className="modal-body">
          <p className="muted" style={{ marginTop: 0 }}>
            {message}
          </p>
          <div className="covered-ribbon-legend">
            <span><i style={{ background: ASSIGNMENT_COLOR }} /> Assigned</span>
            <span><i style={{ background: COMPLETED_COLOR }} /> Covered</span>
            <span className="covered-ribbon-progress">
              {total ? `${Math.min(sealedCount, total)} / ${total} covered` : "No covered segments yet"}
              {done ? " · done" : " · sealing…"}
            </span>
          </div>
          <div className="covered-ribbon-map">
            {animatedFeatures.length > 0 ? (
              <RoadSurveyMap
                features={animatedFeatures}
                center={center}
                zoom={12}
                fitToFeatures
                detailMode
                height={280}
                hideLegend
                fitKey={`seal-${total}-${continuousFeatures.length}`}
              />
            ) : (
              <p className="muted" style={{ padding: "1rem", textAlign: "center" }}>
                Upload saved. Open Survey to see covered roads on the map.
              </p>
            )}
          </div>
        </div>
        <div className="modal-actions">
          <Link to="/survey" className="btn btn-primary" onClick={onClose}>
            Open Survey
          </Link>
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
