import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../lib/auth.jsx";

// Gates child routes behind authentication. Redirects to /login otherwise.
export default function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) return <div className="centered">Loading…</div>;
  if (!user) return <Navigate to="/login" state={{ from: location }} replace />;
  return children;
}
