import { Link, Outlet } from "react-router-dom";
import { useAuth } from "../lib/auth.jsx";

// Shared app shell: top bar + routed content via <Outlet/>.
export default function Layout() {
  const { user, logout } = useAuth();
  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          🤖 JARVIS
        </Link>
        <nav className="nav">
          <Link to="/">Chat</Link>
          <Link to="/memory">Memory</Link>
          <Link to="/status">Status</Link>
          {user && (
            <button className="link-btn" onClick={logout}>
              Log out ({user.username})
            </button>
          )}
        </nav>
      </header>
   