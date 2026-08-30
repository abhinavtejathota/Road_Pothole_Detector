import { motion } from "framer-motion";

export default function EmptyState({ message = "Nothing here yet", hint }) {
  return (
    <motion.div
      className="empty-state"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
    >
      <div className="empty-state-icon">◇</div>
      <p>{message}</p>
      {hint && <span>{hint}</span>}
    </motion.div>
  );
}
