import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";

export default function Modal({ open, onClose, title, children, className = "" }) {
  if (typeof document === "undefined") return null;

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          className="modal-backdrop"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
        >
          <motion.div
            className={`modal${className ? ` ${className}` : ""}`}
            role="dialog"
            aria-modal="true"
            aria-labelledby={title ? "modal-title" : undefined}
            onClick={(e) => e.stopPropagation()}
            initial={{ opacity: 0, scale: 0.94, y: 12 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 8 }}
            transition={{ type: "spring", damping: 24, stiffness: 320 }}
          >
            <div className="modal-header">
              {title ? (
                <h2 id="modal-title" className="modal-title">
                  {title}
                </h2>
              ) : (
                <span className="modal-title-spacer" aria-hidden="true" />
              )}
              {typeof onClose === "function" && (
                <button
                  type="button"
                  className="modal-close-x"
                  onClick={onClose}
                  aria-label="Close"
                >
                  ×
                </button>
              )}
            </div>
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
