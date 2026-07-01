import { Link } from "react-router-dom";

export default function NotFoundPage() {
  return (
    <div className="centered">
      <div className="card">
        <h1>404</h1>
        <p>That page doesn’t exist.</p>
        <Link to="/">Back to dashboard</Link>
      </div>
    </div>
  );
}
