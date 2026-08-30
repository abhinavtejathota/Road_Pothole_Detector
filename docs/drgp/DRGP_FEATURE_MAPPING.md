# Digital Roads Governance Platform (DRGP) - Feature Mapping

This document provides a direct comparison of the features implemented in the **SmartRoad AP** project against the specifications suggested in the **DRGP** concept notes.

---

## Section 1: Features Implemented in the Current Project (Our End)

*   **YOLOv12 AI Inference Pipeline ([pothole_detector.py](file:///d:/Docs/Problem%20Solving/Forks%20/smart_road_app/pothole_detector.py))**: Core video and image inference code that extracts coordinates, frames, and classifies pothole severity based on area and position.
*   **Video Parallel Processing ([ffmpeg_accel.py](file:///d:/Docs/Problem%20Solving/Forks/smart_road_app/ffmpeg_accel.py))**: Splits large video files into concurrent chunks for faster processing on multi-core CPU/GPU systems and collapses overlapping edge detections.
*   **Dust Guard Filtering ([dust_guard.py](file:///d:/Docs/Problem%20Solving/Forks/smart_road_app/dust_guard.py))**: Post-processing heuristic model that identifies and filters out false positive dust clouds during detection.
*   **Chunked Mobile Ingest Server ([upload_app.py](file:///d:/Docs/Problem%20Solving/Forks/smart_road_app/upload_app.py))**: Separate endpoint service configured to handle sequential 60-second video stream chunk uploads with high-frequency tracking pings.
*   **Mobile Survey Client**: Expo React Native ([mobile_app](mobile_app/)) for videographers — assigned routes, GPS logging, and capture streaming.
*   **GIS Road Snap Indexing ([survey_service.py](file:///d:/Docs/Problem%20Solving/Forks/smart_road_app/routes/survey_service.py))**: Grid-based segment indices (`.snap.pkl`) that calculate sub-second snapping of GPS locations to the nearest road segment within allowed districts.
*   **Vendor and Zone Registry**: Relational database schema ([schema.sql](file:///d:/Docs/Problem%20Solving/Forks/smart_road_app/schema.sql)) managing contractor information, service polygon zones (`vendor_zones`), and task assignments.
*   **Work Order Status Machine ([constants.py](file:///d:/Docs/Problem%20Solving/Forks/smart_road_app/routes/constants.py))**: A robust lifecycle for tasks (Created → Allocated → WIP → Completed → Verified/Failed) enforced on the server-side status endpoints.
*   **Geofenced Repair Validation ([validation.py](file:///d:/Docs/Problem%20Solving/Forks/smart_road_app/routes/validation.py))**: Geofences the vendor's submitted completion photo within a 30m boundary of the defect, performs EXIF timestamp validity checks, and automatically runs YOLO on the after photo.
*   **Automatic Warranty Registration**: Creates a warranty schedule (`warranty_records`) for verified repairs, tracking expiry dates and claims.
*   **Model Comparison Benchmarking ([model_bench_service.py](file:///d:/Docs/Problem%20Solving/Forks/smart_road_app/routes/model_bench_service.py))**: A web panel allowing developers to test alternate weights against production weights on raw files.

---

## Section 2: Features Suggested in Concept Documents vs. Current Status

*   **Permanent Digital Road Registry** — **Partially Implemented**
    *   *Status*: Road geometries from OpenStreetMap are indexed on-disk as GeoJSON segments, but no database mapping exists to store construction metadata (dates, costs, funding sources, and contractors).
*   **Road Observer Network** — **Implemented**
    *   *Status*: Surveyor daily assignments, maps, and field tracking are fully integrated and operational.
*   **GPS-Enabled Inspection Mobile App** — **Implemented**
    *   *Status*: Mobile clients capture and stream video chunks with concurrent GPS logs. (Offline caching is missing).
*   **Multi-Class AI Defect Detection** — **Partially Implemented**
    *   *Status*: The YOLOv12 model is trained and optimized for potholes, but does not yet detect cracks, rutting, shoulder damage, or missing road signs/markings.
*   **Road Health Index (RHI) Scoring** — **Unimplemented**
    *   *Status*: No monthly 0-100 pavement index is calculated for road IDs, nor is RHI historical trend data saved.
*   **Citizen Reporting App / Portal** — **Unimplemented**
    *   *Status*: No citizen-facing portal, database tables, or complaint endpoints exist.
*   **Citizen Complaint Tracking Workflow** — **Unimplemented**
    *   *Status*: Work orders can only be spawned from surveyor video sessions; no citizen ticket workflow (Verify → Inspect → Repair → Close) exists.
*   **Separation of RHI and Citizen Alerts** — **Unimplemented**
    *   *Status*: Because citizen alerts are missing, there is no separation in place to protect the official RHI from raw complaint noise.
*   **Dynamic Traffic Classification** — **Unimplemented**
    *   *Status*: No database attributes or services exist to manage High/Medium/Low traffic volumes or time-of-day traffic shifts.
*   **Maintenance Priority Score (MPS)** — **Unimplemented**
    *   *Status*: Report statistics use simple local pothole density sorting, but no algorithm combines RHI, Traffic, Complaints, and Warranty to rank repair prioritization.
*   **Role-Specific Contractor/Engineer KPI Dashboards** — **Partially Implemented**
    *   *Status*: Dashboards track videographer progress and video streams, but lack metrics for contractor repair speed, defect rate, and engineer response timelines.
