import { motion } from "framer-motion";

export default function Loader({ label = "Loading", fullScreen = false }) {
  return (
    <div className={`loader-wrap${fullScreen ? " loader-full" : ""}`}>
      <motion.div
        className="loader-orbit"
        animate={{ rotate: 360 }}
        transition={{ duration: 1.2, repeat: Infinity, ease: "linear" }}
      >
        <span className="loader-dot" />
        <span className="loader-dot loader-dot-2" />
        <span className="loader-dot loader-dot-3" />
      </motion.div>
      <motion.p
        className="loader-label"
        animate={{ opacity: [0.4, 1, 0.4] }}
        transition={{ duration: 1.5, repeat: Infinity }}
      >
        {label}
      </motion.p>
    </div>
  );
}
