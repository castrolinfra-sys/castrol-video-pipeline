import { Bar, Loading } from "../skeleton";

// Usage has its own shape — headline, two stat rows, chart, table — so it gets
// its own skeleton rather than the generic table one. A skeleton that does not
// match what arrives is its own kind of jolt.
export default function UsageLoading() {
  return (
    <Loading>
      <div className="page-head">
        <Bar width="90px" />
      </div>

      <div className="headline card">
        <Bar width="180px" />
        <div style={{ marginTop: 10 }}>
          <Bar width="240px" />
        </div>
      </div>

      {[0, 1].map((row) => (
        <div className="stats" key={row}>
          {Array.from({ length: 4 }, (_, i) => (
            <div className="card stat" key={i}>
              <Bar width="70%" />
              <div style={{ marginTop: 10 }}>
                <Bar width="45%" />
              </div>
            </div>
          ))}
        </div>
      ))}

      <div className="card" style={{ height: 260 }}>
        <Bar width="30%" />
      </div>
    </Loading>
  );
}
