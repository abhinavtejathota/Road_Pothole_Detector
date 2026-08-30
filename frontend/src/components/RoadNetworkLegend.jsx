import { ROAD_CLASS, ASSIGNMENT_COLOR, COMPLETED_COLOR } from "../data/surveyConstants";

const POTHOLE_COLORS = { High: "#b3261e", Medium: "#c98a1a", Low: "#1e7a4c" };
const SHORT_LABEL = { nh: "NH", sh: "SH", mdr: "MDR", other: "Local" };

export default function RoadNetworkLegend({
  showPotholes = false,
  showAssignment = false,
  showCompleted = false,
}) {
  const roads = Object.entries(ROAD_CLASS).sort((a, b) => a[1].order - b[1].order);

  return (
    <div className="road-network-legend" aria-label="Map legend">
      {roads.map(([key, { color, label }]) => (
        <span key={key} className="survey-legend-item" title={label}>
          <i style={{ background: color }} />
          {SHORT_LABEL[key] || label}
        </span>
      ))}
      {showAssignment && (
        <span className="survey-legend-item" title="Today's generated assignment">
          <i style={{ background: ASSIGNMENT_COLOR }} />
          Assigned today
        </span>
      )}
      {showCompleted && (
        <span className="survey-legend-item" title="Historically completed roads">
          <i style={{ background: COMPLETED_COLOR }} />
          Completed
        </span>
      )}
      {showPotholes && Object.entries(POTHOLE_COLORS).map(([severity, color]) => (
        <span key={severity} className="survey-legend-item" title={`Pothole · ${severity}`}>
          <i style={{ background: color, borderRadius: "50%" }} />
          Pothole {severity}
        </span>
      ))}
    </div>
  );
}
