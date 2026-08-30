import { motion } from "framer-motion";

const page = {
  initial: { opacity: 0, y: 12, filter: "blur(4px)" },
  animate: { opacity: 1, y: 0, filter: "blur(0px)" },
  exit: { opacity: 0, y: -8, filter: "blur(4px)" },
};

export default function PageTransition({ children, className = "" }) {
  return (
    <motion.div
      className={className}
      variants={page}
      initial="initial"
      animate="animate"
      exit="exit"
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
    >
      {children}
    </motion.div>
  );
}

export const stagger = {
  animate: { transition: { staggerChildren: 0.06 } },
};

export const fadeUp = {
  initial: { opacity: 0, y: 16 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.22, 1, 0.36, 1] } },
};

export function MotionCard({ children, className = "card", delay = 0, ...props }) {
  const interactive = typeof className === "string" && className.includes("card-interactive");
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 20, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.4, delay, ease: [0.22, 1, 0.36, 1] }}
      {...(interactive
        ? { whileHover: { y: -2, boxShadow: "0 8px 32px rgba(15, 58, 95, 0.12)" } }
        : {})}
      {...props}
    >
      {children}
    </motion.div>
  );
}
